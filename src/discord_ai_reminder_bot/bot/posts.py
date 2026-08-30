"""Guild-only read commands for Phase 1 schedules."""

from __future__ import annotations

import asyncio
import logging
import secrets
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import discord
from discord import app_commands
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.name_generation import NameGenerationRegistrationPolicy
from discord_ai_reminder_bot.application.schedule_creation import (
    DuplicateScheduleWarning,
    OnceScheduleCreationService,
    RecurringScheduleCreationService,
)
from discord_ai_reminder_bot.application.schedule_deletion import (
    DeleteReasonRequired,
    ScheduleDeletionService,
    ScheduleDeletionUnavailable,
    ScheduleDeletionVersionConflict,
)
from discord_ai_reminder_bot.application.schedule_editing import (
    EditedSchedule,
    EditValues,
    InvalidScheduleEditOptions,
    ScheduleEditingService,
    ScheduleEditNoChanges,
    ScheduleEditUnavailable,
    ScheduleEditVersionConflict,
)
from discord_ai_reminder_bot.application.schedule_naming import (
    ScheduleNameEditUnavailable,
    ScheduleNameNoChanges,
    ScheduleNameVersionConflict,
    ScheduleNamingService,
)
from discord_ai_reminder_bot.application.schedule_pause import (
    ResumeMode,
    SchedulePauseService,
    ScheduleStateChangeUnavailable,
    ScheduleVersionConflict,
)
from discord_ai_reminder_bot.application.schedule_queries import (
    MAX_PAGE_NUMBER,
    InvalidScheduleQueryError,
    ScheduleAutocompleteOperation,
    ScheduleDetail,
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
    schedule_autocomplete_choice,
    schedule_deletion_preview_embed,
    schedule_detail_embed,
    schedule_list_embed,
    schedule_select_option,
)
from discord_ai_reminder_bot.bot.post_views import (
    ScheduleDetailContext,
    ScheduleDetailView,
    ScheduleListOrigin,
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
    validate_required_delete_reason,
)

DETAIL_EDIT_MODAL_PREFIX = "post_detail_edit_modal"
DETAIL_NAME_EDIT_MODAL_PREFIX = "post_detail_name_edit_modal"
DETAIL_DELETE_REASON_MODAL_PREFIX = "post_detail_delete_reason"
RESUME_TIME_MODAL_PREFIX = "post_resume_time_modal"


def _modal_custom_id(prefix: str) -> str:
    """Create a non-identifying per-instance dispatch key within Discord's limit."""
    custom_id = f"{prefix}:{secrets.token_hex(16)}"
    if len(custom_id) > 100:
        raise ValueError("modal custom_id exceeds Discord's limit")
    return custom_id


NOT_FOUND_MESSAGE = "指定された予約は見つからないか、表示する権限がありません。"
INVALID_INPUT_MESSAGE = "入力内容を確認してください。"
EDIT_REQUEST_REQUIRED_MESSAGE = (
    "変更する項目を1つ以上指定してください。"
    "画面を見ながら編集する場合は /post show を使用してください。"
)
DETAIL_EDIT_CHANNEL_MESSAGE = (
    "投稿先を確認してください。Botが閲覧・送信できる同じサーバーのテキストチャンネルを"
    "選択してください。"
)
DATETIME_INPUT_MESSAGE = "投稿日時を確認してください。例：今日21:00、8/25 19:30、2027-08-25 19:30"
FULLWIDTH_DATETIME_INPUT_MESSAGE = (
    "投稿日時の数字と記号は半角で入力してください。例：今日21:00、8/25 19:30"
)
DUPLICATE_WARNING_MESSAGE = (
    "同一予約の可能性があります。意図的に作成する場合はallow_duplicate=trueで再実行してください。"
)
DELETE_UNAVAILABLE_MESSAGE = "指定された予約は見つからないか、削除できません。"
DELETE_REASON_INPUT_MESSAGE = "削除理由を1文字以上入力してください。"
DELETE_REASON_REQUIRED_MESSAGE = DELETE_REASON_INPUT_MESSAGE
DELETE_CANCELLED_MESSAGE = "予約の削除をキャンセルしました。"
DELETE_EXPIRED_MESSAGE = (
    "確認の有効期限が切れました。必要な場合はもう一度 /post delete を実行してください。"
)
STATE_CHANGE_UNAVAILABLE_MESSAGE = "指定された予約は見つからないか、この操作を実行できません。"
EDIT_UNAVAILABLE_MESSAGE = "指定された予約は見つからないか、編集できません。"
EDIT_NO_CHANGES_MESSAGE = "実際に変更される項目がありません。"
DETAIL_EDIT_NO_CHANGES_MESSAGE = "変更内容がありません。"
DETAIL_EDITED_MESSAGE = "予約を編集しました。"
DETAIL_NAME_EDITED_MESSAGE = "予約名を変更しました。"
DETAIL_NAME_NO_CHANGES_MESSAGE = "予約名に変更はありません。"
DETAIL_NAME_PERMISSION_LOST_MESSAGE = (
    "現在の権限ではこの予約を編集できません。/post showを再実行してください。"
)
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
DETAIL_CONFLICT_MESSAGE = "予約の状態が別の操作で変更されました。最新の内容を確認してください。"
DETAIL_PAUSED_MESSAGE = "予約を一時停止しました。"
DETAIL_RESUMED_MESSAGE = "予約を再開しました。"
DETAIL_DELETED_MESSAGE = "予約を削除しました。"
DETAIL_RESUME_CANCELLED_MESSAGE = "再開操作をキャンセルしました。"
DETAIL_DELETE_CANCELLED_MESSAGE = "削除をキャンセルしました。"
END_DATE_DESCRIPTION = "終了日｜例：明日、8/30、2026-08-30"
END_DATE_INPUT_MESSAGE = "終了日を確認してください。例：明日、8/30、2026-08-30"
FULLWIDTH_END_DATE_INPUT_MESSAGE = (
    "終了日の数字と記号は半角で入力してください。例：8/30、2026-08-30"
)

_EDIT_FIELD_LABELS = {
    "channel_id": "投稿先",
    "content": "本文",
    "scheduled_at": "投稿日時",
    "local_time": "投稿時刻",
    "weekday": "曜日",
    "end_date": "終了日",
}


def _safe_changed_fields(fields: tuple[str, ...]) -> str:
    return "、".join(_EDIT_FIELD_LABELS[item] for item in fields if item in _EDIT_FIELD_LABELS)


def _detail_edit_success_message(edited: EditedSchedule) -> str:
    lines = [DETAIL_EDITED_MESSAGE, "変更した項目: " + _safe_changed_fields(edited.changed_fields)]
    if edited.status is ScheduleStatus.PAUSED:
        lines.append("一時停止を維持しています。再開するまで投稿されません。")
    if edited.previous_status is ScheduleStatus.ACTIVE and edited.status is ScheduleStatus.DRAFT:
        lines.append("本文削除により下書きになりました。")
    if edited.previous_status is ScheduleStatus.DRAFT and edited.status is ScheduleStatus.ACTIVE:
        lines.append("本文設定により有効になりました。")
    if edited.status is ScheduleStatus.ENDED:
        lines.append("終了日内に次回投稿がないため終了済みになりました。")
    if edited.run_replaced:
        lines.append("変更前の実行予定を見送り、新しい次回投稿を作成しました。")
    if edited.retry_pending_preserved:
        lines.append("次回試行は変更後の内容を使用します。")
    return "\n".join(lines)


@dataclass(frozen=True)
class InteractionActor:
    user_id: int
    administrator: bool


class DetailEditChannelError(Exception):
    """The selected destination is unavailable or unsafe to use."""


class DetailEditModalStateError(Exception):
    """The submitted modal component state does not match the expected structure."""


class ScheduleEditModal(discord.ui.Modal):
    """Type-specific editor retaining only detached detail values."""

    def __init__(
        self,
        *,
        commands: PostCommands,
        detail_view: ScheduleDetailView,
        default_channel: discord.TextChannel | None,
    ) -> None:
        titles = {
            ScheduleType.ONCE: "単発予約を編集",
            ScheduleType.DAILY: "毎日予約を編集",
            ScheduleType.WEEKLY: "毎週予約を編集",
        }
        super().__init__(
            title=titles[detail_view.context.schedule_type],
            timeout=900.0,
            custom_id=_modal_custom_id(DETAIL_EDIT_MODAL_PREFIX),
        )
        self.commands = commands
        self.detail_view = detail_view
        self.action_lock = asyncio.Lock()
        self.finished = False
        self.closed = False
        defaults = [default_channel] if default_channel is not None else []
        self.channel = discord.ui.ChannelSelect(
            custom_id="post_detail_edit_channel",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1,
            required=False,
            default_values=defaults,
        )
        self.add_item(discord.ui.Label(text="投稿先", component=self.channel))
        context = detail_view.context
        if context.schedule_type is ScheduleType.ONCE:
            assert context.next_run_at is not None
            self.scheduled_at = discord.ui.TextInput(
                custom_id="post_detail_edit_scheduled_at",
                default=context.next_run_at.astimezone(TOKYO).strftime("%Y-%m-%d %H:%M"),
                min_length=16,
                max_length=16,
            )
            self.add_item(discord.ui.Label(text="投稿日時", component=self.scheduled_at))
        else:
            assert context.local_time is not None
            self.local_time = discord.ui.TextInput(
                custom_id="post_detail_edit_local_time",
                default=context.local_time.strftime("%H:%M"),
                min_length=5,
                max_length=5,
            )
            if context.schedule_type is ScheduleType.WEEKLY:
                self.weekday = discord.ui.Select(
                    custom_id="post_detail_edit_weekday",
                    min_values=1,
                    max_values=1,
                    required=True,
                    options=[
                        discord.SelectOption(
                            label=label, value=str(value), default=value == context.weekday
                        )
                        for value, label in enumerate(WEEKDAY_LABELS)
                    ],
                )
                self.add_item(discord.ui.Label(text="曜日", component=self.weekday))
            self.add_item(discord.ui.Label(text="投稿時刻", component=self.local_time))
            self.end_date = discord.ui.TextInput(
                custom_id="post_detail_edit_end_date",
                default=context.end_date.isoformat() if context.end_date else "",
                required=False,
                max_length=10,
            )
            self.add_item(discord.ui.Label(text="終了日", component=self.end_date))
        self.content = discord.ui.TextInput(
            custom_id="post_detail_edit_content",
            style=discord.TextStyle.paragraph,
            default=context.content or "",
            required=False,
            max_length=2_000,
        )
        self.add_item(discord.ui.Label(text="本文", component=self.content))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        async with self.action_lock:
            if self.finished or self.closed:
                return
            self.finished = True
            try:
                await self.commands._submit_detail_edit(self, interaction)
            finally:
                self._release()

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        self.commands._logger.error("schedule_detail_edit_modal_error")
        try:
            await respond_ephemeral(
                interaction, INTERNAL_ERROR_MESSAGE, logger=self.commands._logger
            )
        finally:
            self._release()

    async def on_timeout(self) -> None:
        self._release()

    def _release(self) -> None:
        self.closed = True
        self.commands._edit_modals.discard(self)
        self.stop()


class ScheduleNameEditModal(discord.ui.Modal, title="予約名を編集"):
    """One-field name editor retaining no Session or transaction."""

    def __init__(self, *, commands: PostCommands, detail_view: ScheduleDetailView) -> None:
        super().__init__(
            timeout=900.0,
            custom_id=_modal_custom_id(DETAIL_NAME_EDIT_MODAL_PREFIX),
        )
        self.commands = commands
        self.detail_view = detail_view
        self.action_lock = asyncio.Lock()
        self.finished = False
        self.closed = False
        self.display_name = discord.ui.TextInput(
            label="予約名（空欄で解除）",
            custom_id="post_detail_display_name",
            default=detail_view.context.display_name or "",
            required=False,
            max_length=32,
        )
        self.add_item(self.display_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        async with self.action_lock:
            if self.finished or self.closed:
                return
            self.finished = True
            try:
                await self.commands._submit_detail_name_edit(self, interaction)
            finally:
                self._release()

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        self.commands._logger.error("schedule_detail_name_edit_modal_error")
        try:
            await respond_ephemeral(
                interaction, INTERNAL_ERROR_MESSAGE, logger=self.commands._logger
            )
        finally:
            self._release()

    async def on_timeout(self) -> None:
        self._release()

    def _release(self) -> None:
        self.closed = True
        self.commands._name_edit_modals.discard(self)
        self.stop()


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
        embed: discord.Embed,
    ) -> None:
        super().__init__(timeout=None)
        self.commands = commands
        self.initial_interaction = interaction
        self.actor_user_id = actor_user_id
        self.administrator = administrator
        self.status = status
        self.schedule_type = schedule_type
        self.page = page.page
        self.current_embed = embed
        self.action_lock = asyncio.Lock()
        self.finished = False
        self.closed = False
        self._render_controls(page)

    def _render_controls(self, page: SchedulePage) -> None:
        self.clear_items()
        self.page = page.page
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
        detail_context: ScheduleDetailContext | None = None,
    ) -> None:
        super().__init__(timeout=900.0 if detail_context is not None else 120.0)
        self.commands = commands
        self.initial_interaction = interaction
        self.public_id = public_id
        self.reason = reason
        self.actor_user_id = actor_user_id
        self.detail_context = detail_context
        self.action_lock = asyncio.Lock()
        self.finished = False
        self.closed = False

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


class DeleteReasonModal(discord.ui.Modal, title="削除理由を入力"):
    reason = discord.ui.TextInput(
        label="削除理由",
        required=True,
        min_length=1,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, *, commands: PostCommands, detail_view: ScheduleDetailView) -> None:
        super().__init__(
            timeout=900.0,
            custom_id=_modal_custom_id(DETAIL_DELETE_REASON_MODAL_PREFIX),
        )
        self.commands = commands
        self.detail_view = detail_view
        self.action_lock = asyncio.Lock()
        self.finished = False
        self.closed = False

    async def on_submit(self, interaction: discord.Interaction) -> None:
        async with self.action_lock:
            if self.finished or self.closed:
                return
            self.finished = True
            try:
                reason = validate_required_delete_reason(str(self.reason.value))
            except InvalidDeleteReasonError:
                await respond_ephemeral(
                    interaction, DELETE_REASON_INPUT_MESSAGE, logger=self.commands._logger
                )
            else:
                await self.commands._continue_detail_delete(self.detail_view, interaction, reason)
            finally:
                self._release()

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        self.commands._logger.error("schedule_detail_delete_reason_modal_error")
        try:
            await respond_ephemeral(
                interaction, INTERNAL_ERROR_MESSAGE, logger=self.commands._logger
            )
        finally:
            self._release()

    async def on_timeout(self) -> None:
        self._release()

    def _release(self) -> None:
        self.closed = True
        self.stop()
        self.commands._delete_reason_modals.discard(self)


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
        self.closed = False

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
        super().__init__(
            timeout=120.0,
            custom_id=_modal_custom_id(RESUME_TIME_MODAL_PREFIX),
        )
        self.resume_view = view
        self.action_lock = asyncio.Lock()
        self.finished = False
        self.closed = False

    async def on_submit(self, interaction: discord.Interaction) -> None:
        async with self.action_lock:
            if self.finished or self.closed:
                return
            self.finished = True
            try:
                await self.resume_view.commands._submit_resume_time(
                    self.resume_view, interaction, str(self.local_time.value)
                )
            finally:
                self._release()

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        self.resume_view.commands._logger.error("schedule_resume_time_modal_error")
        try:
            await respond_ephemeral(
                interaction, INTERNAL_ERROR_MESSAGE, logger=self.resume_view.commands._logger
            )
        finally:
            self._release()

    async def on_timeout(self) -> None:
        self._release()

    def _release(self) -> None:
        self.closed = True
        self.stop()
        self.resume_view.commands._resume_modals.discard(self)


class ResumeChoiceView(discord.ui.View):
    def __init__(
        self,
        *,
        commands: PostCommands,
        interaction: discord.Interaction,
        public_id: str,
        actor_user_id: int,
        rescue_allowed: bool,
        detail_context: ScheduleDetailContext | None = None,
    ) -> None:
        super().__init__(timeout=900.0 if detail_context is not None else 120.0)
        self.commands = commands
        self.initial_interaction = interaction
        self.public_id = public_id
        self.actor_user_id = actor_user_id
        self.detail_context = detail_context
        self.action_lock = asyncio.Lock()
        self.finished = False
        self.closed = False
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
        async with self.action_lock:
            if self.finished or self.closed:
                await respond_ephemeral(
                    interaction, STATE_CHANGE_UNAVAILABLE_MESSAGE, logger=self.commands._logger
                )
                return
            modal = ResumeTimeModal(self)
            self.commands._resume_modals.add(modal)
            try:
                await interaction.response.send_modal(modal)
            except Exception:  # noqa: BLE001 - Discord details remain private
                self.commands._resume_modals.discard(modal)
                modal.stop()
                self.commands._logger.error("schedule_resume_modal_response_failed")

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
        name_generation_policy: NameGenerationRegistrationPolicy | None = None,
    ) -> None:
        super().__init__(name="post", description="予約投稿を確認します")
        self._queries = queries
        self._session_factory = session_factory
        self._clock = clock
        self._configured_guild_id = configured_guild_id
        self._allowed_role_ids = allowed_role_ids
        self._logger = logger
        self._name_generation_policy = name_generation_policy or NameGenerationRegistrationPolicy()
        self._delete_views: set[ScheduleDeletionConfirmView] = set()
        self._create_views: set[OnceScheduleConfirmView] = set()
        self._resume_views: set[ResumeChoiceView] = set()
        self._resume_modals: set[ResumeTimeModal] = set()
        self._delete_reason_modals: set[DeleteReasonModal] = set()
        self._edit_modals: set[ScheduleEditModal] = set()
        self._name_edit_modals: set[ScheduleNameEditModal] = set()
        self._list_views: set[ScheduleListView] = set()
        self._detail_views: set[ScheduleDetailView] = set()

    def _editing_service(self, session: AsyncSession) -> ScheduleEditingService:
        if not self._name_generation_policy.permits_registration:
            return ScheduleEditingService(session)
        return ScheduleEditingService(
            session,
            name_generation_policy=self._name_generation_policy,
            logger=self._logger,
        )

    def _once_creation_service(self, session: AsyncSession) -> OnceScheduleCreationService:
        if not self._name_generation_policy.permits_registration:
            return OnceScheduleCreationService(session)
        return OnceScheduleCreationService(
            session,
            name_generation_policy=self._name_generation_policy,
            logger=self._logger,
        )

    def _recurring_creation_service(
        self, session: AsyncSession
    ) -> RecurringScheduleCreationService:
        if not self._name_generation_policy.permits_registration:
            return RecurringScheduleCreationService(session)
        return RecurringScheduleCreationService(
            session,
            name_generation_policy=self._name_generation_policy,
            logger=self._logger,
        )

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
                embed=embed,
            )
        except Exception:  # noqa: BLE001 - presentation failures remain sanitized
            self._logger.error("schedule_presentation_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        if await respond_ephemeral(
            interaction,
            embed=embed,
            view=list_view,
            long_lived_view=True,
            logger=self._logger,
        ):
            self._list_views.add(list_view)

    @app_commands.command(name="show", description="予約の詳細を表示します")
    @app_commands.describe(public_id="予約IDを指定します")
    async def show_command(self, interaction: discord.Interaction, public_id: str) -> None:
        actor = await self._actor_or_respond(interaction)
        if actor is None:
            return
        try:
            detail = await self._queries.get_schedule_detail(
                guild_id=self._configured_guild_id,
                requester_user_id=actor.user_id,
                administrator=actor.administrator,
                public_id=public_id,
                now=self._clock.now(),
            )
        except InvalidScheduleQueryError:
            detail = None
        except Exception:  # noqa: BLE001 - database details must not reach Discord or logs
            self._logger.error("schedule_show_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        if detail is None:
            await respond_ephemeral(interaction, NOT_FOUND_MESSAGE, logger=self._logger)
            return
        try:
            embed = schedule_detail_embed(detail.schedule)
            detail_view = self._build_detail_view(
                interaction=interaction,
                actor_user_id=actor.user_id,
                detail=detail,
                embed=embed,
            )
        except Exception:  # noqa: BLE001 - presentation and context failures remain private
            self._logger.error("schedule_presentation_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        sent = await respond_ephemeral(
            interaction,
            embed=embed,
            view=detail_view if detail_view.has_components else None,
            long_lived_view=detail_view.has_components,
            logger=self._logger,
        )
        if sent and detail_view.has_components:
            self._detail_views.add(detail_view)

    @show_command.autocomplete("public_id")
    async def show_public_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._public_id_autocomplete(
            interaction, current=current, operation=ScheduleAutocompleteOperation.SHOW
        )

    @app_commands.command(name="edit", description="予約IDを選び、変更項目を直接指定して編集します")
    @app_commands.describe(
        public_id="直接編集する予約ID（候補から選択）",
        channel="変更後の投稿先",
        scheduled_at="単発のみ｜投稿日時（YYYY-MM-DD HH:MM）",
        local_time="毎日・毎週のみ｜基本投稿時刻を恒久変更（HH:MM）",
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
        if not has_request:
            await respond_ephemeral(interaction, EDIT_REQUEST_REQUIRED_MESSAGE, logger=self._logger)
            return
        if (content is not None and clear_content) or (end_date is not None and clear_end_date):
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
                edited = await self._editing_service(session).edit(
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

    @edit_command.autocomplete("public_id")
    async def edit_public_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._public_id_autocomplete(
            interaction, current=current, operation=ScheduleAutocompleteOperation.EDIT
        )

    @app_commands.command(name="pause", description="定期投稿を一時停止します")
    @app_commands.describe(public_id="予約IDを指定します")
    async def pause_command(self, interaction: discord.Interaction, public_id: str) -> None:
        await self._change_schedule_state(interaction, public_id=public_id, resume=False)

    @pause_command.autocomplete("public_id")
    async def pause_public_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._public_id_autocomplete(
            interaction, current=current, operation=ScheduleAutocompleteOperation.PAUSE
        )

    @app_commands.command(name="resume", description="一時停止中の定期投稿を再開します")
    @app_commands.describe(public_id="予約IDを指定します")
    async def resume_command(self, interaction: discord.Interaction, public_id: str) -> None:
        await self._change_schedule_state(interaction, public_id=public_id, resume=True)

    @resume_command.autocomplete("public_id")
    async def resume_public_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._public_id_autocomplete(
            interaction, current=current, operation=ScheduleAutocompleteOperation.RESUME
        )

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
                        **(
                            {"expected_version": view.detail_context.expected_version}
                            if view.detail_context is not None
                            else {}
                        ),
                    )
            except ScheduleVersionConflict:
                if view.detail_context is not None:
                    await self._refresh_detail(view, interaction, actor, DETAIL_CONFLICT_MESSAGE)
                else:
                    await self._finish_resume_view(
                        view, interaction, content=STATE_CHANGE_UNAVAILABLE_MESSAGE
                    )
                return
            except ScheduleStateChangeUnavailable:
                await self._finish_resume_view(
                    view, interaction, content=STATE_CHANGE_UNAVAILABLE_MESSAGE
                )
                return
            except Exception:  # noqa: BLE001
                self._logger.error("schedule_resume_failed")
                await self._finish_resume_view(view, interaction, content=INTERNAL_ERROR_MESSAGE)
                return
            if view.detail_context is not None:
                await self._refresh_detail(view, interaction, actor, DETAIL_RESUMED_MESSAGE)
            else:
                await self._finish_resume_view(
                    view, interaction, embed=resumed_schedule_embed(changed)
                )

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
            actor = await self._actor_or_respond(interaction)
            if actor is None or actor.user_id != view.actor_user_id:
                return
            if view.detail_context is not None:
                await self._refresh_detail(
                    view, interaction, actor, DETAIL_RESUME_CANCELLED_MESSAGE, response_edit=True
                )
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
            if view.detail_context is not None:
                view.finished = True
                for item in view.children:
                    if isinstance(item, discord.ui.Button | discord.ui.Select):
                        item.disabled = True
                try:
                    await view.initial_interaction.edit_original_response(
                        content=(
                            "操作期限が切れました。最新の状態は /post show または "
                            "/post list で確認してください。"
                        ),
                        view=view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except Exception:  # noqa: BLE001
                    self._logger.error("schedule_resume_timeout_response_failed")
                finally:
                    view.stop()
                    self._resume_views.discard(view)
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

    @delete_command.autocomplete("public_id")
    async def delete_public_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._public_id_autocomplete(
            interaction, current=current, operation=ScheduleAutocompleteOperation.DELETE
        )

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
                        **(
                            {"expected_version": view.detail_context.expected_version}
                            if view.detail_context is not None
                            else {}
                        ),
                    )
            except ScheduleDeletionVersionConflict:
                if view.detail_context is not None:
                    await self._refresh_detail(view, interaction, actor, DETAIL_CONFLICT_MESSAGE)
                else:
                    await self._finish_delete_view(
                        view, interaction, content=DELETE_UNAVAILABLE_MESSAGE
                    )
                return
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
            if view.detail_context is not None:
                await self._refresh_detail(view, interaction, actor, DETAIL_DELETED_MESSAGE)
            else:
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
                    created = await self._once_creation_service(session).create(
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
            if view.detail_context is not None:
                await self._refresh_detail(
                    view, interaction, actor, DETAIL_DELETE_CANCELLED_MESSAGE, response_edit=True
                )
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
            if view.detail_context is not None:
                view.finished = True
                for item in view.children:
                    if isinstance(item, discord.ui.Button | discord.ui.Select):
                        item.disabled = True
                try:
                    await view.initial_interaction.edit_original_response(
                        content=(
                            "操作期限が切れました。最新の状態は /post show または "
                            "/post list で確認してください。"
                        ),
                        view=view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except Exception:  # noqa: BLE001
                    self._logger.error("schedule_delete_timeout_response_failed")
                finally:
                    view.stop()
                    self._delete_views.discard(view)
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
            view.closed = True
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
                view.current_embed = embed
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
                view.current_embed = embed
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
                detail = await self._queries.get_schedule_detail(
                    guild_id=self._configured_guild_id,
                    requester_user_id=actor.user_id,
                    administrator=actor.administrator,
                    public_id=public_id,
                    now=self._clock.now(),
                )
                if detail is None:
                    await respond_ephemeral(interaction, NOT_FOUND_MESSAGE, logger=self._logger)
                    return
                embed = schedule_detail_embed(detail.schedule)
                detail_view = self._build_detail_view(
                    interaction=view.initial_interaction,
                    actor_user_id=actor.user_id,
                    detail=detail,
                    embed=embed,
                    list_origin=ScheduleListOrigin(
                        status=view.status,
                        schedule_type=view.schedule_type,
                        page=view.page,
                    ),
                )
                await interaction.response.edit_message(
                    content=None,
                    embed=embed,
                    view=detail_view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                view.finished = True
                view.closed = True
                view.stop()
                self._list_views.discard(view)
                self._detail_views.add(detail_view)
            except InvalidScheduleQueryError:
                await respond_ephemeral(interaction, NOT_FOUND_MESSAGE, logger=self._logger)
            except Exception:  # noqa: BLE001 - no private query/Discord details
                self._logger.error("schedule_list_selection_failed")
                await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)

    def _build_detail_view(
        self,
        *,
        interaction: discord.Interaction,
        actor_user_id: int,
        detail: ScheduleDetail,
        embed: discord.Embed,
        list_origin: ScheduleListOrigin | None = None,
    ) -> ScheduleDetailView:
        context = ScheduleDetailContext(
            public_id=detail.schedule.public_id,
            expected_version=detail.schedule.version,
            actor_user_id=actor_user_id,
            creator_user_id=detail.schedule.creator_user_id,
            actions=detail.actions,
            schedule_type=detail.schedule.schedule_type,
            channel_id=detail.schedule.channel_id,
            content=detail.schedule.content,
            next_run_at=detail.schedule.next_run_at,
            local_time=detail.schedule.local_time,
            weekday=detail.schedule.weekday,
            end_date=detail.schedule.end_date,
            display_name=detail.schedule.display_name,
            display_name_source=detail.schedule.display_name_source,
            name_editable=detail.schedule.status
            in {ScheduleStatus.DRAFT, ScheduleStatus.ACTIVE, ScheduleStatus.PAUSED},
            list_origin=list_origin,
        )
        return ScheduleDetailView(
            commands=self,
            interaction=interaction,
            context=context,
            embed=embed,
        )

    async def _pause_from_detail(
        self, view: ScheduleDetailView, interaction: discord.Interaction
    ) -> None:
        async with view.action_lock:
            if view.finished or view.closed or not view.context.actions.can_pause:
                await respond_ephemeral(
                    interaction, STATE_CHANGE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            actor = await self._detail_actor(view, interaction)
            if actor is None:
                return
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:  # noqa: BLE001
                self._logger.error("schedule_detail_pause_defer_failed")
                return
            try:
                async with self._session_factory() as session, session.begin():
                    await SchedulePauseService(session).pause(
                        guild_id=self._configured_guild_id,
                        public_id=str(view.context.public_id),
                        actor_user_id=actor.user_id,
                        administrator=actor.administrator,
                        paused_at=self._clock.now(),
                        expected_version=view.context.expected_version,
                    )
            except ScheduleVersionConflict:
                await self._refresh_detail(view, interaction, actor, DETAIL_CONFLICT_MESSAGE)
                return
            except ScheduleStateChangeUnavailable:
                await self._refresh_detail(
                    view, interaction, actor, STATE_CHANGE_UNAVAILABLE_MESSAGE
                )
                return
            except Exception:  # noqa: BLE001
                self._logger.error("schedule_detail_pause_failed")
                await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
                return
            await self._refresh_detail(view, interaction, actor, DETAIL_PAUSED_MESSAGE)

    async def _resume_from_detail(
        self, view: ScheduleDetailView, interaction: discord.Interaction
    ) -> None:
        async with view.action_lock:
            if view.finished or view.closed or not view.context.actions.can_resume:
                await respond_ephemeral(
                    interaction, STATE_CHANGE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            actor = await self._detail_actor(view, interaction)
            if actor is None:
                return
            try:
                async with self._session_factory() as session:
                    preview = await SchedulePauseService(session).preview_resume(
                        guild_id=self._configured_guild_id,
                        public_id=str(view.context.public_id),
                        actor_user_id=actor.user_id,
                        administrator=actor.administrator,
                        resumed_at=self._clock.now(),
                        expected_version=view.context.expected_version,
                    )
            except ScheduleVersionConflict:
                await self._refresh_detail(
                    view, interaction, actor, DETAIL_CONFLICT_MESSAGE, response_edit=True
                )
                return
            except ScheduleStateChangeUnavailable:
                await self._refresh_detail(
                    view, interaction, actor, STATE_CHANGE_UNAVAILABLE_MESSAGE, response_edit=True
                )
                return
            except Exception:  # noqa: BLE001
                self._logger.error("schedule_detail_resume_preview_failed")
                await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
                return
            now = require_utc(self._clock.now())
            if preview.held_run_at is not None and preview.held_run_at <= now:
                choice = ResumeChoiceView(
                    commands=self,
                    interaction=view.initial_interaction,
                    public_id=str(view.context.public_id),
                    actor_user_id=actor.user_id,
                    rescue_allowed=preview.rescue_allowed,
                    detail_context=view.context,
                )
                embed = discord.Embed(
                    title="予約の再開方法を選択してください",
                    description="一時停止中に投稿時刻を過ぎました。DBはまだ更新されていません。",
                    colour=0xE67E22,
                )
                await interaction.response.edit_message(
                    content=None,
                    embed=embed,
                    view=choice,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self._transfer_detail_to_resume(view, choice)
                return
            try:
                await interaction.response.defer(ephemeral=True)
                async with self._session_factory() as session, session.begin():
                    await SchedulePauseService(session).resume(
                        guild_id=self._configured_guild_id,
                        public_id=str(view.context.public_id),
                        actor_user_id=actor.user_id,
                        administrator=actor.administrator,
                        resumed_at=self._clock.now(),
                        configured_guild_id=self._configured_guild_id,
                        expected_version=view.context.expected_version,
                    )
            except ScheduleVersionConflict:
                await self._refresh_detail(view, interaction, actor, DETAIL_CONFLICT_MESSAGE)
                return
            except ScheduleStateChangeUnavailable:
                await self._refresh_detail(
                    view, interaction, actor, STATE_CHANGE_UNAVAILABLE_MESSAGE
                )
                return
            except Exception:  # noqa: BLE001
                self._logger.error("schedule_detail_resume_failed")
                await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
                return
            await self._refresh_detail(view, interaction, actor, DETAIL_RESUMED_MESSAGE)

    async def _delete_from_detail(
        self, view: ScheduleDetailView, interaction: discord.Interaction
    ) -> None:
        async with view.action_lock:
            if view.finished or view.closed or not view.context.actions.can_delete:
                await respond_ephemeral(
                    interaction, DELETE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            actor = await self._detail_actor(view, interaction)
            if actor is None:
                return
            if actor.administrator and actor.user_id != view.context.creator_user_id:
                modal = DeleteReasonModal(commands=self, detail_view=view)
                self._delete_reason_modals.add(modal)
                try:
                    await interaction.response.send_modal(modal)
                except Exception:  # noqa: BLE001
                    self._delete_reason_modals.discard(modal)
                    modal.stop()
                    self._logger.error("schedule_detail_delete_reason_response_failed")
                return
        await self._continue_detail_delete(view, interaction, None)

    async def _edit_from_detail(
        self, view: ScheduleDetailView, interaction: discord.Interaction
    ) -> None:
        async with view.action_lock:
            if view.finished or view.closed or not view.context.actions.can_edit:
                await respond_ephemeral(interaction, EDIT_UNAVAILABLE_MESSAGE, logger=self._logger)
                return
            actor = await self._detail_actor(view, interaction)
            if actor is None:
                return
            guild = interaction.guild
            candidate = guild.get_channel(view.context.channel_id) if guild is not None else None
            default_channel = candidate if isinstance(candidate, discord.TextChannel) else None
            modal = ScheduleEditModal(
                commands=self, detail_view=view, default_channel=default_channel
            )
            self._edit_modals.add(modal)
            try:
                await interaction.response.send_modal(modal)
            except Exception:  # noqa: BLE001
                modal._release()
                self._logger.error("schedule_detail_edit_modal_response_failed")

    async def _edit_name_from_detail(
        self, view: ScheduleDetailView, interaction: discord.Interaction
    ) -> None:
        async with view.action_lock:
            if view.finished or view.closed or not view.context.name_editable:
                await respond_ephemeral(interaction, EDIT_UNAVAILABLE_MESSAGE, logger=self._logger)
                return
            actor = await self._detail_actor(view, interaction)
            if actor is None:
                return
            modal = ScheduleNameEditModal(commands=self, detail_view=view)
            self._name_edit_modals.add(modal)
            try:
                await interaction.response.send_modal(modal)
            except Exception:  # noqa: BLE001
                modal._release()
                self._logger.error("schedule_detail_name_edit_modal_response_failed")

    async def _submit_detail_name_edit(
        self, modal: ScheduleNameEditModal, interaction: discord.Interaction
    ) -> None:
        view = modal.detail_view
        actor = authorized_actor(
            interaction,
            configured_guild_id=self._configured_guild_id,
            allowed_role_ids=self._allowed_role_ids,
        )
        if actor is None or actor.user_id != view.context.actor_user_id:
            self._retire_detail_source(view)
            await respond_ephemeral(
                interaction, DETAIL_NAME_PERMISSION_LOST_MESSAGE, logger=self._logger
            )
            return
        value = modal.display_name.value
        if not isinstance(value, str):
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        try:
            await interaction.response.defer()
        except Exception:  # noqa: BLE001
            self._logger.error("schedule_detail_name_edit_defer_failed")
            return
        try:
            async with self._session_factory() as session, session.begin():
                await ScheduleNamingService(session).edit_manual_name(
                    guild_id=self._configured_guild_id,
                    public_id=str(view.context.public_id),
                    actor_user_id=actor.user_id,
                    administrator=actor.administrator,
                    submitted_name=value,
                    edited_at=self._clock.now(),
                    expected_version=view.context.expected_version,
                )
        except ScheduleNameVersionConflict:
            await self._refresh_detail(view, interaction, actor, DETAIL_CONFLICT_MESSAGE)
            return
        except ScheduleNameNoChanges:
            await self._refresh_detail(view, interaction, actor, DETAIL_NAME_NO_CHANGES_MESSAGE)
            return
        except ScheduleNameEditUnavailable:
            await self._refresh_detail(
                view,
                interaction,
                actor,
                EDIT_UNAVAILABLE_MESSAGE,
                inaccessible_content=DETAIL_NAME_PERMISSION_LOST_MESSAGE,
            )
            return
        except ValueError:
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        except Exception:  # noqa: BLE001
            self._logger.error("schedule_detail_name_edit_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        await self._refresh_detail(view, interaction, actor, DETAIL_NAME_EDITED_MESSAGE)

    async def _submit_detail_edit(
        self, modal: ScheduleEditModal, interaction: discord.Interaction
    ) -> None:
        view = modal.detail_view
        try:
            actor = authorized_actor(
                interaction,
                configured_guild_id=self._configured_guild_id,
                allowed_role_ids=self._allowed_role_ids,
            )
            if actor is None or actor.user_id != view.context.actor_user_id:
                await respond_ephemeral(interaction, PERMISSION_DENIED_MESSAGE, logger=self._logger)
                return
            selected = list(modal.channel.values)
            if len(selected) > 1:
                raise ValueError("too many channels")
            channel_id = self._detail_edit_channel_id(
                interaction,
                selected[0] if selected else None,
                current_channel_id=view.context.channel_id,
            )
            now = self._clock.now()
            context = view.context
            content_value = modal.content.value
            if not isinstance(content_value, str):
                raise DetailEditModalStateError
            clear_content = not content_value.strip()
            content = None if clear_content else validate_create_content(content_value)
            values_kwargs: dict[str, object] = {
                "channel_id": channel_id,
                "content": content,
                "clear_content": clear_content,
            }
            if context.schedule_type is ScheduleType.ONCE:
                scheduled_at_value = modal.scheduled_at.value
                if not isinstance(scheduled_at_value, str):
                    raise DetailEditModalStateError
                values_kwargs["scheduled_at"] = parse_once_scheduled_at(scheduled_at_value, now=now)
            else:
                local_time_value = modal.local_time.value
                end_date_value = modal.end_date.value
                if not isinstance(local_time_value, str) or not isinstance(end_date_value, str):
                    raise DetailEditModalStateError
                values_kwargs["local_time"] = parse_local_time(local_time_value)
                end_value = end_date_value.strip()
                values_kwargs.update(
                    end_date=parse_end_date(end_value, now=now) if end_value else None,
                    end_date_supplied=bool(end_value),
                    clear_end_date=not end_value,
                )
                if context.schedule_type is ScheduleType.WEEKLY:
                    if len(modal.weekday.values) != 1:
                        raise ValueError("weekday is required")
                    values_kwargs.update(
                        weekday=int(modal.weekday.values[0]), weekday_supplied=True
                    )
            values = EditValues(**values_kwargs)
        except FullwidthEndDateError:
            await respond_ephemeral(
                interaction, FULLWIDTH_END_DATE_INPUT_MESSAGE, logger=self._logger
            )
            return
        except InvalidEndDateFormatError:
            await respond_ephemeral(interaction, END_DATE_INPUT_MESSAGE, logger=self._logger)
            return
        except DetailEditChannelError:
            await respond_ephemeral(interaction, DETAIL_EDIT_CHANNEL_MESSAGE, logger=self._logger)
            return
        except InvalidDateTimeError, InvalidScheduleContentError, ValueError:
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        except Exception:  # noqa: BLE001 - component internals and Discord details stay private
            self._logger.error("schedule_detail_edit_modal_error")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        try:
            # A modal submitted from a detail message must defer as a message update.
            # ``ephemeral=True`` creates a separate modal-response message, leaving the
            # displayed detail message owned by the retired view.
            await interaction.response.defer()
        except Exception:  # noqa: BLE001
            self._logger.error("schedule_detail_edit_defer_failed")
            return
        try:
            async with self._session_factory() as session, session.begin():
                edited = await self._editing_service(session).edit(
                    guild_id=self._configured_guild_id,
                    public_id=str(view.context.public_id),
                    actor_user_id=actor.user_id,
                    administrator=actor.administrator,
                    values=values,
                    edited_at=now,
                    configured_guild_id=self._configured_guild_id,
                    expected_version=view.context.expected_version,
                )
        except ScheduleEditVersionConflict:
            await self._refresh_detail(view, interaction, actor, DETAIL_CONFLICT_MESSAGE)
            return
        except ScheduleEditNoChanges:
            await self._refresh_detail(view, interaction, actor, DETAIL_EDIT_NO_CHANGES_MESSAGE)
            return
        except InvalidScheduleEditOptions:
            await respond_ephemeral(interaction, EDIT_TYPE_OPTIONS_MESSAGE, logger=self._logger)
            return
        except ScheduleEditUnavailable:
            await self._refresh_detail(view, interaction, actor, EDIT_UNAVAILABLE_MESSAGE)
            return
        except Exception:  # noqa: BLE001
            self._logger.error("schedule_detail_edit_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        await self._refresh_detail(
            view,
            interaction,
            actor,
            _detail_edit_success_message(edited),
        )

    async def _continue_detail_delete(
        self, view: ScheduleDetailView, interaction: discord.Interaction, reason: str | None
    ) -> None:
        async with view.action_lock:
            if view.finished or view.closed:
                await respond_ephemeral(
                    interaction, DELETE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            actor = await self._detail_actor(view, interaction)
            if actor is None:
                return
            try:
                async with self._session_factory() as session:
                    preview = await ScheduleDeletionService(session).preview(
                        guild_id=self._configured_guild_id,
                        public_id=str(view.context.public_id),
                        actor_user_id=actor.user_id,
                        administrator=actor.administrator,
                        reason=reason,
                        expected_version=view.context.expected_version,
                    )
                embed = schedule_deletion_preview_embed(preview)
            except ScheduleDeletionVersionConflict:
                await self._refresh_detail(
                    view, interaction, actor, DETAIL_CONFLICT_MESSAGE, response_edit=True
                )
                return
            except DeleteReasonRequired, ScheduleDeletionUnavailable:
                await respond_ephemeral(
                    interaction, DELETE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            except Exception:  # noqa: BLE001
                self._logger.error("schedule_detail_delete_preview_failed")
                await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
                return
            confirm = ScheduleDeletionConfirmView(
                commands=self,
                interaction=view.initial_interaction,
                public_id=str(view.context.public_id),
                reason=preview.reason,
                actor_user_id=actor.user_id,
                detail_context=view.context,
            )
            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=confirm,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            view.finished = view.closed = True
            view.stop()
            self._detail_views.discard(view)
            self._delete_views.add(confirm)

    async def _detail_actor(
        self, view: ScheduleDetailView, interaction: discord.Interaction
    ) -> InteractionActor | None:
        actor = authorized_actor(
            interaction,
            configured_guild_id=self._configured_guild_id,
            allowed_role_ids=self._allowed_role_ids,
        )
        if actor is None or actor.user_id != view.context.actor_user_id:
            await respond_ephemeral(interaction, PERMISSION_DENIED_MESSAGE, logger=self._logger)
            return None
        return actor

    def _transfer_detail_to_resume(
        self, detail: ScheduleDetailView, resume: ResumeChoiceView
    ) -> None:
        detail.finished = detail.closed = True
        detail.stop()
        self._detail_views.discard(detail)
        self._resume_views.add(resume)

    def _retire_detail_source(
        self, source: ScheduleDetailView | ResumeChoiceView | ScheduleDeletionConfirmView
    ) -> None:
        """Release the old message ownership before Discord registers its replacement."""
        source.finished = source.closed = True
        source.stop()
        self._detail_views.discard(source)  # type: ignore[arg-type]
        self._resume_views.discard(source)  # type: ignore[arg-type]
        self._delete_views.discard(source)  # type: ignore[arg-type]

    async def _refresh_detail(
        self,
        source: ScheduleDetailView | ResumeChoiceView | ScheduleDeletionConfirmView,
        interaction: discord.Interaction,
        actor: InteractionActor,
        content: str,
        *,
        response_edit: bool = False,
        inaccessible_content: str | None = None,
    ) -> None:
        context = (
            source.context if isinstance(source, ScheduleDetailView) else source.detail_context
        )
        if context is None:
            return
        try:
            detail = await self._queries.get_schedule_detail(
                guild_id=self._configured_guild_id,
                requester_user_id=actor.user_id,
                administrator=actor.administrator,
                public_id=str(context.public_id),
                now=self._clock.now(),
            )
            if detail is None:
                if inaccessible_content is not None:
                    self._retire_detail_source(source)
                    arguments = {
                        "content": inaccessible_content,
                        "embed": None,
                        "view": None,
                        "allowed_mentions": discord.AllowedMentions.none(),
                    }
                    if response_edit:
                        await interaction.response.edit_message(**arguments)
                    else:
                        await interaction.edit_original_response(**arguments)
                    return
                raise InvalidScheduleQueryError
            embed = schedule_detail_embed(detail.schedule)
            refreshed = self._build_detail_view(
                interaction=source.initial_interaction,
                actor_user_id=actor.user_id,
                detail=detail,
                embed=embed,
                list_origin=context.list_origin,
            )
            arguments = {
                "content": content,
                "embed": embed,
                "view": refreshed,
                "allowed_mentions": discord.AllowedMentions.none(),
            }
            # discord.py keys component dispatch by message ID and fixed custom IDs.
            # Stopping the old view after registering the replacement would remove
            # the replacement's dispatch entries as well.
            self._retire_detail_source(source)
            if response_edit:
                await interaction.response.edit_message(**arguments)
            else:
                await interaction.edit_original_response(**arguments)
        except Exception:  # noqa: BLE001
            self._logger.error("schedule_detail_refresh_response_failed")
            self._retire_detail_source(source)
            return
        self._detail_views.add(refreshed)

    async def _detail_interaction_allowed(
        self, view: ScheduleDetailView, interaction: discord.Interaction
    ) -> bool:
        actor = authorized_actor(
            interaction,
            configured_guild_id=self._configured_guild_id,
            allowed_role_ids=self._allowed_role_ids,
        )
        if actor is not None and actor.user_id == view.context.actor_user_id:
            return True
        await respond_ephemeral(interaction, PERMISSION_DENIED_MESSAGE, logger=self._logger)
        return False

    async def _return_to_list(
        self, view: ScheduleDetailView, interaction: discord.Interaction
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
            if actor is None or actor.user_id != view.context.actor_user_id:
                await respond_ephemeral(interaction, PERMISSION_DENIED_MESSAGE, logger=self._logger)
                return
            origin = view.context.list_origin
            if origin is None:
                await respond_ephemeral(interaction, NOT_FOUND_MESSAGE, logger=self._logger)
                return
            try:
                detail = await self._queries.get_schedule_detail(
                    guild_id=self._configured_guild_id,
                    requester_user_id=actor.user_id,
                    administrator=actor.administrator,
                    public_id=str(view.context.public_id),
                    now=self._clock.now(),
                )
                if detail is None:
                    await respond_ephemeral(
                        interaction, PERMISSION_DENIED_MESSAGE, logger=self._logger
                    )
                    return
                result = await self._queries.get_schedule_page(
                    guild_id=self._configured_guild_id,
                    requester_user_id=actor.user_id,
                    administrator=actor.administrator,
                    status=origin.status,
                    page=origin.page,
                    schedule_type=origin.schedule_type,
                    clamp=True,
                )
                embed = schedule_list_embed(
                    result.schedules,
                    page=result.page,
                    status_filter=origin.status,
                    schedule_type_filter=origin.schedule_type,
                    total_count=result.total_count,
                    total_pages=result.total_pages,
                )
                list_view = ScheduleListView(
                    commands=self,
                    interaction=view.initial_interaction,
                    actor_user_id=actor.user_id,
                    administrator=actor.administrator,
                    status=origin.status,
                    schedule_type=origin.schedule_type,
                    page=result,
                    embed=embed,
                )
                await interaction.response.edit_message(
                    content=None,
                    embed=embed,
                    view=list_view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                view.finished = True
                view.closed = True
                view.stop()
                self._detail_views.discard(view)
                self._list_views.add(list_view)
            except Exception:  # noqa: BLE001 - query and Discord details remain private
                self._logger.error("schedule_detail_back_failed")
                await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)

    async def close_detail_views(self) -> None:
        views = tuple(self._detail_views)
        self._detail_views.clear()
        for view in views:
            view.closed = True
            view.finished = True
            view.stop()
        if views:
            await asyncio.gather(*(view.wait() for view in views), return_exceptions=True)

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
            view.closed = True
            view.finished = True
            view.stop()
        for view in resume_views:
            view.closed = True
            view.finished = True
            view.stop()
        resume_modals = tuple(self._resume_modals)
        self._resume_modals.clear()
        for modal in resume_modals:
            modal.closed = True
            modal.stop()
        delete_reason_modals = tuple(self._delete_reason_modals)
        self._delete_reason_modals.clear()
        for modal in delete_reason_modals:
            modal.closed = True
            modal.stop()
        edit_modals = tuple(self._edit_modals)
        self._edit_modals.clear()
        for modal in edit_modals:
            modal.closed = True
            modal.stop()
        name_edit_modals = tuple(self._name_edit_modals)
        self._name_edit_modals.clear()
        for modal in name_edit_modals:
            modal.closed = True
            modal.stop()
        await self.close_delete_views()
        await self.close_list_views()
        await self.close_detail_views()
        if create_views:
            await asyncio.gather(*(view.wait() for view in create_views), return_exceptions=True)
        if resume_views:
            await asyncio.gather(*(view.wait() for view in resume_views), return_exceptions=True)
        if resume_modals:
            await asyncio.gather(*(modal.wait() for modal in resume_modals), return_exceptions=True)
        if delete_reason_modals:
            await asyncio.gather(
                *(modal.wait() for modal in delete_reason_modals), return_exceptions=True
            )
        if edit_modals:
            await asyncio.gather(*(modal.wait() for modal in edit_modals), return_exceptions=True)
        if name_edit_modals:
            await asyncio.gather(
                *(modal.wait() for modal in name_edit_modals), return_exceptions=True
            )

    async def _actor_or_respond(self, interaction: discord.Interaction) -> InteractionActor | None:
        actor = authorized_actor(
            interaction,
            configured_guild_id=self._configured_guild_id,
            allowed_role_ids=self._allowed_role_ids,
        )
        if actor is None:
            await respond_ephemeral(interaction, PERMISSION_DENIED_MESSAGE, logger=self._logger)
        return actor

    async def _public_id_autocomplete(
        self,
        interaction: discord.Interaction,
        *,
        current: str,
        operation: ScheduleAutocompleteOperation,
    ) -> list[app_commands.Choice[str]]:
        actor = authorized_actor(
            interaction,
            configured_guild_id=self._configured_guild_id,
            allowed_role_ids=self._allowed_role_ids,
        )
        if actor is None:
            return []
        try:
            channel_ids = self._autocomplete_channel_ids(interaction, current)
            if channel_ids is None:
                return []
            schedules = await self._queries.autocomplete_schedules(
                guild_id=self._configured_guild_id,
                requester_user_id=actor.user_id,
                administrator=actor.administrator,
                operation=operation,
                current=current,
                channel_ids=channel_ids,
                now=self._clock.now(),
            )
            return [
                schedule_autocomplete_choice(
                    schedule,
                    channel_name=self._autocomplete_channel_name(interaction, schedule.channel_id),
                )
                for schedule in schedules
            ]
        except Exception:  # noqa: BLE001 - DB and presentation details remain private
            self._logger.error("schedule_autocomplete_failed")
            return []

    @staticmethod
    def _autocomplete_channel_ids(
        interaction: discord.Interaction, current: str
    ) -> frozenset[int] | None:
        if not isinstance(current, str) or len(current) > 100:
            return None
        if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in current):
            return None
        search = current.strip().removeprefix("#")
        search = search.strip().casefold()
        if not search:
            return frozenset() if current == "" else None
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return frozenset()
        matches: set[int] = set()
        for channel in guild.text_channels:
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
                continue
            if not channel.permissions_for(member).view_channel:
                continue
            if search in channel.name.casefold():
                matches.add(channel.id)
        return frozenset(matches)

    @staticmethod
    def _autocomplete_channel_name(interaction: discord.Interaction, channel_id: int) -> str:
        guild = interaction.guild
        channel = guild.get_channel(channel_id) if guild is not None else None
        name = getattr(channel, "name", None)
        return name if isinstance(name, str) else ""

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
                created = await self._recurring_creation_service(session).create(
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

    def _detail_edit_channel_id(
        self,
        interaction: discord.Interaction,
        selected: object | None,
        *,
        current_channel_id: int,
    ) -> int:
        if selected is None:
            channel_id = current_channel_id
        else:
            if not isinstance(selected, (app_commands.AppCommandChannel, discord.TextChannel)):
                raise DetailEditModalStateError
            channel_id = getattr(selected, "id", None)
        if (
            isinstance(channel_id, bool)
            or not isinstance(channel_id, int)
            or not 1 <= channel_id <= 9_223_372_036_854_775_807
        ):
            raise DetailEditModalStateError
        guild = interaction.guild
        if guild is None or guild.id != self._configured_guild_id:
            raise DetailEditChannelError
        channel = guild.get_channel(channel_id)
        try:
            return self._validated_channel(interaction, channel, require_current=True)
        except ValueError as error:
            raise DetailEditChannelError from error

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
