from __future__ import annotations

import inspect
import logging
import uuid
from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

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
from discord_ai_reminder_bot.application.schedule_pause import (
    PausedSchedule,
    ResumedSchedule,
    ScheduleStateChangeUnavailable,
)
from discord_ai_reminder_bot.application.schedule_queries import ScheduleView
from discord_ai_reminder_bot.bot.posts import (
    DELETE_CANCELLED_MESSAGE,
    DELETE_EXPIRED_MESSAGE,
    DELETE_REASON_REQUIRED_MESSAGE,
    DELETE_UNAVAILABLE_MESSAGE,
    INTERNAL_ERROR_MESSAGE,
    NOT_FOUND_MESSAGE,
    STATE_CHANGE_UNAVAILABLE_MESSAGE,
    PostCommands,
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
    embed = value.followup.send.await_args.kwargs["embed"]
    assert embed.title == "単発予約を作成しました"
    assert "line 1 line 2" in embed.fields[3].value


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
    embed = value.response.send_message.await_args.kwargs["embed"]
    assert embed.title == "予約一覧"
    assert "表示できる予約はありません" in embed.fields[0].value


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
        "編集する予約ID",
        "変更後の投稿先",
        "単発のみ｜投稿日時（YYYY-MM-DD HH:MM）",
        "毎日・毎週のみ｜投稿時刻（HH:MM）",
        "毎週のみ｜投稿する曜日",
        "毎日・毎週のみ｜変更後の終了日（YYYY-MM-DD）",
        "変更後の本文｜本文削除とは併用不可",
        "本文を削除｜新しい本文とは併用不可",
        "毎日・毎週のみ｜終了日を解除",
    ]
    assert all(len(parameter.description) <= 100 for parameter in parameters)
    assert [choice.value for choice in parameters[4].choices] == list(range(7))

    callback_parameters = inspect.signature(group.edit_command.callback).parameters
    assert callback_parameters["clear_content"].default is False
    assert callback_parameters["clear_end_date"].default is False


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
    )
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
