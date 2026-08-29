from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands
from discord.app_commands.namespace import ResolveKey
from discord.ui.view import ViewStore

from discord_ai_reminder_bot.application.schedule_creation import (
    CreatedOnceSchedule,
    CreatedRecurringSchedule,
)
from discord_ai_reminder_bot.application.schedule_deletion import (
    DeletedSchedule,
    DeleteReasonRequired,
    ScheduleDeletionUnavailable,
    ScheduleDeletionView,
)
from discord_ai_reminder_bot.application.schedule_editing import (
    EditedSchedule,
    ScheduleEditNoChanges,
    ScheduleEditVersionConflict,
)
from discord_ai_reminder_bot.application.schedule_pause import (
    PausedSchedule,
    ResumedSchedule,
    ResumePreview,
    ScheduleStateChangeUnavailable,
)
from discord_ai_reminder_bot.application.schedule_queries import (
    ScheduleActionAvailability,
    ScheduleActionReason,
    ScheduleAutocompleteView,
    ScheduleDetail,
    SchedulePage,
    ScheduleView,
)
from discord_ai_reminder_bot.bot.post_presenter import LIST_OPERATION_GUIDANCE
from discord_ai_reminder_bot.bot.post_views import (
    DETAIL_BACK_CUSTOM_ID,
    DETAIL_EDIT_CUSTOM_ID,
    ScheduleListOrigin,
)
from discord_ai_reminder_bot.bot.posts import (
    CREATE_CANCELLED_MESSAGE,
    CREATE_DATETIME_DESCRIPTION,
    CREATE_EXPIRED_MESSAGE,
    CREATE_UNAVAILABLE_MESSAGE,
    DATETIME_INPUT_MESSAGE,
    DELETE_CANCELLED_MESSAGE,
    DELETE_EXPIRED_MESSAGE,
    DELETE_REASON_REQUIRED_MESSAGE,
    DELETE_UNAVAILABLE_MESSAGE,
    DETAIL_CONFLICT_MESSAGE,
    DETAIL_EDIT_CHANNEL_MESSAGE,
    DETAIL_EDIT_NO_CHANGES_MESSAGE,
    EDIT_NO_CHANGES_MESSAGE,
    EDIT_REQUEST_REQUIRED_MESSAGE,
    END_DATE_DESCRIPTION,
    END_DATE_INPUT_MESSAGE,
    FULLWIDTH_DATETIME_INPUT_MESSAGE,
    INTERNAL_ERROR_MESSAGE,
    NOT_FOUND_MESSAGE,
    PERMISSION_DENIED_MESSAGE,
    STATE_CHANGE_UNAVAILABLE_MESSAGE,
    InteractionActor,
    PostCommands,
    ResumeChoiceView,
    ScheduleEditModal,
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
    value.edit_original_response = AsyncMock()
    value.response.defer = AsyncMock()
    value.response.edit_message = AsyncMock()
    value.response.send_modal = AsyncMock()
    value.response.autocomplete = AsyncMock()
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
        version=1,
    )


def detail(schedule: ScheduleView) -> ScheduleDetail:
    return ScheduleDetail(
        schedule=schedule,
        actions=ScheduleActionAvailability(
            can_edit=True,
            can_pause=False,
            can_resume=False,
            can_delete=True,
            reason_code=ScheduleActionReason.AVAILABLE,
            observed_version=schedule.version,
        ),
    )


def detail_with_actions(
    schedule: ScheduleView, *, pause: bool = False, resume: bool = False, delete: bool = True
) -> ScheduleDetail:
    return ScheduleDetail(
        schedule=schedule,
        actions=ScheduleActionAvailability(
            can_edit=False,
            can_pause=pause,
            can_resume=resume,
            can_delete=delete,
            reason_code=ScheduleActionReason.AVAILABLE,
            observed_version=schedule.version,
        ),
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


def cached_text_channel(guild: MagicMock, channel_id: int = 400) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.guild = guild
    permissions = MagicMock(spec=discord.Permissions)
    permissions.view_channel = permissions.send_messages = True
    channel.permissions_for.return_value = permissions
    return channel


def app_command_channel(channel_id: int = 400) -> app_commands.AppCommandChannel:
    return app_commands.AppCommandChannel(
        state=MagicMock(),
        guild_id=GUILD_ID,
        data={
            "id": str(channel_id),
            "type": discord.ChannelType.text.value,
            "name": "submitted",
            "permissions": "0",
        },
    )


def autocomplete_view(*, status=ScheduleStatus.ACTIVE, schedule_type=ScheduleType.DAILY):
    return ScheduleAutocompleteView(
        public_id=uuid.uuid7(),
        channel_id=400,
        creator_user_id=USER_ID,
        schedule_type=schedule_type,
        status=status,
        display_at=None if status is ScheduleStatus.PAUSED else NOW,
    )


def test_all_public_id_commands_register_autocomplete() -> None:
    for command in (
        PostCommands.show_command,
        PostCommands.edit_command,
        PostCommands.delete_command,
        PostCommands.pause_command,
        PostCommands.resume_command,
    ):
        parameter = command.get_parameter("public_id")
        assert parameter is not None and parameter.autocomplete is not None
        assert parameter.required is True


@pytest.mark.asyncio
async def test_autocomplete_returns_full_uuid_and_admin_scope() -> None:
    queries = AsyncMock()
    item = autocomplete_view()
    queries.autocomplete_schedules.return_value = (item,)
    group = commands(queries)
    value = interaction(administrator=True)
    value.guild.get_channel.return_value = MagicMock(name="channel", name_attr="unused")
    value.guild.get_channel.return_value.name = "一般"

    choices = await group.show_public_id_autocomplete(value, "")

    assert choices[0].value == str(item.public_id)
    assert "#一般" in choices[0].name
    assert len(choices[0].name) <= 100
    assert "本文" not in choices[0].name
    assert queries.autocomplete_schedules.await_args.kwargs["administrator"] is True


@pytest.mark.asyncio
async def test_autocomplete_failure_is_empty_and_logs_only_fixed_event(caplog) -> None:
    queries = AsyncMock()
    secret = "sensitive-exception-detail-that-must-not-be-logged"
    queries.autocomplete_schedules.side_effect = RuntimeError(secret)
    group = commands(queries)

    with caplog.at_level(logging.ERROR, logger="test.posts"):
        choices = await group.edit_public_id_autocomplete(interaction(), "")

    assert choices == []
    assert "schedule_autocomplete_failed" in caplog.text
    assert secret not in caplog.text


def cached_channel(
    value: MagicMock,
    *,
    channel_id: int,
    name: str,
    visible: bool = True,
    guild_id: int = GUILD_ID,
) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = name
    channel.guild = MagicMock(spec=discord.Guild)
    channel.guild.id = guild_id
    permissions = MagicMock(spec=discord.Permissions)
    permissions.view_channel = visible
    channel.permissions_for.return_value = permissions
    return channel


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("tester-a", frozenset({401})),
        ("tester", frozenset({401, 402})),
        ("ster-a", frozenset({401})),
        ("#tester-a", frozenset({401})),
        ("TESTER-A", frozenset({401})),
        ("お知らせ", frozenset({403, 404})),
        ("#一般", frozenset({405})),
    ],
)
async def test_autocomplete_resolves_visible_cached_text_channel_names(current, expected) -> None:
    queries = AsyncMock()
    queries.autocomplete_schedules.return_value = ()
    group = commands(queries)
    value = interaction()
    category = MagicMock(spec=discord.CategoryChannel)
    category.name = "tester-category"
    value.guild.text_channels = [
        cached_channel(value, channel_id=401, name="tester-a"),
        cached_channel(value, channel_id=402, name="tester-b"),
        cached_channel(value, channel_id=403, name="運営お知らせ"),
        cached_channel(value, channel_id=404, name="お知らせ"),
        cached_channel(value, channel_id=405, name="一般"),
        cached_channel(value, channel_id=406, name="tester-secret", visible=False),
        cached_channel(value, channel_id=407, name="tester-other", guild_id=GUILD_ID + 1),
        category,
    ]

    await group.show_public_id_autocomplete(value, current)

    assert queries.autocomplete_schedules.await_args.kwargs["channel_ids"] == expected
    category.permissions_for.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("current", ["x\n", "x\x00", "x\u200b", "x" * 101])
async def test_autocomplete_rejects_unsafe_channel_search_without_query(current) -> None:
    queries = AsyncMock()
    group = commands(queries)
    assert await group.show_public_id_autocomplete(interaction(), current) == []
    queries.autocomplete_schedules.assert_not_awaited()


@pytest.mark.asyncio
async def test_autocomplete_uses_cache_only_and_cache_failure_is_safe() -> None:
    queries = AsyncMock()
    group = commands(queries)
    value = interaction()
    type(value.guild).text_channels = property(lambda unused: (_ for _ in ()).throw(RuntimeError()))
    value.guild.fetch_channel = AsyncMock()

    assert await group.show_public_id_autocomplete(value, "tester") == []
    value.guild.fetch_channel.assert_not_awaited()
    queries.autocomplete_schedules.assert_not_awaited()


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
    value.guild.get_channel.return_value = channel
    return channel


async def create_confirmation(group: PostCommands, value: MagicMock, channel: MagicMock):
    value.response.is_done.return_value = True
    await group.create_command.callback(group, value, channel, "8/20 19:30", "body", False)
    return value.followup.send.await_args.kwargs["view"]


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

    service.create.assert_not_awaited()
    confirmation = value.followup.send.await_args.kwargs
    assert confirmation["ephemeral"] is True
    assert confirmation["allowed_mentions"].to_dict() == {"parse": []}
    assert confirmation["embed"].title == "単発予約を確認してください"
    assert "2026-08-20 19:30 JST" in str(confirmation["embed"].to_dict())
    create_view = confirmation["view"]
    await group._confirm_once_creation(create_view, value)
    value.response.defer.assert_awaited_once_with(ephemeral=True)
    service.create.assert_awaited_once()
    arguments = service.create.await_args.kwargs
    assert arguments["guild_id"] == value.guild_id == GUILD_ID
    assert arguments["creator_user_id"] == value.user.id == USER_ID
    assert arguments["channel_id"] == channel.id
    assert arguments["scheduled_for"] == datetime(2026, 8, 20, 10, 30, tzinfo=UTC)
    embed = value.edit_original_response.await_args.kwargs["embed"]
    assert embed.title == "単発予約を作成しました"
    assert "line 1 line 2" in embed.fields[3].value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scheduled_at",
    ["今日12:05", "明日09:00", "今日 12:05", " 今日12:05 ", "今日   12:05", "今日\u300012:05"],
)
async def test_create_short_datetime_shows_confirmation_without_database(
    scheduled_at: str,
) -> None:
    session = MagicMock()
    group = commands(AsyncMock(), session=session)
    value = interaction()
    channel = text_channel(value)

    await group.create_command.callback(group, value, channel, scheduled_at, "body", False)

    session.__aenter__.assert_not_awaited()
    kwargs = value.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert kwargs["embed"].title == "単発予約を確認してください"
    assert [item.label for item in kwargs["view"].children] == ["予約する", "キャンセル"]


@pytest.mark.asyncio
async def test_create_invalid_datetime_uses_specific_safe_response() -> None:
    group = commands(AsyncMock())
    value = interaction()
    channel = text_channel(value)

    await group.create_command.callback(group, value, channel, "今夜 22:30", "body", False)

    assert value.response.send_message.await_args.args == (DATETIME_INPUT_MESSAGE,)
    kwargs = value.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("scheduled_at", ["今日２１:００", "今日21：00", "８／２５ 19:30"])
async def test_create_fullwidth_datetime_uses_halfwidth_guidance_without_side_effects(
    scheduled_at: str,
) -> None:
    session = MagicMock()
    group = commands(AsyncMock(), session=session)
    value = interaction()
    channel = text_channel(value)

    await group.create_command.callback(group, value, channel, scheduled_at, "body", False)

    assert value.response.send_message.await_args.args == (FULLWIDTH_DATETIME_INPUT_MESSAGE,)
    kwargs = value.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert "view" not in kwargs
    session.__aenter__.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("scheduled_at", ["今日9:00", "明日の9時", "8/2519:30"])
async def test_create_other_invalid_datetime_uses_general_guidance_without_side_effects(
    scheduled_at: str,
) -> None:
    session = MagicMock()
    group = commands(AsyncMock(), session=session)
    value = interaction()
    channel = text_channel(value)

    await group.create_command.callback(group, value, channel, scheduled_at, "body", False)

    assert value.response.send_message.await_args.args == (DATETIME_INPUT_MESSAGE,)
    kwargs = value.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert "view" not in kwargs
    session.__aenter__.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_five_minute_boundary_is_not_classified_as_fullwidth() -> None:
    group = commands(AsyncMock())
    value = interaction()
    channel = text_channel(value)

    await group.create_command.callback(group, value, channel, "今日12:04", "body", False)

    assert value.response.send_message.await_args.args == (DATETIME_INPUT_MESSAGE,)
    assert value.response.send_message.await_args.args != (FULLWIDTH_DATETIME_INPUT_MESSAGE,)


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
    assert value.response.send_message.await_args.args == ("入力内容を確認してください。",)


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
        create_view = value.followup.send.await_args.kwargs["view"]
        await group._confirm_once_creation(create_view, value)
    assert value.edit_original_response.await_args.kwargs["content"] == INTERNAL_ERROR_MESSAGE
    assert secret not in caplog.text
    assert session.begin.return_value.__aexit__.await_args.args[0] is RuntimeError


@pytest.mark.asyncio
async def test_create_cancel_and_timeout_never_open_database() -> None:
    session = MagicMock()
    group = commands(AsyncMock(), session=session)
    value = interaction()
    channel = text_channel(value)
    create_view = await create_confirmation(group, value, channel)
    await group._cancel_once_creation(create_view, value)
    session.__aenter__.assert_not_awaited()
    assert value.response.edit_message.await_args.kwargs["content"] == CREATE_CANCELLED_MESSAGE
    assert value.response.edit_message.await_args.kwargs["view"] is None

    timeout_value = interaction()
    timeout_channel = text_channel(timeout_value)
    timeout_view = await create_confirmation(group, timeout_value, timeout_channel)
    await group._expire_once_creation(timeout_view)
    session.__aenter__.assert_not_awaited()
    assert (
        timeout_value.edit_original_response.await_args.kwargs["content"] == CREATE_EXPIRED_MESSAGE
    )


@pytest.mark.asyncio
async def test_create_view_rejects_other_user_and_permission_loss() -> None:
    group = commands(AsyncMock())
    value = interaction()
    channel = text_channel(value)
    create_view = await create_confirmation(group, value, channel)
    stranger = interaction()
    stranger.user.id = USER_ID + 1
    assert await create_view.interaction_check(stranger) is False
    stranger.response.send_message.assert_awaited_once()

    channel.permissions_for.return_value.send_messages = False
    await group._confirm_once_creation(create_view, value)
    assert value.response.edit_message.await_args.kwargs["content"] == CREATE_UNAVAILABLE_MESSAGE


@pytest.mark.asyncio
async def test_create_double_confirmation_creates_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = commands(AsyncMock())
    value = interaction()
    channel = text_channel(value)
    create_view = await create_confirmation(group, value, channel)
    service = AsyncMock()
    service.create.return_value = CreatedOnceSchedule(
        public_id=uuid.uuid7(),
        channel_id=channel.id,
        status=ScheduleStatus.ACTIVE,
        content="body",
        scheduled_for=datetime(2026, 8, 20, 10, 30, tzinfo=UTC),
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.OnceScheduleCreationService", lambda unused: service
    )
    await group._confirm_once_creation(create_view, value)
    await group._confirm_once_creation(create_view, value)
    service.create.assert_awaited_once()


def test_create_option_description_and_other_command_ranges_are_stable() -> None:
    assert (
        CREATE_DATETIME_DESCRIPTION
        == "数字・記号は半角｜例：今日21:00、8/25 19:30、2027-08-25 19:30"
    )
    assert len(CREATE_DATETIME_DESCRIPTION) <= 100
    create_scheduled_at = PostCommands.create_command.parameters[1]
    assert create_scheduled_at.name == "scheduled_at"
    assert create_scheduled_at.min_value == 7
    assert create_scheduled_at.max_value == 16
    assert PostCommands.create_daily_command.parameters[1].name == "local_time"
    assert PostCommands.create_weekly_command.parameters[2].name == "local_time"
    assert PostCommands.edit_command.parameters[2].name == "scheduled_at"
    for command in (
        PostCommands.create_daily_command,
        PostCommands.create_weekly_command,
        PostCommands.edit_command,
    ):
        parameter = next(item for item in command.parameters if item.name == "end_date")
        assert parameter.description == END_DATE_DESCRIPTION
        assert len(parameter.description) <= 100
        assert (parameter.min_value, parameter.max_value) == (2, 10)


@pytest.mark.asyncio
async def test_daily_short_end_date_is_normalized_before_database(monkeypatch) -> None:
    group = commands(AsyncMock())
    value = interaction(done=True)
    channel = text_channel(value)
    service = AsyncMock()
    service.create.return_value = CreatedRecurringSchedule(
        public_id=uuid.uuid7(),
        channel_id=channel.id,
        schedule_type=ScheduleType.DAILY,
        status=ScheduleStatus.ACTIVE,
        content="body",
        local_time=time(12, 5),
        weekday=None,
        end_date=date(2026, 8, 19),
        next_run_at=NOW.replace(minute=5),
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.RecurringScheduleCreationService",
        lambda unused: service,
    )
    await group.create_daily_command.callback(group, value, channel, "12:05", "明日", "body", False)
    assert service.create.await_args.kwargs["end_date"] == date(2026, 8, 19)
    embed = value.followup.send.await_args.kwargs["embed"]
    assert "2026-08-19" in str(embed.to_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("schedule_type", "weekday"),
    [(ScheduleType.DAILY, None), (ScheduleType.WEEKLY, 1)],
)
async def test_recurring_create_uses_safe_boundary_and_interaction_identity(
    monkeypatch: pytest.MonkeyPatch,
    schedule_type: ScheduleType,
    weekday: int | None,
) -> None:
    group = commands(AsyncMock())
    value = interaction()
    value.response.defer = AsyncMock()
    value.response.is_done.return_value = True
    channel = text_channel(value)
    created = CreatedRecurringSchedule(
        public_id=uuid.uuid7(),
        channel_id=channel.id,
        schedule_type=schedule_type,
        status=ScheduleStatus.ACTIVE,
        content="body",
        local_time=time(12, 5),
        weekday=weekday,
        end_date=date(2026, 8, 31),
        next_run_at=NOW.replace(minute=5),
    )
    service = AsyncMock()
    service.create.return_value = created
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.RecurringScheduleCreationService",
        lambda unused: service,
    )
    if schedule_type is ScheduleType.DAILY:
        await group.create_daily_command.callback(
            group, value, channel, "12:05", "2026-08-31", "body", False
        )
    else:
        choice = app_commands.Choice(name="火曜日", value=1)
        await group.create_weekly_command.callback(
            group, value, channel, choice, "12:05", "2026-08-31", "body", False
        )
    value.response.defer.assert_awaited_once_with(ephemeral=True)
    arguments = service.create.await_args.kwargs
    assert arguments["guild_id"] == value.guild_id
    assert arguments["creator_user_id"] == value.user.id
    assert arguments["schedule_type"] is schedule_type
    assert arguments["local_time"] == time(12, 5)
    assert arguments["weekday"] == weekday
    assert arguments["end_date"] == date(2026, 8, 31)
    kwargs = value.followup.send.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert kwargs["embed"].title.startswith("毎")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local_time_value", "end_date_value"),
    [("9:00", None), ("09:00", "2026-2-03"), ("25:00", "2026-08-31")],
)
async def test_recurring_create_rejects_invalid_local_input_before_defer(
    local_time_value: str, end_date_value: str | None
) -> None:
    group = commands(AsyncMock())
    value = interaction()
    value.response.defer = AsyncMock()
    channel = text_channel(value)
    await group.create_daily_command.callback(
        group, value, channel, local_time_value, end_date_value, "body", False
    )
    value.response.defer.assert_not_awaited()
    value.response.send_message.assert_awaited_once()


def test_weekly_command_exposes_all_seven_japanese_weekday_choices() -> None:
    group = commands(AsyncMock())
    parameter = next(
        item for item in group.create_weekly_command.parameters if item.name == "weekday"
    )
    assert [(choice.name, choice.value) for choice in parameter.choices] == [
        ("月曜日", 0),
        ("火曜日", 1),
        ("水曜日", 2),
        ("木曜日", 3),
        ("金曜日", 4),
        ("土曜日", 5),
        ("日曜日", 6),
    ]


@pytest.mark.asyncio
async def test_creator_list_responds_ephemerally_without_mentions() -> None:
    queries = AsyncMock()
    queries.get_schedule_page.return_value = SchedulePage((view(),), 1, 1)
    group = commands(queries)
    value = interaction()
    await group.list_command.callback(group, value, None, 1)
    queries.get_schedule_page.assert_awaited_once_with(
        guild_id=GUILD_ID,
        requester_user_id=USER_ID,
        administrator=False,
        status=None,
        page=1,
        schedule_type=None,
        clamp=False,
    )
    kwargs = value.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert LIST_OPERATION_GUIDANCE in kwargs["embed"].description
    assert kwargs["view"].timeout is None


@pytest.mark.asyncio
async def test_admin_list_passes_administrator_and_deleted_filter() -> None:
    queries = AsyncMock()
    queries.get_schedule_page.return_value = SchedulePage((), 2, 12)
    group = commands(queries)
    value = interaction(administrator=True)
    choice = app_commands.Choice(name="削除済み", value="deleted")
    await group.list_command.callback(group, value, choice, 2)
    queries.get_schedule_page.assert_awaited_once_with(
        guild_id=GUILD_ID,
        requester_user_id=USER_ID,
        administrator=True,
        status=ScheduleStatus.DELETED,
        page=2,
        schedule_type=None,
        clamp=False,
    )
    embed = value.response.send_message.await_args.kwargs["embed"]
    assert embed.title == "予約一覧"
    assert "表示できる予約はありません" in embed.fields[0].value


@pytest.mark.asyncio
async def test_list_view_navigation_refreshes_and_clamps_latest_page() -> None:
    queries = AsyncMock()
    first = SchedulePage(tuple(view() for _ in range(10)), 2, 24)
    refreshed = SchedulePage((view(),), 2, 11)
    queries.get_schedule_page.side_effect = [first, refreshed]
    group = commands(queries)
    original = interaction()
    await group.list_command.callback(group, original, None, 2)
    list_view = original.response.send_message.await_args.kwargs["view"]
    assert [item.label for item in list_view.children[:2]] == ["前へ", "次へ"]
    assert all(not item.disabled for item in list_view.children[:2])
    assert len(list_view.children[2].options) == 4
    assert len(list_view.children[3].options) == 10

    clicked = interaction()
    await group._move_list_page(list_view, clicked, 3)
    assert queries.get_schedule_page.await_args.kwargs["clamp"] is True
    assert list_view.page == 2
    assert list_view.children[1].disabled is True
    kwargs = clicked.response.edit_message.await_args.kwargs
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}


@pytest.mark.asyncio
async def test_list_selection_shows_detail_and_back_refreshes() -> None:
    selected = view()
    queries = AsyncMock()
    queries.get_schedule_page.side_effect = [
        SchedulePage((selected,), 1, 1),
        SchedulePage((selected,), 1, 1),
    ]
    queries.get_schedule_detail.return_value = detail(selected)
    group = commands(queries)
    original = interaction()
    await group.list_command.callback(group, original, None, 1)
    list_view = original.response.send_message.await_args.kwargs["view"]

    clicked = interaction()
    await group._show_list_selection(list_view, clicked, str(selected.public_id))
    detail_view = clicked.response.edit_message.await_args.kwargs["view"]
    assert clicked.response.edit_message.await_args.kwargs["embed"].title == "予約詳細"
    assert [item.label for item in detail_view.children] == ["編集", "削除", "一覧へ戻る"]
    assert detail_view.context.expected_version == selected.version
    assert list_view.is_finished()
    assert list_view not in group._list_views
    assert detail_view in group._detail_views

    back = interaction()
    await group._return_to_list(detail_view, back)
    assert back.response.edit_message.await_args.kwargs["embed"].title == "予約一覧"
    assert detail_view.is_finished()
    assert detail_view not in group._detail_views
    assert back.response.edit_message.await_args.kwargs["view"] in group._list_views


@pytest.mark.parametrize(
    ("pause", "resume", "delete", "labels", "delete_disabled"),
    [
        (True, False, True, ["編集", "一時停止", "削除"], False),
        (False, True, True, ["編集", "再開", "削除"], False),
        (False, False, True, ["編集", "削除"], False),
        (False, False, False, ["編集", "削除"], True),
    ],
)
def test_detail_action_buttons_follow_read_only_availability(
    pause: bool,
    resume: bool,
    delete: bool,
    labels: list[str],
    delete_disabled: bool,
) -> None:
    selected = view()
    group = commands(AsyncMock())
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail_with_actions(selected, pause=pause, resume=resume, delete=delete),
        embed=discord.Embed(title="予約詳細"),
    )
    assert [item.label for item in detail_view.children] == labels
    assert detail_view.children[-1].disabled is delete_disabled
    assert all(str(selected.public_id) not in str(item.custom_id) for item in detail_view.children)
    assert detail_view.children[0].disabled is True


@pytest.mark.parametrize(
    ("schedule_type", "top_labels"),
    [
        (ScheduleType.ONCE, ["投稿先", "投稿日時", "本文"]),
        (ScheduleType.DAILY, ["投稿先", "投稿時刻", "終了日", "本文"]),
        (ScheduleType.WEEKLY, ["投稿先", "曜日", "投稿時刻", "終了日", "本文"]),
    ],
)
def test_detail_edit_modal_has_type_specific_v2_labels_and_defaults(
    schedule_type: ScheduleType, top_labels: list[str]
) -> None:
    selected = replace(
        view(),
        schedule_type=schedule_type,
        local_time=time(10, 30) if schedule_type is not ScheduleType.ONCE else None,
        weekday=2 if schedule_type is ScheduleType.WEEKLY else None,
        end_date=date(2026, 9, 1) if schedule_type is not ScheduleType.ONCE else None,
    )
    group = commands(AsyncMock())
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = selected.channel_id
    modal = ScheduleEditModal(commands=group, detail_view=detail_view, default_channel=channel)
    assert modal.timeout == 900.0
    assert len(modal.children) == len(top_labels) <= 5
    assert [item.text for item in modal.children] == top_labels
    assert all(isinstance(item, discord.ui.Label) for item in modal.children)
    assert modal.channel.channel_types == [discord.ChannelType.text]
    assert modal.channel.min_values == 0 and modal.channel.required is False
    assert modal.content.default == "本文" and modal.content.max_length == 2_000
    if schedule_type is ScheduleType.ONCE:
        assert modal.scheduled_at.default == "2026-08-20 19:30"
    else:
        assert modal.local_time.default == "10:30"
        assert modal.end_date.default == "2026-09-01"
    if schedule_type is ScheduleType.WEEKLY:
        assert [option.default for option in modal.weekday.options] == [
            False,
            False,
            True,
            False,
            False,
            False,
            False,
        ]


@pytest.mark.asyncio
async def test_detail_edit_modal_submits_multiple_fields_and_clear_flags_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = replace(
        view(),
        schedule_type=ScheduleType.WEEKLY,
        local_time=time(10, 30),
        weekday=2,
        end_date=date(2026, 9, 1),
    )
    refreshed_schedule = replace(
        selected,
        channel_id=401,
        content=None,
        local_time=time(11),
        weekday=4,
        end_date=None,
        version=2,
    )
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = detail(refreshed_schedule)
    group = commands(queries)
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
        list_origin=ScheduleListOrigin(None, None, 2),
    )
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 401
    channel.guild = MagicMock(spec=discord.Guild)
    channel.guild.id = GUILD_ID
    permissions = MagicMock(spec=discord.Permissions)
    permissions.view_channel = permissions.send_messages = True
    channel.permissions_for.return_value = permissions
    modal = ScheduleEditModal(commands=group, detail_view=detail_view, default_channel=None)
    modal.channel._values = [channel]
    modal.local_time._value = "11:00"
    modal.weekday._values = ["4"]
    modal.end_date._value = ""
    modal.content._value = " \n"
    service = AsyncMock()
    service.edit.return_value = EditedSchedule(
        public_id=selected.public_id,
        channel_id=401,
        schedule_type=ScheduleType.WEEKLY,
        status=ScheduleStatus.DRAFT,
        content=None,
        next_run_at=selected.next_run_at,
        local_time=time(11),
        weekday=4,
        end_date=None,
        changed_fields=("channel_id", "content", "local_time", "weekday", "end_date"),
        pending_runs_skipped=1,
        run_replaced=True,
        retry_pending_preserved=False,
        previous_status=ScheduleStatus.ACTIVE,
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleEditingService", lambda unused: service
    )
    submitted = interaction()
    submitted.guild.get_channel.return_value = channel
    submitted.guild.me = MagicMock(spec=discord.Member)

    await group._submit_detail_edit(modal, submitted)

    values = service.edit.await_args.kwargs["values"]
    assert values.channel_id == 401 and values.local_time == time(11)
    assert values.weekday == 4 and values.weekday_supplied is True
    assert values.clear_content is True and values.content is None
    assert values.clear_end_date is True and values.end_date_supplied is False
    service.edit.assert_awaited_once()
    submitted.response.defer.assert_awaited_once_with()
    assert submitted.edit_original_response.await_args.kwargs["allowed_mentions"].to_dict() == {
        "parse": []
    }
    assert group._detail_views.pop().context.list_origin == ScheduleListOrigin(None, None, 2)


@pytest.mark.asyncio
async def test_detail_refresh_retires_old_view_before_registering_replacement() -> None:
    selected = view()
    refreshed_schedule = replace(selected, content="更新後", version=2)
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = detail(refreshed_schedule)
    group = commands(queries)
    original = interaction()
    old_view = group._build_detail_view(
        interaction=original,
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    group._detail_views.add(old_view)
    store = ViewStore(MagicMock())
    message_id = 42
    store.add_view(old_view, message_id)
    submitted = interaction()

    async def register_replacement(**kwargs: object) -> None:
        replacement = kwargs["view"]
        assert old_view.is_finished()
        store.add_view(replacement, message_id)  # type: ignore[arg-type]

    submitted.edit_original_response.side_effect = register_replacement

    await group._refresh_detail(
        old_view,
        submitted,
        InteractionActor(user_id=USER_ID, administrator=False),
        "予約を編集しました。",
    )

    assert old_view not in group._detail_views
    replacement = group._detail_views.pop()
    assert replacement is not old_view
    assert replacement.is_finished() is False
    assert replacement.timeout is None
    dispatch_item = store._views[message_id][
        (discord.ComponentType.button.value, DETAIL_EDIT_CUSTOM_ID)
    ]
    assert dispatch_item.view is replacement
    replacement.stop()


@pytest.mark.asyncio
async def test_second_edit_opens_modal_and_no_op_refreshes_same_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = replace(
        view(),
        content="更新後",
        version=2,
        schedule_type=ScheduleType.DAILY,
        local_time=time(19, 30),
        end_date=date(2026, 8, 30),
    )
    cleared = replace(selected, content=None, end_date=None, version=3)
    queries = AsyncMock()
    queries.get_schedule_detail.side_effect = [
        detail(selected),
        detail(selected),
        detail(cleared),
    ]
    group = commands(queries)
    origin = ScheduleListOrigin(ScheduleStatus.ACTIVE, ScheduleType.DAILY, 2)
    old_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(replace(selected, content="更新前", version=1)),
        embed=discord.Embed(title="予約詳細"),
        list_origin=origin,
    )
    group._detail_views.add(old_view)
    first_submit = interaction()
    await group._refresh_detail(
        old_view,
        first_submit,
        InteractionActor(user_id=USER_ID, administrator=False),
        "予約を編集しました。",
    )
    refreshed = next(iter(group._detail_views))

    clicked = interaction()
    clicked.guild.get_channel.return_value = cached_text_channel(clicked.guild)
    edit_button = next(
        item for item in refreshed.children if item.custom_id == DETAIL_EDIT_CUSTOM_ID
    )
    await edit_button.callback(clicked)

    modal = clicked.response.send_modal.await_args.args[0]
    assert modal.detail_view is refreshed
    modal.channel._handle_submit(interaction(), {"values": []}, {})
    modal.local_time._value = "19:30"
    modal.end_date._value = "2026-08-30"
    modal.content._value = "更新後"
    service = AsyncMock()
    service.edit.side_effect = [
        ScheduleEditNoChanges,
        EditedSchedule(
            public_id=selected.public_id,
            channel_id=selected.channel_id,
            schedule_type=ScheduleType.DAILY,
            status=ScheduleStatus.DRAFT,
            content=None,
            next_run_at=selected.next_run_at,
            local_time=selected.local_time,
            weekday=None,
            end_date=None,
            changed_fields=("content", "end_date"),
            pending_runs_skipped=1,
            run_replaced=True,
            retry_pending_preserved=False,
            previous_status=ScheduleStatus.ACTIVE,
        ),
    ]
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleEditingService", lambda unused: service
    )
    second_submit = interaction()
    second_submit.guild.get_channel.return_value = cached_text_channel(second_submit.guild)

    await modal.on_submit(second_submit)

    assert second_submit.edit_original_response.await_args.kwargs["content"] == (
        DETAIL_EDIT_NO_CHANGES_MESSAGE
    )
    assert refreshed.is_finished()
    latest = next(iter(group._detail_views))
    assert latest is not refreshed and not latest.is_finished()
    assert latest.context.expected_version == 2
    assert latest.context.public_id == selected.public_id
    assert latest.context.actor_user_id == USER_ID
    assert latest.context.list_origin == origin

    third_click = interaction()
    third_click.guild.get_channel.return_value = cached_text_channel(third_click.guild)
    edit_button = next(item for item in latest.children if item.custom_id == DETAIL_EDIT_CUSTOM_ID)
    await edit_button.callback(third_click)
    clear_modal = third_click.response.send_modal.await_args.args[0]
    clear_modal.channel._handle_submit(interaction(), {"values": []}, {})
    clear_modal.local_time._value = "19:30"
    clear_modal.end_date._value = ""
    clear_modal.content._value = ""
    clear_submit = interaction()
    clear_submit.guild.get_channel.return_value = cached_text_channel(clear_submit.guild)

    await clear_modal.on_submit(clear_submit)

    assert service.edit.await_args.kwargs["expected_version"] == 2
    assert service.edit.await_args.kwargs["values"].clear_content is True
    assert service.edit.await_args.kwargs["values"].clear_end_date is True
    newest = next(iter(group._detail_views))
    assert newest.context.expected_version == 3
    assert newest.context.content is None and newest.context.end_date is None
    assert newest.context.list_origin == origin
    assert second_submit.response.defer.await_args.kwargs == {}
    assert clear_submit.response.defer.await_args.kwargs == {}
    await group.close_confirmation_views()


def test_detail_edit_modal_refreshes_v2_label_components_with_untouched_defaults() -> None:
    selected = replace(
        view(),
        schedule_type=ScheduleType.WEEKLY,
        local_time=time(13, 40),
        weekday=2,
        end_date=date(2026, 8, 30),
    )
    group = commands(AsyncMock())
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    default_channel = cached_text_channel(interaction().guild)
    modal = ScheduleEditModal(
        commands=group, detail_view=detail_view, default_channel=default_channel
    )
    resolved_channel = app_command_channel()
    assert [item.id for item in modal.channel.default_values] == [400]

    modal._refresh(
        interaction(),
        [
            {
                "type": 18,
                "component": {
                    "type": 8,
                    "custom_id": "post_detail_edit_channel",
                    "values": ["400"],
                },
            },
            {
                "type": 18,
                "component": {
                    "type": 3,
                    "custom_id": "post_detail_edit_weekday",
                    "values": ["2"],
                },
            },
            {
                "type": 18,
                "component": {
                    "type": 4,
                    "custom_id": "post_detail_edit_local_time",
                    "value": "13:40",
                },
            },
            {
                "type": 18,
                "component": {
                    "type": 4,
                    "custom_id": "post_detail_edit_end_date",
                    "value": "2026-08-30",
                },
            },
            {
                "type": 18,
                "component": {
                    "type": 4,
                    "custom_id": "post_detail_edit_content",
                    "value": "",
                },
            },
        ],
        {ResolveKey(id="400", type=discord.AppCommandOptionType.channel.value): (resolved_channel)},
    )

    assert all(isinstance(item, discord.ui.Label) for item in modal.children)
    assert modal.channel.values == [resolved_channel]
    assert isinstance(modal.channel.values[0], app_commands.AppCommandChannel)
    assert modal.weekday.values == ["2"]
    assert modal.local_time.value == "13:40"
    assert modal.end_date.value == "2026-08-30"
    assert modal.content.value == ""
    modal.channel._handle_submit(interaction(), {"values": []}, {})
    assert modal.channel.values == []


@pytest.mark.parametrize(
    "selected_id",
    [401, 400],
    ids=["changed", "untouched-default"],
)
def test_detail_edit_channel_resolves_app_command_channel_through_guild_cache(
    selected_id: int,
) -> None:
    value = interaction()
    cached = cached_text_channel(value.guild, selected_id)
    value.guild.get_channel.return_value = cached
    group = commands(AsyncMock())
    submitted = app_command_channel(selected_id)

    assert group._detail_edit_channel_id(value, submitted, current_channel_id=400) == selected_id
    value.guild.get_channel.assert_called_with(selected_id)
    cached.permissions_for.assert_called_once_with(value.guild.me)
    assert not hasattr(value.guild, "fetch_channel") or not value.guild.fetch_channel.called


def test_detail_edit_channel_optional_empty_keeps_current_channel() -> None:
    value = interaction()
    cached = cached_text_channel(value.guild)
    value.guild.get_channel.return_value = cached
    group = commands(AsyncMock())

    assert group._detail_edit_channel_id(value, None, current_channel_id=400) == 400
    value.guild.get_channel.assert_called_with(400)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["cache-miss", "other-guild", "thread", "dm", "category", "voice", "permissions", "member"],
)
async def test_detail_edit_channel_failures_use_destination_message_before_transaction(
    failure: str,
) -> None:
    selected = replace(
        view(), schedule_type=ScheduleType.DAILY, local_time=time(13, 40), end_date=None
    )
    queries = AsyncMock()
    session = MagicMock()
    group = commands(queries, session=session)
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    modal = ScheduleEditModal(commands=group, detail_view=detail_view, default_channel=None)
    modal.channel._handle_submit(
        interaction(),
        {"values": ["400"]},
        {
            ResolveKey(id="400", type=discord.AppCommandOptionType.channel.value): (
                app_command_channel()
            )
        },
    )
    submitted = interaction()
    cached: object | None = cached_text_channel(submitted.guild)
    if failure == "cache-miss":
        cached = None
    elif failure == "other-guild":
        cached = cached_text_channel(MagicMock(spec=discord.Guild))
        cached.guild.id = GUILD_ID + 1
    elif failure == "thread":
        cached = MagicMock(spec=discord.Thread)
    elif failure == "dm":
        cached = MagicMock(spec=discord.DMChannel)
    elif failure == "category":
        cached = MagicMock(spec=discord.CategoryChannel)
    elif failure == "voice":
        cached = MagicMock(spec=discord.VoiceChannel)
    elif failure == "permissions":
        cached = cached_text_channel(submitted.guild)
        cached.permissions_for.return_value.view_channel = False
    elif failure == "member":
        submitted.guild.me = None
    submitted.guild.get_channel.return_value = cached

    await group._submit_detail_edit(modal, submitted)

    assert submitted.response.send_message.await_args.args == (DETAIL_EDIT_CHANNEL_MESSAGE,)
    assert submitted.response.send_message.await_args.kwargs["ephemeral"] is True
    assert submitted.response.send_message.await_args.kwargs["allowed_mentions"].to_dict() == {
        "parse": []
    }
    session.__aenter__.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_value", [object(), True, 0, -1, 9_223_372_036_854_775_808])
async def test_detail_edit_component_or_channel_id_corruption_is_internal_error(
    bad_value: object,
) -> None:
    selected = replace(
        view(), schedule_type=ScheduleType.DAILY, local_time=time(13, 40), end_date=None
    )
    session = MagicMock()
    group = commands(AsyncMock(), session=session)
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    modal = ScheduleEditModal(commands=group, detail_view=detail_view, default_channel=None)
    if isinstance(bad_value, int):
        bad_channel = MagicMock(spec=app_commands.AppCommandChannel)
        bad_channel.id = bad_value
        submitted_value = bad_channel
    else:
        submitted_value = bad_value
    modal.channel._handle_submit(
        interaction(),
        {"values": ["bad"]},
        {ResolveKey(id="bad", type=discord.AppCommandOptionType.channel.value): submitted_value},
    )
    submitted = interaction()

    await group._submit_detail_edit(modal, submitted)

    assert submitted.response.send_message.await_args.args == (INTERNAL_ERROR_MESSAGE,)
    session.__aenter__.assert_not_awaited()


@pytest.mark.asyncio
async def test_detail_edit_channel_missing_id_is_internal_error() -> None:
    selected = replace(
        view(), schedule_type=ScheduleType.DAILY, local_time=time(13, 40), end_date=None
    )
    session = MagicMock()
    group = commands(AsyncMock(), session=session)
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    modal = ScheduleEditModal(commands=group, detail_view=detail_view, default_channel=None)
    bad_channel = MagicMock(spec=app_commands.AppCommandChannel)
    del bad_channel.id
    modal.channel._handle_submit(
        interaction(),
        {"values": ["bad"]},
        {ResolveKey(id="bad", type=discord.AppCommandOptionType.channel.value): bad_channel},
    )
    submitted = interaction()

    await group._submit_detail_edit(modal, submitted)

    assert submitted.response.send_message.await_args.args == (INTERNAL_ERROR_MESSAGE,)
    session.__aenter__.assert_not_awaited()


@pytest.mark.asyncio
async def test_detail_edit_multiple_channels_is_input_error_before_transaction() -> None:
    selected = replace(
        view(), schedule_type=ScheduleType.DAILY, local_time=time(13, 40), end_date=None
    )
    session = MagicMock()
    group = commands(AsyncMock(), session=session)
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    modal = ScheduleEditModal(commands=group, detail_view=detail_view, default_channel=None)
    modal.channel._handle_submit(
        interaction(),
        {"values": ["400", "401"]},
        {
            ResolveKey(id="400", type=discord.AppCommandOptionType.channel.value): (
                app_command_channel()
            ),
            ResolveKey(id="401", type=discord.AppCommandOptionType.channel.value): (
                app_command_channel(401)
            ),
        },
    )
    submitted = interaction()

    await group._submit_detail_edit(modal, submitted)

    assert submitted.response.send_message.await_args.args == ("入力内容を確認してください。",)
    session.__aenter__.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local_time_value", "end_date_value", "content_value", "expected_time", "expected_end"),
    [
        ("13:40", "2026-08-30", "本文", time(13, 40), date(2026, 8, 30)),
        ("14:20", "明後日", "変更本文", time(14, 20), date(2026, 8, 20)),
        ("13:40", "", "", time(13, 40), None),
    ],
    ids=["untouched", "changed-and-relative", "clear-optional-fields"],
)
async def test_daily_detail_edit_v2_submit_reaches_service_with_all_field_semantics(
    monkeypatch: pytest.MonkeyPatch,
    local_time_value: str,
    end_date_value: str,
    content_value: str,
    expected_time: time,
    expected_end: date | None,
) -> None:
    selected = replace(
        view(content="本文"),
        schedule_type=ScheduleType.DAILY,
        local_time=time(13, 40),
        end_date=date(2026, 8, 30),
    )
    refreshed = replace(selected, version=2)
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = detail(refreshed)
    group = commands(queries)
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    modal = ScheduleEditModal(commands=group, detail_view=detail_view, default_channel=None)
    submitted_channel = app_command_channel(401)
    modal._refresh(
        interaction(),
        [
            {
                "type": 18,
                "component": {
                    "type": 8,
                    "custom_id": "post_detail_edit_channel",
                    "values": ["401"],
                },
            },
            {
                "type": 18,
                "component": {
                    "type": 4,
                    "custom_id": "post_detail_edit_local_time",
                    "value": local_time_value,
                },
            },
            {
                "type": 18,
                "component": {
                    "type": 4,
                    "custom_id": "post_detail_edit_end_date",
                    "value": end_date_value,
                },
            },
            {
                "type": 18,
                "component": {
                    "type": 4,
                    "custom_id": "post_detail_edit_content",
                    "value": content_value,
                },
            },
        ],
        {
            ResolveKey(id="401", type=discord.AppCommandOptionType.channel.value): (
                submitted_channel
            )
        },
    )
    submitted = interaction()
    cached = cached_text_channel(submitted.guild, 401)
    submitted.guild.get_channel.return_value = cached
    service = AsyncMock()
    service.edit.return_value = EditedSchedule(
        public_id=selected.public_id,
        channel_id=401,
        schedule_type=ScheduleType.DAILY,
        status=ScheduleStatus.ACTIVE if content_value else ScheduleStatus.DRAFT,
        content=content_value or None,
        next_run_at=selected.next_run_at,
        local_time=expected_time,
        weekday=None,
        end_date=expected_end,
        changed_fields=("channel_id",),
        pending_runs_skipped=0,
        run_replaced=False,
        retry_pending_preserved=False,
        previous_status=ScheduleStatus.ACTIVE,
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleEditingService", lambda unused: service
    )

    await modal.on_submit(submitted)

    values = service.edit.await_args.kwargs["values"]
    assert values.channel_id == 401
    assert values.local_time == expected_time
    assert values.end_date == expected_end
    assert values.end_date_supplied is bool(end_date_value)
    assert values.clear_end_date is not bool(end_date_value)
    assert values.content == (content_value or None)
    assert values.clear_content is not bool(content_value)
    assert service.edit.await_args.kwargs["expected_version"] == selected.version


@pytest.mark.asyncio
async def test_daily_detail_edit_complete_noop_uses_no_changes_response_not_input_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = replace(
        view(content="本文"),
        schedule_type=ScheduleType.DAILY,
        local_time=time(13, 40),
        end_date=date(2026, 8, 30),
    )
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = detail(selected)
    group = commands(queries)
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    modal = ScheduleEditModal(commands=group, detail_view=detail_view, default_channel=None)
    modal.channel._handle_submit(
        interaction(),
        {"values": ["400"]},
        {
            ResolveKey(id="400", type=discord.AppCommandOptionType.channel.value): (
                app_command_channel()
            )
        },
    )
    modal.local_time._value = "13:40"
    modal.end_date._value = "2026-08-30"
    modal.content._value = "本文"
    submitted = interaction()
    submitted.guild.get_channel.return_value = cached_text_channel(submitted.guild)
    service = AsyncMock()
    service.edit.side_effect = ScheduleEditNoChanges
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleEditingService", lambda unused: service
    )

    await modal.on_submit(submitted)

    assert submitted.edit_original_response.await_args.kwargs["content"] == (
        DETAIL_EDIT_NO_CHANGES_MESSAGE
    )
    submitted.response.send_message.assert_not_awaited()
    assert detail_view.finished is True


@pytest.mark.asyncio
async def test_detail_edit_expected_version_conflict_still_refreshes_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = replace(
        view(), schedule_type=ScheduleType.DAILY, local_time=time(13, 40), end_date=None
    )
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = detail(replace(selected, version=2))
    group = commands(queries)
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    modal = ScheduleEditModal(commands=group, detail_view=detail_view, default_channel=None)
    modal.channel._handle_submit(interaction(), {"values": []}, {})
    modal.local_time._value = "14:00"
    modal.end_date._value = ""
    modal.content._value = "本文"
    submitted = interaction()
    submitted.guild.get_channel.return_value = cached_text_channel(submitted.guild)
    service = AsyncMock()
    service.edit.side_effect = ScheduleEditVersionConflict
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleEditingService", lambda unused: service
    )

    await modal.on_submit(submitted)

    assert submitted.edit_original_response.await_args.kwargs["content"] == DETAIL_CONFLICT_MESSAGE
    assert service.edit.await_args.kwargs["expected_version"] == selected.version


@pytest.mark.asyncio
@pytest.mark.parametrize("done", [False, True], ids=["initial-response", "followup"])
async def test_detail_edit_modal_on_error_is_sanitized_and_preserves_parent(
    caplog: pytest.LogCaptureFixture, done: bool
) -> None:
    selected = replace(
        view(), schedule_type=ScheduleType.DAILY, local_time=time(13, 40), end_date=None
    )
    group = commands(AsyncMock())
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    modal = ScheduleEditModal(commands=group, detail_view=detail_view, default_channel=None)
    group._edit_modals.add(modal)
    submitted = interaction(done=done)
    secret = "secret-component-value"

    with caplog.at_level(logging.ERROR, logger="test.posts"):
        await modal.on_error(submitted, RuntimeError(secret))

    sender = submitted.followup.send if done else submitted.response.send_message
    assert sender.await_args.args == (INTERNAL_ERROR_MESSAGE,)
    assert sender.await_args.kwargs["ephemeral"] is True
    assert sender.await_args.kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert detail_view.finished is False and detail_view.closed is False
    assert modal not in group._edit_modals and modal.is_finished()
    records = [record for record in caplog.records if record.name == "test.posts"]
    assert [record.message for record in records] == ["schedule_detail_edit_modal_error"]
    assert records[0].exc_info is None
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_closed_edit_modal_can_be_reopened_without_retiring_parent() -> None:
    selected = view()
    group = commands(AsyncMock())
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    group._detail_views.add(detail_view)
    edit_button = next(
        item for item in detail_view.children if item.custom_id == DETAIL_EDIT_CUSTOM_ID
    )

    first_click = interaction()
    first_click.guild.get_channel.return_value = cached_text_channel(first_click.guild)
    await edit_button.callback(first_click)
    first_modal = first_click.response.send_modal.await_args.args[0]

    # Discord sends no interaction when the user closes a modal with the X.
    assert detail_view.finished is False and detail_view.closed is False
    assert not detail_view.is_finished()
    second_click = interaction()
    second_click.guild.get_channel.return_value = cached_text_channel(second_click.guild)
    await edit_button.callback(second_click)
    second_modal = second_click.response.send_modal.await_args.args[0]

    assert second_modal is not first_modal
    assert first_modal.closed and first_modal.is_finished()
    assert second_modal in group._edit_modals
    assert detail_view in group._detail_views
    assert detail_view.finished is False and detail_view.closed is False
    assert not detail_view.is_finished()

    group._submit_detail_edit = AsyncMock()  # type: ignore[method-assign]
    await second_modal.on_submit(interaction())
    group._submit_detail_edit.assert_awaited_once()
    await first_modal.on_submit(interaction())
    group._submit_detail_edit.assert_awaited_once()
    assert detail_view.finished is False and detail_view.closed is False
    await group.close_confirmation_views()


@pytest.mark.asyncio
async def test_edit_modal_timeout_preserves_parent_detail() -> None:
    selected = view()
    group = commands(AsyncMock())
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    clicked = interaction()
    await group._edit_from_detail(detail_view, clicked)
    modal = clicked.response.send_modal.await_args.args[0]

    await modal.on_timeout()

    assert modal.closed and modal.is_finished()
    assert detail_view.finished is False and detail_view.closed is False
    assert not detail_view.is_finished()


@pytest.mark.asyncio
async def test_direct_show_builds_detail_context_and_delete_button() -> None:
    selected = view()
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = detail(selected)
    group = commands(queries)
    value = interaction()

    await group.show_command.callback(group, value, str(selected.public_id))

    queries.get_schedule_detail.assert_awaited_once_with(
        guild_id=GUILD_ID,
        requester_user_id=USER_ID,
        administrator=False,
        public_id=str(selected.public_id),
        now=NOW,
    )
    kwargs = value.response.send_message.await_args.kwargs
    assert kwargs["embed"].title == "予約詳細"
    built = kwargs["view"]
    assert [item.label for item in built.children] == ["編集", "削除"]
    assert built.context.public_id == selected.public_id
    assert built.context.expected_version == selected.version
    assert built.timeout is None
    assert built in group._detail_views


@pytest.mark.asyncio
async def test_detail_back_preserves_filters_page_and_clamps_latest_list() -> None:
    selected = view()
    queries = AsyncMock()
    queries.get_schedule_page.side_effect = [
        SchedulePage((selected,), 4, 31),
        SchedulePage((selected,), 2, 11),
    ]
    queries.get_schedule_detail.return_value = detail(selected)
    group = commands(queries)
    original = interaction()
    status = app_commands.Choice(name="有効", value=ScheduleStatus.ACTIVE.value)
    await group.list_command.callback(group, original, status, 4)
    list_view = original.response.send_message.await_args.kwargs["view"]
    list_view.schedule_type = ScheduleType.WEEKLY

    selected_interaction = interaction()
    await group._show_list_selection(list_view, selected_interaction, str(selected.public_id))
    detail_view = selected_interaction.response.edit_message.await_args.kwargs["view"]
    back = interaction()
    await group._return_to_list(detail_view, back)

    assert queries.get_schedule_page.await_args.kwargs == {
        "guild_id": GUILD_ID,
        "requester_user_id": USER_ID,
        "administrator": False,
        "status": ScheduleStatus.ACTIVE,
        "page": 4,
        "schedule_type": ScheduleType.WEEKLY,
        "clamp": True,
    }
    latest_view = back.response.edit_message.await_args.kwargs["view"]
    assert latest_view.page == 2
    assert latest_view.status is ScheduleStatus.ACTIVE
    assert latest_view.schedule_type is ScheduleType.WEEKLY


@pytest.mark.asyncio
async def test_detail_remains_active_after_900_seconds_without_database_resources() -> None:
    selected = view()
    queries = AsyncMock()
    queries.get_schedule_page.return_value = SchedulePage((selected,), 1, 1)
    queries.get_schedule_detail.return_value = detail(selected)
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    group = commands(queries, session=session)
    original = interaction()
    await group.list_command.callback(group, original, None, 1)
    list_view = original.response.send_message.await_args.kwargs["view"]
    chosen = interaction()
    await group._show_list_selection(list_view, chosen, str(selected.public_id))
    detail_view = chosen.response.edit_message.await_args.kwargs["view"]
    assert detail_view.timeout is None
    assert not detail_view.is_finished()
    assert not any(item.disabled for item in detail_view.children)
    assert detail_view in group._detail_views
    session.__aenter__.assert_not_awaited()


@pytest.mark.asyncio
async def test_detail_rejects_other_user_dm_wrong_guild_and_permission_loss() -> None:
    selected = view()
    group = commands(AsyncMock())
    original = interaction()
    detail_view = group._build_detail_view(
        interaction=original,
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
        list_origin=ScheduleListOrigin(status=None, schedule_type=None, page=1),
    )
    invalid = []
    other = interaction()
    other.user.id = USER_ID + 1
    invalid.append(other)
    dm = interaction()
    dm.guild = None
    dm.guild_id = None
    invalid.append(dm)
    wrong_guild = interaction()
    wrong_guild.guild.id = GUILD_ID + 1
    wrong_guild.guild_id = GUILD_ID + 1
    invalid.append(wrong_guild)
    lost_role = interaction()
    lost_role.user.roles = []
    invalid.append(lost_role)

    for value in invalid:
        assert await detail_view.interaction_check(value) is False
        assert value.response.send_message.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_detail_back_rechecks_schedule_ownership_and_prevents_double_action() -> None:
    selected = view()
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = None
    group = commands(queries)
    original = interaction()
    detail_view = group._build_detail_view(
        interaction=original,
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
        list_origin=ScheduleListOrigin(status=None, schedule_type=None, page=1),
    )
    first = interaction()
    await group._return_to_list(detail_view, first)
    assert first.response.send_message.await_args.args == (PERMISSION_DENIED_MESSAGE,)
    queries.get_schedule_page.assert_not_awaited()

    queries.get_schedule_detail.return_value = detail(selected)
    queries.get_schedule_page.return_value = SchedulePage((selected,), 1, 1)
    await group._return_to_list(detail_view, interaction())
    duplicate = interaction()
    await group._return_to_list(detail_view, duplicate)
    assert duplicate.response.send_message.await_args.args == (NOT_FOUND_MESSAGE,)
    queries.get_schedule_page.assert_awaited_once()


@pytest.mark.asyncio
async def test_detail_custom_id_is_fixed_and_close_collects_view() -> None:
    selected = view()
    group = commands(AsyncMock())
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
        list_origin=ScheduleListOrigin(status=None, schedule_type=None, page=1),
    )
    group._detail_views.add(detail_view)
    custom_ids = [item.custom_id for item in detail_view.children]
    assert custom_ids[-1] == DETAIL_BACK_CUSTOM_ID
    for forbidden in (
        str(selected.public_id),
        str(GUILD_ID),
        str(USER_ID),
        str(selected.version),
        selected.content,
    ):
        assert all(forbidden not in custom_id for custom_id in custom_ids)

    await group.close_confirmation_views()
    await group.close_confirmation_views()
    assert detail_view.closed and detail_view.finished and detail_view.is_finished()
    assert not group._detail_views


@pytest.mark.asyncio
async def test_close_collects_open_resume_modal_and_parent_view() -> None:
    group = commands(AsyncMock())
    parent = ResumeChoiceView(
        commands=group,
        interaction=interaction(),
        public_id=str(uuid.uuid7()),
        actor_user_id=USER_ID,
        rescue_allowed=True,
    )
    group._resume_views.add(parent)
    clicked = interaction()

    await parent.time_button.callback(clicked)

    modal = clicked.response.send_modal.await_args.args[0]
    assert modal in group._resume_modals
    await group.close_confirmation_views()
    await group.close_confirmation_views()
    assert parent.closed and parent.finished and parent.is_finished()
    assert modal.closed and modal.is_finished()
    assert not group._resume_views
    assert not group._resume_modals


@pytest.mark.asyncio
async def test_closed_resume_time_modal_can_be_reopened_without_retiring_parent() -> None:
    group = commands(AsyncMock())
    parent = ResumeChoiceView(
        commands=group,
        interaction=interaction(),
        public_id=str(uuid.uuid7()),
        actor_user_id=USER_ID,
        rescue_allowed=True,
    )
    first_click = interaction()
    await parent.time_button.callback(first_click)
    first_modal = first_click.response.send_modal.await_args.args[0]

    second_click = interaction()
    await parent.time_button.callback(second_click)
    second_modal = second_click.response.send_modal.await_args.args[0]

    assert second_modal is not first_modal
    assert first_modal.closed and first_modal.is_finished()
    assert second_modal in group._resume_modals
    assert parent.finished is False and parent.closed is False
    assert not parent.is_finished()
    await group.close_confirmation_views()


@pytest.mark.asyncio
async def test_closed_delete_reason_modal_can_be_reopened_without_retiring_detail() -> None:
    selected = replace(view(), creator_user_id=USER_ID + 1)
    group = commands(AsyncMock())
    detail_view = group._build_detail_view(
        interaction=interaction(administrator=True),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    first_click = interaction(administrator=True)
    await group._delete_from_detail(detail_view, first_click)
    first_modal = first_click.response.send_modal.await_args.args[0]

    second_click = interaction(administrator=True)
    await group._delete_from_detail(detail_view, second_click)
    second_modal = second_click.response.send_modal.await_args.args[0]

    assert second_modal is not first_modal
    assert first_modal.closed and first_modal.is_finished()
    assert second_modal in group._delete_reason_modals
    assert detail_view.finished is False and detail_view.closed is False
    assert not detail_view.is_finished()
    await group.close_confirmation_views()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("schedule_type", "value", "label"),
    [
        (None, "all", "すべて"),
        (ScheduleType.ONCE, "once", "単発"),
        (ScheduleType.DAILY, "daily", "毎日"),
        (ScheduleType.WEEKLY, "weekly", "毎週"),
    ],
)
async def test_list_type_filter_resets_page_and_marks_default(
    schedule_type: ScheduleType | None, value: str, label: str
) -> None:
    queries = AsyncMock()
    selected = view()
    queries.get_schedule_page.side_effect = [
        SchedulePage((selected,), 2, 11),
        SchedulePage((selected,), 1, 1),
    ]
    group = commands(queries)
    original = interaction()
    await group.list_command.callback(group, original, None, 2)
    list_view = original.response.send_message.await_args.kwargs["view"]

    clicked = interaction()
    await group._filter_list_type(list_view, clicked, schedule_type)

    assert queries.get_schedule_page.await_args.kwargs == {
        "guild_id": GUILD_ID,
        "requester_user_id": USER_ID,
        "administrator": False,
        "status": None,
        "page": 1,
        "schedule_type": schedule_type,
        "clamp": True,
    }
    type_select = list_view.children[2]
    defaults = [option for option in type_select.options if option.default]
    assert [(option.value, option.label) for option in defaults] == [(value, label)]
    assert f"種類：{label}" in clicked.response.edit_message.await_args.kwargs["embed"].description


@pytest.mark.asyncio
async def test_empty_type_filter_keeps_filter_available_and_disables_paging() -> None:
    queries = AsyncMock()
    queries.get_schedule_page.side_effect = [
        SchedulePage((view(),), 1, 1),
        SchedulePage((), 1, 0),
    ]
    group = commands(queries)
    original = interaction()
    await group.list_command.callback(group, original, None, 1)
    list_view = original.response.send_message.await_args.kwargs["view"]
    await group._filter_list_type(list_view, interaction(), ScheduleType.WEEKLY)
    assert len(list_view.children) == 3
    assert list_view.children[0].disabled and list_view.children[1].disabled
    assert list_view.children[2].custom_id == "post_list_schedule_type_filter"


@pytest.mark.asyncio
async def test_list_view_rejects_other_user_and_remains_enabled_without_timeout() -> None:
    queries = AsyncMock()
    queries.get_schedule_page.side_effect = [
        SchedulePage(tuple(view() for _ in range(10)), 2, 24),
        SchedulePage((view(),), 1, 1),
    ]
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    group = commands(queries, session=session)
    original = interaction()
    await group.list_command.callback(group, original, None, 2)
    list_view = original.response.send_message.await_args.kwargs["view"]

    other = interaction()
    other.user.id = USER_ID + 1
    assert await list_view.interaction_check(other) is False
    assert other.response.send_message.await_args.kwargs["ephemeral"] is True

    await group._filter_list_type(list_view, interaction(), ScheduleType.WEEKLY)
    assert list_view.timeout is None
    assert list_view.finished is False
    assert not list_view.is_finished()
    assert list_view in group._list_views
    assert not all(item.disabled for item in list_view.children)
    session.__aenter__.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_component_dispatches_after_900_second_equivalent() -> None:
    queries = AsyncMock()
    queries.get_schedule_page.return_value = SchedulePage(tuple(view() for _ in range(10)), 1, 11)
    group = commands(queries)
    original = interaction()
    await group.list_command.callback(group, original, None, 1)
    list_view = original.response.send_message.await_args.kwargs["view"]
    group._move_list_page = AsyncMock()  # type: ignore[method-assign]
    store = ViewStore(MagicMock())
    store.add_view(list_view, message_id=42)
    clicked = interaction()
    clicked.message = MagicMock(spec=discord.Message)
    clicked.message.id = 42
    clicked.data = {}

    store.dispatch_view(
        discord.ComponentType.button.value,
        "post_list_next",
        clicked,
    )
    for _ in range(10):
        if group._move_list_page.await_count:
            break
        await asyncio.sleep(0)

    group._move_list_page.assert_awaited_once_with(list_view, clicked, 2)
    assert list_view.timeout is None
    assert not list_view.is_finished()
    list_view.stop()


@pytest.mark.asyncio
async def test_close_collects_list_view_wait_task() -> None:
    queries = AsyncMock()
    queries.get_schedule_page.return_value = SchedulePage((), 1, 0)
    group = commands(queries)
    original = interaction()
    await group.list_command.callback(group, original, None, 1)
    list_view = original.response.send_message.await_args.kwargs["view"]
    await group.close_confirmation_views()
    assert list_view.closed is True
    assert list_view.finished is True
    assert not group._list_views


@pytest.mark.asyncio
async def test_show_missing_invalid_and_unauthorized_share_safe_response() -> None:
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = None
    group = commands(queries)
    for public_id in (str(uuid.uuid7()), "invalid"):
        value = interaction()
        await group.show_command.callback(group, value, public_id)
        assert value.response.send_message.await_args.args == (NOT_FOUND_MESSAGE,)


@pytest.mark.asyncio
async def test_show_uses_followup_when_interaction_already_responded() -> None:
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = detail(view(status=ScheduleStatus.DELETED))
    group = commands(queries)
    value = interaction(done=True)
    await group.show_command.callback(group, value, str(uuid.uuid7()))
    value.followup.send.assert_awaited_once()
    assert value.followup.send.await_args.kwargs["ephemeral"] is True
    assert value.followup.send.await_args.kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert value.followup.send.await_args.kwargs["embed"].title == "予約詳細"


@pytest.mark.asyncio
async def test_delete_preview_is_read_only_ephemeral_and_uses_interaction_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_id = uuid.uuid7()
    preview = ScheduleDeletionView(
        public_id=public_id,
        channel_id=400,
        schedule_type=ScheduleType.ONCE,
        previous_status=ScheduleStatus.ACTIVE,
        content="body",
        next_run_at=NOW,
        reason="planned",
    )
    service = AsyncMock()
    service.preview.return_value = preview
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleDeletionService", lambda unused: service
    )
    value = interaction()
    group = commands(AsyncMock())
    await group.delete_command.callback(group, value, str(public_id), "  planned  ")
    service.preview.assert_awaited_once_with(
        guild_id=GUILD_ID,
        public_id=str(public_id),
        actor_user_id=USER_ID,
        administrator=False,
        reason="planned",
    )
    service.delete.assert_not_awaited()
    kwargs = value.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert kwargs["embed"].title == "予約削除の確認"
    confirm_view = kwargs["view"]
    assert confirm_view.timeout == 120.0
    assert confirm_view.is_persistent() is False
    assert {item.custom_id for item in confirm_view.children} == {
        "post_delete_confirm",
        "post_delete_cancel",
    }


def test_delete_command_has_no_confirm_option_and_optional_reason() -> None:
    group = commands(AsyncMock())
    assert [parameter.name for parameter in group.delete_command.parameters] == [
        "public_id",
        "reason",
    ]
    assert group.delete_command.parameters[1].required is False


def test_edit_command_exposes_one_required_and_eight_optional_parameters() -> None:
    group = commands(AsyncMock())
    parameters = group.edit_command.parameters
    assert [parameter.name for parameter in parameters] == [
        "public_id",
        "channel",
        "scheduled_at",
        "local_time",
        "weekday",
        "end_date",
        "content",
        "clear_content",
        "clear_end_date",
    ]
    assert parameters[0].required is True
    assert all(not parameter.required for parameter in parameters[1:])
    assert [parameter.type for parameter in parameters] == [
        discord.AppCommandOptionType.string,
        discord.AppCommandOptionType.channel,
        discord.AppCommandOptionType.string,
        discord.AppCommandOptionType.string,
        discord.AppCommandOptionType.integer,
        discord.AppCommandOptionType.string,
        discord.AppCommandOptionType.string,
        discord.AppCommandOptionType.boolean,
        discord.AppCommandOptionType.boolean,
    ]
    assert [parameter.description for parameter in parameters] == [
        "直接編集する予約ID（候補から選択）",
        "変更後の投稿先",
        "単発のみ｜投稿日時（YYYY-MM-DD HH:MM）",
        "毎日・毎週のみ｜基本投稿時刻を恒久変更（HH:MM）",
        "毎週のみ｜投稿する曜日",
        END_DATE_DESCRIPTION,
        "変更後の本文｜本文削除とは併用不可",
        "本文を削除｜新しい本文とは併用不可",
        "毎日・毎週のみ｜終了日を解除",
    ]
    assert all(len(parameter.description) <= 100 for parameter in parameters)
    assert len(group.edit_command.description) <= 100
    assert "直接指定" in group.edit_command.description
    assert [choice.value for choice in parameters[4].choices] == list(range(7))

    callback_parameters = inspect.signature(group.edit_command.callback).parameters
    assert callback_parameters["clear_content"].default is False
    assert callback_parameters["clear_end_date"].default is False


@pytest.mark.asyncio
async def test_edit_public_id_only_uses_dedicated_safe_response_without_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    group = commands(AsyncMock(), session=session)
    value = interaction()
    service_factory = MagicMock()
    monkeypatch.setattr("discord_ai_reminder_bot.bot.posts.ScheduleEditingService", service_factory)

    await group.edit_command.callback(group, value, str(uuid.uuid7()))

    assert value.response.send_message.await_args.args == (EDIT_REQUEST_REQUIRED_MESSAGE,)
    kwargs = value.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert "本文" not in EDIT_REQUEST_REQUIRED_MESSAGE
    session.__aenter__.assert_not_awaited()
    session.begin.assert_not_called()
    service_factory.assert_not_called()
    value.response.defer.assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_autocomplete_selected_public_id_only_uses_same_dedicated_response() -> None:
    item = autocomplete_view()
    queries = AsyncMock()
    queries.autocomplete_schedules.return_value = (item,)
    session = MagicMock()
    group = commands(queries, session=session)
    value = interaction()
    value.guild.get_channel.return_value = MagicMock(name="channel")
    value.guild.get_channel.return_value.name = "一般"
    choice = (await group.edit_public_id_autocomplete(value, ""))[0]
    submitted = interaction()

    await group.edit_command.callback(group, submitted, choice.value)

    assert submitted.response.send_message.await_args.args == (EDIT_REQUEST_REQUIRED_MESSAGE,)
    session.__aenter__.assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_one_explicit_field_reaches_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_id = uuid.uuid7()
    session = MagicMock()
    group = commands(AsyncMock(), session=session)
    value = interaction()
    service = AsyncMock()
    service.edit.return_value = EditedSchedule(
        public_id=public_id,
        channel_id=400,
        schedule_type=ScheduleType.ONCE,
        status=ScheduleStatus.ACTIVE,
        content="変更後",
        next_run_at=NOW,
        local_time=None,
        weekday=None,
        end_date=None,
        changed_fields=("content",),
        pending_runs_skipped=0,
        run_replaced=False,
        retry_pending_preserved=False,
        previous_status=ScheduleStatus.ACTIVE,
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleEditingService", lambda unused: service
    )

    await group.edit_command.callback(group, value, str(public_id), content="変更後")

    service.edit.assert_awaited_once()
    assert service.edit.await_args.kwargs["values"].content == "変更後"
    session.__aenter__.assert_awaited_once()


@pytest.mark.asyncio
async def test_edit_explicit_same_value_remains_service_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    group = commands(AsyncMock(), session=session)
    value = interaction()
    service = AsyncMock()
    service.edit.side_effect = ScheduleEditNoChanges
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleEditingService", lambda unused: service
    )

    await group.edit_command.callback(group, value, str(uuid.uuid7()), content="現在値")

    service.edit.assert_awaited_once()
    session.__aenter__.assert_awaited_once()
    assert value.response.send_message.await_args.args == (EDIT_NO_CHANGES_MESSAGE,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"scheduled_at": "not-a-date"}, "入力内容を確認してください。"),
        ({"end_date": "not-a-date"}, END_DATE_INPUT_MESSAGE),
        ({"content": "@everyone"}, "入力内容を確認してください。"),
        (
            {"content": "本文", "clear_content": True},
            "入力内容を確認してください。",
        ),
        (
            {"end_date": "2026-08-30", "clear_end_date": True},
            "入力内容を確認してください。",
        ),
    ],
    ids=["datetime", "end-date", "content", "content-exclusive", "end-date-exclusive"],
)
async def test_edit_invalid_inputs_keep_existing_guidance_before_session(
    options: dict[str, object], expected: str
) -> None:
    session = MagicMock()
    group = commands(AsyncMock(), session=session)
    value = interaction()

    await group.edit_command.callback(group, value, str(uuid.uuid7()), **options)

    assert value.response.send_message.await_args.args == (expected,)
    session.__aenter__.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_pause_resume_defer_commit_and_use_interaction_identity(
    monkeypatch: pytest.MonkeyPatch, resume: bool
) -> None:
    public_id = uuid.uuid7()
    service = AsyncMock()
    service.pause.return_value = PausedSchedule(
        public_id=public_id,
        channel_id=400,
        schedule_type=ScheduleType.DAILY,
        previous_status=ScheduleStatus.ACTIVE,
        pending_runs_skipped=1,
        local_time=time(12),
        weekday=None,
        end_date=None,
    )
    service.resume.return_value = ResumedSchedule(
        public_id=public_id,
        channel_id=400,
        schedule_type=ScheduleType.DAILY,
        status=ScheduleStatus.ACTIVE,
        next_run_at=NOW,
        local_time=time(12),
        weekday=None,
        end_date=None,
        content="body",
        held_run_reused=True,
    )
    service.preview_resume.return_value = ResumePreview(public_id, None, False, False)
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.SchedulePauseService", lambda unused: service
    )
    group = commands(AsyncMock())
    value = interaction(administrator=True)
    value.response.is_done.return_value = True
    callback = group.resume_command if resume else group.pause_command
    await callback.callback(group, value, str(public_id))
    value.response.defer.assert_awaited_once_with(ephemeral=True)
    operation = service.resume if resume else service.pause
    operation.assert_awaited_once()
    arguments = operation.await_args.kwargs
    assert arguments["guild_id"] == GUILD_ID
    assert arguments["actor_user_id"] == USER_ID
    assert arguments["administrator"] is True
    kwargs = value.followup.send.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert kwargs["embed"].title == ("予約を再開しました" if resume else "予約を一時停止しました")
    if resume:
        fields = {field.name: field.value for field in kwargs["embed"].fields}
        assert fields["⚠️ 再開について"].startswith(
            "一時停止前に保持していた投稿回を引き続き使用します。"
        )
        assert "再開結果" not in fields


def test_pause_resume_commands_only_accept_required_public_id() -> None:
    group = commands(AsyncMock())
    for command in (group.pause_command, group.resume_command):
        assert [parameter.name for parameter in command.parameters] == ["public_id"]
        assert command.parameters[0].required is True


@pytest.mark.asyncio
async def test_pause_invalid_uuid_is_rejected_before_defer() -> None:
    group = commands(AsyncMock())
    value = interaction()
    await group.pause_command.callback(group, value, "invalid")
    value.response.defer.assert_not_awaited()
    assert value.response.send_message.await_args.args == (STATE_CHANGE_UNAVAILABLE_MESSAGE,)


@pytest.mark.asyncio
async def test_resume_unavailable_uses_common_response_after_defer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock()
    service.resume.side_effect = ScheduleStateChangeUnavailable
    service.preview_resume.return_value = ResumePreview(uuid.uuid7(), None, False, False)
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.SchedulePauseService", lambda unused: service
    )
    group = commands(AsyncMock())
    value = interaction()
    value.response.is_done.return_value = True
    await group.resume_command.callback(group, value, str(uuid.uuid7()))
    value.response.defer.assert_awaited_once_with(ephemeral=True)
    assert value.followup.send.await_args.args == (STATE_CHANGE_UNAVAILABLE_MESSAGE,)


@pytest.mark.asyncio
async def test_delete_button_defers_commits_and_replaces_original_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_id = uuid.uuid7()
    deleted = DeletedSchedule(
        public_id=public_id,
        channel_id=400,
        schedule_type=ScheduleType.DAILY,
        previous_status=ScheduleStatus.PAUSED,
        content=None,
        next_run_at=None,
        reason="planned",
        deleted_at=NOW,
        pending_runs_skipped=0,
    )
    service = AsyncMock()
    service.preview.return_value = ScheduleDeletionView(
        public_id=public_id,
        channel_id=400,
        schedule_type=ScheduleType.DAILY,
        previous_status=ScheduleStatus.PAUSED,
        content=None,
        next_run_at=NOW,
        reason="planned",
    )
    service.delete.return_value = deleted
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleDeletionService", lambda unused: service
    )
    value = interaction(administrator=True)
    group = commands(AsyncMock())
    await group.delete_command.callback(group, value, str(public_id), "planned")
    confirm_view = value.response.send_message.await_args.kwargs["view"]
    button = interaction(administrator=True)
    await group._confirm_deletion(confirm_view, button)
    button.response.defer.assert_awaited_once_with(ephemeral=True)
    service.delete.assert_awaited_once_with(
        guild_id=GUILD_ID,
        public_id=str(public_id),
        actor_user_id=USER_ID,
        administrator=True,
        reason="planned",
        deleted_at=NOW,
    )
    kwargs = button.edit_original_response.await_args.kwargs
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert kwargs["embed"].title == "予約を削除しました"
    assert kwargs["view"] is None
    assert confirm_view.finished is True


@pytest.mark.asyncio
@pytest.mark.parametrize("public_id", ["invalid", str(uuid.uuid4())])
async def test_delete_unavailable_uses_same_safe_response(
    monkeypatch: pytest.MonkeyPatch, public_id: str
) -> None:
    service = AsyncMock()
    service.preview.side_effect = ScheduleDeletionUnavailable
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleDeletionService", lambda unused: service
    )
    value = interaction()
    group = commands(AsyncMock())
    await group.delete_command.callback(group, value, public_id, "reason")
    assert value.response.send_message.await_args.args == (DELETE_UNAVAILABLE_MESSAGE,)


@pytest.mark.asyncio
async def test_admin_other_without_reason_gets_specific_safe_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock()
    service.preview.side_effect = DeleteReasonRequired
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleDeletionService", lambda unused: service
    )
    value = interaction(administrator=True)
    group = commands(AsyncMock())
    await group.delete_command.callback(group, value, str(uuid.uuid7()), None)
    assert value.response.send_message.await_args.args == (DELETE_REASON_REQUIRED_MESSAGE,)


@pytest.mark.asyncio
async def test_delete_button_database_failure_rolls_back_without_secret_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "postgresql+psycopg://user:password@localhost/private-delete"
    service = AsyncMock()
    public_id = uuid.uuid7()
    service.preview.return_value = ScheduleDeletionView(
        public_id=public_id,
        channel_id=400,
        schedule_type=ScheduleType.ONCE,
        previous_status=ScheduleStatus.ACTIVE,
        content="body",
        next_run_at=NOW,
        reason="reason",
    )
    service.delete.side_effect = RuntimeError(secret)
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleDeletionService", lambda unused: service
    )
    session = MagicMock()
    group = commands(AsyncMock(), session=session)
    value = interaction()
    await group.delete_command.callback(group, value, str(public_id), "reason")
    confirm_view = value.response.send_message.await_args.kwargs["view"]
    button = interaction()
    with caplog.at_level(logging.ERROR):
        await group._confirm_deletion(confirm_view, button)
    assert button.edit_original_response.await_args.kwargs["content"] == INTERNAL_ERROR_MESSAGE
    assert secret not in caplog.text
    assert session.begin.return_value.__aexit__.await_args.args[0] is RuntimeError


@pytest.mark.asyncio
async def test_delete_cancel_and_timeout_never_open_delete_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_id = uuid.uuid7()
    preview = ScheduleDeletionView(
        public_id=public_id,
        channel_id=400,
        schedule_type=ScheduleType.ONCE,
        previous_status=ScheduleStatus.ACTIVE,
        content="body",
        next_run_at=NOW,
        reason="理由未入力",
    )
    service = AsyncMock()
    service.preview.return_value = preview
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleDeletionService", lambda unused: service
    )
    group = commands(AsyncMock())

    original = interaction()
    await group.delete_command.callback(group, original, str(public_id), None)
    cancel_view = original.response.send_message.await_args.kwargs["view"]
    button = interaction()
    await group._cancel_deletion(cancel_view, button)
    assert button.response.edit_message.await_args.kwargs["content"] == DELETE_CANCELLED_MESSAGE
    assert button.response.edit_message.await_args.kwargs["view"] is None

    original = interaction()
    await group.delete_command.callback(group, original, str(public_id), None)
    timeout_view = original.response.send_message.await_args.kwargs["view"]
    await group._expire_deletion(timeout_view)
    assert original.edit_original_response.await_args.kwargs["content"] == DELETE_EXPIRED_MESSAGE
    assert original.edit_original_response.await_args.kwargs["view"] is None
    service.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_view_rejects_other_user_and_double_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_id = uuid.uuid7()
    preview = ScheduleDeletionView(
        public_id=public_id,
        channel_id=400,
        schedule_type=ScheduleType.ONCE,
        previous_status=ScheduleStatus.ACTIVE,
        content="body",
        next_run_at=NOW,
        reason="理由未入力",
    )
    deleted = DeletedSchedule(**vars(preview), deleted_at=NOW, pending_runs_skipped=1)
    service = AsyncMock()
    service.preview.return_value = preview
    service.delete.return_value = deleted
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleDeletionService", lambda unused: service
    )
    group = commands(AsyncMock())
    original = interaction()
    await group.delete_command.callback(group, original, str(public_id), None)
    confirm_view = original.response.send_message.await_args.kwargs["view"]

    other = interaction()
    other.user.id = USER_ID + 1
    assert await confirm_view.interaction_check(other) is False
    assert other.response.send_message.await_args.args == ("この操作を実行する権限がありません。",)

    button = interaction()
    await group._confirm_deletion(confirm_view, button)
    await group._confirm_deletion(confirm_view, interaction())
    service.delete.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["dm", "other_guild", "authorization_lost"])
async def test_delete_view_rejects_invalid_context_without_database(
    monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    public_id = uuid.uuid7()
    service = AsyncMock()
    service.preview.return_value = ScheduleDeletionView(
        public_id=public_id,
        channel_id=400,
        schedule_type=ScheduleType.ONCE,
        previous_status=ScheduleStatus.ACTIVE,
        content="body",
        next_run_at=NOW,
        reason="理由未入力",
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleDeletionService", lambda unused: service
    )
    group = commands(AsyncMock())
    original = interaction()
    await group.delete_command.callback(group, original, str(public_id), None)
    confirm_view = original.response.send_message.await_args.kwargs["view"]
    button = interaction()
    if invalid == "dm":
        button.guild_id = None
        button.guild = None
    elif invalid == "other_guild":
        button.guild_id = GUILD_ID + 1
        button.guild.id = GUILD_ID + 1
    else:
        button.user.roles = []
    assert await confirm_view.interaction_check(button) is False
    service.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_stops_and_collects_delete_views(monkeypatch: pytest.MonkeyPatch) -> None:
    public_id = uuid.uuid7()
    service = AsyncMock()
    service.preview.return_value = ScheduleDeletionView(
        public_id=public_id,
        channel_id=400,
        schedule_type=ScheduleType.ONCE,
        previous_status=ScheduleStatus.ACTIVE,
        content="body",
        next_run_at=NOW,
        reason="理由未入力",
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleDeletionService", lambda unused: service
    )
    group = commands(AsyncMock())
    value = interaction()
    await group.delete_command.callback(group, value, str(public_id), None)
    confirm_view = value.response.send_message.await_args.kwargs["view"]
    await group.close_delete_views()
    assert confirm_view.is_finished()
    assert not group._delete_views


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
