from __future__ import annotations

import asyncio
import inspect
import logging
import re
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
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
    ScheduleDeletionVersionConflict,
    ScheduleDeletionView,
)
from discord_ai_reminder_bot.application.schedule_editing import (
    EditedSchedule,
    ScheduleEditNoChanges,
    ScheduleEditUnavailable,
    ScheduleEditVersionConflict,
)
from discord_ai_reminder_bot.application.schedule_naming import (
    EditedScheduleName,
    ScheduleNameEditUnavailable,
    ScheduleNameNoChanges,
    ScheduleNameVersionConflict,
)
from discord_ai_reminder_bot.application.schedule_pause import (
    PausedSchedule,
    ResumedSchedule,
    ResumeMode,
    ResumePreview,
    ScheduleStateChangeUnavailable,
    ScheduleVersionConflict,
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
    DETAIL_DELETE_CUSTOM_ID,
    DETAIL_EDIT_CUSTOM_ID,
    DETAIL_NAME_EDIT_CUSTOM_ID,
    DETAIL_PAUSE_CUSTOM_ID,
    DETAIL_RESUME_CUSTOM_ID,
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
    DELETE_REASON_INPUT_MESSAGE,
    DELETE_REASON_REQUIRED_MESSAGE,
    DELETE_UNAVAILABLE_MESSAGE,
    DETAIL_CONFLICT_MESSAGE,
    DETAIL_DELETE_REASON_MODAL_PREFIX,
    DETAIL_EDIT_CHANNEL_MESSAGE,
    DETAIL_EDIT_MODAL_PREFIX,
    DETAIL_EDIT_NO_CHANGES_MESSAGE,
    DETAIL_NAME_EDIT_MODAL_PREFIX,
    DETAIL_NAME_EDITED_MESSAGE,
    DETAIL_NAME_NO_CHANGES_MESSAGE,
    DETAIL_NAME_PERMISSION_LOST_MESSAGE,
    DETAIL_PAUSED_MESSAGE,
    DETAIL_RESUMED_MESSAGE,
    EDIT_NO_CHANGES_MESSAGE,
    EDIT_REQUEST_REQUIRED_MESSAGE,
    END_DATE_DESCRIPTION,
    END_DATE_INPUT_MESSAGE,
    FULLWIDTH_DATETIME_INPUT_MESSAGE,
    INTERNAL_ERROR_MESSAGE,
    NOT_FOUND_MESSAGE,
    PERMISSION_DENIED_MESSAGE,
    RESUME_TIME_MESSAGE,
    RESUME_TIME_MODAL_PREFIX,
    STATE_CHANGE_UNAVAILABLE_MESSAGE,
    DeleteReasonModal,
    InteractionActor,
    PostCommands,
    ResumeChoiceView,
    ResumeTimeModal,
    ScheduleDeletionConfirmView,
    ScheduleEditModal,
    ScheduleNameEditModal,
)
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.domain.enums import DisplayNameSource, ScheduleStatus, ScheduleType

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


@pytest.mark.asyncio
async def test_autocomplete_presenter_failure_is_empty_and_logs_only_fixed_event(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    queries = AsyncMock()
    queries.autocomplete_schedules.return_value = (autocomplete_view(),)
    secret = "presenter-traceback-secret"
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.schedule_autocomplete_choice",
        MagicMock(side_effect=RuntimeError(secret)),
    )
    group = commands(queries)
    value = interaction()

    with caplog.at_level(logging.ERROR, logger="test.posts"):
        choices = await group.show_public_id_autocomplete(value, "")

    assert choices == []
    value.response.send_message.assert_not_awaited()
    value.followup.send.assert_not_awaited()
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
    thread = MagicMock(spec=discord.Thread)
    thread.name = "tester-thread"
    voice = MagicMock(spec=discord.VoiceChannel)
    voice.name = "tester-voice"
    dm = MagicMock(spec=discord.DMChannel)
    dm.name = "tester-dm"
    value.guild.fetch_channel = AsyncMock()
    value.guild.text_channels = [
        cached_channel(value, channel_id=401, name="tester-a"),
        cached_channel(value, channel_id=402, name="tester-b"),
        cached_channel(value, channel_id=403, name="運営お知らせ"),
        cached_channel(value, channel_id=404, name="お知らせ"),
        cached_channel(value, channel_id=405, name="一般"),
        cached_channel(value, channel_id=406, name="tester-secret", visible=False),
        cached_channel(value, channel_id=407, name="tester-other", guild_id=GUILD_ID + 1),
        category,
        thread,
        voice,
        dm,
    ]

    await group.show_public_id_autocomplete(value, current)

    assert queries.autocomplete_schedules.await_args.kwargs["channel_ids"] == expected
    for excluded in (category, thread, voice, dm):
        excluded.permissions_for.assert_not_called()
    value.guild.fetch_channel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("current", ["#", " \t ", "x\n", "x\x00", "x\x01", "x\u200b", "x" * 101])
async def test_autocomplete_rejects_unsafe_channel_search_without_query(current, caplog) -> None:
    queries = AsyncMock()
    group = commands(queries)
    value = interaction()
    with caplog.at_level(logging.ERROR, logger="test.posts"):
        assert await group.show_public_id_autocomplete(value, current) == []
    queries.autocomplete_schedules.assert_not_awaited()
    value.response.send_message.assert_not_awaited()
    value.followup.send.assert_not_awaited()
    assert caplog.text == ""


@pytest.mark.asyncio
async def test_autocomplete_uses_cache_only_and_cache_failure_is_safe(caplog) -> None:
    queries = AsyncMock()
    group = commands(queries)
    value = interaction()
    secret = "cache-traceback-secret"
    type(value.guild).text_channels = property(
        lambda unused: (_ for _ in ()).throw(RuntimeError(secret))
    )
    value.guild.fetch_channel = AsyncMock()

    with caplog.at_level(logging.ERROR, logger="test.posts"):
        assert await group.show_public_id_autocomplete(value, "tester") == []
    value.guild.fetch_channel.assert_not_awaited()
    queries.autocomplete_schedules.assert_not_awaited()
    value.response.send_message.assert_not_awaited()
    value.followup.send.assert_not_awaited()
    assert "schedule_autocomplete_failed" in caplog.text
    assert secret not in caplog.text


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
    assert "単発予約 8/20 19:30" in next(
        field.value for field in embed.fields if field.name == "🏷️ 予約名"
    )
    assert "line 1 line 2" in next(field.value for field in embed.fields if field.name == "📝 本文")


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
    assert [item.label for item in detail_view.children] == [
        "編集",
        "予約名を編集",
        "削除",
        "一覧へ戻る",
    ]
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
        (True, False, True, ["編集", "予約名を編集", "一時停止", "削除"], False),
        (False, True, True, ["編集", "予約名を編集", "再開", "削除"], False),
        (False, False, True, ["編集", "予約名を編集", "削除"], False),
        (False, False, False, ["編集", "予約名を編集", "削除"], True),
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
    assert detail_view.children[1].disabled is False


def test_detail_name_edit_button_is_disabled_for_terminal_status() -> None:
    selected = replace(view(), status=ScheduleStatus.COMPLETED)
    group = commands(AsyncMock())
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail_with_actions(selected, delete=False),
        embed=discord.Embed(title="予約詳細"),
    )
    name_edit = next(
        item for item in detail_view.children if item.custom_id == DETAIL_NAME_EDIT_CUSTOM_ID
    )
    assert name_edit.disabled is True


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


def test_detail_name_edit_modal_is_independent_and_uses_persisted_name_default() -> None:
    selected = replace(view(), display_name="現在名", display_name_source=DisplayNameSource.AI)
    group = commands(AsyncMock())
    detail_view = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    modal = ScheduleNameEditModal(commands=group, detail_view=detail_view)
    assert modal.timeout == 900.0
    assert len(modal.children) == 1
    assert modal.display_name.default == "現在名"
    assert modal.display_name.required is False
    assert modal.display_name.max_length == 32


@pytest.mark.asyncio
async def test_detail_name_edit_sets_manual_name_and_refreshes_latest_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = view()
    refreshed = replace(
        selected,
        display_name="新しい名前",
        display_name_source=DisplayNameSource.MANUAL,
        version=2,
    )
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = detail(refreshed)
    group = commands(queries)
    parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    modal = ScheduleNameEditModal(commands=group, detail_view=parent)
    modal.display_name._value = "  新しい名前  "
    service = AsyncMock()
    service.edit_manual_name.return_value = EditedScheduleName(
        public_id=selected.public_id,
        display_name="新しい名前",
        display_name_source=DisplayNameSource.MANUAL,
        version=2,
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleNamingService", lambda unused: service
    )
    submitted = interaction(administrator=True)

    await group._submit_detail_name_edit(modal, submitted)

    service.edit_manual_name.assert_awaited_once_with(
        guild_id=GUILD_ID,
        public_id=str(selected.public_id),
        actor_user_id=USER_ID,
        administrator=True,
        submitted_name="  新しい名前  ",
        edited_at=NOW,
        expected_version=1,
    )
    assert submitted.response.defer.await_args.kwargs == {}
    assert submitted.edit_original_response.await_args.kwargs["content"] == (
        DETAIL_NAME_EDITED_MESSAGE
    )
    latest = next(iter(group._detail_views))
    assert latest.context.display_name == "新しい名前"
    assert latest.context.expected_version == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (ScheduleNameNoChanges(), DETAIL_NAME_NO_CHANGES_MESSAGE),
        (ScheduleNameVersionConflict(), DETAIL_CONFLICT_MESSAGE),
    ],
)
async def test_detail_name_edit_noop_and_conflict_refresh_latest_detail(
    monkeypatch: pytest.MonkeyPatch, error: Exception, message: str
) -> None:
    selected = view()
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = detail(selected)
    group = commands(queries)
    parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    modal = ScheduleNameEditModal(commands=group, detail_view=parent)
    modal.display_name._value = ""
    service = AsyncMock()
    service.edit_manual_name.side_effect = error
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleNamingService", lambda unused: service
    )
    submitted = interaction()
    await group._submit_detail_name_edit(modal, submitted)
    assert submitted.edit_original_response.await_args.kwargs["content"] == message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary",
    ["other-user", "dm", "wrong-guild", "role-loss", "admin-loss"],
)
async def test_name_modal_submit_rechecks_actor_guild_role_and_admin_boundary(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    creator_id = USER_ID + 1 if boundary == "admin-loss" else USER_ID
    selected = replace(view(), creator_user_id=creator_id, version=17)
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = None
    group = commands(queries)
    parent = group._build_detail_view(
        interaction=interaction(administrator=boundary == "admin-loss"),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="古い詳細"),
    )
    group._detail_views.add(parent)
    modal = ScheduleNameEditModal(commands=group, detail_view=parent)
    modal.display_name._value = "拒否される名前"
    group._name_edit_modals.add(modal)
    service = AsyncMock()
    service.edit_manual_name.side_effect = ScheduleNameEditUnavailable
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleNamingService", lambda unused: service
    )
    submitted = interaction(administrator=False)
    if boundary == "other-user":
        submitted.user.id = USER_ID + 1
    elif boundary == "dm":
        submitted.guild = None
        submitted.guild_id = None
    elif boundary == "wrong-guild":
        submitted.guild.id = GUILD_ID + 1
        submitted.guild_id = GUILD_ID + 1
    elif boundary == "role-loss":
        submitted.user.roles = []

    await modal.on_submit(submitted)

    assert parent.closed and parent.finished and parent.is_finished()
    assert parent not in group._detail_views
    assert modal.closed and modal.is_finished() and modal not in group._name_edit_modals
    if boundary == "admin-loss":
        service.edit_manual_name.assert_awaited_once()
        assert service.edit_manual_name.await_args.kwargs["administrator"] is False
        submitted.response.defer.assert_awaited_once_with()
        response = submitted.edit_original_response
    else:
        service.edit_manual_name.assert_not_awaited()
        submitted.response.defer.assert_not_awaited()
        response = submitted.response.send_message
    assert response.await_args.args in ((), (DETAIL_NAME_PERMISSION_LOST_MESSAGE,))
    kwargs = response.await_args.kwargs
    assert kwargs.get("content", DETAIL_NAME_PERMISSION_LOST_MESSAGE) == (
        DETAIL_NAME_PERMISSION_LOST_MESSAGE
    )
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
    if boundary == "admin-loss":
        assert kwargs["embed"] is None and kwargs["view"] is None
    response_text = f"{response.await_args.args} {kwargs}"
    for forbidden in (str(selected.public_id), str(selected.version), selected.content):
        assert forbidden not in response_text


@pytest.mark.asyncio
async def test_name_modal_reopen_double_submit_timeout_and_bot_close_are_safe() -> None:
    selected = view()
    group = commands(AsyncMock())
    parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    button = next(item for item in parent.children if item.custom_id == DETAIL_NAME_EDIT_CUSTOM_ID)
    first = interaction()
    await button.callback(first)
    first_modal = first.response.send_modal.await_args.args[0]
    second = interaction()
    await button.callback(second)
    second_modal = second.response.send_modal.await_args.args[0]
    assert not first_modal.closed and not first_modal.is_finished()
    assert {first_modal, second_modal} <= group._name_edit_modals

    group._submit_detail_name_edit = AsyncMock()  # type: ignore[method-assign]
    await second_modal.on_submit(interaction())
    await second_modal.on_submit(interaction())
    group._submit_detail_name_edit.assert_awaited_once()
    third = interaction()
    await button.callback(third)
    third_modal = third.response.send_modal.await_args.args[0]
    await third_modal.on_timeout()
    assert third_modal.closed and parent.closed is False
    fourth = interaction()
    await button.callback(fourth)
    fourth_modal = fourth.response.send_modal.await_args.args[0]
    await group.close_confirmation_views()
    assert first_modal.closed and first_modal.is_finished()
    assert fourth_modal.closed and fourth_modal.is_finished()


def test_outer_modal_custom_ids_use_distinct_non_identifying_nonces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nonces = iter(
        ("a" * 32, "b" * 32, "c" * 32, "d" * 32, "e" * 32, "f" * 32, "ab" * 16, "cd" * 16)
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.secrets.token_hex",
        lambda unused: next(nonces),
    )
    secret_body = "本文secret-value"
    secret_name = "予約名secret-value"
    selected = replace(view(), content=secret_body, display_name=secret_name, version=47)
    group = commands(AsyncMock())
    first_detail = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    other = replace(selected, public_id=uuid.uuid7(), creator_user_id=USER_ID + 1)
    second_detail = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID + 1,
        detail=detail(other),
        embed=discord.Embed(title="予約詳細"),
    )
    resume_one = ResumeChoiceView(
        commands=group,
        interaction=interaction(),
        public_id=str(selected.public_id),
        actor_user_id=USER_ID,
        rescue_allowed=True,
    )
    resume_two = ResumeChoiceView(
        commands=group,
        interaction=interaction(),
        public_id=str(other.public_id),
        actor_user_id=USER_ID + 1,
        rescue_allowed=True,
    )
    pairs = [
        (
            DETAIL_EDIT_MODAL_PREFIX,
            ScheduleEditModal(commands=group, detail_view=first_detail, default_channel=None),
            ScheduleEditModal(commands=group, detail_view=second_detail, default_channel=None),
        ),
        (
            DETAIL_NAME_EDIT_MODAL_PREFIX,
            ScheduleNameEditModal(commands=group, detail_view=first_detail),
            ScheduleNameEditModal(commands=group, detail_view=second_detail),
        ),
        (
            DETAIL_DELETE_REASON_MODAL_PREFIX,
            DeleteReasonModal(commands=group, detail_view=first_detail),
            DeleteReasonModal(commands=group, detail_view=second_detail),
        ),
        (
            RESUME_TIME_MODAL_PREFIX,
            ResumeTimeModal(resume_one),
            ResumeTimeModal(resume_two),
        ),
    ]
    forbidden = (
        str(selected.public_id),
        str(other.public_id),
        str(USER_ID),
        str(GUILD_ID),
        str(selected.channel_id),
        str(selected.version),
        secret_body,
        secret_name,
        "監査理由secret-value",
        "token-secret-value",
    )
    for prefix, first, second in pairs:
        assert first.custom_id != second.custom_id
        for modal in (first, second):
            assert len(modal.custom_id) <= 100
            assert re.fullmatch(rf"{prefix}:[0-9a-f]{{32}}", modal.custom_id)
            assert all(value not in modal.custom_id for value in forbidden)

    assert first_detail.context.content == secret_body
    assert first_detail.context.display_name == secret_name
    assert (
        next(
            item.custom_id
            for item in pairs[1][1].walk_children()
            if isinstance(item, discord.ui.TextInput)
        )
        == "post_detail_display_name"
    )
    assert {
        item.custom_id
        for item in pairs[0][1].walk_children()
        if getattr(item, "custom_id", None) is not None
    } == {
        "post_detail_edit_channel",
        "post_detail_edit_scheduled_at",
        "post_detail_edit_content",
    }
    assert pairs[2][1].reason.custom_id == pairs[2][2].reason.custom_id
    assert pairs[3][1].local_time.custom_id == pairs[3][2].local_time.custom_id


@pytest.mark.asyncio
async def test_real_view_store_keeps_two_name_modals_dispatchable_independently() -> None:
    group = commands(AsyncMock())
    first_schedule = view()
    second_schedule = replace(
        first_schedule,
        public_id=uuid.uuid7(),
        creator_user_id=USER_ID + 1,
    )
    first_parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(first_schedule),
        embed=discord.Embed(title="予約詳細"),
    )
    second_parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID + 1,
        detail=detail(second_schedule),
        embed=discord.Embed(title="予約詳細"),
    )
    first = ScheduleNameEditModal(commands=group, detail_view=first_parent)
    second = ScheduleNameEditModal(commands=group, detail_view=second_parent)
    first.on_submit = AsyncMock()  # type: ignore[method-assign]
    second.on_submit = AsyncMock()  # type: ignore[method-assign]
    store = ViewStore(MagicMock())
    store.add_view(first)
    store.add_view(second)
    assert store._modals == {first.custom_id: first, second.custom_id: second}

    components = [
        {
            "type": discord.ComponentType.action_row.value,
            "components": [
                {
                    "type": discord.ComponentType.text_input.value,
                    "custom_id": "post_detail_display_name",
                    "value": "新しい名前",
                }
            ],
        }
    ]
    second_submit = interaction()
    store.dispatch_modal(second.custom_id, second_submit, components, {})  # type: ignore[arg-type]
    for _ in range(10):
        if second.on_submit.await_count:
            break
        await asyncio.sleep(0)
    second.on_submit.assert_awaited_once_with(second_submit)
    assert first.custom_id in store._modals
    assert second.custom_id not in store._modals

    first_submit = interaction()
    store.dispatch_modal(first.custom_id, first_submit, components, {})  # type: ignore[arg-type]
    for _ in range(10):
        if first.on_submit.await_count:
            break
        await asyncio.sleep(0)
    first.on_submit.assert_awaited_once_with(first_submit)
    assert first.custom_id not in store._modals


@pytest.mark.asyncio
async def test_real_view_store_keeps_other_modal_types_dispatchable_independently() -> None:
    selected = view()
    group = commands(AsyncMock())
    first_parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    second_parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(replace(selected, public_id=uuid.uuid7())),
        embed=discord.Embed(title="予約詳細"),
    )

    def resume_parent(public_id: uuid.UUID) -> ResumeChoiceView:
        return ResumeChoiceView(
            commands=group,
            interaction=interaction(),
            public_id=str(public_id),
            actor_user_id=USER_ID,
            rescue_allowed=True,
        )

    pairs = [
        (
            ScheduleEditModal(commands=group, detail_view=first_parent, default_channel=None),
            ScheduleEditModal(commands=group, detail_view=second_parent, default_channel=None),
        ),
        (
            DeleteReasonModal(commands=group, detail_view=first_parent),
            DeleteReasonModal(commands=group, detail_view=second_parent),
        ),
        (
            ResumeTimeModal(resume_parent(first_parent.context.public_id)),
            ResumeTimeModal(resume_parent(second_parent.context.public_id)),
        ),
    ]
    for first, second in pairs:
        first.on_submit = AsyncMock()  # type: ignore[method-assign]
        second.on_submit = AsyncMock()  # type: ignore[method-assign]
        store = ViewStore(MagicMock())
        store.add_view(first)
        store.add_view(second)

        second_submit = interaction()
        store.dispatch_modal(second.custom_id, second_submit, [], {})
        for _ in range(10):
            if second.on_submit.await_count:
                break
            await asyncio.sleep(0)
        second.on_submit.assert_awaited_once_with(second_submit)
        assert first.custom_id in store._modals
        assert second.custom_id not in store._modals

        first_submit = interaction()
        store.dispatch_modal(first.custom_id, first_submit, [], {})
        for _ in range(10):
            if first.on_submit.await_count:
                break
            await asyncio.sleep(0)
        first.on_submit.assert_awaited_once_with(first_submit)
        assert not store._modals


@pytest.mark.asyncio
async def test_all_modal_types_release_only_self_and_bot_close_collects_remaining() -> None:
    selected = view()
    group = commands(AsyncMock())
    parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )

    def resume_parent() -> ResumeChoiceView:
        return ResumeChoiceView(
            commands=group,
            interaction=interaction(),
            public_id=str(selected.public_id),
            actor_user_id=USER_ID,
            rescue_allowed=True,
        )

    groups = [
        (
            group._edit_modals,
            tuple(
                ScheduleEditModal(commands=group, detail_view=parent, default_channel=None)
                for _ in range(3)
            ),
        ),
        (
            group._name_edit_modals,
            tuple(ScheduleNameEditModal(commands=group, detail_view=parent) for _ in range(3)),
        ),
        (
            group._delete_reason_modals,
            tuple(DeleteReasonModal(commands=group, detail_view=parent) for _ in range(3)),
        ),
        (
            group._resume_modals,
            tuple(ResumeTimeModal(resume_parent()) for _ in range(3)),
        ),
    ]
    store = ViewStore(MagicMock())
    remaining: list[discord.ui.Modal] = []
    for registry, modals in groups:
        timeout_modal, error_modal, close_modal = modals
        registry.update(modals)
        for modal in modals:
            store.add_view(modal)

        await timeout_modal.on_timeout()
        assert timeout_modal not in registry
        assert timeout_modal.custom_id not in store._modals
        assert error_modal.custom_id in store._modals
        assert close_modal.custom_id in store._modals

        await error_modal.on_error(interaction(), RuntimeError("private-error"))
        assert error_modal not in registry
        assert error_modal.custom_id not in store._modals
        assert close_modal in registry
        assert close_modal.custom_id in store._modals
        remaining.append(close_modal)

    await group.close_confirmation_views()

    assert all(modal.closed and modal.is_finished() for modal in remaining)
    assert not store._modals
    assert not group._edit_modals
    assert not group._name_edit_modals
    assert not group._delete_reason_modals
    assert not group._resume_modals


@pytest.mark.asyncio
async def test_name_modal_real_view_store_reaches_delayed_version_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = view()
    latest = replace(
        selected,
        display_name="競合後の最新名",
        display_name_source=DisplayNameSource.MANUAL,
        version=2,
    )
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = detail(latest)
    group = commands(queries)
    first_parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    second_parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    first = ScheduleNameEditModal(commands=group, detail_view=first_parent)
    second = ScheduleNameEditModal(commands=group, detail_view=second_parent)
    group._name_edit_modals.update((first, second))
    service = AsyncMock()
    service.edit_manual_name.side_effect = [
        EditedScheduleName(
            public_id=selected.public_id,
            display_name="競合後の最新名",
            display_name_source=DisplayNameSource.MANUAL,
            version=2,
        ),
        ScheduleNameVersionConflict(),
    ]
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleNamingService", lambda unused: service
    )
    store = ViewStore(MagicMock())
    store.add_view(first)
    store.add_view(second)

    def components(value: str) -> list[dict[str, object]]:
        return [
            {
                "type": discord.ComponentType.action_row.value,
                "components": [
                    {
                        "type": discord.ComponentType.text_input.value,
                        "custom_id": "post_detail_display_name",
                        "value": value,
                    }
                ],
            }
        ]

    second_submit = interaction()
    store.dispatch_modal(
        second.custom_id,
        second_submit,
        components("競合後の最新名"),  # type: ignore[arg-type]
        {},
    )
    for _ in range(20):
        if second_submit.edit_original_response.await_count:
            break
        await asyncio.sleep(0)
    second_submit.response.defer.assert_awaited_once_with()
    assert second_submit.edit_original_response.await_args.kwargs["content"] == (
        DETAIL_NAME_EDITED_MESSAGE
    )
    assert first.custom_id in store._modals

    first_submit = interaction()
    store.dispatch_modal(
        first.custom_id,
        first_submit,
        components("古い画面からの名前"),  # type: ignore[arg-type]
        {},
    )
    for _ in range(20):
        if first_submit.edit_original_response.await_count:
            break
        await asyncio.sleep(0)
    first_submit.response.defer.assert_awaited_once_with()
    assert first_submit.edit_original_response.await_args.kwargs["content"] == (
        DETAIL_CONFLICT_MESSAGE
    )
    refreshed_embed = first_submit.edit_original_response.await_args.kwargs["embed"]
    assert "競合後の最新名" in " ".join(field.value for field in refreshed_embed.fields)
    assert "古い画面からの名前" not in " ".join(field.value for field in refreshed_embed.fields)
    assert service.edit_manual_name.await_args_list[0].kwargs["expected_version"] == 1
    assert service.edit_manual_name.await_args_list[1].kwargs["expected_version"] == 1
    assert first not in group._name_edit_modals


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["role-loss", "admin-loss"])
async def test_delayed_name_modal_view_store_submit_rechecks_current_authorization(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    selected = replace(
        view(),
        creator_user_id=USER_ID + 1 if boundary == "admin-loss" else USER_ID,
        version=23,
    )
    group = commands(AsyncMock())
    group._queries.get_schedule_detail.return_value = None
    parent = group._build_detail_view(
        interaction=interaction(administrator=boundary == "admin-loss"),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="古い詳細"),
    )
    other_parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(replace(view(), public_id=uuid.uuid7())),
        embed=discord.Embed(title="別Modalの詳細"),
    )
    delayed = ScheduleNameEditModal(commands=group, detail_view=parent)
    other_modal = ScheduleNameEditModal(commands=group, detail_view=other_parent)
    group._name_edit_modals.update((delayed, other_modal))
    service = AsyncMock()
    service.edit_manual_name.side_effect = ScheduleNameEditUnavailable
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleNamingService", lambda unused: service
    )
    store = ViewStore(MagicMock())
    store.add_view(delayed)
    store.add_view(other_modal)
    submitted = interaction(administrator=False)
    if boundary == "role-loss":
        submitted.user.roles = []
    components = [
        {
            "type": discord.ComponentType.action_row.value,
            "components": [
                {
                    "type": discord.ComponentType.text_input.value,
                    "custom_id": "post_detail_display_name",
                    "value": "遅延送信名",
                }
            ],
        }
    ]

    store.dispatch_modal(delayed.custom_id, submitted, components, {})  # type: ignore[arg-type]
    for _ in range(20):
        response = (
            submitted.edit_original_response
            if boundary == "admin-loss"
            else submitted.response.send_message
        )
        if response.await_count:
            break
        await asyncio.sleep(0)

    if boundary == "role-loss":
        service.edit_manual_name.assert_not_awaited()
        assert submitted.response.send_message.await_args.args == (
            DETAIL_NAME_PERMISSION_LOST_MESSAGE,
        )
    else:
        service.edit_manual_name.assert_awaited_once()
        assert service.edit_manual_name.await_args.kwargs["administrator"] is False
        assert submitted.edit_original_response.await_args.kwargs["content"] == (
            DETAIL_NAME_PERMISSION_LOST_MESSAGE
        )
    assert other_modal.custom_id in store._modals
    assert delayed.custom_id not in store._modals
    assert other_modal in group._name_edit_modals


@pytest.mark.asyncio
async def test_name_modal_rejection_exposes_only_fixed_boundary(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "token-private traceback-sentinel database-password"
    selected = replace(
        view(content=f"秘密本文 {secret}"),
        creator_user_id=USER_ID + 1,
        display_name=f"秘密予約名 {secret}",
        display_name_source=DisplayNameSource.MANUAL,
        version=91,
    )
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = None
    group = commands(queries)
    parent = group._build_detail_view(
        interaction=interaction(administrator=True),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title=f"古い詳細 {secret}"),
    )
    modal = ScheduleNameEditModal(commands=group, detail_view=parent)
    modal.display_name._value = f"拒否入力 {secret}"
    group._name_edit_modals.add(modal)
    service = AsyncMock()
    service.edit_manual_name.side_effect = ScheduleNameEditUnavailable(secret)
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleNamingService", lambda unused: service
    )
    submitted = interaction(administrator=False)

    with caplog.at_level(logging.ERROR, logger="test.posts"):
        await modal.on_submit(submitted)

    kwargs = submitted.edit_original_response.await_args.kwargs
    assert kwargs == {
        "content": DETAIL_NAME_PERMISSION_LOST_MESSAGE,
        "embed": None,
        "view": None,
        "allowed_mentions": kwargs["allowed_mentions"],
    }
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
    exposed = f"{submitted.edit_original_response.await_args} {caplog.text}"
    for forbidden in (
        secret,
        str(selected.public_id),
        str(selected.version),
        selected.content,
        selected.display_name,
    ):
        assert forbidden not in exposed
    assert not [record for record in caplog.records if record.name == "test.posts"]
    assert modal.closed and modal.is_finished() and modal not in group._name_edit_modals


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
    [
        "cache-miss",
        "other-guild",
        "thread",
        "dm",
        "category",
        "voice",
        "view-permission",
        "send-permission",
        "member",
    ],
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
    elif failure == "view-permission":
        cached = cached_text_channel(submitted.guild)
        cached.permissions_for.return_value.view_channel = False
    elif failure == "send-permission":
        cached = cached_text_channel(submitted.guild)
        cached.permissions_for.return_value.send_messages = False
    elif failure == "member":
        submitted.guild.me = None
    submitted.guild.get_channel.return_value = cached

    await group._submit_detail_edit(modal, submitted)

    assert submitted.response.send_message.await_args.args == (DETAIL_EDIT_CHANNEL_MESSAGE,)
    assert submitted.response.send_message.await_args.kwargs["ephemeral"] is True
    assert submitted.response.send_message.await_args.kwargs["allowed_mentions"].to_dict() == {
        "parse": []
    }
    assert not hasattr(submitted.guild, "fetch_channel") or not submitted.guild.fetch_channel.called
    assert detail_view.finished is False and not detail_view.is_finished()
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
    assert not first_modal.closed and not first_modal.is_finished()
    assert {first_modal, second_modal} <= group._edit_modals
    assert second_modal in group._edit_modals
    assert detail_view in group._detail_views
    assert detail_view.finished is False and detail_view.closed is False
    assert not detail_view.is_finished()

    group._submit_detail_edit = AsyncMock()  # type: ignore[method-assign]
    await second_modal.on_submit(interaction())
    group._submit_detail_edit.assert_awaited_once()
    await first_modal.on_submit(interaction())
    assert group._submit_detail_edit.await_count == 2
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
    assert [item.label for item in built.children] == ["編集", "予約名を編集", "削除"]
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
async def test_detail_back_uses_changed_service_snapshot_and_recomputes_filtered_page() -> None:
    selected = replace(
        view(), schedule_type=ScheduleType.WEEKLY, status=ScheduleStatus.ACTIVE, version=1
    )
    changed = replace(selected, status=ScheduleStatus.PAUSED, content="変更後", version=2)
    queries = AsyncMock()
    queries.get_schedule_page.side_effect = [
        SchedulePage((selected,), 3, 21),
        SchedulePage((changed,), 2, 11),
    ]
    queries.get_schedule_detail.side_effect = [detail(selected), detail(changed)]
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    group = commands(queries, session=session)
    original = interaction()
    await group.list_command.callback(group, original, None, 3)
    list_view = original.response.send_message.await_args.kwargs["view"]
    list_view.schedule_type = ScheduleType.WEEKLY

    opened = interaction()
    await group._show_list_selection(list_view, opened, str(selected.public_id))
    detail_view = opened.response.edit_message.await_args.kwargs["view"]

    # A separate query/service snapshot now observes the changed reservation.
    returned = interaction()
    await group._return_to_list(detail_view, returned)

    queries.get_schedule_detail.assert_awaited_with(
        guild_id=GUILD_ID,
        requester_user_id=USER_ID,
        administrator=False,
        public_id=str(selected.public_id),
        now=NOW,
    )
    assert queries.get_schedule_page.await_args.kwargs == {
        "guild_id": GUILD_ID,
        "requester_user_id": USER_ID,
        "administrator": False,
        "status": None,
        "page": 3,
        "schedule_type": ScheduleType.WEEKLY,
        "clamp": True,
    }
    latest = returned.response.edit_message.await_args.kwargs["view"]
    assert latest.page == 2
    assert latest.status is None
    assert latest.schedule_type is ScheduleType.WEEKLY
    assert changed.status is ScheduleStatus.PAUSED and changed.content == "変更後"
    assert changed.version == 2 and selected.version == 1
    assert (
        "変更後" not in returned.response.edit_message.await_args.kwargs["embed"].fields[-1].value
    )
    assert (
        "名称未設定" in returned.response.edit_message.await_args.kwargs["embed"].fields[-1].value
    )
    session.__aenter__.assert_not_awaited()


@pytest.mark.asyncio
async def test_detail_back_excludes_changed_status_and_clamps_recomputed_page() -> None:
    selected = replace(
        view(), schedule_type=ScheduleType.WEEKLY, status=ScheduleStatus.ACTIVE, version=1
    )
    remaining = replace(view(), schedule_type=ScheduleType.WEEKLY, status=ScheduleStatus.ACTIVE)
    changed = replace(selected, status=ScheduleStatus.PAUSED, version=2)
    queries = AsyncMock()
    queries.get_schedule_page.side_effect = [
        SchedulePage((selected,), 3, 21),
        SchedulePage((remaining,), 2, 11),
    ]
    queries.get_schedule_detail.side_effect = [detail(selected), detail(changed)]
    group = commands(queries)
    original = interaction()
    status = app_commands.Choice(name="有効", value=ScheduleStatus.ACTIVE.value)
    await group.list_command.callback(group, original, status, 3)
    list_view = original.response.send_message.await_args.kwargs["view"]
    list_view.schedule_type = ScheduleType.WEEKLY

    opened = interaction()
    await group._show_list_selection(list_view, opened, str(selected.public_id))
    returned = interaction()
    await group._return_to_list(opened.response.edit_message.await_args.kwargs["view"], returned)

    assert queries.get_schedule_page.await_args.kwargs["clamp"] is True
    assert queries.get_schedule_page.await_args.kwargs["status"] is ScheduleStatus.ACTIVE
    latest = returned.response.edit_message.await_args.kwargs["view"]
    assert latest.page == 2 and latest.status is ScheduleStatus.ACTIVE
    assert latest.schedule_type is ScheduleType.WEEKLY
    select = next(item for item in latest.children if item.custom_id == "post_list_select")
    assert [option.value for option in select.options] == [str(remaining.public_id)]
    assert str(changed.public_id) not in [option.value for option in select.options]


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
async def test_list_and_detail_real_view_store_dispatch_without_timeout_and_close_cleanly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    selected = view()
    queries = AsyncMock()
    queries.get_schedule_page.return_value = SchedulePage((selected,), 1, 1)
    queries.get_schedule_detail.return_value = detail(selected)
    group = commands(queries)
    store = ViewStore(MagicMock())

    original = interaction()
    await group.list_command.callback(group, original, None, 1)
    list_view = original.response.send_message.await_args.kwargs["view"]
    store.add_view(list_view, message_id=41)
    group._move_list_page = AsyncMock()  # type: ignore[method-assign]
    list_click = interaction()
    list_click.message = MagicMock(spec=discord.Message)
    list_click.message.id = 41
    list_click.data = {}
    store.dispatch_view(discord.ComponentType.button.value, "post_list_next", list_click)

    direct = interaction()
    await group.show_command.callback(group, direct, str(selected.public_id))
    detail_view = direct.response.send_message.await_args.kwargs["view"]
    store.add_view(detail_view, message_id=42)
    group._edit_from_detail = AsyncMock()  # type: ignore[method-assign]
    detail_click = interaction()
    detail_click.message = MagicMock(spec=discord.Message)
    detail_click.message.id = 42
    detail_click.data = {}
    with caplog.at_level(logging.ERROR, logger="test.posts"):
        store.dispatch_view(
            discord.ComponentType.button.value,
            DETAIL_EDIT_CUSTOM_ID,
            detail_click,
        )
        for _ in range(10):
            if group._move_list_page.await_count and group._edit_from_detail.await_count:
                break
            await asyncio.sleep(0)

    group._move_list_page.assert_awaited_once_with(list_view, list_click, 2)
    group._edit_from_detail.assert_awaited_once_with(detail_view, detail_click)
    for active_view in (list_view, detail_view):
        assert active_view.timeout is None
        assert active_view._BaseView__timeout_task is None
        assert not active_view.is_finished()
        assert not all(getattr(item, "disabled", False) for item in active_view.children)
    original.edit_original_response.assert_not_awaited()
    direct.edit_original_response.assert_not_awaited()
    assert "schedule_list_timeout_response_failed" not in caplog.text
    assert "schedule_detail_timeout_response_failed" not in caplog.text
    assert "/post list" in original.response.send_message.await_args.kwargs["embed"].description
    assert "/post show" in direct.response.send_message.await_args.kwargs["embed"].description

    await group.close_confirmation_views()
    await group.close_confirmation_views()
    assert list_view.is_finished() and detail_view.is_finished()
    assert not group._list_views and not group._detail_views
    assert not store._views


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
async def test_detail_resume_cancel_timeout_and_races_are_read_only_and_recoverable() -> None:
    selected = replace(
        view(),
        schedule_type=ScheduleType.DAILY,
        status=ScheduleStatus.PAUSED,
        next_run_at=None,
        version=7,
    )
    paused_detail = detail(selected)
    paused_detail = replace(
        paused_detail,
        actions=replace(paused_detail.actions, can_pause=False, can_resume=True),
    )
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = paused_detail
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    group = commands(queries, session=session)
    parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=paused_detail,
        embed=discord.Embed(title="予約詳細"),
    )
    assert parent.timeout is None
    snapshot = (selected.status, selected.version, selected.next_run_at, selected.content)

    cancel_view = ResumeChoiceView(
        commands=group,
        interaction=parent.initial_interaction,
        public_id=str(selected.public_id),
        actor_user_id=USER_ID,
        rescue_allowed=True,
        detail_context=parent.context,
    )
    group._resume_views.add(cancel_view)
    cancelled = interaction()
    await group._cancel_resume_choice(cancel_view, cancelled)
    await group._cancel_resume_choice(cancel_view, interaction())
    await group._expire_resume_choice(cancel_view)

    assert cancel_view.timeout == 900.0 and cancel_view.is_finished()
    assert queries.get_schedule_detail.await_count == 1
    latest_parent = next(iter(group._detail_views))
    assert latest_parent.timeout is None and not latest_parent.is_finished()
    assert latest_parent.context.expected_version == 7
    assert (selected.status, selected.version, selected.next_run_at, selected.content) == snapshot
    assert "キャンセル" in cancelled.response.edit_message.await_args.kwargs["content"]

    timeout_origin = interaction()
    timeout_view = ResumeChoiceView(
        commands=group,
        interaction=timeout_origin,
        public_id=str(selected.public_id),
        actor_user_id=USER_ID,
        rescue_allowed=True,
        detail_context=latest_parent.context,
    )
    group._resume_views.add(timeout_view)
    await group._expire_resume_choice(timeout_view)
    await group._expire_resume_choice(timeout_view)
    await group._cancel_resume_choice(timeout_view, interaction())

    assert timeout_view.is_finished()
    assert all(item.disabled for item in timeout_view.children)
    timeout_kwargs = timeout_origin.edit_original_response.await_args.kwargs
    assert "/post show" in timeout_kwargs["content"] and "/post list" in timeout_kwargs["content"]
    assert timeout_kwargs["view"] is timeout_view
    assert (selected.status, selected.version, selected.next_run_at, selected.content) == snapshot
    session.__aenter__.assert_not_awaited()
    await group.close_confirmation_views()
    await group.close_confirmation_views()
    assert not group._resume_views and not group._detail_views


@pytest.mark.asyncio
async def test_detail_delete_cancel_timeout_and_races_are_read_only_and_recoverable() -> None:
    selected = replace(view(), schedule_type=ScheduleType.DAILY, version=9)
    selected_detail = detail(selected)
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = selected_detail
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    group = commands(queries, session=session)
    parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=selected_detail,
        embed=discord.Embed(title="予約詳細"),
    )
    assert parent.timeout is None
    snapshot = (selected.status, selected.version, selected.next_run_at, selected.content)

    cancel_view = ScheduleDeletionConfirmView(
        commands=group,
        interaction=parent.initial_interaction,
        public_id=str(selected.public_id),
        reason="creator_deleted",
        actor_user_id=USER_ID,
        detail_context=parent.context,
    )
    group._delete_views.add(cancel_view)
    cancelled = interaction()
    await group._cancel_deletion(cancel_view, cancelled)
    await group._cancel_deletion(cancel_view, interaction())
    await group._expire_deletion(cancel_view)

    assert cancel_view.timeout == 900.0 and cancel_view.is_finished()
    assert queries.get_schedule_detail.await_count == 1
    latest_parent = next(iter(group._detail_views))
    assert latest_parent.timeout is None and not latest_parent.is_finished()
    assert latest_parent.context.expected_version == 9
    assert (selected.status, selected.version, selected.next_run_at, selected.content) == snapshot
    assert "キャンセル" in cancelled.response.edit_message.await_args.kwargs["content"]

    timeout_origin = interaction()
    timeout_view = ScheduleDeletionConfirmView(
        commands=group,
        interaction=timeout_origin,
        public_id=str(selected.public_id),
        reason="creator_deleted",
        actor_user_id=USER_ID,
        detail_context=latest_parent.context,
    )
    group._delete_views.add(timeout_view)
    await group._expire_deletion(timeout_view)
    await group._expire_deletion(timeout_view)
    await group._confirm_deletion(timeout_view, interaction())
    await group._cancel_deletion(timeout_view, interaction())

    assert timeout_view.is_finished()
    assert all(item.disabled for item in timeout_view.children)
    timeout_kwargs = timeout_origin.edit_original_response.await_args.kwargs
    assert "/post show" in timeout_kwargs["content"] and "/post list" in timeout_kwargs["content"]
    assert timeout_kwargs["view"] is timeout_view
    assert (selected.status, selected.version, selected.next_run_at, selected.content) == snapshot
    session.__aenter__.assert_not_awaited()
    await group.close_confirmation_views()
    await group.close_confirmation_views()
    assert not group._delete_views and not group._detail_views


@pytest.mark.asyncio
@pytest.mark.parametrize("from_list", [False, True], ids=["direct-show", "list-detail"])
async def test_detail_pause_callback_refreshes_paused_detail_and_preserves_origin(
    monkeypatch: pytest.MonkeyPatch, from_list: bool
) -> None:
    selected = replace(
        view(),
        schedule_type=ScheduleType.DAILY,
        status=ScheduleStatus.ACTIVE,
        local_time=time(9),
        version=4,
    )
    active = detail_with_actions(selected, pause=True)
    paused_schedule = replace(selected, status=ScheduleStatus.PAUSED, next_run_at=None, version=5)
    paused = detail_with_actions(paused_schedule, resume=True)
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = paused
    service = AsyncMock()
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.SchedulePauseService", lambda unused: service
    )
    group = commands(queries)
    origin = (
        ScheduleListOrigin(status=ScheduleStatus.ACTIVE, schedule_type=ScheduleType.DAILY, page=3)
        if from_list
        else None
    )
    parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=active,
        embed=discord.Embed(title="予約詳細"),
        list_origin=origin,
    )
    group._detail_views.add(parent)
    clicked = interaction()
    pause_button = next(
        item for item in parent.children if item.custom_id == DETAIL_PAUSE_CUSTOM_ID
    )

    await pause_button.callback(clicked)
    await pause_button.callback(interaction())

    service.pause.assert_awaited_once()
    assert service.pause.await_args.kwargs["expected_version"] == 4
    assert service.pause.await_args.kwargs["paused_at"] == NOW
    arguments = clicked.edit_original_response.await_args.kwargs
    assert arguments["content"] == DETAIL_PAUSED_MESSAGE
    assert arguments["allowed_mentions"].to_dict() == {"parse": []}
    refreshed = arguments["view"]
    assert refreshed.context.expected_version == 5
    assert refreshed.context.list_origin == origin
    custom_ids = {item.custom_id for item in refreshed.children}
    assert DETAIL_RESUME_CUSTOM_ID in custom_ids and DETAIL_DELETE_CUSTOM_ID in custom_ids
    if from_list:
        assert DETAIL_BACK_CUSTOM_ID in custom_ids
        assert refreshed.context.list_origin == origin
    else:
        assert DETAIL_BACK_CUSTOM_ID not in custom_ids
    embed_text = " ".join(
        [arguments["embed"].title, arguments["embed"].description or ""]
        + [f"{field.name} {field.value}" for field in arguments["embed"].fields]
    )
    assert "⏸️ 一時停止中" in embed_text
    assert "一時停止中は投稿されません" in embed_text
    assert arguments["embed"].fields[-1].name == "⚠️ 一時停止について"
    assert parent.is_finished() and refreshed.timeout is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "replacement_at"),
    [
        (ResumeMode.NEXT_REGULAR, None),
        (ResumeMode.IMMEDIATE_ONCE, None),
        (ResumeMode.RESCHEDULED_ONCE, NOW + timedelta(hours=1)),
    ],
)
async def test_detail_overdue_resume_choices_use_expected_version_and_refresh_owner(
    monkeypatch: pytest.MonkeyPatch,
    mode: ResumeMode,
    replacement_at: datetime | None,
) -> None:
    selected = replace(
        view(),
        schedule_type=ScheduleType.WEEKLY,
        status=ScheduleStatus.PAUSED,
        next_run_at=None,
        local_time=time(9),
        weekday=2,
        end_date=date(2026, 8, 31),
        version=7,
    )
    paused = detail_with_actions(selected, resume=True)
    resumed_schedule = replace(selected, status=ScheduleStatus.ACTIVE, next_run_at=NOW, version=8)
    resumed = detail_with_actions(resumed_schedule, pause=True)
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = resumed
    service = AsyncMock()
    service.preview_resume.return_value = ResumePreview(selected.public_id, NOW, True, True)
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.SchedulePauseService", lambda unused: service
    )
    group = commands(queries)
    parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=paused,
        embed=discord.Embed(title="予約詳細"),
    )
    group._detail_views.add(parent)
    opened = interaction()

    await group._resume_from_detail(parent, opened)

    choice = opened.response.edit_message.await_args.kwargs["view"]
    assert parent.is_finished() and choice in group._resume_views
    assert choice.detail_context.expected_version == 7
    assert opened.response.edit_message.await_args.kwargs["allowed_mentions"].to_dict() == {
        "parse": []
    }
    submitted = interaction()
    await group._finish_resume_choice(choice, submitted, mode, replacement_at)
    service.resume.assert_awaited_once()
    arguments = service.resume.await_args.kwargs
    assert arguments["expected_version"] == 7
    assert arguments["mode"] is mode and arguments["replacement_at"] == replacement_at
    assert arguments["resumed_at"] == NOW
    refreshed_kwargs = submitted.edit_original_response.await_args.kwargs
    assert refreshed_kwargs["content"] == DETAIL_RESUMED_MESSAGE
    assert refreshed_kwargs["allowed_mentions"].to_dict() == {"parse": []}
    refreshed = refreshed_kwargs["view"]
    assert refreshed.context.expected_version == 8
    assert refreshed.context.local_time == time(9)
    assert refreshed.context.weekday == 2
    assert refreshed.context.end_date == date(2026, 8, 31)
    assert choice.is_finished() and refreshed in group._detail_views


@pytest.mark.asyncio
async def test_detail_pause_resume_delete_conflicts_refresh_latest_safe_views(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_id = uuid.uuid7()
    base = replace(
        view(), public_id=public_id, schedule_type=ScheduleType.DAILY, local_time=time(9), version=3
    )
    active = detail_with_actions(base, pause=True)
    paused_schedule = replace(base, status=ScheduleStatus.PAUSED, next_run_at=None, version=4)
    paused = detail_with_actions(paused_schedule, resume=True)
    deleted_schedule = replace(base, status=ScheduleStatus.DELETED, next_run_at=None, version=5)
    deleted = detail_with_actions(deleted_schedule, delete=False)
    queries = AsyncMock()
    queries.get_schedule_detail.side_effect = [paused, active, deleted]
    pause_service = AsyncMock()
    pause_service.pause.side_effect = ScheduleVersionConflict
    pause_service.resume.side_effect = ScheduleVersionConflict
    delete_service = AsyncMock()
    delete_service.delete.side_effect = ScheduleDeletionVersionConflict
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.SchedulePauseService", lambda unused: pause_service
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleDeletionService", lambda unused: delete_service
    )
    group = commands(queries)

    pause_parent = group._build_detail_view(
        interaction=interaction(), actor_user_id=USER_ID, detail=active, embed=discord.Embed()
    )
    pause_click = interaction()
    await group._pause_from_detail(pause_parent, pause_click)
    assert (
        pause_click.edit_original_response.await_args.kwargs["content"] == DETAIL_CONFLICT_MESSAGE
    )
    assert DETAIL_RESUME_CUSTOM_ID in {
        item.custom_id
        for item in pause_click.edit_original_response.await_args.kwargs["view"].children
    }

    resume_choice = ResumeChoiceView(
        commands=group,
        interaction=interaction(),
        public_id=str(public_id),
        actor_user_id=USER_ID,
        rescue_allowed=True,
        detail_context=replace(
            pause_parent.context,
            actions=replace(paused.actions, observed_version=pause_parent.context.expected_version),
        ),
    )
    resume_click = interaction()
    await group._finish_resume_choice(resume_choice, resume_click, ResumeMode.NEXT_REGULAR)
    assert (
        resume_click.edit_original_response.await_args.kwargs["content"] == DETAIL_CONFLICT_MESSAGE
    )
    assert DETAIL_PAUSE_CUSTOM_ID in {
        item.custom_id
        for item in resume_click.edit_original_response.await_args.kwargs["view"].children
    }

    deletion = ScheduleDeletionConfirmView(
        commands=group,
        interaction=interaction(),
        public_id=str(public_id),
        reason="creator_deleted",
        actor_user_id=USER_ID,
        detail_context=pause_parent.context,
    )
    delete_click = interaction()
    await group._confirm_deletion(deletion, delete_click)
    delete_kwargs = delete_click.edit_original_response.await_args.kwargs
    assert delete_kwargs["content"] == DETAIL_CONFLICT_MESSAGE
    assert DETAIL_DELETE_CUSTOM_ID not in {
        item.custom_id for item in delete_kwargs["view"].children if not item.disabled
    }
    assert pause_service.pause.await_args.kwargs["expected_version"] == 3
    assert pause_service.resume.await_args.kwargs["expected_version"] == 3
    assert delete_service.delete.await_args.kwargs["expected_version"] == 3
    for value in (pause_click, resume_click, delete_click):
        assert value.edit_original_response.await_args.kwargs["allowed_mentions"].to_dict() == {
            "parse": []
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "event_name"),
    [
        ("pause", "schedule_detail_pause_failed"),
        ("resume", "schedule_resume_failed"),
        ("delete", "schedule_delete_failed"),
    ],
)
async def test_detail_state_operation_failures_expose_only_fixed_boundary(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    operation: str,
    event_name: str,
) -> None:
    secret = (
        "token_test_private postgresql+psycopg://user:password@development/private "
        "worker=12345678 traceback-sentinel"
    )
    public_id = uuid.uuid7()
    selected = replace(
        view(content="visible preview " + "x" * 1000 + secret),
        public_id=public_id,
        schedule_type=ScheduleType.DAILY,
        local_time=time(9),
        version=71,
    )
    pause_service = AsyncMock()
    delete_service = AsyncMock()
    pause_service.pause.side_effect = RuntimeError(secret)
    pause_service.resume.side_effect = RuntimeError(secret)
    delete_service.delete.side_effect = RuntimeError(secret)
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.SchedulePauseService", lambda unused: pause_service
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleDeletionService", lambda unused: delete_service
    )
    group = commands(AsyncMock())
    clicked = interaction()

    with caplog.at_level(logging.ERROR, logger="test.posts"):
        if operation == "pause":
            parent = group._build_detail_view(
                interaction=interaction(),
                actor_user_id=USER_ID,
                detail=detail_with_actions(selected, pause=True),
                embed=discord.Embed(),
            )
            await group._pause_from_detail(parent, clicked)
            sender = clicked.response.send_message
        elif operation == "resume":
            context = group._build_detail_view(
                interaction=interaction(),
                actor_user_id=USER_ID,
                detail=detail_with_actions(
                    replace(selected, status=ScheduleStatus.PAUSED, next_run_at=None), resume=True
                ),
                embed=discord.Embed(),
            ).context
            choice = ResumeChoiceView(
                commands=group,
                interaction=interaction(),
                public_id=str(public_id),
                actor_user_id=USER_ID,
                rescue_allowed=True,
                detail_context=context,
            )
            await group._finish_resume_choice(choice, clicked, ResumeMode.NEXT_REGULAR)
            sender = clicked.edit_original_response
        else:
            parent = group._build_detail_view(
                interaction=interaction(),
                actor_user_id=USER_ID,
                detail=detail_with_actions(selected),
                embed=discord.Embed(),
            )
            confirmation = ScheduleDeletionConfirmView(
                commands=group,
                interaction=interaction(),
                public_id=str(public_id),
                reason="reason-canary-private",
                actor_user_id=USER_ID,
                detail_context=parent.context,
            )
            await group._confirm_deletion(confirmation, clicked)
            sender = clicked.edit_original_response

    assert (
        sender.await_args.kwargs.get(
            "content", sender.await_args.args[0] if sender.await_args.args else None
        )
        == INTERNAL_ERROR_MESSAGE
    )
    assert sender.await_args.kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert [record.message for record in caplog.records if record.name == "test.posts"] == [
        event_name
    ]
    assert secret not in caplog.text
    assert "reason-canary-private" not in caplog.text
    assert clicked.followup.send.await_count == 0


@pytest.mark.asyncio
async def test_resume_time_modal_enforces_five_minutes_and_midnight_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = replace(
        view(),
        schedule_type=ScheduleType.DAILY,
        status=ScheduleStatus.PAUSED,
        next_run_at=None,
        local_time=time(9),
        version=5,
    )
    paused = detail_with_actions(selected, resume=True)
    resumed = detail_with_actions(
        replace(selected, status=ScheduleStatus.ACTIVE, next_run_at=NOW, version=6), pause=True
    )
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = resumed
    service = AsyncMock()
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.SchedulePauseService", lambda unused: service
    )
    group = commands(queries)
    context = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=paused,
        embed=discord.Embed(),
    ).context

    too_soon = ResumeChoiceView(
        commands=group,
        interaction=interaction(),
        public_id=str(selected.public_id),
        actor_user_id=USER_ID,
        rescue_allowed=True,
        detail_context=context,
    )
    rejected = interaction()
    await group._submit_resume_time(too_soon, rejected, "12:04")
    assert rejected.response.send_message.await_args.args == (RESUME_TIME_MESSAGE,)
    service.resume.assert_not_awaited()

    valid = ResumeChoiceView(
        commands=group,
        interaction=interaction(),
        public_id=str(selected.public_id),
        actor_user_id=USER_ID,
        rescue_allowed=True,
        detail_context=context,
    )
    accepted = interaction()
    await group._submit_resume_time(valid, accepted, "12:05")
    assert service.resume.await_args.kwargs["replacement_at"] == NOW + timedelta(minutes=5)
    assert service.resume.await_args.kwargs["mode"] is ResumeMode.RESCHEDULED_ONCE
    assert service.resume.await_args.kwargs["expected_version"] == 5

    group._clock = FixedClock(datetime(2026, 8, 18, 14, 56, tzinfo=UTC))
    near_midnight = ResumeChoiceView(
        commands=group,
        interaction=interaction(),
        public_id=str(selected.public_id),
        actor_user_id=USER_ID,
        rescue_allowed=True,
        detail_context=context,
    )
    assert near_midnight.time_button.disabled is True


@pytest.mark.asyncio
async def test_detail_state_refresh_presenter_failure_logs_only_fixed_event(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "presenter traceback token_test_private postgresql://private"
    selected = replace(view(), schedule_type=ScheduleType.DAILY, local_time=time(9), version=12)
    latest = detail_with_actions(
        replace(selected, status=ScheduleStatus.PAUSED, next_run_at=None, version=13),
        resume=True,
    )
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = latest
    group = commands(queries)
    source = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail_with_actions(selected, pause=True),
        embed=discord.Embed(),
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.schedule_detail_embed",
        MagicMock(side_effect=RuntimeError(secret)),
    )
    clicked = interaction()

    with caplog.at_level(logging.ERROR, logger="test.posts"):
        await group._refresh_detail(
            source,
            clicked,
            InteractionActor(user_id=USER_ID, administrator=False),
            DETAIL_PAUSED_MESSAGE,
        )

    clicked.edit_original_response.assert_not_awaited()
    assert [record.message for record in caplog.records if record.name == "test.posts"] == [
        "schedule_detail_refresh_response_failed"
    ]
    assert secret not in caplog.text
    assert source.is_finished()


@pytest.mark.asyncio
async def test_detail_show_list_and_failure_paths_keep_content_and_internal_boundaries(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    content = "正式な本文欄 " + "A" * 1_200
    expected_version_canary = 2_147_483_647
    selected = replace(view(content=content), version=expected_version_canary)
    selected_detail = replace(
        detail(selected),
        actions=replace(
            detail(selected).actions,
            reason_code=ScheduleActionReason.RUN_CONFLICT,
            observed_version=expected_version_canary,
        ),
    )
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = selected_detail
    group = commands(queries)

    shown = interaction()
    await group.show_command.callback(group, shown, str(selected.public_id))
    show_kwargs = shown.response.send_message.await_args.kwargs
    show_embed = show_kwargs["embed"]
    show_text = " ".join(
        [show_embed.title, show_embed.description or ""]
        + [f"{field.name} {field.value}" for field in show_embed.fields]
    )
    assert f"`{selected.public_id}`" in show_text
    assert str(expected_version_canary) not in show_text
    assert ScheduleActionReason.RUN_CONFLICT.value not in show_text
    assert len(show_embed) <= 6_000
    assert show_kwargs["ephemeral"] is True
    assert show_kwargs["allowed_mentions"].to_dict() == {"parse": []}
    for item in show_kwargs["view"].children:
        for forbidden in (
            str(selected.public_id),
            str(expected_version_canary),
            str(GUILD_ID),
            str(USER_ID),
            content,
        ):
            assert forbidden not in item.custom_id

    page = SchedulePage((selected,), 1, 1)
    queries.get_schedule_page.return_value = page
    listed = interaction()
    await group.list_command.callback(group, listed, None, 1)
    list_view = listed.response.send_message.await_args.kwargs["view"]
    opened = interaction()
    await group._show_list_selection(list_view, opened, str(selected.public_id))
    list_detail_kwargs = opened.response.edit_message.await_args.kwargs
    assert list_detail_kwargs["embed"].to_dict() == show_embed.to_dict()
    assert list_detail_kwargs["allowed_mentions"].to_dict() == {"parse": []}

    secret = (
        "token_test_private postgresql+psycopg://user:password@development/private "
        "Discord API response traceback-sentinel"
    )
    failing_queries = AsyncMock()
    failing_queries.get_schedule_detail.side_effect = RuntimeError(secret)
    failing_group = commands(failing_queries)
    failed = interaction()
    with caplog.at_level(logging.ERROR, logger="test.posts"):
        await failing_group.show_command.callback(failing_group, failed, str(selected.public_id))
    assert failed.response.send_message.await_args.args == (INTERNAL_ERROR_MESSAGE,)
    assert [record.message for record in caplog.records if record.name == "test.posts"] == [
        "schedule_show_failed"
    ]
    assert secret not in caplog.text

    caplog.clear()
    presenter_group = commands(queries)
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.schedule_detail_embed",
        MagicMock(side_effect=RuntimeError(secret)),
    )
    presenter_failed = interaction()
    with caplog.at_level(logging.ERROR, logger="test.posts"):
        await presenter_group.show_command.callback(
            presenter_group, presenter_failed, str(selected.public_id)
        )
    assert presenter_failed.response.send_message.await_args.args == (INTERNAL_ERROR_MESSAGE,)
    assert [record.message for record in caplog.records if record.name == "test.posts"] == [
        "schedule_presentation_failed"
    ]
    assert secret not in caplog.text
    assert set(ScheduleView.__dataclass_fields__) == {
        "public_id",
        "channel_id",
        "creator_user_id",
        "schedule_type",
        "status",
        "content",
        "display_name",
        "display_name_source",
        "next_run_at",
        "local_time",
        "weekday",
        "end_date",
        "version",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary",
    ["owner", "administrator", "other-owner", "dm", "wrong-guild", "role-loss", "admin-loss"],
)
async def test_detail_edit_submit_rechecks_actor_and_administrator_boundary(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    creator_id = USER_ID + 1 if boundary in {"administrator", "admin-loss"} else USER_ID
    selected = replace(
        view(content="before"),
        creator_user_id=creator_id,
        schedule_type=ScheduleType.DAILY,
        local_time=time(9),
        version=9,
    )
    latest = replace(selected, content="after", version=10)
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = detail(latest)
    group = commands(queries)
    parent = group._build_detail_view(
        interaction=interaction(administrator=boundary in {"administrator", "admin-loss"}),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(),
        list_origin=ScheduleListOrigin(ScheduleStatus.ACTIVE, ScheduleType.DAILY, 2),
    )
    modal = ScheduleEditModal(commands=group, detail_view=parent, default_channel=None)
    modal.channel._handle_submit(interaction(), {"values": []}, {})
    modal.local_time._value = "09:00"
    modal.end_date._value = ""
    modal.content._value = "after"
    submitted = interaction(administrator=boundary == "administrator")
    submitted.guild.get_channel.return_value = cached_text_channel(submitted.guild)
    if boundary == "other-owner":
        submitted.user.id = USER_ID + 1
    elif boundary == "dm":
        submitted.guild = None
        submitted.guild_id = None
    elif boundary == "wrong-guild":
        submitted.guild.id = GUILD_ID + 1
        submitted.guild_id = GUILD_ID + 1
    elif boundary == "role-loss":
        submitted.user.roles = []
    service = AsyncMock()
    service.edit.return_value = EditedSchedule(
        public_id=selected.public_id,
        channel_id=selected.channel_id,
        schedule_type=ScheduleType.DAILY,
        status=ScheduleStatus.ACTIVE,
        content="after",
        next_run_at=selected.next_run_at,
        local_time=time(9),
        weekday=None,
        end_date=None,
        changed_fields=("content",),
        pending_runs_skipped=0,
        run_replaced=False,
        retry_pending_preserved=False,
        previous_status=ScheduleStatus.ACTIVE,
    )
    if boundary == "admin-loss":
        service.edit.side_effect = ScheduleEditUnavailable
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleEditingService", lambda unused: service
    )

    await modal.on_submit(submitted)

    if boundary in {"owner", "administrator"}:
        service.edit.assert_awaited_once()
        assert service.edit.await_args.kwargs["administrator"] is (boundary == "administrator")
        assert service.edit.await_args.kwargs["expected_version"] == 9
        assert submitted.edit_original_response.await_args.kwargs["view"].context.list_origin == (
            parent.context.list_origin
        )
    elif boundary == "admin-loss":
        service.edit.assert_awaited_once()
        assert service.edit.await_args.kwargs["administrator"] is False
        assert submitted.edit_original_response.await_args.kwargs["content"] == (
            "指定された予約は見つからないか、編集できません。"
        )
    else:
        service.edit.assert_not_awaited()
        assert submitted.response.send_message.await_args.args == (PERMISSION_DENIED_MESSAGE,)
        assert parent.finished is False and not parent.is_finished()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "noop", "conflict", "database"])
async def test_detail_edit_result_keeps_body_only_in_modal_and_latest_detail(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    outcome: str,
) -> None:
    secret_body = "正当なModal初期本文 " + "x" * 100 + " token_test_private traceback-sentinel"
    selected = replace(
        view(content=secret_body), schedule_type=ScheduleType.DAILY, local_time=time(9), version=17
    )
    latest = replace(selected, version=18)
    queries = AsyncMock()
    queries.get_schedule_detail.return_value = detail(latest)
    group = commands(queries)
    parent = group._build_detail_view(
        interaction=interaction(),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(),
    )
    modal = ScheduleEditModal(commands=group, detail_view=parent, default_channel=None)
    assert modal.content.default == secret_body
    modal.channel._handle_submit(interaction(), {"values": []}, {})
    modal.local_time._value = "09:00"
    modal.end_date._value = ""
    modal.content._value = secret_body
    service = AsyncMock()
    service.edit.return_value = EditedSchedule(
        public_id=selected.public_id,
        channel_id=selected.channel_id,
        schedule_type=ScheduleType.DAILY,
        status=ScheduleStatus.ACTIVE,
        content=secret_body,
        next_run_at=selected.next_run_at,
        local_time=time(9),
        weekday=None,
        end_date=None,
        changed_fields=("content",),
        pending_runs_skipped=0,
        run_replaced=False,
        retry_pending_preserved=False,
        previous_status=ScheduleStatus.ACTIVE,
    )
    if outcome == "noop":
        service.edit.side_effect = ScheduleEditNoChanges
    elif outcome == "conflict":
        service.edit.side_effect = ScheduleEditVersionConflict
    elif outcome == "database":
        service.edit.side_effect = RuntimeError(secret_body)
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleEditingService", lambda unused: service
    )
    submitted = interaction()
    submitted.guild.get_channel.return_value = cached_text_channel(submitted.guild)

    with caplog.at_level(logging.ERROR, logger="test.posts"):
        await modal.on_submit(submitted)

    if outcome == "database":
        assert submitted.response.send_message.await_args.args == (INTERNAL_ERROR_MESSAGE,)
        assert parent.finished is False and not parent.is_finished()
        assert [record.message for record in caplog.records if record.name == "test.posts"] == [
            "schedule_detail_edit_failed"
        ]
    else:
        kwargs = submitted.edit_original_response.await_args.kwargs
        expected = {
            "success": "予約を編集しました。\n変更した項目: 本文",
            "noop": DETAIL_EDIT_NO_CHANGES_MESSAGE,
            "conflict": DETAIL_CONFLICT_MESSAGE,
        }[outcome]
        assert kwargs["content"] == expected
        assert secret_body not in kwargs["content"]
        assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
        detail_text = " ".join(field.value for field in kwargs["embed"].fields)
        assert "正当なModal初期本文" in detail_text
        assert "token\\_test\\_private" in detail_text
        assert parent.is_finished() and kwargs["view"] in group._detail_views
    for component in modal.walk_children():
        custom_id = getattr(component, "custom_id", None)
        if custom_id:
            for forbidden in (
                str(selected.public_id),
                str(selected.version),
                str(GUILD_ID),
                str(USER_ID),
                secret_body,
            ):
                assert forbidden not in custom_id
    assert secret_body not in caplog.text


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
    assert not first_modal.closed and not first_modal.is_finished()
    assert {first_modal, second_modal} <= group._resume_modals
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
    assert not first_modal.closed and not first_modal.is_finished()
    assert {first_modal, second_modal} <= group._delete_reason_modals
    assert detail_view.finished is False and detail_view.closed is False
    assert not detail_view.is_finished()
    await group.close_confirmation_views()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["", " ", "　", "\t", "\n", " \t\n　 ", "x" * 501])
async def test_delete_reason_modal_rejects_invalid_input_before_session_and_can_reopen(
    reason: str,
) -> None:
    selected = replace(view(), creator_user_id=USER_ID + 1)
    group = commands(AsyncMock())
    session_factory = MagicMock()
    group._session_factory = session_factory
    parent = group._build_detail_view(
        interaction=interaction(administrator=True),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    opened = interaction(administrator=True)
    await group._delete_from_detail(parent, opened)
    modal = opened.response.send_modal.await_args.args[0]
    assert isinstance(modal, DeleteReasonModal)
    modal.reason._value = reason

    submitted = interaction(administrator=True)
    await modal.on_submit(submitted)

    assert submitted.response.send_message.await_args.args == (DELETE_REASON_INPUT_MESSAGE,)
    assert submitted.response.send_message.await_args.kwargs["ephemeral"] is True
    assert submitted.response.send_message.await_args.kwargs["allowed_mentions"].to_dict() == {
        "parse": []
    }
    if reason:
        assert reason not in submitted.response.send_message.await_args.args[0]
    session_factory.assert_not_called()
    assert parent.finished is False and parent.closed is False and not parent.is_finished()
    assert modal.finished and modal.closed and modal.is_finished()
    assert modal not in group._delete_reason_modals

    reopened = interaction(administrator=True)
    await group._delete_from_detail(parent, reopened)
    replacement = reopened.response.send_modal.await_args.args[0]
    assert replacement is not modal
    assert modal.closed and modal.is_finished()
    assert not parent.is_finished()
    await group.close_confirmation_views()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "expected"),
    [("x", "x"), ("  監査 理由\n詳細  ", "監査 理由\n詳細"), ("x" * 500, "x" * 500)],
)
async def test_delete_reason_modal_passes_only_trimmed_valid_reason_to_delete_flow(
    reason: str, expected: str
) -> None:
    selected = replace(view(), creator_user_id=USER_ID + 1)
    group = commands(AsyncMock())
    parent = group._build_detail_view(
        interaction=interaction(administrator=True),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    modal = DeleteReasonModal(commands=group, detail_view=parent)
    group._delete_reason_modals.add(modal)
    assert modal.reason.required is True
    assert modal.reason.min_length == 1 and modal.reason.max_length == 500
    modal.reason._value = reason
    group._continue_detail_delete = AsyncMock()

    submitted = interaction(administrator=True)
    await modal.on_submit(submitted)
    await modal.on_submit(submitted)

    group._continue_detail_delete.assert_awaited_once_with(parent, submitted, expected)
    assert modal.closed and modal.is_finished()
    assert modal not in group._delete_reason_modals
    assert re.fullmatch(rf"{DETAIL_DELETE_REASON_MODAL_PREFIX}:[0-9a-f]{{32}}", modal.custom_id)
    for component in modal.walk_children():
        custom_id = getattr(component, "custom_id", None)
        if custom_id:
            for forbidden in (
                str(selected.public_id),
                expected,
            ):
                assert forbidden not in custom_id


@pytest.mark.asyncio
async def test_delete_reason_modal_submit_rechecks_administrator_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = replace(view(), creator_user_id=USER_ID + 1)
    service = AsyncMock()
    service.preview.side_effect = ScheduleDeletionUnavailable
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.posts.ScheduleDeletionService", lambda unused: service
    )
    group = commands(AsyncMock())
    parent = group._build_detail_view(
        interaction=interaction(administrator=True),
        actor_user_id=USER_ID,
        detail=detail(selected),
        embed=discord.Embed(title="予約詳細"),
    )
    modal = DeleteReasonModal(commands=group, detail_view=parent)
    modal.reason._value = "監査理由"

    lost_permission = interaction(administrator=False)
    await modal.on_submit(lost_permission)

    assert service.preview.await_args.kwargs["administrator"] is False
    assert lost_permission.response.send_message.await_args.args == (DELETE_UNAVAILABLE_MESSAGE,)
    assert lost_permission.response.send_message.await_args.kwargs["ephemeral"] is True
    assert lost_permission.response.send_message.await_args.kwargs[
        "allowed_mentions"
    ].to_dict() == {"parse": []}
    service.delete.assert_not_awaited()
    assert parent.finished is False and parent.closed is False and not parent.is_finished()


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
