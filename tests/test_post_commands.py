from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from discord_ai_reminder_bot.application.schedule_creation import CreatedOnceSchedule
from discord_ai_reminder_bot.application.schedule_queries import ScheduleView
from discord_ai_reminder_bot.bot.posts import (
    DISCORD_MESSAGE_LIMIT,
    EMPTY_PAGE_MESSAGE,
    INTERNAL_ERROR_MESSAGE,
    NOT_FOUND_MESSAGE,
    PostCommands,
    format_schedule_detail,
    format_schedule_list,
)
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType

GUILD_ID = 100
USER_ID = 300
ROLE_ID = 200
NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)


def interaction(*, administrator: bool = False, done: bool = False) -> MagicMock:
    value = MagicMock(spec=discord.Interaction)
    value.guild_id = GUILD_ID
    value.guild = MagicMock(spec=discord.Guild)
    value.guild.id = GUILD_ID
    member = MagicMock(spec=discord.Member)
    member.id = USER_ID
    member.guild = value.guild
    member.guild_permissions = MagicMock(spec=discord.Permissions)
    member.guild_permissions.administrator = administrator
    role = MagicMock(spec=discord.Role)
    role.id = ROLE_ID
    role.guild = value.guild
    member.roles = [role]
    value.user = member
    value.response = MagicMock(spec=discord.InteractionResponse)
    value.response.is_done.return_value = done
    value.response.send_message = AsyncMock()
    value.followup = MagicMock(spec=discord.Webhook)
    value.followup.send = AsyncMock()
    value.extras = {}
    return value


def view(*, content: str | None = "本文", status: ScheduleStatus = ScheduleStatus.ACTIVE):
    return ScheduleView(
        public_id=uuid.uuid7(),
        channel_id=400,
        creator_user_id=USER_ID,
        schedule_type=ScheduleType.ONCE,
        status=status,
        content=content,
        next_run_at=datetime(2026, 8, 20, 10, 30, tzinfo=UTC),
        local_time=None,
        weekday=None,
        end_date=None,
    )


def commands(queries: AsyncMock, *, session: MagicMock | None = None) -> PostCommands:
    session = session or MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=transaction)
    transaction.__aexit__ = AsyncMock(return_value=None)
    session.begin.return_value = transaction
    return PostCommands(
        queries=queries,
        session_factory=lambda: session,  # type: ignore[arg-type]
        clock=FixedClock(NOW),
        configured_guild_id=GUILD_ID,
        allowed_role_ids=(ROLE_ID,),
        logger=logging.getLogger("test.posts"),
    )


def text_channel(value: MagicMock, *, guild_id: int = GUILD_ID) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 400
    channel.guild = MagicMock(spec=discord.Guild)
    channel.guild.id = guild_id
    permissions = MagicMock(spec=discord.Permissions)
    permissions.view_channel = True
    permissions.send_messages = True
    channel.permissions_for.return_value = permissions
    value.guild.me = MagicMock(spec=discord.Member)
    return channel


@pytest.mark.asyncio
async def test_create_defers_then_commits_and_uses_interaction_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries = AsyncMock()
    group = commands(queries)
    value = interaction()
    value.response.defer = AsyncMock()
    value.response.is_done.return_value = True
    channel = text_channel(value)
    created = CreatedOnceSchedule(
        public_id=uuid.uuid7(),
        channel_id=channel.id,
        status=ScheduleStatus.ACTIVE,
        content="line 1\nline 2",
        scheduled_for=datetime(2026, 8, 20, 10, 30, tzinfo=UTC),
    )
    service = AsyncMock()
    service.create.return_value = created
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.OnceScheduleCreationService", lambda unused: service
    )

    await group.create_command.callback(
        group,
        value,
        channel,
        "2026-08-20 19:30",
        "line 1\nline 2",
        False,
    )

    value.response.defer.assert_awaited_once_with(ephemeral=True)
    service.create.assert_awaited_once()
    arguments = service.create.await_args.kwargs
    assert arguments["guild_id"] == value.guild_id == GUILD_ID
    assert arguments["creator_user_id"] == value.user.id == USER_ID
    assert arguments["channel_id"] == channel.id
    assert arguments["scheduled_for"] == datetime(2026, 8, 20, 10, 30, tzinfo=UTC)
    value.followup.send.assert_awaited_once()
    assert value.followup.send.await_args.kwargs["ephemeral"] is True
    assert value.followup.send.await_args.kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert "line 1 line 2" in value.followup.send.await_args.args[0]


@pytest.mark.parametrize("kind", ["other_guild", "voice", "view", "send"])
@pytest.mark.asyncio
async def test_create_rejects_invalid_channel_before_defer(kind: str) -> None:
    group = commands(AsyncMock())
    value = interaction()
    value.response.defer = AsyncMock()
    channel = text_channel(value, guild_id=999 if kind == "other_guild" else GUILD_ID)
    if kind == "voice":
        channel = MagicMock(spec=discord.VoiceChannel)
    elif kind in {"view", "send"}:
        permissions = channel.permissions_for.return_value
        setattr(permissions, "view_channel" if kind == "view" else "send_messages", False)
    await group.create_command.callback(group, value, channel, "2026-08-20 19:30", "body", False)
    value.response.defer.assert_not_awaited()
    value.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_database_failure_rolls_back_and_returns_safe_followup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "postgresql+psycopg://user:password@localhost/private"
    session = MagicMock()
    group = commands(AsyncMock(), session=session)
    value = interaction()
    value.response.defer = AsyncMock()
    value.response.is_done.return_value = True
    channel = text_channel(value)
    service = AsyncMock()
    service.create.side_effect = RuntimeError(secret)
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.OnceScheduleCreationService", lambda unused: service
    )
    with caplog.at_level(logging.ERROR):
        await group.create_command.callback(
            group, value, channel, "2026-08-20 19:30", "body", False
        )
    assert value.followup.send.await_args.args == (INTERNAL_ERROR_MESSAGE,)
    assert secret not in caplog.text
    assert session.begin.return_value.__aexit__.await_args.args[0] is RuntimeError


@pytest.mark.asyncio
async def test_creator_list_responds_ephemerally_without_mentions() -> None:
    queries = AsyncMock()
    queries.list_schedules.return_value = [view()]
    group = commands(queries)
    value = interaction()
    await group.list_command.callback(group, value, None, 1)
    queries.list_schedules.assert_awaited_once_with(
        guild_id=GUILD_ID,
        requester_user_id=USER_ID,
        administrator=False,
        status=None,
        page=1,
    )
    kwargs = value.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}


@pytest.mark.asyncio
async def test_admin_list_passes_administrator_and_deleted_filter() -> None:
    queries = AsyncMock()
    queries.list_schedules.return_value = []
    group = commands(queries)
    value = interaction(administrator=True)
    choice = app_commands.Choice(name="削除済み", value="deleted")
    await group.list_command.callback(group, value, choice, 2)
    queries.list_schedules.assert_awaited_once_with(
        guild_id=GUILD_ID,
        requester_user_id=USER_ID,
        administrator=True,
        status=ScheduleStatus.DELETED,
        page=2,
    )
    assert value.response.send_message.await_args.args == (EMPTY_PAGE_MESSAGE,)


@pytest.mark.asyncio
async def test_show_missing_invalid_and_unauthorized_share_safe_response() -> None:
    queries = AsyncMock()
    queries.show_schedule.return_value = None
    group = commands(queries)
    for public_id in (str(uuid.uuid7()), "invalid"):
        value = interaction()
        await group.show_command.callback(group, value, public_id)
        assert value.response.send_message.await_args.args == (NOT_FOUND_MESSAGE,)


@pytest.mark.asyncio
async def test_show_uses_followup_when_interaction_already_responded() -> None:
    queries = AsyncMock()
    queries.show_schedule.return_value = view(status=ScheduleStatus.DELETED)
    group = commands(queries)
    value = interaction(done=True)
    await group.show_command.callback(group, value, str(uuid.uuid7()))
    value.followup.send.assert_awaited_once()
    assert value.followup.send.await_args.kwargs["ephemeral"] is True
    assert value.followup.send.await_args.kwargs["allowed_mentions"].to_dict() == {"parse": []}


@pytest.mark.asyncio
async def test_database_error_returns_safe_message_and_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "postgresql+psycopg://user:password@localhost/private"
    queries = AsyncMock()
    queries.list_schedules.side_effect = RuntimeError(secret)
    group = commands(queries)
    value = interaction()
    with caplog.at_level(logging.ERROR):
        await group.list_command.callback(group, value, None, 1)
    assert value.response.send_message.await_args.args == (INTERNAL_ERROR_MESSAGE,)
    assert secret not in caplog.text


def test_list_is_stable_bounded_and_converts_utc_to_tokyo() -> None:
    schedules = [view(content=f"本文{i}") for i in range(10)]
    rendered = format_schedule_list(schedules, page=1)
    positions = [rendered.index(str(item.public_id)) for item in schedules]
    assert positions == sorted(positions)
    assert "2026-08-20 19:30 JST" in rendered
    assert len(rendered) <= DISCORD_MESSAGE_LIMIT


def test_list_shows_only_content_prefix() -> None:
    secret_tail = "do-not-show-full-body"
    rendered = format_schedule_list([view(content="a" * 50 + secret_tail)], page=1)
    assert secret_tail not in rendered


def test_list_preview_collapses_line_breaks_without_changing_structure() -> None:
    rendered = format_schedule_list([view(content="first\nsecond\r\nthird")], page=1)
    assert "本文: first second third" in rendered
    assert rendered.count("本文:") == 1


def test_detail_truncates_content_to_discord_limit() -> None:
    rendered = format_schedule_detail(view(content="x" * 2_000))
    assert len(rendered) == DISCORD_MESSAGE_LIMIT
    assert rendered.endswith("…")
