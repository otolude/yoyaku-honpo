"""Unregistered Discord UI adapters for the ephemeral post-draft state machine."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from datetime import datetime
from typing import cast

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
    ) -> None:
        if not isinstance(controller, PostDraftUISessionController):
            raise TypeError("invalid post draft Discord UI")
        timeout = _validated_timeout(timeout_seconds)
        if not callable(now) or not callable(reservation_factory):
            raise TypeError("invalid post draft Discord UI dependency")
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
        if not await self.ui.claim(self, consume=True):
            await _respond_stale(interaction)
            return
        user_id, guild_id = self.ui.ids(interaction)
        try:
            await self.ui.controller.accept(
                owner_user_id=user_id, guild_id=guild_id, now=self.ui._now()
            )
            await self.ui.deactivate(self)
        except PostDraftUISessionError as error:
            await _respond_error(interaction, error.code)
            return
        try:
            await interaction.response.edit_message(
                content=ACCEPTED_MESSAGE,
                embed=None,
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:  # noqa: BLE001, S110 - accepted state remains authoritative
            pass

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


def _preview_embed(draft: GeneratedPostDraft) -> discord.Embed:
    return discord.Embed(description=draft.value, colour=discord.Colour.blurple())


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
    await interaction.response.send_message(
        content,
        embed=embed,
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


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
