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
    ResumePreview,
    ScheduleStateChangeUnavailable,
)
from discord_ai_reminder_bot.application.schedule_queries import SchedulePage, ScheduleView
from discord_ai_reminder_bot.bot.post_presenter import (
    LIST_EXPIRED_GUIDANCE,
    LIST_OPERATION_GUIDANCE,
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
    END_DATE_DESCRIPTION,
    FULLWIDTH_DATETIME_INPUT_MESSAGE,
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
    assert kwargs["view"].timeout == 900.0


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
    queries.show_schedule.return_value = selected
    group = commands(queries)
    original = interaction()
    await group.list_command.callback(group, original, None, 1)
    list_view = original.response.send_message.await_args.kwargs["view"]

    clicked = interaction()
    await group._show_list_selection(list_view, clicked, str(selected.public_id))
    assert clicked.response.edit_message.await_args.kwargs["embed"].title == "予約詳細"
    assert [item.label for item in list_view.children] == ["一覧へ戻る"]

    back = interaction()
    await group._move_list_page(list_view, back, 1)
    assert back.response.edit_message.await_args.kwargs["embed"].title == "予約一覧"


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
async def test_list_view_rejects_other_user_and_timeout_disables_retained_view() -> None:
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
    displayed = list_view.current_embed.to_dict()
    queries.reset_mock()
    await group._expire_list(list_view)
    assert list_view.finished is True
    assert list_view.is_finished()
    assert list_view not in group._list_views
    assert all(item.disabled for item in list_view.children)
    queries.get_schedule_page.assert_not_awaited()
    session.__aenter__.assert_not_awaited()
    kwargs = original.edit_original_response.await_args.kwargs
    assert kwargs["view"] is list_view
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
    expired = kwargs["embed"]
    assert LIST_EXPIRED_GUIDANCE in expired.description
    assert "1 / 1ページ｜全1件" in expired.description
    assert "種類：毎週" in expired.description
    assert expired.fields == list_view.current_embed.fields
    assert list_view.current_embed.to_dict() == displayed


@pytest.mark.asyncio
async def test_list_timeout_edit_failure_is_sanitized_and_stops_view(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "token=discord-secret-value"
    queries = AsyncMock()
    queries.get_schedule_page.return_value = SchedulePage((view(),), 1, 1)
    group = commands(queries)
    original = interaction()
    original.edit_original_response.side_effect = RuntimeError(secret)
    await group.list_command.callback(group, original, None, 1)
    list_view = original.response.send_message.await_args.kwargs["view"]

    with caplog.at_level(logging.ERROR):
        await list_view.on_timeout()

    assert list_view.is_finished()
    assert list_view not in group._list_views
    assert "schedule_list_timeout_response_failed" in caplog.text
    assert secret not in caplog.text
    assert "RuntimeError" not in caplog.text


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
        END_DATE_DESCRIPTION,
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
