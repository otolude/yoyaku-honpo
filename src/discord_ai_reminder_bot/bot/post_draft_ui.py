"""Unregistered Discord UI adapters for the ephemeral post-draft state machine."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

import discord

from discord_ai_reminder_bot.application.post_draft_ui_session import (
    PostDraftUIErrorCode,
    PostDraftUISessionController,
    PostDraftUISessionError,
)
from discord_ai_reminder_bot.application.post_draft_usage import PostDraftUsageReservation
from discord_ai_reminder_bot.domain.post_draft_generation import (
    MAX_GENERATED_POST_CHARACTERS,
    MAX_KEY_POINTS_CHARACTERS,
    MAX_PURPOSE_CHARACTERS,
    GeneratedPostDraft,
    PostDraftGenerationRequest,
    PostLength,
    PostTone,
)

MODE_MANUAL_CUSTOM_ID = "post_draft_mode_manual"
MODE_AI_CUSTOM_ID = "post_draft_mode_ai"
CANCEL_CUSTOM_ID = "post_draft_cancel"
TONE_CUSTOM_ID = "post_draft_tone"
LENGTH_CUSTOM_ID = "post_draft_length"
OPEN_AI_INPUT_CUSTOM_ID = "post_draft_open_ai_input"
EDIT_CUSTOM_ID = "post_draft_edit"
REGENERATE_CUSTOM_ID = "post_draft_regenerate"
ACCEPT_CUSTOM_ID = "post_draft_accept"

_LOGGER = logging.getLogger(__name__)

_SCHEDULE_TYPE_CANCEL_SUCCEEDED = "schedule_type_cancel_succeeded"
_SCHEDULE_TYPE_CANCEL_DEFER_FAILED = "schedule_type_cancel_defer_failed"
_SCHEDULE_TYPE_CANCEL_CLAIM_FAILED = "schedule_type_cancel_claim_failed"
_SCHEDULE_TYPE_CANCEL_CONTROLLER_FAILED = "schedule_type_cancel_controller_failed"
_SCHEDULE_TYPE_CANCEL_RENDER_FAILED = "schedule_type_cancel_render_failed"
_SCHEDULE_TYPE_CANCEL_RESPONSE_FAILED = "schedule_type_cancel_response_failed"
_SCHEDULE_TYPE_CANCEL_AUTHORIZATION_REJECTED = "schedule_type_cancel_authorization_rejected"
_SCHEDULE_TYPE_CANCEL_ABORT_FAILED = "schedule_type_cancel_abort_failed"


class _CancelDeferFailed(Exception):
    """Detail-free dispatch signal: the failed transport must not be retried."""


AI_NOTICE = (
    "AIの文章は下書きです。内容を必ず確認してください。"
    "この本文を使用しても予約・投稿はされません。予約は後続画面で明示的に確定します。"
)
AI_DISABLED_NOTICE = "AI文章作成は現在準備中です。手入力をご利用ください。"
GENERATING_MESSAGE = "文章を作成しています…"
STALE_UI_MESSAGE = "この画面は古くなっています。現在の画面から操作してください。"
ACCEPTED_MESSAGE = (
    "本文を採用しました。まだ予約・投稿はされていません。後続画面で予約を確定してください。"
)
SCHEDULE_TYPE_SELECTION_MESSAGE = "本文を採用しました。まだ予約・投稿はされていません。"
SCHEDULE_HANDOFF_ERROR_MESSAGE = "予約設定画面を開始できませんでした。最初からやり直してください。"
POST_DRAFT_SCHEDULE_CREATED_MESSAGE = "予約を作成しました。"
POST_DRAFT_SCHEDULE_ALREADY_CREATED_MESSAGE = "この操作の予約はすでに作成されています。"
POST_DRAFT_SCHEDULE_CONFLICT_MESSAGE = (
    "予約を確定できませんでした。入力内容を確認し、文章作成からやり直してください。"
)
POST_DRAFT_SCHEDULE_UNKNOWN_MESSAGE = (
    "予約結果を確認できませんでした。重複を避けるため再実行していません。"
    "予約一覧で登録状況を確認してください。"
)

_ERROR_MESSAGES = {
    PostDraftUIErrorCode.DISABLED: "AI文章作成は現在利用できません。手入力をご利用ください。",
    PostDraftUIErrorCode.UNAVAILABLE: (
        "AI文章作成に一時的に接続できません。時間を置くか、手入力をご利用ください。"
    ),
    PostDraftUIErrorCode.TIMEOUT: (
        "AI文章作成が時間内に完了しませんでした。もう一度試すか、手入力をご利用ください。"
    ),
    PostDraftUIErrorCode.INVALID_RESPONSE: (
        "安全に使用できる文章を作成できませんでした。入力を見直して、もう一度お試しください。"
    ),
    PostDraftUIErrorCode.UNKNOWN: "AI文章作成を現在利用できません。手入力をご利用ください。",
    PostDraftUIErrorCode.ALREADY_RESERVED: (
        "この文章作成操作はすでに処理されています。最初からやり直すか、手入力をご利用ください。"
    ),
    PostDraftUIErrorCode.USER_RATE_LIMITED: (
        "短時間の利用回数上限に達しました。時間を置くか、手入力をご利用ください。"
    ),
    PostDraftUIErrorCode.GUILD_RATE_LIMITED: (
        "現在AI文章作成の利用上限に達しています。手入力をご利用ください。"
    ),
    PostDraftUIErrorCode.GLOBAL_DAILY_EXHAUSTED: (
        "現在AI文章作成の利用上限に達しています。手入力をご利用ください。"
    ),
    PostDraftUIErrorCode.GLOBAL_MONTHLY_EXHAUSTED: (
        "現在AI文章作成の利用上限に達しています。手入力をご利用ください。"
    ),
    PostDraftUIErrorCode.GLOBAL_COST_EXHAUSTED: (
        "現在AI文章作成の利用上限に達しています。手入力をご利用ください。"
    ),
    PostDraftUIErrorCode.USAGE_UNAVAILABLE: (
        "AI文章作成を現在利用できません。手入力をご利用ください。"
    ),
    PostDraftUIErrorCode.INVALID_TRANSITION: (
        "この操作は現在実行できません。表示を確認してやり直してください。"
    ),
    PostDraftUIErrorCode.NOT_OWNER: "この操作は、作成を開始した本人だけが実行できます。",
    PostDraftUIErrorCode.EXPIRED: "操作の有効時間が切れました。最初からやり直してください。",
    PostDraftUIErrorCode.CANCELLED: "文章作成をキャンセルしました。",
}


def post_draft_ui_error_message(code: PostDraftUIErrorCode) -> str:
    if not isinstance(code, PostDraftUIErrorCode):
        return _ERROR_MESSAGES[PostDraftUIErrorCode.UNKNOWN]
    return _ERROR_MESSAGES[code]


def _validated_timeout(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError("invalid post draft Discord UI timeout")
    return float(value)


class PostDraftDiscordUI:
    """Discord-only presentation adapter retaining no interaction or persistence resource."""

    __slots__ = (
        "_active_component",
        "_now",
        "_pending_component",
        "_pending_lease",
        "_pending_source",
        "_reservation_factory",
        "_transition_generation",
        "_ui_generation",
        "_ui_lock",
        "controller",
        "length",
        "schedule_composition",
        "schedule_scope",
        "timeout_seconds",
        "tone",
    )

    def __init__(
        self,
        *,
        controller: PostDraftUISessionController,
        now: Callable[[], datetime],
        reservation_factory: Callable[[datetime], PostDraftUsageReservation],
        timeout_seconds: object,
        schedule_scope: object,
        schedule_composition: object,
    ) -> None:
        if not isinstance(controller, PostDraftUISessionController):
            raise TypeError("invalid post draft Discord UI")
        timeout = _validated_timeout(timeout_seconds)
        if not callable(now) or not callable(reservation_factory):
            raise TypeError("invalid post draft Discord UI dependency")
        if schedule_scope is None or schedule_composition is None:
            raise TypeError("invalid post draft schedule dependency")
        self.controller = controller
        self._active_component: _PostDraftView | _PostDraftModal | None = None
        self._pending_component: _PostDraftView | _PostDraftModal | None = None
        self._pending_source: _PostDraftView | _PostDraftModal | None = None
        self._pending_lease: int | None = None
        self._transition_generation = 0
        self._ui_generation = 0
        self._ui_lock = asyncio.Lock()
        self._now = now
        self._reservation_factory = reservation_factory
        self.timeout_seconds = timeout
        self.tone = PostTone.POLITE
        self.length = PostLength.STANDARD
        self.schedule_scope = schedule_scope
        self.schedule_composition = schedule_composition

    def __repr__(self) -> str:
        return "PostDraftDiscordUI()"

    def ids(self, interaction: discord.Interaction) -> tuple[object, object]:
        user = getattr(interaction, "user", None)
        return getattr(user, "id", None), getattr(interaction, "guild_id", None)

    @staticmethod
    def _stop_component(component: _PostDraftView | _PostDraftModal) -> None:
        if isinstance(component, discord.ui.View):
            for child in component.children:
                if hasattr(child, "disabled"):
                    child.disabled = True
        component.stop()

    def activate_initial(self, component: _PostDraftView) -> None:
        if self._active_component is not None:
            self._stop_component(self._active_component)
        self._ui_generation += 1
        component._ui_token = self._ui_generation
        component._consumed = False
        self._active_component = component

    async def claim(self, component: _PostDraftView | _PostDraftModal, *, consume: bool) -> bool:
        async with self._ui_lock:
            if (
                self._active_component is None
                and component._ui_token is None
                and not component._consumed
            ):
                self._ui_generation += 1
                component._ui_token = self._ui_generation
                self._active_component = component
            active = (
                self._active_component is component
                and component._ui_token == self._ui_generation
                and not component._consumed
            )
            if active and consume:
                component._consumed = True
            return active

    async def update_selection(
        self,
        component: _PostDraftView,
        *,
        tone: PostTone | None = None,
        length: PostLength | None = None,
    ) -> bool:
        async with self._ui_lock:
            active = (
                self._active_component is component
                and component._ui_token == self._ui_generation
                and not component._consumed
            )
            if not active:
                return False
            if tone is not None:
                self.tone = tone
            if length is not None:
                self.length = length
            return True

    async def deactivate(self, component: _PostDraftView | _PostDraftModal) -> bool:
        async with self._ui_lock:
            if (
                self._active_component is not component
                or component._ui_token != self._ui_generation
            ):
                return False
            component._consumed = True
            self._stop_component(component)
            self._active_component = None
            return True

    async def begin_transition(self, source: _PostDraftView | _PostDraftModal | None) -> int | None:
        async with self._ui_lock:
            if self._pending_lease is not None:
                return None
            if source is not None:
                if (
                    self._active_component is None
                    and source._ui_token is None
                    and not source._consumed
                ):
                    self._ui_generation += 1
                    source._ui_token = self._ui_generation
                    self._active_component = source
                if (
                    self._active_component is not source
                    or source._ui_token != self._ui_generation
                    or source._consumed
                ):
                    return None
                source._consumed = True
            self._transition_generation += 1
            lease = self._transition_generation
            self._pending_lease = lease
            self._pending_source = source
            self._pending_component = None
            return lease

    async def set_pending(self, lease: int, component: _PostDraftView | _PostDraftModal) -> bool:
        async with self._ui_lock:
            if self._pending_lease != lease or self._pending_component is not None:
                self._stop_component(component)
                component._consumed = True
                return False
            component._consumed = True
            self._pending_component = component
            return True

    async def commit_transition(self, lease: int) -> bool:
        async with self._ui_lock:
            if self._pending_lease != lease:
                return False
            source = self._pending_source
            pending = self._pending_component
            if source is not None:
                self._stop_component(source)
            if pending is None:
                self._active_component = None
            else:
                self._ui_generation += 1
                pending._ui_token = self._ui_generation
                pending._consumed = False
                self._active_component = pending
            self._pending_lease = None
            self._pending_source = None
            self._pending_component = None
            return True

    async def release_transition(self, lease: int) -> None:
        async with self._ui_lock:
            if self._pending_lease != lease:
                return
            source = self._pending_source
            pending = self._pending_component
            if pending is not None:
                pending._consumed = True
                self._stop_component(pending)
            if source is not None and self._active_component is source:
                source._consumed = False
            self._pending_lease = None
            self._pending_source = None
            self._pending_component = None

    async def abort_transition(self, lease: int) -> bool:
        async with self._ui_lock:
            if self._pending_lease != lease:
                return False
            source = self._pending_source
            pending = self._pending_component
            if source is not None:
                source._consumed = True
                self._stop_component(source)
            if pending is not None:
                pending._consumed = True
                self._stop_component(pending)
            if self._active_component is source:
                self._active_component = None
            self._pending_lease = None
            self._pending_source = None
            self._pending_component = None
            return True

    async def abort_transport(self, lease: int) -> None:
        if not await self.abort_transition(lease):
            return
        session = self.controller.session
        try:
            await self.controller.cancel(
                owner_user_id=session.owner_user_id,
                guild_id=session.guild_id,
                now=self._now(),
            )
        except PostDraftUISessionError:
            pass

    async def interaction_allowed(self, interaction: discord.Interaction) -> bool:
        user_id, guild_id = self.ids(interaction)
        session = self.controller.session
        if (
            isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or isinstance(guild_id, bool)
            or not isinstance(guild_id, int)
            or user_id != session.owner_user_id
            or guild_id != session.guild_id
        ):
            await _send_initial(
                interaction,
                content=post_draft_ui_error_message(PostDraftUIErrorCode.NOT_OWNER),
            )
            return False
        return True

    async def choose_manual(
        self, interaction: discord.Interaction, component: _PostDraftView | None = None
    ) -> None:
        lease = await self.begin_transition(component)
        if lease is None:
            await _respond_stale(interaction)
            return
        user_id, guild_id = self.ids(interaction)
        try:
            await self.controller.choose_manual(
                owner_user_id=user_id, guild_id=guild_id, now=self._now()
            )
            modal = PostDraftManualInputModal(ui=self, timeout=self.timeout_seconds)
            if not await self.set_pending(lease, modal):
                return
        except PostDraftUISessionError as error:
            await self.release_transition(lease)
            await _respond_error(interaction, error.code)
            return
        failed = False
        try:
            await interaction.response.send_modal(modal)
        except Exception:  # noqa: BLE001 - transport details remain private
            failed = True
        if failed:
            await self.abort_transport(lease)
            return
        await self.commit_transition(lease)

    async def choose_ai(
        self, interaction: discord.Interaction, component: _PostDraftView | None = None
    ) -> None:
        lease = await self.begin_transition(component)
        if lease is None:
            await _respond_stale(interaction)
            return
        user_id, guild_id = self.ids(interaction)
        try:
            await self.controller.choose_ai(
                owner_user_id=user_id, guild_id=guild_id, now=self._now()
            )
            view = PostDraftAISettingsView(ui=self, timeout=self.timeout_seconds)
            if not await self.set_pending(lease, view):
                return
        except PostDraftUISessionError as error:
            await self.release_transition(lease)
            await _respond_error(interaction, error.code)
            return
        failed = False
        try:
            await interaction.response.edit_message(
                content=AI_NOTICE,
                embed=None,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:  # noqa: BLE001
            failed = True
        if failed:
            await self.abort_transport(lease)
            return
        await self.commit_transition(lease)

    async def cancel(
        self, interaction: discord.Interaction, component: _PostDraftView | None = None
    ) -> None:
        if interaction.response.is_done():
            return
        defer_failed = False
        try:
            # A component message update acknowledges without creating a new message.
            await interaction.response.defer(thinking=False)
        except Exception:  # noqa: BLE001 - retain neither transport details nor traceback
            _LOGGER.warning("cancel_defer_failed", extra={"stage": "defer"})
            defer_failed = True
        if defer_failed:
            # Raise outside the handler so the original exception is not chained.
            raise _CancelDeferFailed
        if component is not None and not await self.claim(component, consume=True):
            await _render_cancel(interaction, content=STALE_UI_MESSAGE, clear_view=False)
            return
        user_id, guild_id = self.ids(interaction)
        try:
            await self.controller.cancel(owner_user_id=user_id, guild_id=guild_id, now=self._now())
            if component is not None:
                await self.deactivate(component)
        except PostDraftUISessionError as error:
            _LOGGER.warning("cancel_controller_failed", extra={"stage": "controller"})
            await _respond_error(interaction, error.code)
            return
        except Exception:  # noqa: BLE001 - report only a fixed stage
            _LOGGER.warning("cancel_controller_failed", extra={"stage": "controller"})
            await _respond_error(interaction, PostDraftUIErrorCode.UNKNOWN)
            return
        await _render_cancel(
            interaction, content=post_draft_ui_error_message(PostDraftUIErrorCode.CANCELLED)
        )

    async def expire(self) -> None:
        session = self.controller.session
        try:
            await self.controller.expire(
                owner_user_id=session.owner_user_id,
                guild_id=session.guild_id,
                now=session.expires_at,
            )
        except PostDraftUISessionError:
            pass

    async def generate(
        self,
        interaction: discord.Interaction,
        *,
        purpose: str,
        key_points: str,
        request: PostDraftGenerationRequest | None = None,
        component_interaction: bool = False,
        lease: int | None = None,
    ) -> None:
        active_lease = lease if lease is not None else await self.begin_transition(None)
        if active_lease is None:
            await _respond_stale(interaction)
            return
        failed = False
        try:
            if component_interaction:
                await interaction.response.defer()
            else:
                await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception:  # noqa: BLE001
            failed = True
        if failed:
            await self.abort_transport(active_lease)
            return
        generating_view = PostDraftGeneratingView(ui=self, timeout=self.timeout_seconds)
        if not await self.set_pending(active_lease, generating_view):
            return
        failed = False
        try:
            await interaction.edit_original_response(
                content=GENERATING_MESSAGE,
                embed=None,
                view=generating_view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:  # noqa: BLE001
            failed = True
        if failed:
            await self.abort_transport(active_lease)
            return
        if not await self.commit_transition(active_lease):
            return
        user_id, guild_id = self.ids(interaction)
        failure = PostDraftUIErrorCode.UNKNOWN
        try:
            generated_request = request or PostDraftGenerationRequest(
                purpose=purpose,
                key_points=key_points,
                tone=self.tone,
                length=self.length,
            )
            instant = self._now()
            draft = await self.controller.generate(
                request=generated_request,
                reservation=self._reservation_factory(instant),
                owner_user_id=user_id,
                guild_id=guild_id,
                now=instant,
            )
        except asyncio.CancelledError:
            if await self.deactivate(generating_view):
                try:
                    await interaction.edit_original_response(
                        content=post_draft_ui_error_message(PostDraftUIErrorCode.CANCELLED),
                        embed=None,
                        view=None,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except Exception:  # noqa: BLE001, S110 - cancellation remains authoritative
                    pass
            raise
        except PostDraftUISessionError as error:
            failure = error.code
        except TypeError, ValueError:
            failure = PostDraftUIErrorCode.INVALID_RESPONSE
        except Exception:  # noqa: BLE001
            failure = PostDraftUIErrorCode.UNKNOWN
        else:
            preview = PostDraftPreviewView(ui=self, timeout=self.timeout_seconds)
            preview_lease = await self.begin_transition(generating_view)
            if preview_lease is None or not await self.set_pending(preview_lease, preview):
                return
            failed = False
            try:
                await _edit_deferred_preview(interaction, view=preview, draft=draft)
            except Exception:  # noqa: BLE001
                failed = True
            if failed:
                await self.abort_transport(preview_lease)
                return
            await self.commit_transition(preview_lease)
            return
        error_lease = await self.begin_transition(generating_view)
        if error_lease is None:
            return
        failed = False
        try:
            await interaction.edit_original_response(
                content=post_draft_ui_error_message(failure),
                embed=None,
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:  # noqa: BLE001
            failed = True
        if failed:
            await self.abort_transport(error_lease)
            return
        await self.commit_transition(error_lease)

    async def timeout_view(self, view: discord.ui.View) -> None:
        if not isinstance(view, _PostDraftView) or not await self.claim(view, consume=True):
            return
        await self.deactivate(view)
        await self.expire()

    async def timeout_modal(self, modal: _PostDraftModal) -> None:
        if not await self.claim(modal, consume=True):
            return
        await self.deactivate(modal)
        await self.expire()


class _PostDraftView(discord.ui.View):
    def __init__(self, *, ui: PostDraftDiscordUI, timeout: float) -> None:
        super().__init__(timeout=_validated_timeout(timeout))
        self.ui = ui
        self._ui_token: int | None = None
        self._consumed = False

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return await self.ui.interaction_allowed(interaction)

    async def on_timeout(self) -> None:
        await self.ui.timeout_view(self)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[object], /
    ) -> None:
        defer_failed = isinstance(error, _CancelDeferFailed)
        del error, item
        _LOGGER.warning("view_callback_failed", extra={"stage": "callback"})
        if defer_failed:
            return
        await _respond_error(interaction, PostDraftUIErrorCode.UNKNOWN)


class PostDraftModeView(_PostDraftView):
    def __init__(self, *, ui: PostDraftDiscordUI, timeout: float) -> None:
        super().__init__(ui=ui, timeout=timeout)
        manual = discord.ui.Button(
            label="手入力", style=discord.ButtonStyle.secondary, custom_id=MODE_MANUAL_CUSTOM_ID
        )
        manual.callback = self._choose_manual
        self.add_item(manual)
        ai = discord.ui.Button(
            label="AIで作成", style=discord.ButtonStyle.primary, custom_id=MODE_AI_CUSTOM_ID
        )
        ai.callback = self._choose_ai
        self.add_item(ai)
        cancel = discord.ui.Button(
            label="キャンセル", style=discord.ButtonStyle.danger, custom_id=CANCEL_CUSTOM_ID
        )
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def _choose_manual(self, interaction: discord.Interaction) -> None:
        await self.ui.choose_manual(interaction, self)

    async def _choose_ai(self, interaction: discord.Interaction) -> None:
        await self.ui.choose_ai(interaction, self)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await self.ui.cancel(interaction, self)


class PostDraftAISettingsView(_PostDraftView):
    def __init__(self, *, ui: PostDraftDiscordUI, timeout: float) -> None:
        super().__init__(ui=ui, timeout=timeout)
        tone = discord.ui.Select(
            placeholder="文体を選択",
            custom_id=TONE_CUSTOM_ID,
            options=[
                discord.SelectOption(label="丁寧", value=PostTone.POLITE.value, default=True),
                discord.SelectOption(label="親しみやすい", value=PostTone.FRIENDLY.value),
                discord.SelectOption(label="簡潔", value=PostTone.CONCISE.value),
            ],
        )
        tone.callback = self._select_tone
        self.add_item(tone)
        length = discord.ui.Select(
            placeholder="長さを選択",
            custom_id=LENGTH_CUSTOM_ID,
            options=[
                discord.SelectOption(label="短め", value=PostLength.SHORT.value),
                discord.SelectOption(label="標準", value=PostLength.STANDARD.value, default=True),
                discord.SelectOption(label="長め", value=PostLength.LONG.value),
            ],
        )
        length.callback = self._select_length
        self.add_item(length)
        enter = discord.ui.Button(
            label="内容を入力",
            style=discord.ButtonStyle.primary,
            custom_id=OPEN_AI_INPUT_CUSTOM_ID,
        )
        enter.callback = self._open_input
        self.add_item(enter)
        cancel = discord.ui.Button(
            label="キャンセル", style=discord.ButtonStyle.danger, custom_id=CANCEL_CUSTOM_ID
        )
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def _select_tone(self, interaction: discord.Interaction) -> None:
        select = cast(discord.ui.Select[object], item_by_id(self, TONE_CUSTOM_ID))
        try:
            if not await self.ui.update_selection(self, tone=PostTone(select.values[0])):
                await _respond_stale(interaction)
                return
            await interaction.response.defer()
        except TypeError, ValueError, IndexError:
            await _respond_error(interaction, PostDraftUIErrorCode.INVALID_TRANSITION)
        except Exception:  # noqa: BLE001
            lease = await self.ui.begin_transition(self)
            if lease is not None:
                await self.ui.abort_transport(lease)

    async def _select_length(self, interaction: discord.Interaction) -> None:
        select = cast(discord.ui.Select[object], item_by_id(self, LENGTH_CUSTOM_ID))
        try:
            if not await self.ui.update_selection(self, length=PostLength(select.values[0])):
                await _respond_stale(interaction)
                return
            await interaction.response.defer()
        except TypeError, ValueError, IndexError:
            await _respond_error(interaction, PostDraftUIErrorCode.INVALID_TRANSITION)
        except Exception:  # noqa: BLE001
            lease = await self.ui.begin_transition(self)
            if lease is not None:
                await self.ui.abort_transport(lease)

    async def _open_input(self, interaction: discord.Interaction) -> None:
        lease = await self.ui.begin_transition(self)
        if lease is None:
            await _respond_stale(interaction)
            return
        modal = PostDraftAIInputModal(ui=self.ui, timeout=self.ui.timeout_seconds)
        if not await self.ui.set_pending(lease, modal):
            return
        failed = False
        try:
            await interaction.response.send_modal(modal)
        except Exception:  # noqa: BLE001
            failed = True
        if failed:
            await self.ui.abort_transport(lease)
            return
        await self.ui.commit_transition(lease)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await self.ui.cancel(interaction, self)


class PostDraftPreviewView(_PostDraftView):
    def __init__(self, *, ui: PostDraftDiscordUI, timeout: float) -> None:
        super().__init__(ui=ui, timeout=timeout)
        actions = (
            ("編集", discord.ButtonStyle.secondary, EDIT_CUSTOM_ID, self._edit),
            ("もう一度作成", discord.ButtonStyle.primary, REGENERATE_CUSTOM_ID, self._regenerate),
            ("この本文を使用", discord.ButtonStyle.success, ACCEPT_CUSTOM_ID, self._accept),
            ("キャンセル", discord.ButtonStyle.danger, CANCEL_CUSTOM_ID, self._cancel),
        )
        for label, style, custom_id, callback in actions:
            button = discord.ui.Button(label=label, style=style, custom_id=custom_id)
            button.callback = callback
            self.add_item(button)

    async def _edit(self, interaction: discord.Interaction) -> None:
        lease = await self.ui.begin_transition(self)
        if lease is None:
            await _respond_stale(interaction)
            return
        user_id, guild_id = self.ui.ids(interaction)
        try:
            await self.ui.controller.begin_edit(
                owner_user_id=user_id, guild_id=guild_id, now=self.ui._now()
            )
            draft = self.ui.controller.session.current_draft()
            if draft is None:
                raise PostDraftUISessionError(PostDraftUIErrorCode.INVALID_RESPONSE)
            modal = PostDraftEditModal(
                ui=self.ui, timeout=self.ui.timeout_seconds, current_body=draft.value
            )
            if not await self.ui.set_pending(lease, modal):
                return
        except PostDraftUISessionError as error:
            await self.ui.release_transition(lease)
            await _respond_error(interaction, error.code)
            return
        failed = False
        try:
            await interaction.response.send_modal(modal)
        except Exception:  # noqa: BLE001
            failed = True
        if failed:
            await self.ui.abort_transport(lease)
            return
        await self.ui.commit_transition(lease)

    async def _regenerate(self, interaction: discord.Interaction) -> None:
        lease = await self.ui.begin_transition(self)
        if lease is None:
            await _respond_stale(interaction)
            return
        request = self.ui.controller.session.request
        if request is None:
            await _respond_error(interaction, PostDraftUIErrorCode.INVALID_TRANSITION)
            return
        await self.ui.generate(
            interaction,
            purpose=request.purpose,
            key_points=request.key_points,
            request=request,
            component_interaction=True,
            lease=lease,
        )

    async def _accept(self, interaction: discord.Interaction) -> None:
        if not _schedule_handoff_scope_matches(interaction, self.ui.schedule_scope):
            await _respond_error(interaction, PostDraftUIErrorCode.NOT_OWNER)
            return
        if getattr(interaction.response, "is_done", lambda: False)():
            return
        try:
            await interaction.response.defer(thinking=False)
        except Exception:  # noqa: BLE001 - an ambiguous defer is never retried
            await _abort_schedule_handoff(
                ui=self.ui,
                source=self,
                candidate=None,
                schedule_controller=None,
                event="schedule_handoff_defer_failed",
                cancel_post_draft=True,
            )
            return
        if not await self.ui.claim(self, consume=True):
            return
        user_id, guild_id = self.ui.ids(interaction)
        try:
            await self.ui.controller.accept(
                owner_user_id=user_id, guild_id=guild_id, now=self.ui._now()
            )
        except Exception:  # noqa: BLE001 - acceptance details remain private
            await _abort_schedule_handoff(
                ui=self.ui,
                source=self,
                candidate=None,
                schedule_controller=None,
                event="schedule_handoff_accept_failed",
            )
            await _respond_schedule_handoff_error(interaction)
            return
        try:
            accepted_draft = self.ui.controller.accepted_draft()
        except Exception:  # noqa: BLE001 - accepted draft details remain private
            await _abort_schedule_handoff(
                ui=self.ui,
                source=self,
                candidate=None,
                schedule_controller=None,
                event="schedule_handoff_accept_failed",
            )
            await _respond_schedule_handoff_error(interaction)
            return
        schedule_controller: object | None = None
        try:
            schedule_controller = self.ui.schedule_composition.start(
                scope=self.ui.schedule_scope,
                accepted_draft=accepted_draft,
            )
        except Exception:  # noqa: BLE001 - composition details remain private
            await _abort_schedule_handoff(
                ui=self.ui,
                source=self,
                candidate=None,
                schedule_controller=None,
                event="schedule_handoff_composition_failed",
            )
            await _respond_schedule_handoff_error(interaction)
            return
        candidate: object | None = None
        try:
            candidate = PostDraftScheduleTypeView(
                controller=schedule_controller,
                now=self.ui._now,
                timeout=self.ui.timeout_seconds,
            )
            embed = _schedule_type_handoff_embed(accepted_draft)
        except Exception:  # noqa: BLE001 - render details remain private
            await _abort_schedule_handoff(
                ui=self.ui,
                source=self,
                candidate=candidate,
                schedule_controller=schedule_controller,
                event="schedule_handoff_render_failed",
            )
            return
        try:
            await interaction.edit_original_response(
                content=SCHEDULE_TYPE_SELECTION_MESSAGE,
                embed=embed,
                view=candidate,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await self.ui.deactivate(self)
        except Exception:  # noqa: BLE001 - an ambiguous response is never retried
            await _abort_schedule_handoff(
                ui=self.ui,
                source=self,
                candidate=candidate,
                schedule_controller=schedule_controller,
                event="schedule_handoff_response_failed",
            )

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await self.ui.cancel(interaction, self)


class PostDraftGeneratingView(_PostDraftView):
    def __init__(self, *, ui: PostDraftDiscordUI, timeout: float) -> None:
        super().__init__(ui=ui, timeout=timeout)
        cancel = discord.ui.Button(
            label="キャンセル", style=discord.ButtonStyle.danger, custom_id=CANCEL_CUSTOM_ID
        )
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await self.ui.cancel(interaction, self)


class _PostDraftModal(discord.ui.Modal):
    def __init__(
        self, *, ui: PostDraftDiscordUI, title: str, custom_id: str, timeout: float
    ) -> None:
        super().__init__(title=title, custom_id=custom_id, timeout=_validated_timeout(timeout))
        self.ui = ui
        self._ui_token: int | None = None
        self._consumed = False

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return await self.ui.interaction_allowed(interaction)

    async def on_timeout(self) -> None:
        await self.ui.timeout_modal(self)

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        del error
        await _respond_error(interaction, PostDraftUIErrorCode.UNKNOWN)


class PostDraftAIInputModal(_PostDraftModal):
    def __init__(self, *, ui: PostDraftDiscordUI, timeout: float) -> None:
        super().__init__(
            ui=ui, title="AI文章の内容を入力", custom_id="post_draft_ai_input", timeout=timeout
        )
        self.purpose = discord.ui.TextInput(
            custom_id="post_draft_purpose",
            required=True,
            min_length=1,
            max_length=MAX_PURPOSE_CHARACTERS,
        )
        self.key_points = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            custom_id="post_draft_key_points",
            required=True,
            min_length=1,
            max_length=MAX_KEY_POINTS_CHARACTERS,
        )
        self.purpose_label = discord.ui.Label(text="文章の目的", component=self.purpose)
        self.key_points_label = discord.ui.Label(text="含めたい要点", component=self.key_points)
        self.add_item(self.purpose_label)
        self.add_item(self.key_points_label)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        lease = await self.ui.begin_transition(self)
        if lease is None:
            await _respond_stale(interaction)
            return
        await self.ui.generate(
            interaction,
            purpose=self.purpose.value,
            key_points=self.key_points.value,
            lease=lease,
        )


class PostDraftManualInputModal(_PostDraftModal):
    def __init__(self, *, ui: PostDraftDiscordUI, timeout: float) -> None:
        super().__init__(
            ui=ui, title="投稿本文を入力", custom_id="post_draft_manual_input", timeout=timeout
        )
        self.body = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            custom_id="post_draft_manual_body",
            required=True,
            min_length=1,
            max_length=MAX_GENERATED_POST_CHARACTERS,
        )
        self.body_label = discord.ui.Label(text="本文", component=self.body)
        self.add_item(self.body_label)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        lease = await self.ui.begin_transition(self)
        if lease is None:
            await _respond_stale(interaction)
            return
        user_id, guild_id = self.ui.ids(interaction)
        try:
            draft = await self.ui.controller.submit_manual(
                text=self.body.value,
                owner_user_id=user_id,
                guild_id=guild_id,
                now=self.ui._now(),
            )
            preview = PostDraftPreviewView(ui=self.ui, timeout=self.ui.timeout_seconds)
            if not await self.ui.set_pending(lease, preview):
                return
        except PostDraftUISessionError as error:
            await self.ui.release_transition(lease)
            await _respond_error(interaction, error.code)
            return
        failed = False
        try:
            await _send_initial(
                interaction,
                embed=_preview_embed(draft),
                view=preview,
            )
        except Exception:  # noqa: BLE001
            failed = True
        if failed:
            await self.ui.abort_transport(lease)
            return
        await self.ui.commit_transition(lease)


class PostDraftEditModal(_PostDraftModal):
    def __init__(self, *, ui: PostDraftDiscordUI, timeout: float, current_body: str) -> None:
        super().__init__(
            ui=ui, title="投稿本文を編集", custom_id="post_draft_edit_body", timeout=timeout
        )
        self.body = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            custom_id="post_draft_edited_body",
            default=current_body,
            required=True,
            min_length=1,
            max_length=MAX_GENERATED_POST_CHARACTERS,
        )
        self.body_label = discord.ui.Label(text="本文", component=self.body)
        self.add_item(self.body_label)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        lease = await self.ui.begin_transition(self)
        if lease is None:
            await _respond_stale(interaction)
            return
        user_id, guild_id = self.ui.ids(interaction)
        try:
            draft = await self.ui.controller.confirm_edit(
                text=self.body.value,
                owner_user_id=user_id,
                guild_id=guild_id,
                now=self.ui._now(),
            )
            preview = PostDraftPreviewView(ui=self.ui, timeout=self.ui.timeout_seconds)
            if not await self.ui.set_pending(lease, preview):
                return
        except PostDraftUISessionError as error:
            await self.ui.release_transition(lease)
            await _respond_error(interaction, error.code)
            return
        failed = False
        try:
            await _send_initial(
                interaction,
                embed=_preview_embed(draft),
                view=preview,
            )
        except Exception:  # noqa: BLE001
            failed = True
        if failed:
            await self.ui.abort_transport(lease)
            return
        await self.ui.commit_transition(lease)


def create_post_draft_mode_view(*, ui: PostDraftDiscordUI) -> PostDraftModeView:
    view = PostDraftModeView(ui=ui, timeout=ui.timeout_seconds)
    ui.activate_initial(view)
    return view


def create_disabled_post_draft_mode_view(*, ui: PostDraftDiscordUI) -> PostDraftModeView:
    """Create the production entry view while the provider gate remains closed."""
    view = create_post_draft_mode_view(ui=ui)
    ai = item_by_id(view, MODE_AI_CUSTOM_ID)
    if isinstance(ai, discord.ui.Button):
        ai.label = "AIで作成（準備中）"
        ai.disabled = True
    return view


async def send_disabled_post_draft_mode(
    interaction: discord.Interaction, *, ui: PostDraftDiscordUI
) -> None:
    view = create_disabled_post_draft_mode_view(ui=ui)
    failed = False
    try:
        await _send_initial(interaction, content=AI_DISABLED_NOTICE, view=view)
    except Exception:  # noqa: BLE001
        failed = True
    if failed:
        lease = await ui.begin_transition(view)
        if lease is not None:
            await ui.abort_transport(lease)


async def send_post_draft_mode(interaction: discord.Interaction, *, ui: PostDraftDiscordUI) -> None:
    """Send the unregistered entry UI only as an ephemeral response."""
    view = create_post_draft_mode_view(ui=ui)
    failed = False
    try:
        await _send_initial(
            interaction,
            content=(
                "作成方法を選んでください。AI文章は確認が必要な下書きです。"
                "AIが利用できない場合は手入力をご利用ください。"
            ),
            view=view,
        )
    except Exception:  # noqa: BLE001
        failed = True
    if failed:
        lease = await ui.begin_transition(view)
        if lease is not None:
            await ui.abort_transport(lease)


def item_by_id(view: discord.ui.View, custom_id: str) -> discord.ui.Item[object]:
    return next(item for item in view.children if item.custom_id == custom_id)


_PREVIEW_URL_SCHEMES = ("http://", "https://")
_PREVIEW_URL_PUNCTUATION = frozenset("-._~:/?#@!$&'()*+,;=%")
_PREVIEW_MARKDOWN_CHARACTERS = frozenset("\\*_~`|><#[]-.")
_PREVIEW_MENTION_PREFIXES = ("<@!", "<@&", "<@", "<#")


def _preview_url_scheme_end(value: str, start: int) -> int | None:
    for scheme in _PREVIEW_URL_SCHEMES:
        if start + len(scheme) > len(value):
            continue
        for offset, expected in enumerate(scheme):
            actual = value[start + offset]
            if "A" <= actual <= "Z":
                actual = chr(ord(actual) + ord("a") - ord("A"))
            if actual != expected:
                break
        else:
            return start + len(scheme)
    return None


def _is_preview_url_character(character: str) -> bool:
    return character.isascii() and (character.isalnum() or character in _PREVIEW_URL_PUNCTUATION)


def _preview_url_end(value: str, start: int) -> int | None:
    scheme_end = _preview_url_scheme_end(value, start)
    if scheme_end is None:
        return None
    cursor = scheme_end
    while (
        cursor < len(value)
        and _is_preview_url_character(value[cursor])
        and value[cursor] not in "/?#"
    ):
        cursor += 1
    if cursor == scheme_end:
        return None
    while cursor < len(value) and _is_preview_url_character(value[cursor]):
        cursor += 1
    return cursor


def _preview_mention_end(value: str, start: int) -> int | None:
    prefix = next(
        (
            candidate
            for candidate in _PREVIEW_MENTION_PREFIXES
            if value.startswith(candidate, start)
        ),
        None,
    )
    if prefix is None:
        return None
    digits_start = start + len(prefix)
    cursor = digits_start
    while cursor < len(value) and "0" <= value[cursor] <= "9":
        cursor += 1
    if 15 <= cursor - digits_start <= 20 and cursor < len(value) and value[cursor] == ">":
        return cursor + 1
    return None


def _escape_preview_text(value: str) -> str:
    """Escape untrusted Preview text once without modifying ordinary URL spans."""
    parts: list[str] = []
    cursor = 0
    while cursor < len(value):
        mention_end = _preview_mention_end(value, cursor)
        if mention_end is not None:
            parts.append("<\u200b")
            parts.append(value[cursor + 1 : mention_end])
            cursor = mention_end
            continue

        url_end = _preview_url_end(value, cursor)
        if url_end is not None:
            parts.append(value[cursor:url_end])
            cursor = url_end
            continue

        character = value[cursor]
        if character in _PREVIEW_MARKDOWN_CHARACTERS:
            parts.append("\\")
        parts.append(character)
        cursor += 1
    return "".join(parts)


def _preview_embed(draft: GeneratedPostDraft) -> discord.Embed:
    return discord.Embed(
        description=_escape_preview_text(draft.value),
        colour=discord.Colour.blurple(),
    )


def _schedule_type_handoff_embed(draft: GeneratedPostDraft) -> discord.Embed:
    description = _escape_preview_text(draft.value)
    if len(description) > 2000:
        raise ValueError("schedule type selection exceeds embed limit")
    return discord.Embed(
        title="予約種別を選択",
        description=description,
        colour=discord.Colour.blurple(),
    )


def _schedule_handoff_scope_matches(interaction: discord.Interaction, scope: object) -> bool:
    user_id = getattr(getattr(interaction, "user", None), "id", None)
    guild_id = getattr(interaction, "guild_id", None)
    channel_id = getattr(interaction, "channel_id", None)
    channel = getattr(interaction, "channel", None)
    return (
        isinstance(user_id, int)
        and not isinstance(user_id, bool)
        and user_id == getattr(scope, "owner_user_id", None)
        and isinstance(guild_id, int)
        and not isinstance(guild_id, bool)
        and guild_id == getattr(scope, "guild_id", None)
        and isinstance(channel_id, int)
        and not isinstance(channel_id, bool)
        and channel_id == getattr(scope, "channel_id", None)
        and getattr(channel, "id", None) == channel_id
        and getattr(getattr(channel, "guild", None), "id", None) == guild_id
        and getattr(channel, "type", None) is discord.ChannelType.text
    )


async def _abort_schedule_handoff(
    *,
    ui: PostDraftDiscordUI,
    source: object,
    candidate: object | None,
    schedule_controller: object | None,
    event: str,
    cancel_post_draft: bool = False,
) -> None:
    _LOGGER.warning(event)
    abort_failed = False
    if cancel_post_draft:
        try:
            scope = ui.schedule_scope
            await ui.controller.cancel(
                owner_user_id=scope.owner_user_id,
                guild_id=scope.guild_id,
                now=ui._now(),
            )
        except Exception:  # noqa: BLE001 - the fixed event is the complete failure record
            abort_failed = True
    if schedule_controller is not None:
        try:
            schedule_controller.session.cancel()
        except Exception:  # noqa: BLE001 - the fixed event is the complete failure record
            abort_failed = True
    try:
        if not await ui.deactivate(source):
            ui._stop_component(source)
    except Exception:  # noqa: BLE001 - attempt every terminal cleanup step
        abort_failed = True
        try:
            ui._stop_component(source)
        except Exception:  # noqa: BLE001, S110 - report only the fixed abort event
            pass
    if candidate is not None:
        try:
            if hasattr(candidate, "_claimed"):
                candidate._claimed = True
            candidate.stop()
        except Exception:  # noqa: BLE001 - attempt every terminal cleanup step
            abort_failed = True
    if abort_failed:
        _LOGGER.warning("schedule_handoff_abort_failed")


async def _respond_schedule_handoff_error(interaction: discord.Interaction) -> None:
    try:
        await interaction.edit_original_response(
            content=SCHEDULE_HANDOFF_ERROR_MESSAGE,
            embed=None,
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:  # noqa: BLE001, S110 - an ambiguous response is never retried
        pass


def _schedule_confirmation_embed(controller: object) -> discord.Embed:
    snapshot = controller.snapshot()
    draft = controller.session.accepted_draft
    description = _escape_preview_text(draft.value)
    if len(description) > 2000:
        raise ValueError("schedule confirmation exceeds embed limit")
    return (
        discord.Embed(
            title="予約内容の確認",
            description=description,
            colour=discord.Colour.blurple(),
        )
        .add_field(name="予約種別", value=str(snapshot.schedule_type.value), inline=False)
        .add_field(name="タイムゾーン", value="Asia/Tokyo", inline=True)
        .add_field(name="重複予約", value="許可しない", inline=True)
    )


async def _schedule_guard(
    interaction: discord.Interaction, controller: object, expected: str
) -> bool:
    response = getattr(interaction, "response", None)
    if response is None or getattr(response, "is_done", lambda: False)():
        return False
    user_id = getattr(getattr(interaction, "user", None), "id", None)
    scope = getattr(getattr(controller, "session", None), "scope", None)
    channel = getattr(interaction, "channel", None)
    state = getattr(controller.snapshot(), "state", None)
    valid = (
        isinstance(user_id, int)
        and not isinstance(user_id, bool)
        and user_id > 0
        and scope is not None
        and user_id == scope.owner_user_id
        and getattr(interaction, "guild_id", None) == scope.guild_id
        and getattr(interaction, "channel_id", None) == scope.channel_id
        and getattr(channel, "id", None) == scope.channel_id
        and getattr(getattr(channel, "guild", None), "id", None) == scope.guild_id
        and getattr(channel, "type", None) is discord.ChannelType.text
        and getattr(state, "value", None) == expected
    )
    if not valid:
        await _respond_stale(interaction)
        return False
    return True


def _abort_schedule_edit(
    *,
    controller: object,
    source: object,
    candidate: object | None,
    event: str,
) -> None:
    _LOGGER.warning(event)
    abort_failed = False
    try:
        controller.session.cancel()
    except Exception:  # noqa: BLE001 - the fixed event is the complete failure record
        abort_failed = True
    seen: set[int] = set()
    for component in (source, candidate):
        if component is None or id(component) in seen:
            continue
        seen.add(id(component))
        try:
            if hasattr(component, "_claimed"):
                component._claimed = True
            component.stop()
        except Exception:  # noqa: BLE001 - attempt every terminal cleanup step
            abort_failed = True
    if abort_failed:
        _LOGGER.warning("schedule_edit_abort_failed")


def _abort_schedule_confirm(*, controller: object, source: object, event: str) -> None:
    _LOGGER.warning(event)
    abort_failed = False
    try:
        state = getattr(controller.snapshot(), "state", None)
        if getattr(state, "value", None) == "final_confirmation":
            controller.session.cancel()
    except Exception:  # noqa: BLE001 - the fixed event is the complete failure record
        abort_failed = True
    try:
        if hasattr(source, "_claimed"):
            source._claimed = True
        source.stop()
    except Exception:  # noqa: BLE001 - confirmation failure remains bounded
        abort_failed = True
    if abort_failed:
        _LOGGER.warning("schedule_confirm_abort_failed")


def _abort_schedule_cancel(
    *, controller: object, source: object, event: str, cancel_session: bool
) -> None:
    _LOGGER.warning(event)
    abort_failed = False
    if cancel_session:
        try:
            controller.session.cancel()
        except Exception:  # noqa: BLE001 - the fixed event is the complete failure record
            abort_failed = True
    try:
        if hasattr(source, "_claimed"):
            source._claimed = True
        source.stop()
    except Exception:  # noqa: BLE001 - cancellation failure remains bounded
        abort_failed = True
    if abort_failed:
        _LOGGER.warning("schedule_cancel_abort_failed")


def _render_schedule_cancel_response() -> dict[str, Any]:
    return {
        "content": "予約設定をキャンセルしました。予約は作成されていません。",
        "embed": None,
        "view": None,
        "allowed_mentions": discord.AllowedMentions.none(),
    }


def _record_schedule_type_cancel_event(event: str, *, success: bool = False) -> None:
    try:
        if success:
            _LOGGER.info(event)
        else:
            _LOGGER.warning(event)
    except Exception:  # noqa: BLE001 - fixed fallback must not leak logging failures
        try:
            _LOGGER.error(_SCHEDULE_TYPE_CANCEL_ABORT_FAILED)
        except Exception:  # noqa: BLE001, S110 - logging is the failed boundary
            pass


def _abort_schedule_type_cancel(
    *, controller: object, source: object, event: str, cancel_session: bool = False
) -> None:
    abort_failed = False
    if cancel_session:
        try:
            controller.session.cancel()
        except Exception:  # noqa: BLE001 - the fixed event is the complete failure record
            abort_failed = True
    try:
        if hasattr(source, "_claimed"):
            source._claimed = True
        source.stop()
    except Exception:  # noqa: BLE001 - no local cleanup exception may cross dispatch
        abort_failed = True
    _record_schedule_type_cancel_event(event)
    if abort_failed:
        _record_schedule_type_cancel_event(_SCHEDULE_TYPE_CANCEL_ABORT_FAILED)


def _abort_schedule_type(
    *, controller: object, source: object, candidate: object | None, event: str
) -> None:
    _LOGGER.warning(event)
    abort_failed = False
    try:
        controller.session.cancel()
    except Exception:  # noqa: BLE001 - the fixed event is the complete failure record
        abort_failed = True
    seen: set[int] = set()
    for component in (source, candidate):
        if component is None or id(component) in seen:
            continue
        seen.add(id(component))
        try:
            if hasattr(component, "_claimed"):
                component._claimed = True
            component.stop()
        except Exception:  # noqa: BLE001 - attempt every terminal cleanup step
            abort_failed = True
    if abort_failed:
        _LOGGER.warning("schedule_type_abort_failed")


def _abort_schedule_input(
    *,
    controller: object,
    source: object | None,
    modal: object,
    candidate: object | None,
    event: str,
) -> None:
    _LOGGER.warning(event)
    abort_failed = False
    try:
        controller.session.cancel()
    except Exception:  # noqa: BLE001 - the fixed event is the complete failure record
        abort_failed = True
    seen: set[int] = set()
    for component in (source, modal, candidate):
        if component is None or id(component) in seen:
            continue
        seen.add(id(component))
        try:
            if hasattr(component, "_claimed"):
                component._claimed = True
            component.stop()
        except Exception:  # noqa: BLE001 - attempt every terminal cleanup step
            abort_failed = True
    if abort_failed:
        _LOGGER.warning("schedule_input_abort_failed")


class PostDraftScheduleTypeView(discord.ui.View):
    """Unconnected schedule-type selection UI for the next post-draft slice."""

    def __init__(self, *, controller: object, now: Callable[[], datetime], timeout: float) -> None:
        super().__init__(timeout=_validated_timeout(timeout))
        self.controller = controller
        self._now = now
        self._claim_lock = asyncio.Lock()
        self._claimed = False
        self._edit_generation = 0
        for label, value, style in (
            ("単発", "once", discord.ButtonStyle.primary),
            ("毎日", "daily", discord.ButtonStyle.secondary),
            ("毎週", "weekly", discord.ButtonStyle.secondary),
        ):
            button = discord.ui.Button(
                label=label, style=style, custom_id=f"post_draft_schedule_{value}"
            )
            button.callback = self._select(value)
            self.add_item(button)
        cancel = discord.ui.Button(
            label="キャンセル",
            style=discord.ButtonStyle.danger,
            custom_id="post_draft_schedule_cancel",
        )
        cancel.callback = self._cancel
        self.add_item(cancel)

    def _select(self, value: str) -> Callable[[discord.Interaction], object]:
        async def callback(interaction: discord.Interaction) -> None:
            if not await _schedule_guard(interaction, self.controller, "schedule_type_selection"):
                return
            if not await self._claim():
                await _respond_stale(interaction)
                return
            if getattr(interaction.response, "is_done", lambda: False)():
                await _respond_stale(interaction)
                return
            try:
                schedule_type = __import__(
                    "discord_ai_reminder_bot.domain.enums", fromlist=["ScheduleType"]
                ).ScheduleType
                self.controller.session.select_type(schedule_type(value))
            except Exception:  # noqa: BLE001 - mutation details remain private
                _abort_schedule_type(
                    controller=self.controller,
                    source=self,
                    candidate=None,
                    event="schedule_type_controller_failed",
                )
                return
            modal: object | None = None
            try:
                modal_type = {
                    "once": PostDraftOnceScheduleModal,
                    "daily": PostDraftDailyScheduleModal,
                    "weekly": PostDraftWeeklyScheduleModal,
                }[value]
                modal = modal_type(
                    controller=self.controller, source_type=self, timeout=self.timeout
                )
            except Exception:  # noqa: BLE001 - modal construction details remain private
                _abort_schedule_type(
                    controller=self.controller,
                    source=self,
                    candidate=modal,
                    event="schedule_type_render_failed",
                )
                return
            try:
                await interaction.response.send_modal(modal)
            except Exception:  # noqa: BLE001 - an ambiguous transport is never retried
                _abort_schedule_type(
                    controller=self.controller,
                    source=self,
                    candidate=modal,
                    event="schedule_type_modal_transport_failed",
                )

        return callback

    async def _cancel(self, interaction: discord.Interaction) -> None:
        try:
            allowed = await _schedule_guard(interaction, self.controller, "schedule_type_selection")
        except Exception:  # noqa: BLE001 - authorization details remain private
            _abort_schedule_type_cancel(
                controller=self.controller,
                source=self,
                event=_SCHEDULE_TYPE_CANCEL_AUTHORIZATION_REJECTED,
            )
            return
        if not allowed:
            _record_schedule_type_cancel_event(_SCHEDULE_TYPE_CANCEL_AUTHORIZATION_REJECTED)
            return
        try:
            await interaction.response.defer(thinking=False)
        except Exception:  # noqa: BLE001 - an ambiguous defer is never retried
            _abort_schedule_type_cancel(
                controller=self.controller,
                source=self,
                event=_SCHEDULE_TYPE_CANCEL_DEFER_FAILED,
                cancel_session=True,
            )
            return
        try:
            claimed = await self._claim()
        except Exception:  # noqa: BLE001 - claim failure is terminal for this view
            _abort_schedule_type_cancel(
                controller=self.controller,
                source=self,
                event=_SCHEDULE_TYPE_CANCEL_CLAIM_FAILED,
                cancel_session=True,
            )
            return
        if not claimed:
            _record_schedule_type_cancel_event(_SCHEDULE_TYPE_CANCEL_CLAIM_FAILED)
            return
        try:
            self.controller.session.cancel()
        except Exception:  # noqa: BLE001 - mutation details remain private
            _abort_schedule_type_cancel(
                controller=self.controller,
                source=self,
                event=_SCHEDULE_TYPE_CANCEL_CONTROLLER_FAILED,
            )
            return
        try:
            response = _render_schedule_cancel_response()
        except Exception:  # noqa: BLE001 - render details remain private
            _abort_schedule_type_cancel(
                controller=self.controller,
                source=self,
                event=_SCHEDULE_TYPE_CANCEL_RENDER_FAILED,
            )
            return
        try:
            await interaction.edit_original_response(**response)
        except Exception:  # noqa: BLE001 - an ambiguous response is never retried
            _abort_schedule_type_cancel(
                controller=self.controller,
                source=self,
                event=_SCHEDULE_TYPE_CANCEL_RESPONSE_FAILED,
            )
            return
        try:
            self.stop()
        except Exception:  # noqa: BLE001 - the Discord view is already removed
            _record_schedule_type_cancel_event(_SCHEDULE_TYPE_CANCEL_ABORT_FAILED)
            return
        _record_schedule_type_cancel_event(_SCHEDULE_TYPE_CANCEL_SUCCEEDED, success=True)

    async def _claim(self) -> bool:
        async with self._claim_lock:
            if self._claimed or self.is_finished():
                return False
            self._claimed = True
            return True


class _PostDraftScheduleInputModal(discord.ui.Modal):
    def __init__(
        self, *, controller: object, title: str, timeout: float, source_type: object | None = None
    ) -> None:
        super().__init__(title=title, timeout=_validated_timeout(timeout))
        self.controller = controller
        self.source_type = source_type
        self._claim_lock = asyncio.Lock()
        self._claimed = False

    async def _claim(self) -> bool:
        async with self._claim_lock:
            if self._claimed or self.is_finished():
                return False
            self._claimed = True
            return True

    async def on_submit(self, interaction: discord.Interaction) -> None:
        expected_state = "final_confirmation" if hasattr(self, "source") else "schedule_input"
        if not await _schedule_guard(interaction, self.controller, expected_state):
            return
        if not await self._claim():
            await _respond_stale(interaction)
            return
        if getattr(interaction.response, "is_done", lambda: False)():
            await _respond_stale(interaction)
            return
        if hasattr(self, "source"):
            try:
                value = self._parse()
            except TypeError, ValueError:
                await _respond_error(interaction, PostDraftUIErrorCode.INVALID_TRANSITION)
                return
            await self._submit_edit(interaction, value)
            return
        try:
            value = self._parse()
        except Exception:  # noqa: BLE001 - validation details remain private
            _abort_schedule_input(
                controller=self.controller,
                source=self.source_type,
                modal=self,
                candidate=None,
                event="schedule_input_validation_failed",
            )
            if not getattr(interaction.response, "is_done", lambda: False)():
                try:
                    await _send_initial(
                        interaction,
                        content=post_draft_ui_error_message(
                            PostDraftUIErrorCode.INVALID_TRANSITION
                        ),
                    )
                except Exception:  # noqa: BLE001, S110 - never retry validation transport
                    pass
            return
        try:
            self.controller.session.set_validated_input(value)
        except Exception:  # noqa: BLE001 - mutation details remain private
            _abort_schedule_input(
                controller=self.controller,
                source=self.source_type,
                modal=self,
                candidate=None,
                event="schedule_input_controller_failed",
            )
            if not getattr(interaction.response, "is_done", lambda: False)():
                try:
                    await _send_initial(
                        interaction,
                        content=post_draft_ui_error_message(
                            PostDraftUIErrorCode.INVALID_TRANSITION
                        ),
                    )
                except Exception:  # noqa: BLE001, S110 - never retry mutation transport
                    pass
            return
        candidate: object | None = None
        try:
            candidate = PostDraftScheduleConfirmationView(
                controller=self.controller, now=lambda: datetime.now(UTC), timeout=900
            )
            embed = _schedule_confirmation_embed(self.controller)
        except Exception:  # noqa: BLE001 - render details remain private
            _abort_schedule_input(
                controller=self.controller,
                source=self.source_type,
                modal=self,
                candidate=candidate,
                event="schedule_input_render_failed",
            )
            return
        try:
            await interaction.response.edit_message(
                embed=embed,
                content=None,
                view=candidate,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:  # noqa: BLE001 - an ambiguous response is never retried
            _abort_schedule_input(
                controller=self.controller,
                source=self.source_type,
                modal=self,
                candidate=candidate,
                event="schedule_input_response_failed",
            )

    async def _submit_edit(self, interaction: discord.Interaction, value: object) -> None:
        source = self.source
        async with source._claim_lock:
            snapshot = self.controller.snapshot()
            if (
                source.is_finished()
                or source._claimed
                or self.generation != source._edit_generation
                or snapshot.confirmation_revision != self.revision
                or snapshot.state.value != "final_confirmation"
                or snapshot.schedule_type.value != self.schedule_type.value
            ):
                await _respond_stale(interaction)
                return
            try:
                self.controller.session.replace_validated_input(
                    value, expected_revision=self.revision
                )
            except TypeError, ValueError:
                await _respond_error(interaction, PostDraftUIErrorCode.INVALID_TRANSITION)
                return
            except Exception:  # noqa: BLE001 - never expose a schedule edit failure
                _abort_schedule_edit(
                    controller=self.controller,
                    source=source,
                    candidate=None,
                    event="schedule_edit_replace_failed",
                )
                return
            new_view: object | None = None
            try:
                new_view = PostDraftScheduleConfirmationView(
                    controller=self.controller, now=source._now, timeout=source.timeout
                )
                embed = _schedule_confirmation_embed(self.controller)
            except Exception:  # noqa: BLE001 - render details remain private
                _abort_schedule_edit(
                    controller=self.controller,
                    source=source,
                    candidate=new_view,
                    event="schedule_edit_render_failed",
                )
                return
            try:
                await interaction.response.edit_message(
                    embed=embed,
                    view=new_view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:  # noqa: BLE001 - an ambiguous transport is never retried
                _abort_schedule_edit(
                    controller=self.controller,
                    source=source,
                    candidate=new_view,
                    event="schedule_edit_response_failed",
                )
                return
            source.stop()

    def _parse(self) -> object:
        raise NotImplementedError


class PostDraftOnceScheduleModal(_PostDraftScheduleInputModal):
    scheduled_at = discord.ui.TextInput(
        label="日時", required=True, custom_id="post_draft_schedule_at"
    )

    def __init__(
        self, *, controller: object, timeout: float, source_type: object | None = None
    ) -> None:
        super().__init__(
            controller=controller, title="単発予約", timeout=timeout, source_type=source_type
        )

    def _parse(self) -> object:
        from datetime import datetime

        input_type = __import__(
            "discord_ai_reminder_bot.application.post_draft_schedule",
            fromlist=["PostDraftOnceScheduleInput"],
        ).PostDraftOnceScheduleInput
        return input_type(datetime.fromisoformat(str(self.scheduled_at.value)))


class PostDraftDailyScheduleModal(_PostDraftScheduleInputModal):
    local_time = discord.ui.TextInput(
        label="時刻", required=True, custom_id="post_draft_daily_time"
    )
    end_date = discord.ui.TextInput(
        label="終了日（任意）", required=False, custom_id="post_draft_daily_end"
    )

    def __init__(
        self, *, controller: object, timeout: float, source_type: object | None = None
    ) -> None:
        super().__init__(
            controller=controller, title="毎日予約", timeout=timeout, source_type=source_type
        )

    def _parse(self) -> object:
        from datetime import date, time

        input_type = __import__(
            "discord_ai_reminder_bot.application.post_draft_schedule",
            fromlist=["PostDraftDailyScheduleInput"],
        ).PostDraftDailyScheduleInput
        end = str(self.end_date.value).strip()
        return input_type(
            local_time=time.fromisoformat(str(self.local_time.value)),
            end_date=date.fromisoformat(end) if end else None,
        )


class PostDraftWeeklyScheduleModal(_PostDraftScheduleInputModal):
    weekday = discord.ui.TextInput(
        label="曜日（0=月曜）", required=True, custom_id="post_draft_weekday"
    )
    local_time = discord.ui.TextInput(
        label="時刻", required=True, custom_id="post_draft_weekly_time"
    )
    end_date = discord.ui.TextInput(
        label="終了日（任意）", required=False, custom_id="post_draft_weekly_end"
    )

    def __init__(
        self, *, controller: object, timeout: float, source_type: object | None = None
    ) -> None:
        super().__init__(
            controller=controller, title="毎週予約", timeout=timeout, source_type=source_type
        )

    def _parse(self) -> object:
        from datetime import date, time

        input_type = __import__(
            "discord_ai_reminder_bot.application.post_draft_schedule",
            fromlist=["PostDraftWeeklyScheduleInput"],
        ).PostDraftWeeklyScheduleInput
        end = str(self.end_date.value).strip()
        return input_type(
            local_time=time.fromisoformat(str(self.local_time.value)),
            weekday=int(str(self.weekday.value)),
            end_date=date.fromisoformat(end) if end else None,
        )


class PostDraftOnceScheduleEditModal(PostDraftOnceScheduleModal):
    def __init__(
        self,
        *,
        controller: object,
        source: object,
        generation: int,
        revision: int,
        timeout: float,
        default: str,
    ) -> None:
        super().__init__(controller=controller, timeout=timeout)
        self.source, self.generation, self.revision = source, generation, revision
        self.schedule_type = __import__(
            "discord_ai_reminder_bot.domain.enums", fromlist=["ScheduleType"]
        ).ScheduleType.ONCE
        self.scheduled_at.default = default


class PostDraftDailyScheduleEditModal(PostDraftDailyScheduleModal):
    def __init__(
        self,
        *,
        controller: object,
        source: object,
        generation: int,
        revision: int,
        timeout: float,
        local_default: str,
        end_default: str,
    ) -> None:
        super().__init__(controller=controller, timeout=timeout)
        self.source, self.generation, self.revision = source, generation, revision
        self.schedule_type = __import__(
            "discord_ai_reminder_bot.domain.enums", fromlist=["ScheduleType"]
        ).ScheduleType.DAILY
        self.local_time.default, self.end_date.default = local_default, end_default


class PostDraftWeeklyScheduleEditModal(PostDraftWeeklyScheduleModal):
    def __init__(
        self,
        *,
        controller: object,
        source: object,
        generation: int,
        revision: int,
        timeout: float,
        weekday_default: str,
        local_default: str,
        end_default: str,
    ) -> None:
        super().__init__(controller=controller, timeout=timeout)
        self.source, self.generation, self.revision = source, generation, revision
        self.schedule_type = __import__(
            "discord_ai_reminder_bot.domain.enums", fromlist=["ScheduleType"]
        ).ScheduleType.WEEKLY
        self.weekday.default, self.local_time.default, self.end_date.default = (
            weekday_default,
            local_default,
            end_default,
        )


class PostDraftScheduleConfirmationView(discord.ui.View):
    """Unconnected final confirmation shell; persistence is owned by the controller."""

    def __init__(self, *, controller: object, now: Callable[[], datetime], timeout: float) -> None:
        super().__init__(timeout=_validated_timeout(timeout))
        self.controller = controller
        self._now = now
        self._claim_lock = asyncio.Lock()
        self._claimed = False
        self._edit_generation = 0
        for label, style, callback in (
            ("予約を確定", discord.ButtonStyle.success, self._confirm),
            ("予約条件を編集", discord.ButtonStyle.secondary, self._edit),
            ("キャンセル", discord.ButtonStyle.danger, self._cancel),
        ):
            button = discord.ui.Button(
                label=label, style=style, custom_id=f"post_draft_schedule_{label}"
            )
            button.callback = callback
            self.add_item(button)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        if not await _schedule_guard(interaction, self.controller, "final_confirmation"):
            return
        if getattr(interaction.response, "is_done", lambda: False)():
            await _respond_stale(interaction)
            return
        try:
            await interaction.response.defer(thinking=False)
        except Exception:  # noqa: BLE001 - an ambiguous defer is never retried
            _abort_schedule_confirm(
                controller=self.controller,
                source=self,
                event="schedule_confirm_defer_failed",
            )
            return
        if not await self._claim():
            return
        try:
            try:
                result = await self.controller.confirm(now=self._now())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - controller details remain private
                _abort_schedule_confirm(
                    controller=self.controller,
                    source=self,
                    event="schedule_confirm_controller_failed",
                )
                try:
                    await interaction.edit_original_response(
                        content=POST_DRAFT_SCHEDULE_UNKNOWN_MESSAGE,
                        embed=None,
                        view=None,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except Exception:  # noqa: BLE001, S110 - never retry the fixed unknown response
                    pass
                return
            try:
                code = getattr(result, "code", None)
                value = getattr(code, "value", None)
                message = {
                    "created": POST_DRAFT_SCHEDULE_CREATED_MESSAGE,
                    "already_created": POST_DRAFT_SCHEDULE_ALREADY_CREATED_MESSAGE,
                    "conflict": POST_DRAFT_SCHEDULE_CONFLICT_MESSAGE,
                    "unknown": POST_DRAFT_SCHEDULE_UNKNOWN_MESSAGE,
                }.get(value, POST_DRAFT_SCHEDULE_UNKNOWN_MESSAGE)
            except Exception:  # noqa: BLE001 - render details remain private
                _abort_schedule_confirm(
                    controller=self.controller,
                    source=self,
                    event="schedule_confirm_render_failed",
                )
                return
            try:
                await interaction.edit_original_response(
                    content=message,
                    embed=None,
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:  # noqa: BLE001 - an ambiguous response is never retried
                _abort_schedule_confirm(
                    controller=self.controller,
                    source=self,
                    event="schedule_confirm_response_failed",
                )
        finally:
            self.stop()

    async def _edit(self, interaction: discord.Interaction) -> None:
        if not await _schedule_guard(interaction, self.controller, "final_confirmation"):
            return
        if getattr(interaction.response, "is_done", lambda: False)():
            await _respond_stale(interaction)
            return
        snapshot = self.controller.snapshot()
        self._edit_generation += 1
        generation = self._edit_generation
        value = snapshot.validated_input
        try:
            if snapshot.schedule_type.value == "once":
                modal = PostDraftOnceScheduleEditModal(
                    controller=self.controller,
                    source=self,
                    generation=generation,
                    revision=snapshot.confirmation_revision,
                    timeout=900,
                    default=value.scheduled_at.isoformat(),
                )
            elif snapshot.schedule_type.value == "daily":
                modal = PostDraftDailyScheduleEditModal(
                    controller=self.controller,
                    source=self,
                    generation=generation,
                    revision=snapshot.confirmation_revision,
                    timeout=900,
                    local_default=value.local_time.isoformat(),
                    end_default=value.end_date.isoformat() if value.end_date else "",
                )
            else:
                modal = PostDraftWeeklyScheduleEditModal(
                    controller=self.controller,
                    source=self,
                    generation=generation,
                    revision=snapshot.confirmation_revision,
                    timeout=900,
                    weekday_default=str(value.weekday),
                    local_default=value.local_time.isoformat(),
                    end_default=value.end_date.isoformat() if value.end_date else "",
                )
        except TypeError, ValueError:
            await _respond_error(interaction, PostDraftUIErrorCode.INVALID_TRANSITION)
            return
        except Exception:  # noqa: BLE001 - render details remain private
            _abort_schedule_edit(
                controller=self.controller,
                source=self,
                candidate=None,
                event="schedule_edit_render_failed",
            )
            return
        try:
            await interaction.response.send_modal(modal)
        except Exception:  # noqa: BLE001 - an ambiguous transport is never retried
            _abort_schedule_edit(
                controller=self.controller,
                source=self,
                candidate=modal,
                event="schedule_edit_modal_transport_failed",
            )

    async def _cancel(self, interaction: discord.Interaction) -> None:
        if not await _schedule_guard(interaction, self.controller, "final_confirmation"):
            return
        if getattr(interaction.response, "is_done", lambda: False)():
            await _respond_stale(interaction)
            return
        try:
            await interaction.response.defer(thinking=False)
        except Exception:  # noqa: BLE001 - an ambiguous defer is never retried
            _abort_schedule_cancel(
                controller=self.controller,
                source=self,
                event="schedule_cancel_defer_failed",
                cancel_session=True,
            )
            return
        if not await self._claim():
            return
        try:
            self.controller.session.cancel()
        except Exception:  # noqa: BLE001 - cancellation is never retried
            _abort_schedule_cancel(
                controller=self.controller,
                source=self,
                event="schedule_cancel_controller_failed",
                cancel_session=False,
            )
            return
        try:
            response = _render_schedule_cancel_response()
        except Exception:  # noqa: BLE001 - render details remain private
            _abort_schedule_cancel(
                controller=self.controller,
                source=self,
                event="schedule_cancel_render_failed",
                cancel_session=False,
            )
            return
        try:
            await interaction.edit_original_response(**response)
        except Exception:  # noqa: BLE001 - an ambiguous response is never retried
            _abort_schedule_cancel(
                controller=self.controller,
                source=self,
                event="schedule_cancel_response_failed",
                cancel_session=False,
            )
            return
        self.stop()

    async def _claim(self) -> bool:
        async with self._claim_lock:
            if self._claimed or self.is_finished():
                return False
            self._claimed = True
            return True


async def _edit_deferred_preview(
    interaction: discord.Interaction, *, view: PostDraftPreviewView, draft: GeneratedPostDraft
) -> None:
    await interaction.edit_original_response(
        content=None,
        embed=_preview_embed(draft),
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _send_initial(
    interaction: discord.Interaction,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
) -> None:
    arguments: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if embed is not None:
        arguments["embed"] = embed
    if view is not None:
        arguments["view"] = view
    await interaction.response.send_message(content, **arguments)


async def _render_cancel(
    interaction: discord.Interaction, *, content: str, clear_view: bool = True
) -> None:
    try:
        if clear_view:
            await interaction.edit_original_response(
                content=content,
                embed=None,
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            # A stale click must not remove the current generation's components.
            await interaction.edit_original_response(
                content=content, allowed_mentions=discord.AllowedMentions.none()
            )
    except Exception:  # noqa: BLE001 - cancellation stays terminal; no transport retry
        _LOGGER.warning("cancel_render_failed", extra={"stage": "render"})


async def _respond_error(interaction: discord.Interaction, code: PostDraftUIErrorCode) -> None:
    content = post_draft_ui_error_message(code)
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(
                content=content,
                embed=None,
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await _send_initial(interaction, content=content)
    except Exception:  # noqa: BLE001 - never retry a Discord transport failure
        _LOGGER.warning("view_error_response_failed", extra={"stage": "error_response"})


async def _respond_stale(interaction: discord.Interaction) -> None:
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(
                content=STALE_UI_MESSAGE,
                embed=None,
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await _send_initial(interaction, content=STALE_UI_MESSAGE)
    except Exception:  # noqa: BLE001, S110 - stale transport failure is terminal
        pass
