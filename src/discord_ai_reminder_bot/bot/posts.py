"""Guild-only read commands for Phase 1 schedules."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import discord
from discord import app_commands
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.schedule_creation import (
    DuplicateScheduleWarning,
    OnceScheduleCreationService,
    RecurringScheduleCreationService,
)
from discord_ai_reminder_bot.application.schedule_deletion import (
    DeleteReasonRequired,
    ScheduleDeletionService,
    ScheduleDeletionUnavailable,
)
from discord_ai_reminder_bot.application.schedule_editing import (
    EditValues,
    InvalidScheduleEditOptions,
    ScheduleEditingService,
    ScheduleEditNoChanges,
    ScheduleEditUnavailable,
)
from discord_ai_reminder_bot.application.schedule_pause import (
    ResumeMode,
    SchedulePauseService,
    ScheduleStateChangeUnavailable,
)
from discord_ai_reminder_bot.application.schedule_queries import (
    MAX_PAGE_NUMBER,
    InvalidScheduleQueryError,
    SchedulePage,
    ScheduleQueryService,
    parse_public_id,
)
from discord_ai_reminder_bot.bot.interactions import (
    INTERNAL_ERROR_MESSAGE,
    PERMISSION_DENIED_MESSAGE,
    is_authorized_interaction,
    respond_ephemeral,
)
from discord_ai_reminder_bot.bot.post_presenter import (
    STATUS_LABELS,
    WEEKDAY_LABELS,
    created_recurring_schedule_embed,
    created_schedule_embed,
    deleted_schedule_embed,
    edited_schedule_embed,
    once_schedule_confirmation_embed,
    paused_schedule_embed,
    resumed_schedule_embed,
    schedule_deletion_preview_embed,
    schedule_detail_embed,
    schedule_list_embed,
    schedule_select_option,
)
from discord_ai_reminder_bot.domain.clock import Clock
from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.domain.recurrence import TOKYO, require_utc
from discord_ai_reminder_bot.domain.schedule_creation import (
    FullwidthCreateDateTimeError,
    FullwidthEndDateError,
    InvalidEndDateFormatError,
    InvalidScheduleContentError,
    ParsedOnceSchedule,
    parse_end_date,
    parse_local_time,
    parse_once_create_input,
    parse_once_scheduled_at,
    validate_create_content,
    validate_once_scheduled_for,
)
from discord_ai_reminder_bot.domain.schedule_deletion import (
    InvalidDeleteReasonError,
    validate_delete_reason,
)

NOT_FOUND_MESSAGE = "指定された予約は見つからないか、表示する権限がありません。"
INVALID_INPUT_MESSAGE = "入力内容を確認してください。"
DATETIME_INPUT_MESSAGE = "投稿日時を確認してください。例：今日21:00、8/25 19:30、2027-08-25 19:30"
FULLWIDTH_DATETIME_INPUT_MESSAGE = (
    "投稿日時の数字と記号は半角で入力してください。例：今日21:00、8/25 19:30"
)
DUPLICATE_WARNING_MESSAGE = (
    "同一予約の可能性があります。意図的に作成する場合はallow_duplicate=trueで再実行してください。"
)
DELETE_UNAVAILABLE_MESSAGE = "指定された予約は見つからないか、削除できません。"
DELETE_REASON_REQUIRED_MESSAGE = (
    "他の利用者が作成した予約を削除する場合は、削除理由を入力してください。"
)
DELETE_CANCELLED_MESSAGE = "予約の削除をキャンセルしました。"
DELETE_EXPIRED_MESSAGE = (
    "確認の有効期限が切れました。必要な場合はもう一度 /post delete を実行してください。"
)
STATE_CHANGE_UNAVAILABLE_MESSAGE = "指定された予約は見つからないか、この操作を実行できません。"
EDIT_UNAVAILABLE_MESSAGE = "指定された予約は見つからないか、編集できません。"
EDIT_NO_CHANGES_MESSAGE = "実際に変更される項目がありません。"
EDIT_TYPE_OPTIONS_MESSAGE = (
    "予約種別に使用できない編集項目があります。予約種別を変更する場合は、現在の予約を削除し、"
    "希望する種別で新しく作成してください。"
)
CREATE_CANCELLED_MESSAGE = "単発予約の作成をキャンセルしました。"
CREATE_EXPIRED_MESSAGE = (
    "確認の有効期限が切れました。必要な場合はもう一度 /post create を実行してください。"
)
CREATE_UNAVAILABLE_MESSAGE = "予約を作成できませんでした。入力内容と権限を確認してください。"
CREATE_DATETIME_DESCRIPTION = "数字・記号は半角｜例：今日21:00、8/25 19:30、2027-08-25 19:30"
RESUME_CANCELLED_MESSAGE = "予約の再開をキャンセルしました。一時停止中のままです。"
RESUME_EXPIRED_MESSAGE = "選択の有効期限が切れました。予約は一時停止中のままです。"
RESUME_TIME_MESSAGE = (
    "指定時刻は現在から5分以上先にしてください。\n"
    "別の時刻を入力するか、「今すぐ投稿」「次回から再開」を選んでください。"
)
END_DATE_DESCRIPTION = "終了日｜例：明日、8/30、2026-08-30"
END_DATE_INPUT_MESSAGE = "終了日を確認してください。例：明日、8/30、2026-08-30"
FULLWIDTH_END_DATE_INPUT_MESSAGE = (
    "終了日の数字と記号は半角で入力してください。例：8/30、2026-08-30"
)


@dataclass(frozen=True)
class InteractionActor:
    user_id: int
    administrator: bool


class ScheduleListSelect(discord.ui.Select):
    def __init__(self, owner: ScheduleListView, page: SchedulePage) -> None:
        options = [
            schedule_select_option(
                item,
                channel_name=owner.commands._channel_name(
                    owner.initial_interaction, item.channel_id
                ),
            )
            for item in page.schedules
        ]
        super().__init__(
            placeholder="詳細を見る予約を選択",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="post_list_select",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.commands._show_list_selection(self.view, interaction, self.values[0])


class ScheduleTypeFilterSelect(discord.ui.Select):
    def __init__(self, owner: ScheduleListView) -> None:
        choices = (
            ("すべて", "all", None),
            ("単発", ScheduleType.ONCE.value, ScheduleType.ONCE),
            ("毎日", ScheduleType.DAILY.value, ScheduleType.DAILY),
            ("毎週", ScheduleType.WEEKLY.value, ScheduleType.WEEKLY),
        )
        options = [
            discord.SelectOption(
                label=label,
                value=value,
                default=owner.schedule_type is schedule_type,
            )
            for label, value, schedule_type in choices
        ]
        super().__init__(
            placeholder="予約種類で絞り込む",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="post_list_schedule_type_filter",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        schedule_type = None if value == "all" else ScheduleType(value)
        await self.view.commands._filter_list_type(self.view, interaction, schedule_type)


class ScheduleListView(discord.ui.View):
    """One-user list navigation retaining no Session or transaction."""

    def __init__(
        self,
        *,
        commands: PostCommands,
        interaction: discord.Interaction,
        actor_user_id: int,
        administrator: bool,
        status: ScheduleStatus | None,
        schedule_type: ScheduleType | None,
        page: SchedulePage,
    ) -> None:
        super().__init__(timeout=120.0)
        self.commands = commands
        self.initial_interaction = interaction
        self.actor_user_id = actor_user_id
        self.administrator = administrator
        self.status = status
        self.schedule_type = schedule_type
        self.page = page.page
        self.action_lock = asyncio.Lock()
        self.finished = False
        self.closed = False
        self._render_controls(page)

    def _render_controls(self, page: SchedulePage, *, detail: bool = False) -> None:
        self.clear_items()
        self.page = page.page
        if detail:
            button = discord.ui.Button(
                label="一覧へ戻る",
                style=discord.ButtonStyle.secondary,
                custom_id="post_list_back",
            )
            button.callback = self._back
            self.add_item(button)
            return
        previous = discord.ui.Button(
            label="前へ",
            style=discord.ButtonStyle.secondary,
            disabled=page.page <= 1,
            custom_id="post_list_previous",
            row=0,
        )
        previous.callback = self._previous
        following = discord.ui.Button(
            label="次へ",
            style=discord.ButtonStyle.secondary,
            disabled=page.page >= page.total_pages,
            custom_id="post_list_next",
            row=0,
        )
        following.callback = self._next
        self.add_item(previous)
        self.add_item(following)
        self.add_item(ScheduleTypeFilterSelect(self))
        if page.schedules:
            self.add_item(ScheduleListSelect(self, page))

    async def _previous(self, interaction: discord.Interaction) -> None:
        await self.commands._move_list_page(self, interaction, self.page - 1)

    async def _next(self, interaction: discord.Interaction) -> None:
        await self.commands._move_list_page(self, interaction, self.page + 1)

    async def _back(self, interaction: discord.Interaction) -> None:
        await self.commands._move_list_page(self, interaction, self.page)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        actor = authorized_actor(
            interaction,
            configured_guild_id=self.commands._configured_guild_id,
            allowed_role_ids=self.commands._allowed_role_ids,
        )
        if actor is not None and actor.user_id == self.actor_user_id:
            return True
        await respond_ephemeral(
            interaction, PERMISSION_DENIED_MESSAGE, logger=self.commands._logger
        )
        return False

    async def on_timeout(self) -> None:
        await self.commands._expire_list(self)


class ScheduleDeletionConfirmView(discord.ui.View):
    """One-user, non-persistent confirmation state with no open DB resources."""

    def __init__(
        self,
        *,
        commands: PostCommands,
        interaction: discord.Interaction,
        public_id: str,
        reason: str,
        actor_user_id: int,
    ) -> None:
        super().__init__(timeout=120.0)
        self.commands = commands
        self.initial_interaction = interaction
        self.public_id = public_id
        self.reason = reason
        self.actor_user_id = actor_user_id
        self.action_lock = asyncio.Lock()
        self.finished = False

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        actor = authorized_actor(
            interaction,
            configured_guild_id=self.commands._configured_guild_id,
            allowed_role_ids=self.commands._allowed_role_ids,
        )
        if actor is not None and actor.user_id == self.actor_user_id:
            return True
        await respond_ephemeral(
            interaction,
            PERMISSION_DENIED_MESSAGE,
            logger=self.commands._logger,
        )
        return False

    @discord.ui.button(
        label="削除する",
        style=discord.ButtonStyle.danger,
        custom_id="post_delete_confirm",
    )
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        unused: discord.ui.Button[ScheduleDeletionConfirmView],
    ) -> None:
        await self.commands._confirm_deletion(self, interaction)

    @discord.ui.button(
        label="キャンセル",
        style=discord.ButtonStyle.secondary,
        custom_id="post_delete_cancel",
    )
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        unused: discord.ui.Button[ScheduleDeletionConfirmView],
    ) -> None:
        await self.commands._cancel_deletion(self, interaction)

    async def on_timeout(self) -> None:
        await self.commands._expire_deletion(self)


class OnceScheduleConfirmView(discord.ui.View):
    """Create confirmation carrying only already-validated, in-memory values."""

    def __init__(
        self,
        *,
        commands: PostCommands,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        parsed: ParsedOnceSchedule,
        content: str | None,
        allow_duplicate: bool,
        actor_user_id: int,
    ) -> None:
        super().__init__(timeout=120.0)
        self.commands = commands
        self.initial_interaction = interaction
        self.channel = channel
        self.parsed = parsed
        self.content = content
        self.allow_duplicate = allow_duplicate
        self.actor_user_id = actor_user_id
        self.action_lock = asyncio.Lock()
        self.finished = False

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        actor = authorized_actor(
            interaction,
            configured_guild_id=self.commands._configured_guild_id,
            allowed_role_ids=self.commands._allowed_role_ids,
        )
        if actor is not None and actor.user_id == self.actor_user_id:
            return True
        await respond_ephemeral(
            interaction, PERMISSION_DENIED_MESSAGE, logger=self.commands._logger
        )
        return False

    @discord.ui.button(
        label="予約する", style=discord.ButtonStyle.success, custom_id="post_create_confirm"
    )
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        unused: discord.ui.Button[OnceScheduleConfirmView],
    ) -> None:
        await self.commands._confirm_once_creation(self, interaction)

    @discord.ui.button(
        label="キャンセル", style=discord.ButtonStyle.secondary, custom_id="post_create_cancel"
    )
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        unused: discord.ui.Button[OnceScheduleConfirmView],
    ) -> None:
        await self.commands._cancel_once_creation(self, interaction)

    async def on_timeout(self) -> None:
        await self.commands._expire_once_creation(self)


class ResumeTimeModal(discord.ui.Modal, title="本日分の投稿時刻を指定"):
    local_time = discord.ui.TextInput(label="時刻（半角HH:MM）", min_length=5, max_length=5)

    def __init__(self, view: ResumeChoiceView) -> None:
        super().__init__(timeout=120.0, custom_id="post_resume_time_modal")
        self.resume_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.resume_view.commands._submit_resume_time(
            self.resume_view, interaction, str(self.local_time.value)
        )


class ResumeChoiceView(discord.ui.View):
    def __init__(
        self,
        *,
        commands: PostCommands,
        interaction: discord.Interaction,
        public_id: str,
        actor_user_id: int,
        rescue_allowed: bool,
    ) -> None:
        super().__init__(timeout=120.0)
        self.commands = commands
        self.initial_interaction = interaction
        self.public_id = public_id
        self.actor_user_id = actor_user_id
        self.action_lock = asyncio.Lock()
        self.finished = False
        if not rescue_allowed:
            self.immediate_button.disabled = True
            self.time_button.disabled = True
        now = require_utc(commands._clock.now()).astimezone(TOKYO)
        if now + timedelta(minutes=5) >= datetime.combine(
            now.date() + timedelta(days=1), time(), TOKYO
        ):
            self.time_button.disabled = True

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        actor = authorized_actor(
            interaction,
            configured_guild_id=self.commands._configured_guild_id,
            allowed_role_ids=self.commands._allowed_role_ids,
        )
        if actor is not None and actor.user_id == self.actor_user_id:
            return True
        await respond_ephemeral(
            interaction, PERMISSION_DENIED_MESSAGE, logger=self.commands._logger
        )
        return False

    @discord.ui.button(
        label="次回から再開", style=discord.ButtonStyle.success, custom_id="post_resume_next"
    )
    async def next_button(self, interaction: discord.Interaction, unused) -> None:
        await self.commands._finish_resume_choice(self, interaction, ResumeMode.NEXT_REGULAR)

    @discord.ui.button(
        label="本日分を今すぐ投稿", style=discord.ButtonStyle.primary, custom_id="post_resume_now"
    )
    async def immediate_button(self, interaction: discord.Interaction, unused) -> None:
        await self.commands._finish_resume_choice(self, interaction, ResumeMode.IMMEDIATE_ONCE)

    @discord.ui.button(
        label="本日分の時刻を指定", style=discord.ButtonStyle.primary, custom_id="post_resume_time"
    )
    async def time_button(self, interaction: discord.Interaction, unused) -> None:
        if self.finished:
            await respond_ephemeral(
                interaction, STATE_CHANGE_UNAVAILABLE_MESSAGE, logger=self.commands._logger
            )
            return
        await interaction.response.send_modal(ResumeTimeModal(self))

    @discord.ui.button(
        label="キャンセル", style=discord.ButtonStyle.secondary, custom_id="post_resume_cancel"
    )
    async def cancel_button(self, interaction: discord.Interaction, unused) -> None:
        await self.commands._cancel_resume_choice(self, interaction)

    async def on_timeout(self) -> None:
        await self.commands._expire_resume_choice(self)


def authorized_actor(
    interaction: discord.Interaction,
    *,
    configured_guild_id: int,
    allowed_role_ids: tuple[int, ...],
) -> InteractionActor | None:
    """Repeat authorization at the operation boundary and extract safe identity data."""
    if not is_authorized_interaction(
        interaction,
        configured_guild_id=configured_guild_id,
        allowed_role_ids=allowed_role_ids,
    ):
        return None
    member = interaction.user
    if not isinstance(member, discord.Member):
        return None
    user_id = member.id
    permissions = member.guild_permissions
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
        or permissions is None
    ):
        return None
    return InteractionActor(user_id=user_id, administrator=permissions.administrator is True)


class PostCommands(app_commands.Group):
    """Phase 1 `/post` command group."""

    def __init__(
        self,
        *,
        queries: ScheduleQueryService,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        configured_guild_id: int,
        allowed_role_ids: tuple[int, ...],
        logger: logging.Logger,
    ) -> None:
        super().__init__(name="post", description="予約投稿を確認します")
        self._queries = queries
        self._session_factory = session_factory
        self._clock = clock
        self._configured_guild_id = configured_guild_id
        self._allowed_role_ids = allowed_role_ids
        self._logger = logger
        self._delete_views: set[ScheduleDeletionConfirmView] = set()
        self._create_views: set[OnceScheduleConfirmView] = set()
        self._resume_views: set[ResumeChoiceView] = set()
        self._list_views: set[ScheduleListView] = set()

    @app_commands.command(name="create", description="単発の予約投稿を作成します")
    @app_commands.describe(
        channel="投稿先のテキストチャンネルです",
        scheduled_at=CREATE_DATETIME_DESCRIPTION,
        content="投稿本文です。未指定の場合は下書きになります",
        allow_duplicate="重複候補があっても意図的に作成します",
    )
    async def create_command(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        scheduled_at: app_commands.Range[str, 7, 16],
        content: app_commands.Range[str, 1, 2_000] | None = None,
        allow_duplicate: bool = False,
    ) -> None:
        actor = await self._actor_or_respond(interaction)
        if actor is None:
            return
        try:
            channel_id = self._validated_channel(interaction, channel)
        except ValueError:
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        now = self._clock.now()
        try:
            parsed = parse_once_create_input(scheduled_at, now=now)
        except FullwidthCreateDateTimeError:
            await respond_ephemeral(
                interaction, FULLWIDTH_DATETIME_INPUT_MESSAGE, logger=self._logger
            )
            return
        except InvalidDateTimeError:
            await respond_ephemeral(interaction, DATETIME_INPUT_MESSAGE, logger=self._logger)
            return
        try:
            content = validate_create_content(content)
        except InvalidScheduleContentError:
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        try:
            embed = once_schedule_confirmation_embed(
                parsed=parsed, channel_id=channel_id, content=content
            )
        except Exception:  # noqa: BLE001 - presentation failures must remain sanitized
            self._logger.error("schedule_presentation_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        view = OnceScheduleConfirmView(
            commands=self,
            interaction=interaction,
            channel=channel,
            parsed=parsed,
            content=content,
            allow_duplicate=allow_duplicate,
            actor_user_id=actor.user_id,
        )
        if await respond_ephemeral(interaction, embed=embed, view=view, logger=self._logger):
            self._create_views.add(view)

    @app_commands.command(name="create-daily", description="毎日の予約投稿を作成します")
    @app_commands.describe(
        channel="投稿先のテキストチャンネルです",
        local_time="日本時間をHH:MMで指定します",
        end_date=END_DATE_DESCRIPTION,
        content="投稿本文です。未指定の場合は下書きになります",
        allow_duplicate="重複候補があっても意図的に作成します",
    )
    async def create_daily_command(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        local_time: app_commands.Range[str, 5, 5],
        end_date: app_commands.Range[str, 2, 10] | None = None,
        content: app_commands.Range[str, 1, 2_000] | None = None,
        allow_duplicate: bool = False,
    ) -> None:
        await self._create_recurring_command(
            interaction,
            channel=channel,
            schedule_type=ScheduleType.DAILY,
            local_time_value=local_time,
            weekday=None,
            end_date_value=end_date,
            content=content,
            allow_duplicate=allow_duplicate,
        )

    @app_commands.command(name="create-weekly", description="毎週の予約投稿を作成します")
    @app_commands.describe(
        channel="投稿先のテキストチャンネルです",
        weekday="投稿する曜日です",
        local_time="日本時間をHH:MMで指定します",
        end_date=END_DATE_DESCRIPTION,
        content="投稿本文です。未指定の場合は下書きになります",
        allow_duplicate="重複候補があっても意図的に作成します",
    )
    @app_commands.choices(
        weekday=[
            app_commands.Choice(name=label, value=value)
            for value, label in enumerate(WEEKDAY_LABELS)
        ]
    )
    async def create_weekly_command(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        weekday: app_commands.Choice[int],
        local_time: app_commands.Range[str, 5, 5],
        end_date: app_commands.Range[str, 2, 10] | None = None,
        content: app_commands.Range[str, 1, 2_000] | None = None,
        allow_duplicate: bool = False,
    ) -> None:
        await self._create_recurring_command(
            interaction,
            channel=channel,
            schedule_type=ScheduleType.WEEKLY,
            local_time_value=local_time,
            weekday=weekday.value,
            end_date_value=end_date,
            content=content,
            allow_duplicate=allow_duplicate,
        )

    @app_commands.command(name="list", description="操作できる予約を一覧表示します")
    @app_commands.describe(status="状態で絞り込みます", page="1から始まるページ番号です")
    @app_commands.choices(
        status=[
            app_commands.Choice(name=label, value=status.value)
            for status, label in STATUS_LABELS.items()
        ]
    )
    async def list_command(
        self,
        interaction: discord.Interaction,
        status: app_commands.Choice[str] | None = None,
        page: app_commands.Range[int, 1, MAX_PAGE_NUMBER] = 1,
    ) -> None:
        actor = await self._actor_or_respond(interaction)
        if actor is None:
            return
        try:
            parsed_status = ScheduleStatus(status.value) if status is not None else None
            result = await self._queries.get_schedule_page(
                guild_id=self._configured_guild_id,
                requester_user_id=actor.user_id,
                administrator=actor.administrator,
                status=parsed_status,
                page=page,
                schedule_type=None,
                clamp=False,
            )
        except InvalidScheduleQueryError:
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        except Exception:  # noqa: BLE001 - database details must not reach Discord or logs
            self._logger.error("schedule_list_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        try:
            embed = schedule_list_embed(
                result.schedules,
                page=result.page,
                status_filter=parsed_status,
                schedule_type_filter=None,
                total_count=result.total_count,
                total_pages=result.total_pages,
            )
            list_view = ScheduleListView(
                commands=self,
                interaction=interaction,
                actor_user_id=actor.user_id,
                administrator=actor.administrator,
                status=parsed_status,
                schedule_type=None,
                page=result,
            )
        except Exception:  # noqa: BLE001 - presentation failures remain sanitized
            self._logger.error("schedule_presentation_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        if await respond_ephemeral(interaction, embed=embed, view=list_view, logger=self._logger):
            self._list_views.add(list_view)

    @app_commands.command(name="show", description="予約の詳細を表示します")
    @app_commands.describe(public_id="予約IDを指定します")
    async def show_command(self, interaction: discord.Interaction, public_id: str) -> None:
        actor = await self._actor_or_respond(interaction)
        if actor is None:
            return
        try:
            schedule = await self._queries.show_schedule(
                guild_id=self._configured_guild_id,
                requester_user_id=actor.user_id,
                administrator=actor.administrator,
                public_id=public_id,
            )
        except InvalidScheduleQueryError:
            schedule = None
        except Exception:  # noqa: BLE001 - database details must not reach Discord or logs
            self._logger.error("schedule_show_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        if schedule is None:
            await respond_ephemeral(interaction, NOT_FOUND_MESSAGE, logger=self._logger)
            return
        await self._respond_embed(interaction, lambda: schedule_detail_embed(schedule))

    @app_commands.command(name="edit", description="予約内容を編集します")
    @app_commands.describe(
        public_id="編集する予約ID",
        channel="変更後の投稿先",
        scheduled_at="単発のみ｜投稿日時（YYYY-MM-DD HH:MM）",
        local_time="毎日・毎週のみ｜投稿時刻（HH:MM）",
        weekday="毎週のみ｜投稿する曜日",
        end_date=END_DATE_DESCRIPTION,
        content="変更後の本文｜本文削除とは併用不可",
        clear_content="本文を削除｜新しい本文とは併用不可",
        clear_end_date="毎日・毎週のみ｜終了日を解除",
    )
    @app_commands.choices(
        weekday=[
            app_commands.Choice(name=label, value=value)
            for value, label in enumerate(WEEKDAY_LABELS)
        ]
    )
    async def edit_command(
        self,
        interaction: discord.Interaction,
        public_id: str,
        channel: discord.TextChannel | None = None,
        scheduled_at: app_commands.Range[str, 16, 16] | None = None,
        local_time: app_commands.Range[str, 5, 5] | None = None,
        weekday: app_commands.Choice[int] | None = None,
        end_date: app_commands.Range[str, 2, 10] | None = None,
        content: app_commands.Range[str, 1, 2_000] | None = None,
        clear_content: bool = False,
        clear_end_date: bool = False,
    ) -> None:
        actor = await self._actor_or_respond(interaction)
        if actor is None:
            return
        has_request = any(
            (
                channel is not None,
                scheduled_at is not None,
                local_time is not None,
                weekday is not None,
                end_date is not None,
                content is not None,
                clear_content,
                clear_end_date,
            )
        )
        if (
            not has_request
            or (content is not None and clear_content)
            or (end_date is not None and clear_end_date)
        ):
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        try:
            parse_public_id(public_id)
            now = self._clock.now()
            channel_id = self._validated_channel(interaction, channel) if channel else None
            parsed_scheduled = (
                parse_once_scheduled_at(scheduled_at, now=now) if scheduled_at else None
            )
            parsed_time = parse_local_time(local_time) if local_time else None
            parsed_end = parse_end_date(end_date, now=now) if end_date else None
        except FullwidthEndDateError:
            await respond_ephemeral(
                interaction, FULLWIDTH_END_DATE_INPUT_MESSAGE, logger=self._logger
            )
            return
        except InvalidEndDateFormatError:
            await respond_ephemeral(interaction, END_DATE_INPUT_MESSAGE, logger=self._logger)
            return
        except InvalidScheduleQueryError, InvalidDateTimeError, ValueError:
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        try:
            if content is not None:
                content = validate_create_content(content)
        except (
            InvalidScheduleQueryError,
            InvalidDateTimeError,
            InvalidScheduleContentError,
            ValueError,
        ):
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:  # noqa: BLE001 - Discord response details must remain private
            self._logger.error("schedule_edit_defer_failed")
            return
        current_actor = authorized_actor(
            interaction,
            configured_guild_id=self._configured_guild_id,
            allowed_role_ids=self._allowed_role_ids,
        )
        if current_actor is None or current_actor.user_id != actor.user_id:
            await respond_ephemeral(interaction, EDIT_UNAVAILABLE_MESSAGE, logger=self._logger)
            return
        try:
            async with self._session_factory() as session, session.begin():
                edited = await ScheduleEditingService(session).edit(
                    guild_id=interaction.guild_id,
                    public_id=public_id,
                    actor_user_id=current_actor.user_id,
                    administrator=current_actor.administrator,
                    values=EditValues(
                        channel_id=channel_id,
                        scheduled_at=parsed_scheduled,
                        local_time=parsed_time,
                        weekday=weekday.value if weekday is not None else None,
                        weekday_supplied=weekday is not None,
                        end_date=parsed_end,
                        end_date_supplied=end_date is not None,
                        content=content,
                        clear_content=clear_content,
                        clear_end_date=clear_end_date,
                    ),
                    edited_at=now,
                    configured_guild_id=self._configured_guild_id,
                )
        except ScheduleEditNoChanges:
            await respond_ephemeral(interaction, EDIT_NO_CHANGES_MESSAGE, logger=self._logger)
            return
        except InvalidScheduleEditOptions:
            await respond_ephemeral(interaction, EDIT_TYPE_OPTIONS_MESSAGE, logger=self._logger)
            return
        except ScheduleEditUnavailable:
            await respond_ephemeral(interaction, EDIT_UNAVAILABLE_MESSAGE, logger=self._logger)
            return
        except Exception:  # noqa: BLE001 - database details must remain private
            self._logger.error("schedule_edit_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        await self._respond_embed(interaction, lambda: edited_schedule_embed(edited))

    @app_commands.command(name="pause", description="定期投稿を一時停止します")
    @app_commands.describe(public_id="予約IDを指定します")
    async def pause_command(self, interaction: discord.Interaction, public_id: str) -> None:
        await self._change_schedule_state(interaction, public_id=public_id, resume=False)

    @app_commands.command(name="resume", description="一時停止中の定期投稿を再開します")
    @app_commands.describe(public_id="予約IDを指定します")
    async def resume_command(self, interaction: discord.Interaction, public_id: str) -> None:
        await self._change_schedule_state(interaction, public_id=public_id, resume=True)

    async def _change_schedule_state(
        self, interaction: discord.Interaction, *, public_id: str, resume: bool
    ) -> None:
        actor = await self._actor_or_respond(interaction)
        if actor is None:
            return
        try:
            # Validate before acknowledging the interaction; the service repeats it safely.
            parse_public_id(public_id)
        except InvalidScheduleQueryError:
            await respond_ephemeral(
                interaction, STATE_CHANGE_UNAVAILABLE_MESSAGE, logger=self._logger
            )
            return
        if resume:
            try:
                async with self._session_factory() as session:
                    preview = await SchedulePauseService(session).preview_resume(
                        guild_id=interaction.guild_id,
                        public_id=public_id,
                        actor_user_id=actor.user_id,
                        administrator=actor.administrator,
                        resumed_at=self._clock.now(),
                    )
            except ScheduleStateChangeUnavailable, ValueError:
                await respond_ephemeral(
                    interaction, STATE_CHANGE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            except Exception:  # noqa: BLE001
                self._logger.error("schedule_resume_preview_failed")
                await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
                return
            now = require_utc(self._clock.now())
            if preview.held_run_at is not None and preview.held_run_at <= now:
                view = ResumeChoiceView(
                    commands=self,
                    interaction=interaction,
                    public_id=public_id,
                    actor_user_id=actor.user_id,
                    rescue_allowed=preview.rescue_allowed,
                )
                embed = discord.Embed(
                    title="予約の再開方法を選択してください",
                    description="一時停止中に投稿時刻を過ぎました。DBはまだ更新されていません。",
                    colour=0xE67E22,
                )
                if await respond_ephemeral(
                    interaction, embed=embed, view=view, logger=self._logger
                ):
                    self._resume_views.add(view)
                return
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:  # noqa: BLE001 - Discord response details must remain private
            self._logger.error("schedule_state_change_defer_failed")
            return
        try:
            async with self._session_factory() as session, session.begin():
                service = SchedulePauseService(session)
                arguments = {
                    "guild_id": interaction.guild_id,
                    "public_id": public_id,
                    "actor_user_id": actor.user_id,
                    "administrator": actor.administrator,
                }
                if resume:
                    changed = await service.resume(
                        **arguments,
                        resumed_at=self._clock.now(),
                        configured_guild_id=self._configured_guild_id,
                    )
                else:
                    changed = await service.pause(
                        **arguments,
                        paused_at=self._clock.now(),
                    )
        except ScheduleStateChangeUnavailable:
            await respond_ephemeral(
                interaction, STATE_CHANGE_UNAVAILABLE_MESSAGE, logger=self._logger
            )
            return
        except Exception:  # noqa: BLE001 - database details must remain private
            self._logger.error("schedule_state_change_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        presenter = resumed_schedule_embed if resume else paused_schedule_embed
        await self._respond_embed(interaction, lambda: presenter(changed))

    async def _finish_resume_choice(
        self,
        view: ResumeChoiceView,
        interaction: discord.Interaction,
        mode: ResumeMode,
        replacement_at: datetime | None = None,
    ) -> None:
        async with view.action_lock:
            if view.finished:
                await respond_ephemeral(
                    interaction, STATE_CHANGE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            actor = await self._actor_or_respond(interaction)
            if actor is None or actor.user_id != view.actor_user_id:
                return
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:  # noqa: BLE001
                self._logger.error("schedule_resume_defer_failed")
                return
            try:
                async with self._session_factory() as session, session.begin():
                    changed = await SchedulePauseService(session).resume(
                        guild_id=self._configured_guild_id,
                        public_id=view.public_id,
                        actor_user_id=actor.user_id,
                        administrator=actor.administrator,
                        resumed_at=self._clock.now(),
                        configured_guild_id=self._configured_guild_id,
                        mode=mode,
                        replacement_at=replacement_at,
                    )
            except ScheduleStateChangeUnavailable:
                await self._finish_resume_view(
                    view, interaction, content=STATE_CHANGE_UNAVAILABLE_MESSAGE
                )
                return
            except Exception:  # noqa: BLE001
                self._logger.error("schedule_resume_failed")
                await self._finish_resume_view(view, interaction, content=INTERNAL_ERROR_MESSAGE)
                return
            await self._finish_resume_view(view, interaction, embed=resumed_schedule_embed(changed))

    async def _submit_resume_time(
        self, view: ResumeChoiceView, interaction: discord.Interaction, value: str
    ) -> None:
        async with view.action_lock:
            if view.finished:
                await respond_ephemeral(
                    interaction, STATE_CHANGE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            actor = await self._actor_or_respond(interaction)
            if actor is None or actor.user_id != view.actor_user_id:
                return
            try:
                parsed = parse_local_time(value)
                now = require_utc(self._clock.now())
                local_now = now.astimezone(TOKYO)
                replacement = datetime.combine(local_now.date(), parsed, TOKYO).astimezone(
                    now.tzinfo
                )
                if replacement < now + timedelta(minutes=5):
                    raise ValueError
            except InvalidDateTimeError, ValueError:
                await respond_ephemeral(interaction, RESUME_TIME_MESSAGE, logger=self._logger)
                return
        await self._finish_resume_choice(
            view, interaction, ResumeMode.RESCHEDULED_ONCE, replacement
        )

    async def _cancel_resume_choice(
        self, view: ResumeChoiceView, interaction: discord.Interaction
    ) -> None:
        async with view.action_lock:
            if view.finished:
                return
            view.finished = True
            view.stop()
            self._resume_views.discard(view)
            await interaction.response.edit_message(
                content=RESUME_CANCELLED_MESSAGE,
                embed=None,
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _expire_resume_choice(self, view: ResumeChoiceView) -> None:
        async with view.action_lock:
            if view.finished:
                return
            view.finished = True
            view.stop()
            self._resume_views.discard(view)
            try:
                await view.initial_interaction.edit_original_response(
                    content=RESUME_EXPIRED_MESSAGE,
                    embed=None,
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:  # noqa: BLE001
                self._logger.error("schedule_resume_timeout_response_failed")

    async def _finish_resume_view(
        self,
        view: ResumeChoiceView,
        interaction: discord.Interaction,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
    ) -> None:
        view.finished = True
        view.stop()
        self._resume_views.discard(view)
        await interaction.edit_original_response(
            content=content if embed is None else None,
            embed=embed,
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="delete", description="予約を確認して論理削除します")
    @app_commands.describe(
        public_id="予約IDを指定します",
        reason="削除理由です。他の利用者の予約を管理者が削除する場合は必須です",
    )
    async def delete_command(
        self,
        interaction: discord.Interaction,
        public_id: str,
        reason: app_commands.Range[str, 1, 500] | None = None,
    ) -> None:
        actor = await self._actor_or_respond(interaction)
        if actor is None:
            return
        try:
            reason = validate_delete_reason(reason)
        except InvalidDeleteReasonError:
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        try:
            async with self._session_factory() as session:
                preview = await ScheduleDeletionService(session).preview(
                    guild_id=self._configured_guild_id,
                    public_id=public_id,
                    actor_user_id=actor.user_id,
                    administrator=actor.administrator,
                    reason=reason,
                )
        except DeleteReasonRequired:
            await respond_ephemeral(
                interaction, DELETE_REASON_REQUIRED_MESSAGE, logger=self._logger
            )
            return
        except ScheduleDeletionUnavailable:
            await respond_ephemeral(interaction, DELETE_UNAVAILABLE_MESSAGE, logger=self._logger)
            return
        except Exception:  # noqa: BLE001 - database details must remain private
            self._logger.error("schedule_delete_preview_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        try:
            embed = schedule_deletion_preview_embed(preview)
        except Exception:  # noqa: BLE001 - presentation failures must remain sanitized
            self._logger.error("schedule_presentation_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        view = ScheduleDeletionConfirmView(
            commands=self,
            interaction=interaction,
            public_id=public_id,
            reason=preview.reason,
            actor_user_id=actor.user_id,
        )
        if await respond_ephemeral(interaction, embed=embed, view=view, logger=self._logger):
            self._delete_views.add(view)

    async def _confirm_deletion(
        self, view: ScheduleDeletionConfirmView, interaction: discord.Interaction
    ) -> None:
        async with view.action_lock:
            if view.finished:
                await respond_ephemeral(
                    interaction, DELETE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            actor = await self._actor_or_respond(interaction)
            if actor is None or actor.user_id != view.actor_user_id:
                return
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:  # noqa: BLE001 - Discord response details must remain private
                self._logger.error("schedule_delete_defer_failed")
                return
            try:
                async with self._session_factory() as session, session.begin():
                    deleted = await ScheduleDeletionService(session).delete(
                        guild_id=self._configured_guild_id,
                        public_id=view.public_id,
                        actor_user_id=actor.user_id,
                        administrator=actor.administrator,
                        reason=view.reason,
                        deleted_at=self._clock.now(),
                    )
            except DeleteReasonRequired:
                await self._finish_delete_view(
                    view, interaction, content=DELETE_REASON_REQUIRED_MESSAGE
                )
                return
            except ScheduleDeletionUnavailable:
                await self._finish_delete_view(
                    view, interaction, content=DELETE_UNAVAILABLE_MESSAGE
                )
                return
            except Exception:  # noqa: BLE001 - database details must remain private
                self._logger.error("schedule_delete_failed")
                await self._finish_delete_view(view, interaction, content=INTERNAL_ERROR_MESSAGE)
                return
            try:
                embed = deleted_schedule_embed(deleted)
            except Exception:  # noqa: BLE001 - presentation failures must remain sanitized
                self._logger.error("schedule_presentation_failed")
                await self._finish_delete_view(view, interaction, content=INTERNAL_ERROR_MESSAGE)
                return
            await self._finish_delete_view(view, interaction, embed=embed)

    async def _confirm_once_creation(
        self, view: OnceScheduleConfirmView, interaction: discord.Interaction
    ) -> None:
        async with view.action_lock:
            if view.finished:
                await respond_ephemeral(
                    interaction, CREATE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            actor = await self._actor_or_respond(interaction)
            if actor is None or actor.user_id != view.actor_user_id:
                return
            try:
                channel_id = self._validated_channel(
                    interaction, view.channel, require_current=True
                )
                content = validate_create_content(view.content)
                now = self._clock.now()
                scheduled_for = validate_once_scheduled_for(view.parsed.scheduled_for, now=now)
            except InvalidDateTimeError, InvalidScheduleContentError, ValueError:
                await self._finish_create_view(
                    view, interaction, content=CREATE_UNAVAILABLE_MESSAGE, response_edit=True
                )
                return
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:  # noqa: BLE001 - Discord response details must remain private
                self._logger.error("schedule_create_defer_failed")
                return
            try:
                async with self._session_factory() as session, session.begin():
                    created = await OnceScheduleCreationService(session).create(
                        guild_id=self._configured_guild_id,
                        channel_id=channel_id,
                        creator_user_id=actor.user_id,
                        scheduled_for=scheduled_for,
                        content=content,
                        allow_duplicate=view.allow_duplicate,
                        now=now,
                        configured_guild_id=self._configured_guild_id,
                    )
            except DuplicateScheduleWarning:
                await self._finish_create_view(view, interaction, content=DUPLICATE_WARNING_MESSAGE)
                return
            except Exception:  # noqa: BLE001 - database details must remain private
                self._logger.error("schedule_create_failed")
                await self._finish_create_view(view, interaction, content=INTERNAL_ERROR_MESSAGE)
                return
            try:
                embed = created_schedule_embed(created)
            except Exception:  # noqa: BLE001 - presentation details must remain private
                self._logger.error("schedule_presentation_failed")
                await self._finish_create_view(view, interaction, content=INTERNAL_ERROR_MESSAGE)
                return
            await self._finish_create_view(view, interaction, embed=embed)

    async def _cancel_once_creation(
        self, view: OnceScheduleConfirmView, interaction: discord.Interaction
    ) -> None:
        async with view.action_lock:
            if view.finished:
                await respond_ephemeral(
                    interaction, CREATE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            actor = await self._actor_or_respond(interaction)
            if actor is None or actor.user_id != view.actor_user_id:
                return
            await self._finish_create_view(
                view, interaction, content=CREATE_CANCELLED_MESSAGE, response_edit=True
            )

    async def _expire_once_creation(self, view: OnceScheduleConfirmView) -> None:
        async with view.action_lock:
            if view.finished:
                return
            view.finished = True
            view.stop()
            self._create_views.discard(view)
            try:
                await view.initial_interaction.edit_original_response(
                    content=CREATE_EXPIRED_MESSAGE,
                    embed=None,
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:  # noqa: BLE001 - Discord failures can include private details
                self._logger.error("schedule_create_timeout_response_failed")

    async def _finish_create_view(
        self,
        view: OnceScheduleConfirmView,
        interaction: discord.Interaction,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
        response_edit: bool = False,
    ) -> None:
        view.finished = True
        view.stop()
        self._create_views.discard(view)
        arguments: dict[str, object] = {
            "content": content if embed is None else None,
            "embed": embed,
            "view": None,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        try:
            if response_edit:
                await interaction.response.edit_message(**arguments)
            else:
                await interaction.edit_original_response(**arguments)
        except Exception:  # noqa: BLE001 - Discord failures can include private details
            self._logger.error("schedule_create_result_response_failed")

    async def _cancel_deletion(
        self, view: ScheduleDeletionConfirmView, interaction: discord.Interaction
    ) -> None:
        async with view.action_lock:
            if view.finished:
                await respond_ephemeral(
                    interaction, DELETE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            actor = await self._actor_or_respond(interaction)
            if actor is None or actor.user_id != view.actor_user_id:
                return
            view.finished = True
            view.stop()
            self._delete_views.discard(view)
            try:
                await interaction.response.edit_message(
                    content=DELETE_CANCELLED_MESSAGE,
                    embed=None,
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:  # noqa: BLE001 - Discord failures can include private details
                self._logger.error("schedule_delete_cancel_response_failed")

    async def _expire_deletion(self, view: ScheduleDeletionConfirmView) -> None:
        async with view.action_lock:
            if view.finished:
                return
            view.finished = True
            view.stop()
            self._delete_views.discard(view)
            try:
                await view.initial_interaction.edit_original_response(
                    content=DELETE_EXPIRED_MESSAGE,
                    embed=None,
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:  # noqa: BLE001 - Discord failures can include private details
                self._logger.error("schedule_delete_timeout_response_failed")

    async def _finish_delete_view(
        self,
        view: ScheduleDeletionConfirmView,
        interaction: discord.Interaction,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
    ) -> None:
        view.finished = True
        view.stop()
        self._delete_views.discard(view)
        arguments: dict[str, object] = {
            "view": None,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if embed is not None:
            arguments["embed"] = embed
            arguments["content"] = None
        else:
            arguments["content"] = content
            arguments["embed"] = None
        try:
            await interaction.edit_original_response(**arguments)
        except Exception:  # noqa: BLE001 - Discord failures can include private details
            self._logger.error("schedule_delete_result_response_failed")

    async def close_delete_views(self) -> None:
        views = tuple(self._delete_views)
        self._delete_views.clear()
        for view in views:
            view.finished = True
            view.stop()
        if views:
            await asyncio.gather(*(view.wait() for view in views), return_exceptions=True)

    async def _move_list_page(
        self, view: ScheduleListView, interaction: discord.Interaction, page: int
    ) -> None:
        async with view.action_lock:
            if view.finished or view.closed:
                await respond_ephemeral(interaction, NOT_FOUND_MESSAGE, logger=self._logger)
                return
            actor = authorized_actor(
                interaction,
                configured_guild_id=self._configured_guild_id,
                allowed_role_ids=self._allowed_role_ids,
            )
            if actor is None or actor.user_id != view.actor_user_id:
                await respond_ephemeral(interaction, PERMISSION_DENIED_MESSAGE, logger=self._logger)
                return
            try:
                result = await self._queries.get_schedule_page(
                    guild_id=self._configured_guild_id,
                    requester_user_id=actor.user_id,
                    administrator=actor.administrator,
                    status=view.status,
                    page=max(1, page),
                    schedule_type=view.schedule_type,
                    clamp=True,
                )
                embed = schedule_list_embed(
                    result.schedules,
                    page=result.page,
                    status_filter=view.status,
                    schedule_type_filter=view.schedule_type,
                    total_count=result.total_count,
                    total_pages=result.total_pages,
                )
                view._render_controls(result)
                await interaction.response.edit_message(
                    content=None,
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:  # noqa: BLE001 - no private query/Discord details
                self._logger.error("schedule_list_navigation_failed")
                await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)

    async def _filter_list_type(
        self,
        view: ScheduleListView,
        interaction: discord.Interaction,
        schedule_type: ScheduleType | None,
    ) -> None:
        async with view.action_lock:
            if view.finished or view.closed:
                await respond_ephemeral(interaction, NOT_FOUND_MESSAGE, logger=self._logger)
                return
            actor = authorized_actor(
                interaction,
                configured_guild_id=self._configured_guild_id,
                allowed_role_ids=self._allowed_role_ids,
            )
            if actor is None or actor.user_id != view.actor_user_id:
                await respond_ephemeral(interaction, PERMISSION_DENIED_MESSAGE, logger=self._logger)
                return
            try:
                result = await self._queries.get_schedule_page(
                    guild_id=self._configured_guild_id,
                    requester_user_id=actor.user_id,
                    administrator=actor.administrator,
                    status=view.status,
                    page=1,
                    schedule_type=schedule_type,
                    clamp=True,
                )
                view.schedule_type = schedule_type
                embed = schedule_list_embed(
                    result.schedules,
                    page=result.page,
                    status_filter=view.status,
                    schedule_type_filter=schedule_type,
                    total_count=result.total_count,
                    total_pages=result.total_pages,
                )
                view._render_controls(result)
                await interaction.response.edit_message(
                    content=None,
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:  # noqa: BLE001 - no private query/Discord details
                self._logger.error("schedule_list_type_filter_failed")
                await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)

    async def _show_list_selection(
        self, view: ScheduleListView, interaction: discord.Interaction, public_id: str
    ) -> None:
        async with view.action_lock:
            if view.finished or view.closed:
                await respond_ephemeral(interaction, NOT_FOUND_MESSAGE, logger=self._logger)
                return
            actor = authorized_actor(
                interaction,
                configured_guild_id=self._configured_guild_id,
                allowed_role_ids=self._allowed_role_ids,
            )
            if actor is None or actor.user_id != view.actor_user_id:
                await respond_ephemeral(interaction, PERMISSION_DENIED_MESSAGE, logger=self._logger)
                return
            try:
                schedule = await self._queries.show_schedule(
                    guild_id=self._configured_guild_id,
                    requester_user_id=actor.user_id,
                    administrator=actor.administrator,
                    public_id=public_id,
                )
                if schedule is None:
                    await respond_ephemeral(interaction, NOT_FOUND_MESSAGE, logger=self._logger)
                    return
                embed = schedule_detail_embed(schedule)
                placeholder = SchedulePage((), view.page, 0)
                view._render_controls(placeholder, detail=True)
                await interaction.response.edit_message(
                    content=None,
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except InvalidScheduleQueryError:
                await respond_ephemeral(interaction, NOT_FOUND_MESSAGE, logger=self._logger)
            except Exception:  # noqa: BLE001 - no private query/Discord details
                self._logger.error("schedule_list_selection_failed")
                await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)

    async def _expire_list(self, view: ScheduleListView) -> None:
        async with view.action_lock:
            if view.finished or view.closed:
                return
            view.finished = True
            view.stop()
            self._list_views.discard(view)
            try:
                await view.initial_interaction.edit_original_response(
                    view=None, allowed_mentions=discord.AllowedMentions.none()
                )
            except Exception:  # noqa: BLE001 - Discord details remain private
                self._logger.error("schedule_list_timeout_response_failed")

    async def close_list_views(self) -> None:
        views = tuple(self._list_views)
        self._list_views.clear()
        for view in views:
            view.closed = True
            view.finished = True
            view.stop()
        if views:
            await asyncio.gather(*(view.wait() for view in views), return_exceptions=True)

    def _channel_name(self, interaction: discord.Interaction, channel_id: int) -> str:
        guild = interaction.guild
        channel = guild.get_channel(channel_id) if guild is not None else None
        name = getattr(channel, "name", None)
        return name if isinstance(name, str) else str(channel_id)

    async def close_confirmation_views(self) -> None:
        create_views = tuple(self._create_views)
        self._create_views.clear()
        resume_views = tuple(self._resume_views)
        self._resume_views.clear()
        for view in create_views:
            view.finished = True
            view.stop()
        for view in resume_views:
            view.finished = True
            view.stop()
        await self.close_delete_views()
        await self.close_list_views()
        if create_views:
            await asyncio.gather(*(view.wait() for view in create_views), return_exceptions=True)
        if resume_views:
            await asyncio.gather(*(view.wait() for view in resume_views), return_exceptions=True)

    async def _actor_or_respond(self, interaction: discord.Interaction) -> InteractionActor | None:
        actor = authorized_actor(
            interaction,
            configured_guild_id=self._configured_guild_id,
            allowed_role_ids=self._allowed_role_ids,
        )
        if actor is None:
            await respond_ephemeral(interaction, PERMISSION_DENIED_MESSAGE, logger=self._logger)
        return actor

    async def _create_recurring_command(
        self,
        interaction: discord.Interaction,
        *,
        channel: discord.TextChannel,
        schedule_type: ScheduleType,
        local_time_value: str,
        weekday: int | None,
        end_date_value: str | None,
        content: str | None,
        allow_duplicate: bool,
    ) -> None:
        actor = await self._actor_or_respond(interaction)
        if actor is None:
            return
        try:
            channel_id = self._validated_channel(interaction, channel)
            parsed_time: time = parse_local_time(local_time_value)
            now = self._clock.now()
            parsed_end_date: date | None = parse_end_date(end_date_value, now=now)
            content = validate_create_content(content)
        except FullwidthEndDateError:
            await respond_ephemeral(
                interaction, FULLWIDTH_END_DATE_INPUT_MESSAGE, logger=self._logger
            )
            return
        except InvalidEndDateFormatError:
            await respond_ephemeral(interaction, END_DATE_INPUT_MESSAGE, logger=self._logger)
            return
        except InvalidDateTimeError, InvalidScheduleContentError, ValueError:
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:  # noqa: BLE001 - Discord response details must remain private
            self._logger.error("schedule_create_defer_failed")
            return
        try:
            async with self._session_factory() as session, session.begin():
                created = await RecurringScheduleCreationService(session).create(
                    guild_id=interaction.guild_id,
                    channel_id=channel_id,
                    creator_user_id=actor.user_id,
                    schedule_type=schedule_type,
                    local_time=parsed_time,
                    weekday=weekday,
                    end_date=parsed_end_date,
                    content=content,
                    allow_duplicate=allow_duplicate,
                    now=now,
                    configured_guild_id=self._configured_guild_id,
                )
        except DuplicateScheduleWarning:
            await respond_ephemeral(interaction, DUPLICATE_WARNING_MESSAGE, logger=self._logger)
            return
        except InvalidDateTimeError:
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        except Exception:  # noqa: BLE001 - database details must not reach Discord or logs
            self._logger.error("schedule_create_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        await self._respond_embed(interaction, lambda: created_recurring_schedule_embed(created))

    def _validated_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        *,
        require_current: bool = False,
    ) -> int:
        guild = interaction.guild
        if (
            guild is None
            or guild.id != self._configured_guild_id
            or not isinstance(channel, discord.TextChannel)
            or channel.guild.id != guild.id
        ):
            raise ValueError("invalid channel")
        if require_current:
            current = guild.get_channel(channel.id)
            if not isinstance(current, discord.TextChannel):
                raise ValueError("channel no longer exists")
            channel = current
        member = guild.me
        if member is None:
            raise ValueError("missing bot member")
        permissions = channel.permissions_for(member)
        if not permissions.view_channel or not permissions.send_messages:
            raise ValueError("missing channel permission")
        return channel.id

    async def _respond_embed(
        self, interaction: discord.Interaction, factory: Callable[[], discord.Embed]
    ) -> None:
        try:
            embed = factory()
        except Exception:  # noqa: BLE001 - presentation failures must remain sanitized
            self._logger.error("schedule_presentation_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        await respond_ephemeral(interaction, embed=embed, logger=self._logger)
