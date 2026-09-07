from __future__ import annotations

import ast
import asyncio
import logging
import math
import socket
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import discord
import pytest
from discord.webhook.async_ import async_context

from discord_ai_reminder_bot.application.post_draft_schedule import (
    PostDraftScheduleComposition,
    PostDraftScheduleScope,
)
from discord_ai_reminder_bot.application.post_draft_ui_session import (
    PostDraftUIErrorCode,
    PostDraftUISession,
    PostDraftUISessionController,
    PostDraftUISessionState,
)
from discord_ai_reminder_bot.application.post_draft_usage import PostDraftUsageReservation
from discord_ai_reminder_bot.bot.post_draft_ui import (
    PostDraftAIInputModal,
    PostDraftAISettingsView,
    PostDraftDiscordUI,
    PostDraftEditModal,
    PostDraftManualInputModal,
    PostDraftModeView,
    PostDraftPreviewView,
    _escape_preview_text,
    _preview_embed,
    _send_initial,
    create_post_draft_mode_view,
    post_draft_ui_error_message,
    send_post_draft_mode,
)
from discord_ai_reminder_bot.domain.post_draft_generation import (
    GeneratedPostDraft,
    PostDraftGenerationRequest,
    PostLength,
    PostTone,
)

NOW = datetime(2026, 9, 4, 3, tzinfo=UTC)
OWNER = 123
GUILD = 456
CANARY = "discord-ui-private-canary"
MODULE = Path("src/discord_ai_reminder_bot/bot/post_draft_ui.py")
STALE_MESSAGE = "この画面は古くなっています。現在の画面から操作してください。"
USER_MENTION = f"<@{'1' * 17}>"
ROLE_MENTION = f"<@&{'2' * 17}>"
CHANNEL_MENTION = f"<#{'3' * 15}>"
PLAIN_URL = "https://example.invalid/plain"
MARKDOWN_LINK = "[表示名](https://example.invalid/destination)"
MARKDOWN_CHARACTERS = "\\*_~`|><#[]-."


class FakeGenerationService:
    def __init__(self, outcome: object = GeneratedPostDraft("生成本文")) -> None:
        self.outcome = outcome
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.request: object = None

    async def generate(self, request: object, _reservation: object) -> GeneratedPostDraft:
        self.calls += 1
        self.request = request
        self.entered.set()
        if self.block:
            await self.release.wait()
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return cast(GeneratedPostDraft, self.outcome)


class FakeResponse:
    def __init__(self) -> None:
        self.send_message = AsyncMock()
        self.send_modal = AsyncMock()
        self.defer = AsyncMock()
        self.edit_message = AsyncMock()
        self._done = False

    def is_done(self) -> bool:
        return self._done


class BoundaryState:
    def __init__(self) -> None:
        self.allowed_mentions = None
        self.http = SimpleNamespace(proxy=None, proxy_auth=None)
        self.stored_views = 0

    def store_view(self, *_args: object, **_kwargs: object) -> None:
        self.stored_views += 1


class BoundaryResponse(discord.InteractionResponse[object]):
    __slots__ = ("attempts", "failures", "successes")

    def __init__(self, parent: object) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.attempts = 0
        self.successes = 0
        self.failures = 0

    async def send_message(self, *args: object, **kwargs: object) -> object:
        self.attempts += 1
        try:
            result = await super().send_message(*args, **kwargs)  # type: ignore[arg-type]
        except Exception:
            self.failures += 1
            raise
        self.successes += 1
        return result


class BoundaryInteraction:
    def __init__(self) -> None:
        self.id = OWNER
        self.token = "offline-callback-token"
        self._state = BoundaryState()
        self._session = object()
        self.user = SimpleNamespace(id=OWNER)
        self.guild_id = GUILD
        self.channel = None
        self.response = BoundaryResponse(self)
        self.followup = SimpleNamespace(send=AsyncMock())
        self.edit_original_response = AsyncMock()


class BoundaryCallbackAdapter:
    def __init__(self) -> None:
        self.attempts = 0
        self.successes = 0

    async def create_interaction_response(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, object]:
        self.attempts += 1
        self.successes += 1
        return {
            "interaction": {
                "id": str(OWNER),
                "response_message_ephemeral": True,
            }
        }


class TrackingManualInputModal(PostDraftManualInputModal):
    def __init__(self, *, ui: PostDraftDiscordUI, timeout: float) -> None:
        self.on_error_calls = 0
        super().__init__(ui=ui, timeout=timeout)

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        self.on_error_calls += 1
        await super().on_error(interaction, error)


def interaction(*, user_id: int = OWNER, guild_id: int | None = GUILD) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild_id=guild_id,
        response=FakeResponse(),
        edit_original_response=AsyncMock(),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def controller(
    service: FakeGenerationService | None = None,
) -> tuple[PostDraftUISessionController, FakeGenerationService]:
    generation = service or FakeGenerationService()
    session = PostDraftUISession.create(
        owner_user_id=OWNER,
        guild_id=GUILD,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    return PostDraftUISessionController(session=session, generation_service=generation), generation


def ui(
    service: FakeGenerationService | None = None,
) -> tuple[PostDraftDiscordUI, FakeGenerationService]:
    value, generation = controller(service)

    class Port:
        async def create_once(self, **kwargs):
            raise AssertionError

        async def create_recurring(self, **kwargs):
            raise AssertionError

    schedule = PostDraftScheduleComposition(port=Port(), public_id_factory=lambda: None)  # type: ignore[arg-type]
    adapter = PostDraftDiscordUI(
        controller=value,
        now=lambda: NOW,
        reservation_factory=lambda _now: cast(PostDraftUsageReservation, object()),
        timeout_seconds=60,
        schedule_scope=PostDraftScheduleScope(OWNER, GUILD, 300),
        schedule_composition=schedule,
    )
    return adapter, generation


def item(view: discord.ui.View, custom_id: str) -> discord.ui.Item[object]:
    return next(child for child in view.children if child.custom_id == custom_id)


def set_text(text_input: discord.ui.TextInput[object], value: str) -> None:
    text_input._value = value


def assert_preview_text(actual: str | None, expected: str, message: str) -> None:
    if actual != expected:
        pytest.fail(message, pytrace=False)


def utf16_code_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def assert_all_square_brackets_escaped(value: str) -> None:
    for index, character in enumerate(value):
        if character not in "[]":
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and value[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        assert backslashes % 2 == 1


def test_mode_view_structure_and_fixed_custom_ids() -> None:
    adapter, _ = ui()
    view = create_post_draft_mode_view(ui=adapter)
    assert isinstance(view, PostDraftModeView)
    assert view.timeout == 60
    assert not view.is_persistent()
    buttons = {child.label: child for child in view.children}
    assert set(buttons) == {"手入力", "AIで作成", "キャンセル"}
    assert buttons["手入力"].style is discord.ButtonStyle.secondary
    assert buttons["AIで作成"].style is discord.ButtonStyle.primary
    assert buttons["キャンセル"].style is discord.ButtonStyle.danger
    for child in view.children:
        assert str(OWNER) not in child.custom_id
        assert str(GUILD) not in child.custom_id


@pytest.mark.asyncio
async def test_entry_ui_is_sent_ephemerally_with_mentions_disabled() -> None:
    adapter, _ = ui()
    opened = interaction()
    await send_post_draft_mode(opened, ui=adapter)
    opened.response.send_message.assert_awaited_once()
    kwargs = opened.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    assert isinstance(kwargs["view"], PostDraftModeView)


def test_ai_settings_select_options_and_preview_actions() -> None:
    adapter, _ = ui()
    settings = PostDraftAISettingsView(ui=adapter, timeout=60)
    tone = cast(discord.ui.Select[object], item(settings, "post_draft_tone"))
    length = cast(discord.ui.Select[object], item(settings, "post_draft_length"))
    assert [(option.label, option.value) for option in tone.options] == [
        ("丁寧", "polite"),
        ("親しみやすい", "friendly"),
        ("簡潔", "concise"),
    ]
    assert [(option.label, option.value) for option in length.options] == [
        ("短め", "short"),
        ("標準", "standard"),
        ("長め", "long"),
    ]
    preview = PostDraftPreviewView(ui=adapter, timeout=60)
    assert {child.label for child in preview.children} == {
        "編集",
        "もう一度作成",
        "この本文を使用",
        "キャンセル",
    }


def test_modal_public_fields_match_domain_limits() -> None:
    adapter, _ = ui()
    ai = PostDraftAIInputModal(ui=adapter, timeout=60)
    assert ai.title == "AI文章の内容を入力"
    assert ai.purpose_label.text == "文章の目的"
    assert ai.purpose.required and ai.purpose.min_length == 1 and ai.purpose.max_length == 200
    assert ai.key_points_label.text == "含めたい要点"
    assert ai.key_points.required
    assert ai.key_points.min_length == 1 and ai.key_points.max_length == 1000
    manual = PostDraftManualInputModal(ui=adapter, timeout=60)
    assert manual.body.required and manual.body.max_length == 2000


@pytest.mark.parametrize("timeout", [True, 0, -1, math.nan, math.inf, -math.inf, None])
def test_timeout_must_be_positive_and_finite(timeout: object) -> None:
    value, _ = controller()
    with pytest.raises((TypeError, ValueError)):
        PostDraftDiscordUI(
            controller=value,
            now=lambda: NOW,
            reservation_factory=lambda _now: cast(PostDraftUsageReservation, object()),
            timeout_seconds=timeout,
            schedule_scope=PostDraftScheduleScope(OWNER, GUILD, 300),
            schedule_composition=PostDraftScheduleComposition(
                port=type(
                    "Port",
                    (),
                    {
                        "create_once": lambda self, **kwargs: None,
                        "create_recurring": lambda self, **kwargs: None,
                    },
                )(),
                public_id_factory=lambda: None,
            ),
        )


def test_schedule_type_view_has_four_actions() -> None:
    from discord_ai_reminder_bot.bot.post_draft_ui import PostDraftScheduleTypeView

    view = PostDraftScheduleTypeView(controller=object(), now=lambda: NOW, timeout=60)
    assert [item.label for item in view.children] == ["単発", "毎日", "毎週", "キャンセル"]


def test_schedule_confirmation_view_has_no_body_back_action() -> None:
    from discord_ai_reminder_bot.bot.post_draft_ui import PostDraftScheduleConfirmationView

    view = PostDraftScheduleConfirmationView(controller=object(), now=lambda: NOW, timeout=60)
    labels = [item.label for item in view.children]
    assert labels == ["予約を確定", "予約条件を編集", "キャンセル"]


def test_schedule_input_modals_exist_with_expected_fields() -> None:
    from discord_ai_reminder_bot.bot.post_draft_ui import (
        PostDraftDailyScheduleModal,
        PostDraftOnceScheduleModal,
        PostDraftWeeklyScheduleModal,
    )

    assert hasattr(PostDraftOnceScheduleModal, "scheduled_at")
    assert hasattr(PostDraftDailyScheduleModal, "local_time")
    assert hasattr(PostDraftDailyScheduleModal, "end_date")
    assert hasattr(PostDraftWeeklyScheduleModal, "weekday")
    assert hasattr(PostDraftWeeklyScheduleModal, "local_time")
    assert hasattr(PostDraftWeeklyScheduleModal, "end_date")


def test_schedule_result_messages_are_fixed_and_retry_free() -> None:
    from discord_ai_reminder_bot.bot.post_draft_ui import (
        POST_DRAFT_SCHEDULE_ALREADY_CREATED_MESSAGE,
        POST_DRAFT_SCHEDULE_CONFLICT_MESSAGE,
        POST_DRAFT_SCHEDULE_CREATED_MESSAGE,
        POST_DRAFT_SCHEDULE_UNKNOWN_MESSAGE,
    )

    assert "予約を作成しました" in POST_DRAFT_SCHEDULE_CREATED_MESSAGE
    assert "すでに作成" in POST_DRAFT_SCHEDULE_ALREADY_CREATED_MESSAGE
    assert "確認できませんでした" in POST_DRAFT_SCHEDULE_UNKNOWN_MESSAGE
    assert "再実行していません" in POST_DRAFT_SCHEDULE_UNKNOWN_MESSAGE
    assert "retry" not in POST_DRAFT_SCHEDULE_CONFLICT_MESSAGE.lower()


def test_schedule_interaction_guard_and_claim_are_present() -> None:
    from discord_ai_reminder_bot.bot.post_draft_ui import (
        PostDraftScheduleConfirmationView,
        PostDraftScheduleTypeView,
    )

    assert hasattr(PostDraftScheduleTypeView, "_claim")
    assert hasattr(PostDraftScheduleConfirmationView, "_claim")


def test_schedule_edit_modal_classes_exist() -> None:
    from discord_ai_reminder_bot.bot.post_draft_ui import (
        PostDraftDailyScheduleEditModal,
        PostDraftOnceScheduleEditModal,
        PostDraftWeeklyScheduleEditModal,
    )

    assert PostDraftOnceScheduleEditModal
    assert PostDraftDailyScheduleEditModal
    assert PostDraftWeeklyScheduleEditModal


@pytest.mark.asyncio
async def test_schedule_edit_button_opens_modal_without_mutating_session() -> None:
    from datetime import datetime

    from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
        ScheduleCreationPublicId,
    )
    from discord_ai_reminder_bot.application.post_draft_schedule import (
        PostDraftOnceScheduleInput,
        PostDraftScheduleController,
        PostDraftScheduleScope,
        PostDraftScheduleSession,
    )
    from discord_ai_reminder_bot.bot.post_draft_ui import PostDraftScheduleConfirmationView

    class Port:
        async def create_once(self, **kwargs):
            raise AssertionError

        async def create_recurring(self, **kwargs):
            raise AssertionError

    session = PostDraftScheduleSession(
        scope=PostDraftScheduleScope(1, 2, 3), accepted_draft=GeneratedPostDraft("本文")
    )
    session.select_type(
        __import__(
            "discord_ai_reminder_bot.domain.enums", fromlist=["ScheduleType"]
        ).ScheduleType.ONCE
    )
    session.set_validated_input(PostDraftOnceScheduleInput(datetime(2030, 1, 1, tzinfo=UTC)))
    controller = PostDraftScheduleController(
        session=session, port=Port(), public_id_factory=lambda: ScheduleCreationPublicId.generate()
    )
    view = PostDraftScheduleConfirmationView(controller=controller, now=lambda: NOW, timeout=60)
    response = SimpleNamespace(is_done=lambda: False, send_modal=AsyncMock())
    interaction_value = SimpleNamespace(
        user=SimpleNamespace(id=1),
        guild_id=2,
        channel_id=3,
        channel=SimpleNamespace(id=3, guild=SimpleNamespace(id=2), type=discord.ChannelType.text),
        response=response,
    )
    before = session.snapshot()
    await view.children[1].callback(interaction_value)
    assert response.send_modal.await_count == 1
    assert session.snapshot() == before


@pytest.mark.asyncio
async def test_schedule_edit_modal_submit_replaces_input_through_callback() -> None:
    from datetime import datetime

    from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
        ScheduleCreationPublicId,
    )
    from discord_ai_reminder_bot.application.post_draft_schedule import (
        PostDraftOnceScheduleInput,
        PostDraftScheduleController,
        PostDraftScheduleScope,
        PostDraftScheduleSession,
    )
    from discord_ai_reminder_bot.bot.post_draft_ui import PostDraftScheduleConfirmationView
    from discord_ai_reminder_bot.domain.enums import ScheduleType

    class Port:
        async def create_once(self, **kwargs):
            raise AssertionError

        async def create_recurring(self, **kwargs):
            raise AssertionError

    session = PostDraftScheduleSession(
        scope=PostDraftScheduleScope(1, 2, 3), accepted_draft=GeneratedPostDraft("本文")
    )
    session.select_type(ScheduleType.ONCE)
    session.set_validated_input(PostDraftOnceScheduleInput(datetime(2030, 1, 1, tzinfo=UTC)))
    controller = PostDraftScheduleController(
        session=session, port=Port(), public_id_factory=lambda: ScheduleCreationPublicId.generate()
    )
    view = PostDraftScheduleConfirmationView(controller=controller, now=lambda: NOW, timeout=60)
    click_response = SimpleNamespace(is_done=lambda: False, send_modal=AsyncMock())
    click = SimpleNamespace(
        user=SimpleNamespace(id=1),
        guild_id=2,
        channel_id=3,
        channel=SimpleNamespace(id=3, guild=SimpleNamespace(id=2), type=discord.ChannelType.text),
        response=click_response,
    )
    await view.children[1].callback(click)
    modal = click_response.send_modal.await_args.args[0]
    set_text(modal.scheduled_at, "2030-01-02T00:00:00+00:00")
    submit_response = SimpleNamespace(
        is_done=lambda: False, edit_message=AsyncMock(), send_message=AsyncMock()
    )
    submit = SimpleNamespace(
        user=SimpleNamespace(id=1),
        guild_id=2,
        channel_id=3,
        channel=SimpleNamespace(id=3, guild=SimpleNamespace(id=2), type=discord.ChannelType.text),
        response=submit_response,
    )
    await modal.on_submit(submit)
    assert submit_response.edit_message.await_count == 1
    assert session.snapshot().validated_input.scheduled_at.day == 2


@pytest.mark.asyncio
async def test_owner_ai_flow_uses_modal_defer_and_original_edit_once() -> None:
    adapter, generation = ui()
    view = create_post_draft_mode_view(ui=adapter)
    click = interaction()
    await item(view, "post_draft_mode_ai").callback(click)
    assert adapter.controller.session.state is PostDraftUISessionState.AI_INPUT
    click.response.edit_message.assert_awaited_once()
    settings = click.response.edit_message.await_args.kwargs["view"]
    open_modal = interaction()
    await item(settings, "post_draft_open_ai_input").callback(open_modal)
    open_modal.response.send_modal.assert_awaited_once()
    modal = open_modal.response.send_modal.await_args.args[0]
    set_text(modal.purpose, "開催案内")
    set_text(modal.key_points, "9月開催")
    submitted = interaction()
    await modal.on_submit(submitted)
    submitted.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    assert submitted.response.send_message.await_count == 0
    assert submitted.edit_original_response.await_count == 2
    final = submitted.edit_original_response.await_args.kwargs
    assert isinstance(final["embed"], discord.Embed)
    assert final["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    assert isinstance(final["view"], PostDraftPreviewView)
    assert generation.calls == 1
    assert isinstance(generation.request, PostDraftGenerationRequest)
    assert generation.request.tone is PostTone.POLITE
    assert generation.request.length is PostLength.STANDARD


@pytest.mark.asyncio
async def test_manual_modal_reaches_preview_without_generation() -> None:
    adapter, generation = ui()
    view = create_post_draft_mode_view(ui=adapter)
    clicked = interaction()
    await item(view, "post_draft_mode_manual").callback(clicked)
    modal = clicked.response.send_modal.await_args.args[0]
    set_text(modal.body, "手入力本文")
    submitted = interaction()
    await modal.on_submit(submitted)
    assert generation.calls == 0
    assert adapter.controller.session.state is PostDraftUISessionState.PREVIEW
    kwargs = submitted.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["embed"].description == "手入力本文"
    assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()


@pytest.mark.asyncio
async def test_two_thousand_character_preview_uses_embed_without_url_or_content_prefix() -> None:
    adapter, generation = ui()
    await adapter.controller.choose_manual(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    modal = PostDraftManualInputModal(ui=adapter, timeout=60)
    body = "あ" * 2000
    set_text(modal.body, body)
    submitted = interaction()
    await modal.on_submit(submitted)
    kwargs = submitted.response.send_message.await_args.kwargs
    assert submitted.response.send_message.await_args.args == (None,)
    assert kwargs["embed"].description == body
    assert kwargs["embed"].url is None
    assert generation.calls == 0


@pytest.mark.asyncio
async def test_invalid_manual_body_returns_fixed_error_without_generation() -> None:
    adapter, generation = ui()
    await adapter.controller.choose_manual(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    modal = PostDraftManualInputModal(ui=adapter, timeout=60)
    set_text(modal.body, f"{CANARY}@everyone")
    submitted = interaction()
    await modal.on_submit(submitted)
    observed = f"{submitted.response.send_message.await_args}"
    assert CANARY not in observed
    assert post_draft_ui_error_message(PostDraftUIErrorCode.INVALID_RESPONSE) in observed
    assert generation.calls == 0


@pytest.mark.parametrize("dangerous", ["@everyone", "@here"])
@pytest.mark.asyncio
async def test_manual_validation_error_completes_real_interaction_response_once(
    dangerous: str, caplog: pytest.LogCaptureFixture
) -> None:
    adapter, generation = ui()
    await adapter.controller.choose_manual(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    modal = TrackingManualInputModal(ui=adapter, timeout=60)
    set_text(modal.body, f"{CANARY}{dangerous}")
    submitted = BoundaryInteraction()
    callback_adapter = BoundaryCallbackAdapter()

    context = async_context.set(callback_adapter)
    try:
        with caplog.at_level(logging.WARNING, logger="discord_ai_reminder_bot.bot.post_draft_ui"):
            await modal._scheduled_task(submitted, [], {})
    finally:
        async_context.reset(context)

    assert callback_adapter.attempts == 1
    assert callback_adapter.successes == 1
    assert submitted.response.attempts == 1
    assert submitted.response.successes == 1
    assert submitted.response.failures == 0
    assert submitted.response.is_done()
    assert submitted.followup.send.await_count == 0
    assert submitted.edit_original_response.await_count == 0
    assert modal.on_error_calls == 0
    assert modal.is_finished()
    assert adapter.controller.session.state is PostDraftUISessionState.MANUAL_ENTRY
    assert adapter.controller.session.current_draft() is None
    assert generation.calls == 0
    assert not hasattr(adapter, "repository")
    assert not hasattr(adapter, "schedule_service")
    assert sum(record.msg == "view_error_response_failed" for record in caplog.records) == 0


@pytest.mark.parametrize(
    (
        "case",
        "content",
        "include_embed",
        "embed_is_none",
        "include_view",
        "view_is_none",
        "expected_optional_keys",
    ),
    [
        pytest.param("content_only", "本文", False, False, False, False, set(), id="content-only"),
        pytest.param("embed_only", None, True, False, False, False, {"embed"}, id="embed-only"),
        pytest.param(
            "content_view", "本文", False, False, True, False, {"view"}, id="content-view"
        ),
        pytest.param(
            "embed_view", None, True, False, True, False, {"embed", "view"}, id="embed-view"
        ),
        pytest.param(
            "explicit_embed_none", "本文", True, True, False, False, set(), id="explicit-embed-none"
        ),
        pytest.param(
            "explicit_view_none", "本文", False, False, True, True, set(), id="explicit-view-none"
        ),
        pytest.param(
            "all_values", "本文", True, False, True, False, {"embed", "view"}, id="all-values"
        ),
        pytest.param("all_absent", None, False, False, False, False, set(), id="all-absent"),
    ],
)
@pytest.mark.asyncio
async def test_send_initial_omits_absent_optional_fields(
    case: str,
    content: str | None,
    include_embed: bool,
    embed_is_none: bool,
    include_view: bool,
    view_is_none: bool,
    expected_optional_keys: set[str],
) -> None:
    del case
    submitted = interaction()
    embed = discord.Embed(description="確認用")
    view = discord.ui.View(timeout=60)
    arguments: dict[str, object] = {"content": content}
    if include_embed:
        arguments["embed"] = None if embed_is_none else embed
    if include_view:
        arguments["view"] = None if view_is_none else view

    await _send_initial(submitted, **arguments)  # type: ignore[arg-type]

    call = submitted.response.send_message.await_args
    assert call.args == (content,)
    assert set(call.kwargs) == {"ephemeral", "allowed_mentions"} | expected_optional_keys
    assert call.kwargs["ephemeral"] is True
    assert call.kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    if "embed" in expected_optional_keys:
        assert call.kwargs["embed"] is embed
    if "view" in expected_optional_keys:
        assert call.kwargs["view"] is view


@pytest.mark.parametrize("user_id,guild_id", [(999, GUILD), (OWNER, 999), (OWNER, None)])
@pytest.mark.asyncio
async def test_interaction_check_rejects_wrong_owner_guild_and_dm_without_state_change(
    user_id: int, guild_id: int | None
) -> None:
    adapter, _ = ui()
    view = create_post_draft_mode_view(ui=adapter)
    attempted = interaction(user_id=user_id, guild_id=guild_id)
    assert not await view.interaction_check(attempted)
    attempted.response.send_message.assert_awaited_once()
    kwargs = attempted.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert adapter.controller.session.state is PostDraftUISessionState.MODE_SELECTION


@pytest.mark.asyncio
async def test_controller_still_rechecks_owner_after_ui_check() -> None:
    adapter, _ = ui()
    view = create_post_draft_mode_view(ui=adapter)
    allowed = interaction()
    assert await view.interaction_check(allowed)
    adapter.controller.session.owner_user_id = 999
    await item(view, "post_draft_mode_ai").callback(allowed)
    allowed.response.send_message.assert_awaited_once()
    assert adapter.controller.session.state is PostDraftUISessionState.MODE_SELECTION


@pytest.mark.parametrize("code", list(PostDraftUIErrorCode))
def test_every_ui_error_has_fixed_japanese_message(code: PostDraftUIErrorCode) -> None:
    message = post_draft_ui_error_message(code)
    assert isinstance(message, str) and message
    assert code.value not in message
    assert str(OWNER) not in message
    if code in {
        PostDraftUIErrorCode.DISABLED,
        PostDraftUIErrorCode.UNAVAILABLE,
        PostDraftUIErrorCode.USER_RATE_LIMITED,
        PostDraftUIErrorCode.GUILD_RATE_LIMITED,
        PostDraftUIErrorCode.GLOBAL_DAILY_EXHAUSTED,
        PostDraftUIErrorCode.GLOBAL_MONTHLY_EXHAUSTED,
        PostDraftUIErrorCode.GLOBAL_COST_EXHAUSTED,
        PostDraftUIErrorCode.USAGE_UNAVAILABLE,
        PostDraftUIErrorCode.UNKNOWN,
    }:
        assert "手入力" in message


@pytest.mark.asyncio
async def test_accept_only_reports_not_reserved_and_performs_no_save_or_post() -> None:
    adapter, _ = ui()
    await adapter.controller.choose_manual(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    await adapter.controller.submit_manual(
        text="本文", owner_user_id=OWNER, guild_id=GUILD, now=NOW
    )
    view = PostDraftPreviewView(ui=adapter, timeout=60)
    clicked = interaction()
    await item(view, "post_draft_accept").callback(clicked)
    assert adapter.controller.session.state is PostDraftUISessionState.ACCEPTED
    content = clicked.response.edit_message.await_args.kwargs["content"]
    assert "本文を採用しました" in content
    assert "まだ予約・投稿はされていません" in content
    assert not hasattr(adapter, "repository")
    assert not hasattr(adapter, "schedule_service")


@pytest.mark.asyncio
async def test_edit_modal_starts_with_current_body_and_replaces_preview() -> None:
    adapter, _ = ui()
    await adapter.controller.choose_manual(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    await adapter.controller.submit_manual(
        text="変更前", owner_user_id=OWNER, guild_id=GUILD, now=NOW
    )
    view = PostDraftPreviewView(ui=adapter, timeout=60)
    clicked = interaction()
    await item(view, "post_draft_edit").callback(clicked)
    modal = clicked.response.send_modal.await_args.args[0]
    assert isinstance(modal, PostDraftEditModal)
    assert modal.body.default == "変更前"
    assert modal.body.max_length == 2000
    set_text(modal.body, "変更後")
    submitted = interaction()
    await modal.on_submit(submitted)
    assert adapter.controller.session.current_draft().value == "変更後"


@pytest.mark.asyncio
async def test_manual_and_edit_preview_escape_only_display_and_accept_keeps_raw() -> None:
    raw = (
        f"{USER_MENTION} {ROLE_MENTION} {CHANNEL_MENTION} @mention-like\n"
        f"{MARKDOWN_LINK}\n"
        "**太字** *斜体*\n# 見出し\n"
        "`inline`\n```text\ncode\n```\n"
        f"日本語と絵文字 🎉\n{PLAIN_URL}\n"
        "既存\\backslash"
    )
    expected = (
        f"<\u200b@{'1' * 17}> <\u200b@&{'2' * 17}> <\u200b#{'3' * 15}> @mention\\-like\n"
        "\\[表示名\\](https://example.invalid/destination)\n"
        "\\*\\*太字\\*\\* \\*斜体\\*\n\\# 見出し\n"
        "\\`inline\\`\n\\`\\`\\`text\ncode\n\\`\\`\\`\n"
        f"日本語と絵文字 🎉\n{PLAIN_URL}\n"
        "既存\\\\backslash"
    )
    adapter, generation = ui()
    mode = create_post_draft_mode_view(ui=adapter)
    selected = interaction()
    await item(mode, "post_draft_mode_manual").callback(selected)
    manual = selected.response.send_modal.await_args.args[0]
    set_text(manual.body, raw)

    submitted = interaction()
    await manual.on_submit(submitted)

    first = submitted.response.send_message.await_args.kwargs
    assert_preview_text(
        first["embed"].description,
        expected,
        "Manual Preview did not apply the fixed display-only escape sequence",
    )
    assert first["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    assert adapter.controller.session.current_draft().value == raw
    preview = first["view"]
    edit_clicked = interaction()
    await item(preview, "post_draft_edit").callback(edit_clicked)
    edit = edit_clicked.response.send_modal.await_args.args[0]
    assert edit.body.default == raw
    set_text(edit.body, raw)

    edited = interaction()
    await edit.on_submit(edited)

    second = edited.response.send_message.await_args.kwargs
    assert_preview_text(
        second["embed"].description,
        expected,
        "Edit Preview did not reuse the Manual Preview display escape",
    )
    assert second["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    assert adapter.controller.session.current_draft().value == raw
    assert first["embed"].description == second["embed"].description
    assert PLAIN_URL in second["embed"].description.splitlines()
    assert USER_MENTION not in second["embed"].description
    assert ROLE_MENTION not in second["embed"].description
    assert CHANNEL_MENTION not in second["embed"].description
    assert generation.calls == 0

    accepted = interaction()
    await item(second["view"], "post_draft_accept").callback(accepted)

    assert adapter.controller.session.state is PostDraftUISessionState.ACCEPTED
    assert adapter.controller.accepted_draft().value == raw
    accept_kwargs = accepted.response.edit_message.await_args.kwargs
    assert accept_kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    assert raw not in accept_kwargs["content"]
    assert accept_kwargs["embed"] is None


@pytest.mark.asyncio
async def test_manual_and_edit_keep_raw_while_escaping_strict_url_boundaries() -> None:
    raw = "HTTPS://example.invalid/a_b`tail` - item 12. item"
    expected = "HTTPS://example.invalid/a_b\\`tail\\` \\- item 12\\. item"
    adapter, generation = ui()
    mode = create_post_draft_mode_view(ui=adapter)
    selected = interaction()
    await item(mode, "post_draft_mode_manual").callback(selected)
    manual = selected.response.send_modal.await_args.args[0]
    set_text(manual.body, raw)

    submitted = interaction()
    await manual.on_submit(submitted)

    first = submitted.response.send_message.await_args.kwargs
    assert_preview_text(first["embed"].description, expected, "Manual strict Preview changed")
    assert first["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    assert adapter.controller.session.current_draft().value == raw
    edit_clicked = interaction()
    await item(first["view"], "post_draft_edit").callback(edit_clicked)
    edit = edit_clicked.response.send_modal.await_args.args[0]
    assert edit.body.default == raw
    set_text(edit.body, raw)

    edited = interaction()
    await edit.on_submit(edited)

    second = edited.response.send_message.await_args.kwargs
    assert_preview_text(second["embed"].description, expected, "Edit strict Preview changed")
    assert second["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    assert adapter.controller.session.current_draft().value == raw
    assert first["embed"].description == second["embed"].description
    assert generation.calls == 0

    accepted = interaction()
    await item(second["view"], "post_draft_accept").callback(accepted)

    assert adapter.controller.session.state is PostDraftUISessionState.ACCEPTED
    assert adapter.controller.accepted_draft().value == raw
    accept_kwargs = accepted.response.edit_message.await_args.kwargs
    assert accept_kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    assert accept_kwargs["embed"] is None


def test_preview_plain_url_is_unchanged_and_does_not_open_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls = 0

    def reject_network(*_args: object, **_kwargs: object) -> None:
        nonlocal network_calls
        network_calls += 1
        pytest.fail("Preview generation attempted a network connection", pytrace=False)

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    draft = GeneratedPostDraft(PLAIN_URL)

    embed = _preview_embed(draft)

    assert_preview_text(
        embed.description,
        PLAIN_URL,
        "plain URL display must remain identical to the raw URL",
    )
    assert draft.value == PLAIN_URL
    assert network_calls == 0


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.invalid/path_name",
        "https://example.invalid/path?query_name=value",
        "https://example.invalid/path?query=*value*",
        "https://example.invalid/%E6%97%A5%E6%9C%AC",
        "https://example.invalid/path#fragment",
        "https://example.invalid/path_(segment)",
        "https://example.invalid/~user",
        "https://example.invalid/path-name",
        "https://example.invalid/%F0%9F%98%80",
    ],
    ids=(
        "path-underscore",
        "query-underscore",
        "query-asterisk",
        "percent-encoding",
        "fragment",
        "parentheses",
        "tilde",
        "hyphen",
        "unicode-percent-encoding",
    ),
)
def test_preview_preserves_complete_normal_url_span(raw: str) -> None:
    embed = _preview_embed(GeneratedPostDraft(raw))

    assert_preview_text(embed.description, raw, "normal URL span changed during Preview")


@pytest.mark.parametrize(
    "separator", [" ", "\t", "\n", "\u3000"], ids=("space", "tab", "lf", "wide-space")
)
def test_preview_url_keeps_uri_punctuation_and_stops_before_closing_bracket(
    separator: str,
) -> None:
    url = "https://example.invalid/path_~(*)?q=*value*#fragment.,!?)]"
    raw = f"*before*{separator}{url}{separator}*after*"
    expected = f"\\*before\\*{separator}{url[:-1]}\\]{separator}\\*after\\*"

    rendered = _escape_preview_text(raw)

    assert_preview_text(
        rendered,
        expected,
        "URL terminator or surrounding Markdown escape changed",
    )


@pytest.mark.parametrize(
    "scheme",
    ("http://", "https://", "HTTP://", "HTTPS://", "Http://", "HtTpS://"),
)
def test_preview_matches_http_scheme_ascii_case_insensitively_without_rewriting(
    scheme: str,
) -> None:
    raw = f"{scheme}example.invalid/path_name*~(segment)?query=*value*#fragment"

    rendered = _preview_embed(GeneratedPostDraft(raw)).description

    assert_preview_text(rendered, raw, "HTTP scheme case or URL content changed")


def test_preview_preserves_each_allowed_ascii_uri_character_and_percent_text() -> None:
    raw = "https://user@example.invalid/AZaz09-._~:/?#@!$&'()*+,;=%25%ZZ"

    rendered = _preview_embed(GeneratedPostDraft(raw)).description

    assert_preview_text(rendered, raw, "an allowed ASCII URI character changed")


@pytest.mark.parametrize(
    ("terminator", "expected_suffix"),
    (
        ("\\", "\\\\\\_tail\\_"),
        ("`", "\\`\\_tail\\_"),
        ("|", "\\|\\_tail\\_"),
        ("<", "\\<\\_tail\\_"),
        (">", "\\>\\_tail\\_"),
        ("[", "\\[\\_tail\\_"),
        ("]", "\\]\\_tail\\_"),
        ('"', '"\\_tail\\_'),
        ("“", "“\\_tail\\_"),
        (" ", " \\_tail\\_"),
        ("\t", "\t\\_tail\\_"),
        ("\n", "\n\\_tail\\_"),
        ("\x00", "\x00\\_tail\\_"),
        ("日", "日\\_tail\\_"),
    ),
    ids=(
        "backslash",
        "backtick",
        "vertical-bar",
        "less-than",
        "greater-than",
        "opening-bracket",
        "closing-bracket",
        "ascii-quote",
        "unicode-quote",
        "space",
        "tab",
        "lf",
        "control",
        "non-ascii",
    ),
)
def test_preview_url_stops_before_each_disallowed_character(
    terminator: str, expected_suffix: str
) -> None:
    url = "https://example.invalid/path_name*~(segment)?query=value#fragment"
    raw = f"{url}{terminator}_tail_"

    rendered = _escape_preview_text(raw)

    assert_preview_text(rendered, f"{url}{expected_suffix}", "URL consumed a disallowed suffix")


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("https://", "https://"),
        ("https:///path-name", "https:///path\\-name"),
        ("https://?query=*value*", "https://?query=\\*value\\*"),
        ("https://#fragment*", "https://\\#fragment\\*"),
        ("https://`code`", "https://\\`code\\`"),
    ),
    ids=("no-tail", "empty-authority", "query-only", "fragment-only", "invalid-start"),
)
def test_preview_does_not_protect_url_with_empty_authority(raw: str, expected: str) -> None:
    rendered = _escape_preview_text(raw)

    assert_preview_text(rendered, expected, "URL with empty authority was protected")


def test_preview_treats_ipv6_literal_url_as_unsupported_and_escapes_it_safely() -> None:
    raw = "https://[2001:db8::1]/path_name"
    expected = "https://\\[2001:db8::1\\]/path\\_name"

    rendered = _escape_preview_text(raw)

    assert_preview_text(rendered, expected, "unsupported IPv6 literal URL was protected")


@pytest.mark.parametrize(
    ("raw_suffix", "expected_suffix"),
    (
        ("`code`", "\\`code\\`"),
        ("```code```", "\\`\\`\\`code\\`\\`\\`"),
        ("||spoiler||", "\\|\\|spoiler\\|\\|"),
        ("[label](https://other.invalid/path)", "\\[label\\](https://other.invalid/path)"),
    ),
    ids=("inline-code", "fenced-code", "spoiler", "masked-link"),
)
def test_preview_escapes_markdown_immediately_after_url(
    raw_suffix: str, expected_suffix: str
) -> None:
    url = "https://example.invalid/path_name"

    rendered = _preview_embed(GeneratedPostDraft(f"{url}{raw_suffix}")).description

    assert_preview_text(rendered, f"{url}{expected_suffix}", "URL absorbed adjacent Markdown")


def test_preview_escapes_both_backticks_around_url() -> None:
    url = "https://example.invalid/path_name"

    inline = _preview_embed(GeneratedPostDraft(f"`{url}`")).description
    fenced = _preview_embed(GeneratedPostDraft(f"```{url}```")).description

    assert_preview_text(inline, f"\\`{url}\\`", "inline URL code delimiter remained active")
    assert_preview_text(
        fenced,
        f"\\`\\`\\`{url}\\`\\`\\`",
        "fenced URL code delimiter remained active",
    )


def test_preview_escapes_angle_autolink_and_preserves_inner_url() -> None:
    url = "https://example.invalid/path_name"

    rendered = _preview_embed(GeneratedPostDraft(f"<{url}>")).description

    assert_preview_text(rendered, f"\\<{url}\\>", "angle autolink remained active")


@pytest.mark.parametrize(
    "mention",
    (
        f"<@{'1' * 15}>",
        f"<@!{'2' * 16}>",
        f"<@&{'3' * 17}>",
        f"<#{'4' * 20}>",
    ),
    ids=("user", "nickname-user", "role", "channel"),
)
def test_preview_url_stops_before_and_escapes_adjacent_mention(mention: str) -> None:
    url = "https://user@example.invalid/path"
    expected_mention = f"<\u200b{mention[1:]}"

    rendered = _preview_embed(GeneratedPostDraft(f"{url}{mention}")).description

    assert_preview_text(rendered, f"{url}{expected_mention}", "URL absorbed adjacent mention")


def test_preview_keeps_separated_urls_and_does_not_guess_a_missing_delimiter() -> None:
    first = "HTTPS://example.invalid/a_b"
    second = "http://other.invalid/c*d"

    separated = _preview_embed(GeneratedPostDraft(f"{first} {second}")).description
    joined = _preview_embed(GeneratedPostDraft(f"{first}{second}")).description

    assert_preview_text(separated, f"{first} {second}", "separated URLs changed")
    assert_preview_text(joined, f"{first}{second}", "joined URL-like text was split or changed")


def test_preview_escapes_masked_link_before_a_separate_normal_url() -> None:
    masked_url = "https://example.invalid/masked_path"
    plain_url = "HTTP://other.invalid/plain*path"
    raw = f"[label]({masked_url}) {plain_url}"
    expected = f"\\[label\\]({masked_url}) {plain_url}"

    rendered = _preview_embed(GeneratedPostDraft(raw)).description

    assert_preview_text(rendered, expected, "masked link or following normal URL changed")


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("- item", "\\- item"),
        ("* item", "\\* item"),
        ("1. item", "1\\. item"),
        ("12. item", "12\\. item"),
        ("-# subtext", "\\-\\# subtext"),
        ("# heading", "\\# heading"),
        ("> quote", "\\> quote"),
        (">>> quote", "\\>\\>\\> quote"),
        ("||spoiler||", "\\|\\|spoiler\\|\\|"),
        ("[label](https://example.invalid/path)", "\\[label\\](https://example.invalid/path)"),
        ("<https://example.invalid/path>", "\\<https://example.invalid/path\\>"),
        ("`code`", "\\`code\\`"),
        ("```text\ncode\n```", "\\`\\`\\`text\ncode\n\\`\\`\\`"),
        ("__underline__", "\\_\\_underline\\_\\_"),
        ("**bold**", "\\*\\*bold\\*\\*"),
        ("_italic_", "\\_italic\\_"),
        ("~~strike~~", "\\~\\~strike\\~\\~"),
        ("version 1.2 - ok", "version 1\\.2 \\- ok"),
    ),
)
def test_preview_disables_each_supported_discord_markdown_form(raw: str, expected: str) -> None:
    rendered = _preview_embed(GeneratedPostDraft(raw)).description

    assert_preview_text(rendered, expected, "Discord Markdown remained active")


def test_preview_disables_every_markdown_link_on_same_and_separate_lines() -> None:
    urls = (
        "https://example.invalid/first_path",
        "https://example.invalid/second_(path)",
        "https://example.invalid/nested~path",
        "https://example.invalid/escaped*path",
    )
    raw = (
        f"[first]({urls[0]}) [second]({urls[1]})\n"
        f"[outer [nested]]({urls[2]})\n"
        f"\\[already escaped]({urls[3]})"
    )

    rendered = _preview_embed(GeneratedPostDraft(raw)).description

    assert rendered is not None
    assert_all_square_brackets_escaped(rendered)
    for url in urls:
        assert url in rendered
    assert rendered.count("\u200b") == 0


@pytest.mark.parametrize("kind", ("user", "nickname-user", "role", "channel"))
@pytest.mark.parametrize("digits", (15, 16, 17, 20))
def test_preview_escapes_every_complete_discord_mention(kind: str, digits: int) -> None:
    prefixes = {"user": "<@", "nickname-user": "<@!", "role": "<@&", "channel": "<#"}
    raw = f"{prefixes[kind]}{'1' * digits}>"
    expected = f"<\u200b{raw[1:]}"

    rendered = _preview_embed(GeneratedPostDraft(raw)).description

    assert_preview_text(rendered, expected, "complete Discord mention was not escaped exactly")


@pytest.mark.parametrize("kind", ("user", "nickname-user", "role", "channel"))
@pytest.mark.parametrize("digits", (14, 21))
def test_preview_does_not_partially_escape_out_of_range_mentions(kind: str, digits: int) -> None:
    prefixes = {"user": "<@", "nickname-user": "<@!", "role": "<@&", "channel": "<#"}
    raw = f"{prefixes[kind]}{'1' * digits}>"

    rendered = _preview_embed(GeneratedPostDraft(raw)).description

    assert rendered is not None
    assert "\u200b" not in rendered
    assert "1" * digits in rendered


@pytest.mark.parametrize(
    "raw",
    (
        "<@１２３４５６７８９０１２３４５>",
        "<@!１２３４５６７８９０１２３４５>",
        "<@&１２３４５６７８９０１２３４５>",
        "<#１２３４５６７８９０１２３４５>",
        "<@123456789012345x>",
        "<@!123456789012345!>",
        "<@&123456789012345&>",
        "<#123456789012345#>",
        f"<@{'1' * 17}x>",
        f"<@!{'1' * 17}!>",
        f"<@&{'1' * 17}&>",
        "<@>",
        "<not-a-mention>",
    ),
)
def test_preview_does_not_partially_escape_malformed_mentions(raw: str) -> None:
    rendered = _preview_embed(GeneratedPostDraft(raw)).description

    assert rendered is not None
    assert "\u200b" not in rendered


def test_preview_escapes_each_adjacent_complete_mention_once() -> None:
    mentions = (
        f"<@{'1' * 15}>",
        f"<@!{'2' * 16}>",
        f"<@&{'3' * 17}>",
        f"<#{'4' * 20}>",
    )
    raw = "".join(mentions)
    expected = "".join(f"<\u200b{mention[1:]}" for mention in mentions)

    rendered = _preview_embed(GeneratedPostDraft(raw)).description

    assert_preview_text(rendered, expected, "adjacent mentions were not escaped exactly once")


def test_preview_does_not_overconvert_ordinary_mention_like_characters() -> None:
    raw = "通常の<tag>と#hashと@nameと<#short>"
    expected = "通常の\\<tag\\>と\\#hashと@nameと\\<\\#short\\>"

    rendered = _preview_embed(GeneratedPostDraft(raw)).description

    assert_preview_text(rendered, expected, "ordinary mention-like text was over-converted")


def test_preview_generation_has_no_warnings() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        embed = _preview_embed(GeneratedPostDraft("**表示** https://example.invalid/a_b"))

    assert embed.description is not None


@pytest.mark.parametrize(
    ("raw", "expected_length", "expected_utf16_units"),
    [
        ("\\" * 2_000, 4_000, 4_000),
        (
            (MARKDOWN_CHARACTERS * math.ceil(2_000 / len(MARKDOWN_CHARACTERS)))[:2_000],
            4_000,
            4_000,
        ),
        ((USER_MENTION + ROLE_MENTION + CHANNEL_MENTION) * 33 + "x" * 53, 2_099, 2_099),
        ("https://example.invalid/" + "*_~()#?q=_" * 197 + "x" * 6, 2_000, 2_000),
        ("HTTPS://EXAMPLE.invalid/" + "*_~()#?q=_" * 197 + "x" * 6, 2_000, 2_000),
        ("😀" * 2_000, 2_000, 4_000),
    ],
    ids=(
        "backslashes",
        "all-markdown",
        "all-mentions",
        "normal-url",
        "uppercase-url",
        "astral-unicode",
    ),
)
def test_two_thousand_character_preview_is_lossless_within_both_embed_limits(
    raw: str, expected_length: int, expected_utf16_units: int
) -> None:

    embed = _preview_embed(GeneratedPostDraft(raw))

    assert len(raw) == 2_000
    assert embed.description is not None
    assert len(embed.description) == expected_length
    assert utf16_code_units(embed.description) == expected_utf16_units
    assert len(embed.description) <= 4_096
    assert utf16_code_units(embed.description) <= 4_096


@pytest.mark.parametrize(
    "invalid",
    [
        pytest.param("@everyone", id="everyone"),
        pytest.param("@here", id="here"),
        pytest.param(" \n ", id="whitespace"),
        pytest.param("control\x00", id="control"),
        pytest.param("x" * 2_001, id="over-limit"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_manual_and_edit_body_never_reaches_preview(invalid: str) -> None:
    manual_adapter, manual_generation = ui()
    mode = create_post_draft_mode_view(ui=manual_adapter)
    selected = interaction()
    await item(mode, "post_draft_mode_manual").callback(selected)
    manual = selected.response.send_modal.await_args.args[0]
    set_text(manual.body, invalid)
    manual_submitted = interaction()

    await manual.on_submit(manual_submitted)

    manual_kwargs = manual_submitted.response.send_message.await_args.kwargs
    assert manual_adapter.controller.session.state is PostDraftUISessionState.MANUAL_ENTRY
    assert manual_adapter.controller.session.current_draft() is None
    assert "embed" not in manual_kwargs
    assert "view" not in manual_kwargs
    assert manual_kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    assert manual_generation.calls == 0

    edit_adapter, edit_generation = ui()
    await edit_adapter.controller.choose_manual(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    await edit_adapter.controller.submit_manual(
        text="変更前", owner_user_id=OWNER, guild_id=GUILD, now=NOW
    )
    preview = PostDraftPreviewView(ui=edit_adapter, timeout=60)
    edit_adapter.activate_initial(preview)
    edit_clicked = interaction()
    await item(preview, "post_draft_edit").callback(edit_clicked)
    edit = edit_clicked.response.send_modal.await_args.args[0]
    set_text(edit.body, invalid)
    edit_submitted = interaction()

    await edit.on_submit(edit_submitted)

    edit_kwargs = edit_submitted.response.send_message.await_args.kwargs
    assert edit_adapter.controller.session.state is PostDraftUISessionState.EDITING
    assert edit_adapter.controller.session.current_draft().value == "変更前"
    assert "embed" not in edit_kwargs
    assert "view" not in edit_kwargs
    assert edit_kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    assert edit_generation.calls == 0


@pytest.mark.asyncio
async def test_preview_regenerate_calls_service_once_per_operation() -> None:
    adapter, service = ui()
    await adapter.controller.choose_ai(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    first = interaction()
    await adapter.generate(first, purpose="目的", key_points="要点")
    preview = first.edit_original_response.await_args.kwargs["view"]
    regenerated = interaction()
    await item(preview, "post_draft_regenerate").callback(regenerated)
    assert service.calls == 2
    assert adapter.controller.session.state is PostDraftUISessionState.PREVIEW


@pytest.mark.asyncio
async def test_timeout_expires_clears_payload_disables_components_and_stops() -> None:
    adapter, _ = ui()
    await adapter.controller.choose_manual(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    adapter._now = lambda: NOW + timedelta(minutes=15)
    view = PostDraftModeView(ui=adapter, timeout=60)
    await view.on_timeout()
    assert adapter.controller.session.state is PostDraftUISessionState.EXPIRED
    assert adapter.controller.session.request is None
    assert adapter.controller.session.current_draft() is None
    assert all(child.disabled for child in view.children)
    assert view.is_finished()
    assert not hasattr(view, "interaction")
    assert not hasattr(view, "message")


@pytest.mark.asyncio
async def test_cancel_during_generation_prevents_late_preview_edit() -> None:
    service = FakeGenerationService()
    service.block = True
    adapter, _ = ui(service)
    await adapter.controller.choose_ai(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    modal = PostDraftAIInputModal(ui=adapter, timeout=60)
    set_text(modal.purpose, "目的")
    set_text(modal.key_points, "要点")
    submitted = interaction()
    generation = asyncio.create_task(modal.on_submit(submitted))
    await service.entered.wait()
    cancel = interaction()
    await adapter.cancel(cancel)
    service.release.set()
    await generation
    assert adapter.controller.session.state is PostDraftUISessionState.CANCELLED
    assert submitted.edit_original_response.await_count == 2
    final_content = submitted.edit_original_response.await_args.kwargs.get("content")
    assert final_content != "生成本文"
    assert service.calls == 1


@pytest.mark.asyncio
async def test_generation_coroutine_cancellation_completes_response_and_rethrows_same_object() -> (
    None
):
    cancellation = asyncio.CancelledError()
    adapter, service = ui(FakeGenerationService(cancellation))
    await adapter.controller.choose_ai(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    modal = PostDraftAIInputModal(ui=adapter, timeout=60)
    set_text(modal.purpose, "目的")
    set_text(modal.key_points, "要点")
    submitted = interaction()
    with pytest.raises(asyncio.CancelledError) as caught:
        await modal.on_submit(submitted)
    assert caught.value is cancellation
    submitted.response.defer.assert_awaited_once()
    assert submitted.edit_original_response.await_count == 2
    assert post_draft_ui_error_message(PostDraftUIErrorCode.CANCELLED) in str(
        submitted.edit_original_response.await_args
    )
    assert service.calls == 1


def imported_modules(source: str) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(
                f"relative:{node.level}:{node.module or ''}" if node.level else node.module or ""
            )
    return result


def test_ui_module_imports_are_default_deny_allowlisted() -> None:
    allowed = {
        "__future__",
        "asyncio",
        "math",
        "logging",
        "collections.abc",
        "datetime",
        "typing",
        "discord",
        "discord_ai_reminder_bot.application.post_draft_ui_session",
        "discord_ai_reminder_bot.application.post_draft_usage",
        "discord_ai_reminder_bot.domain.post_draft_generation",
    }
    assert imported_modules(MODULE.read_text(encoding="utf-8")) <= allowed


@pytest.mark.parametrize(
    "source",
    [
        "import openai",
        "import sqlalchemy as sa",
        "from discord_ai_reminder_bot.infrastructure.database import models",
        "from . import client",
        "from discord_ai_reminder_bot.infrastructure.ai import adapter as ai",
    ],
)
def test_import_guard_rejects_forbidden_alias_relative_and_infrastructure(source: str) -> None:
    assert not imported_modules(source) <= {"discord"}


def test_import_guard_ignores_comments_docstrings_and_strings() -> None:
    source = '''"""import openai"""\n# import sqlalchemy\nTEXT = "from . import client"\nimport discord\n'''
    assert imported_modules(source) == {"discord"}


def test_repr_and_custom_ids_do_not_expose_payload_or_identifiers() -> None:
    adapter, _ = ui()
    view = create_post_draft_mode_view(ui=adapter)
    observed = " ".join((repr(adapter), repr(view), *(child.custom_id for child in view.children)))
    assert CANARY not in observed
    assert str(OWNER) not in observed
    assert str(GUILD) not in observed
    assert "token" not in observed.lower()


@pytest.mark.asyncio
async def test_stale_mode_timeout_does_not_expire_new_ai_settings_view() -> None:
    adapter, _ = ui()
    mode = create_post_draft_mode_view(ui=adapter)
    clicked = interaction()
    await item(mode, "post_draft_mode_ai").callback(clicked)
    assert adapter.controller.session.state is PostDraftUISessionState.AI_INPUT
    await mode.on_timeout()
    assert adapter.controller.session.state is PostDraftUISessionState.AI_INPUT

    settings = clicked.response.edit_message.await_args.kwargs["view"]
    await settings.on_timeout()
    assert adapter.controller.session.state is PostDraftUISessionState.EXPIRED


@pytest.mark.asyncio
async def test_stale_mode_buttons_do_not_call_controller() -> None:
    adapter, generation = ui()
    mode = create_post_draft_mode_view(ui=adapter)
    first = interaction()
    await item(mode, "post_draft_mode_ai").callback(first)

    for custom_id in ("post_draft_mode_manual", "post_draft_mode_ai", "post_draft_cancel"):
        attempted = interaction()
        await item(mode, custom_id).callback(attempted)
        if custom_id == "post_draft_cancel":
            attempted.response.defer.assert_awaited_once_with(thinking=False)
            attempted.response.send_message.assert_not_awaited()
            assert STALE_MESSAGE in str(attempted.edit_original_response.await_args)
            assert "view" not in attempted.edit_original_response.await_args.kwargs
        else:
            assert STALE_MESSAGE in str(attempted.response.send_message.await_args)
    assert adapter.controller.session.state is PostDraftUISessionState.AI_INPUT
    assert generation.calls == 0


@pytest.mark.asyncio
async def test_submitted_ai_modal_timeout_does_not_expire_preview() -> None:
    adapter, _ = ui()
    mode = create_post_draft_mode_view(ui=adapter)
    clicked = interaction()
    await item(mode, "post_draft_mode_ai").callback(clicked)
    settings = clicked.response.edit_message.await_args.kwargs["view"]
    opened = interaction()
    await item(settings, "post_draft_open_ai_input").callback(opened)
    modal = opened.response.send_modal.await_args.args[0]
    set_text(modal.purpose, "目的")
    set_text(modal.key_points, "要点")
    submitted = interaction()
    await modal.on_submit(submitted)
    assert adapter.controller.session.state is PostDraftUISessionState.PREVIEW
    await modal.on_timeout()
    assert adapter.controller.session.state is PostDraftUISessionState.PREVIEW


@pytest.mark.asyncio
async def test_stale_settings_callback_does_not_open_modal_or_change_selection() -> None:
    adapter, _ = ui()
    mode = create_post_draft_mode_view(ui=adapter)
    clicked = interaction()
    await item(mode, "post_draft_mode_ai").callback(clicked)
    settings = clicked.response.edit_message.await_args.kwargs["view"]
    opened = interaction()
    await item(settings, "post_draft_open_ai_input").callback(opened)
    modal = opened.response.send_modal.await_args.args[0]

    stale_open = interaction()
    await item(settings, "post_draft_open_ai_input").callback(stale_open)
    stale_open.response.send_modal.assert_not_awaited()
    assert STALE_MESSAGE in str(stale_open.response.send_message.await_args)

    tone = cast(discord.ui.Select[object], item(settings, "post_draft_tone"))
    tone._values = [PostTone.FRIENDLY.value]
    stale_select = interaction()
    await tone.callback(stale_select)
    assert adapter.tone is PostTone.POLITE
    assert STALE_MESSAGE in str(stale_select.response.send_message.await_args)
    assert modal is not None


@pytest.mark.asyncio
async def test_manual_modal_double_submit_calls_controller_once() -> None:
    adapter, generation = ui()
    mode = create_post_draft_mode_view(ui=adapter)
    clicked = interaction()
    await item(mode, "post_draft_mode_manual").callback(clicked)
    modal = clicked.response.send_modal.await_args.args[0]
    set_text(modal.body, "手入力本文")

    first = interaction()
    second = interaction()
    await modal.on_submit(first)
    await modal.on_submit(second)

    assert adapter.controller.session.state is PostDraftUISessionState.PREVIEW
    assert generation.calls == 0
    assert first.response.send_message.await_count == 1
    assert second.response.send_message.await_count == 1
    assert STALE_MESSAGE in str(second.response.send_message.await_args)


@pytest.mark.asyncio
async def test_submitted_manual_and_edit_modal_timeouts_are_noops() -> None:
    adapter, _ = ui()
    mode = create_post_draft_mode_view(ui=adapter)
    selected = interaction()
    await item(mode, "post_draft_mode_manual").callback(selected)
    manual = selected.response.send_modal.await_args.args[0]
    set_text(manual.body, "最初の本文")
    submitted = interaction()
    await manual.on_submit(submitted)
    await manual.on_timeout()
    assert adapter.controller.session.state is PostDraftUISessionState.PREVIEW

    preview = submitted.response.send_message.await_args.kwargs["view"]
    edit_click = interaction()
    await item(preview, "post_draft_edit").callback(edit_click)
    edit = edit_click.response.send_modal.await_args.args[0]
    set_text(edit.body, "編集後本文")
    edited = interaction()
    await edit.on_submit(edited)
    await edit.on_timeout()
    assert adapter.controller.session.state is PostDraftUISessionState.PREVIEW


@pytest.mark.asyncio
async def test_stale_preview_callbacks_do_not_mutate_or_generate() -> None:
    adapter, service = ui()
    await adapter.controller.choose_ai(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    generated = interaction()
    await adapter.generate(generated, purpose="目的", key_points="要点")
    stale = generated.edit_original_response.await_args.kwargs["view"]

    edit = interaction()
    await item(stale, "post_draft_edit").callback(edit)
    modal = edit.response.send_modal.await_args.args[0]
    set_text(modal.body, "編集後")
    submitted = interaction()
    await modal.on_submit(submitted)
    calls = service.calls

    for custom_id in ("post_draft_regenerate", "post_draft_accept", "post_draft_cancel"):
        attempted = interaction()
        await item(stale, custom_id).callback(attempted)
        if custom_id == "post_draft_cancel":
            attempted.response.defer.assert_awaited_once_with(thinking=False)
            attempted.response.send_message.assert_not_awaited()
            assert STALE_MESSAGE in str(attempted.edit_original_response.await_args)
            assert "view" not in attempted.edit_original_response.await_args.kwargs
        else:
            assert STALE_MESSAGE in str(attempted.response.send_message.await_args)
    assert service.calls == calls
    assert adapter.controller.session.state is PostDraftUISessionState.PREVIEW


@pytest.mark.asyncio
async def test_generating_view_can_cancel_before_service_finishes_and_discards_result() -> None:
    service = FakeGenerationService()
    service.block = True
    adapter, _ = ui(service)
    mode = create_post_draft_mode_view(ui=adapter)
    clicked = interaction()
    await item(mode, "post_draft_mode_ai").callback(clicked)
    settings = clicked.response.edit_message.await_args.kwargs["view"]
    opened = interaction()
    await item(settings, "post_draft_open_ai_input").callback(opened)
    modal = opened.response.send_modal.await_args.args[0]
    set_text(modal.purpose, "目的")
    set_text(modal.key_points, "要点")
    submitted = interaction()
    task = asyncio.create_task(modal.on_submit(submitted))
    await service.entered.wait()
    generating = submitted.edit_original_response.await_args_list[0].kwargs["view"]
    assert {child.label for child in generating.children} == {"キャンセル"}

    cancelled = interaction()
    await asyncio.wait_for(item(generating, "post_draft_cancel").callback(cancelled), timeout=0.2)
    assert adapter.controller.session.state is PostDraftUISessionState.CANCELLED
    service.release.set()
    await task
    assert service.calls == 1
    assert submitted.edit_original_response.await_count == 1


@pytest.mark.asyncio
async def test_stale_generating_timeout_and_cancel_are_noops() -> None:
    service = FakeGenerationService()
    service.block = True
    adapter, _ = ui(service)
    await adapter.controller.choose_ai(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    submitted = interaction()
    task = asyncio.create_task(adapter.generate(submitted, purpose="目的", key_points="要点"))
    await service.entered.wait()
    generating = submitted.edit_original_response.await_args_list[0].kwargs["view"]
    await adapter.cancel(interaction())
    await generating.on_timeout()
    stale_cancel = interaction()
    await item(generating, "post_draft_cancel").callback(stale_cancel)
    assert adapter.controller.session.state is PostDraftUISessionState.CANCELLED
    assert STALE_MESSAGE in str(stale_cancel.edit_original_response.await_args)
    service.release.set()
    await task


@pytest.mark.asyncio
async def test_active_generating_timeout_expires_and_discards_late_result() -> None:
    service = FakeGenerationService()
    service.block = True
    adapter, _ = ui(service)
    await adapter.controller.choose_ai(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    submitted = interaction()
    task = asyncio.create_task(adapter.generate(submitted, purpose="目的", key_points="要点"))
    await service.entered.wait()
    generating = submitted.edit_original_response.await_args_list[0].kwargs["view"]
    await generating.on_timeout()
    assert adapter.controller.session.state is PostDraftUISessionState.EXPIRED
    service.release.set()
    await task
    assert service.calls == 1
    assert submitted.edit_original_response.await_count == 1


def test_ui_manager_does_not_retain_interaction_message_or_public_token() -> None:
    adapter, _ = ui()
    create_post_draft_mode_view(ui=adapter)
    slots = set(adapter.__slots__)
    assert "interaction" not in slots
    assert "message" not in slots
    assert "token" not in slots
    observed = repr(adapter)
    assert str(OWNER) not in observed
    assert str(GUILD) not in observed


@pytest.mark.asyncio
async def test_stale_ai_settings_cancel_cannot_cancel_preview() -> None:
    adapter, service = ui()
    mode = create_post_draft_mode_view(ui=adapter)
    selected = interaction()
    await item(mode, "post_draft_mode_ai").callback(selected)
    settings = selected.response.edit_message.await_args.kwargs["view"]
    opened = interaction()
    await item(settings, "post_draft_open_ai_input").callback(opened)
    modal = opened.response.send_modal.await_args.args[0]
    set_text(modal.purpose, "目的")
    set_text(modal.key_points, "要点")
    await modal.on_submit(interaction())
    tone, length = adapter.tone, adapter.length

    stale = interaction()
    await item(settings, "post_draft_cancel").callback(stale)
    assert adapter.controller.session.state is PostDraftUISessionState.PREVIEW
    assert (adapter.tone, adapter.length) == (tone, length)
    assert service.calls == 1
    assert STALE_MESSAGE in str(stale.edit_original_response.await_args)


@pytest.mark.parametrize("mode_custom_id", ["post_draft_mode_manual", "post_draft_mode_ai"])
@pytest.mark.asyncio
async def test_mode_transport_failure_aborts_without_retry(mode_custom_id: str) -> None:
    adapter, service = ui()
    view = create_post_draft_mode_view(ui=adapter)
    attempted = interaction()
    method = "send_modal" if mode_custom_id.endswith("manual") else "edit_message"
    getattr(attempted.response, method).side_effect = RuntimeError(CANARY)

    await item(view, mode_custom_id).callback(attempted)

    assert adapter.controller.session.state is PostDraftUISessionState.CANCELLED
    assert adapter._active_component is None
    assert view.is_finished() and all(child.disabled for child in view.children)
    assert getattr(attempted.response, method).await_count == 1
    assert attempted.response.send_message.await_count == 0
    assert service.calls == 0


@pytest.mark.asyncio
async def test_initial_send_failure_aborts_without_retry() -> None:
    adapter, service = ui()
    attempted = interaction()
    attempted.response.send_message.side_effect = RuntimeError(CANARY)
    await send_post_draft_mode(attempted, ui=adapter)
    assert attempted.response.send_message.await_count == 1
    assert adapter.controller.session.state is PostDraftUISessionState.CANCELLED
    assert adapter._active_component is None
    assert service.calls == 0


@pytest.mark.asyncio
async def test_two_simultaneous_mode_transitions_call_discord_once() -> None:
    adapter, service = ui()
    view = create_post_draft_mode_view(ui=adapter)
    first, second = interaction(), interaction()
    await asyncio.gather(
        item(view, "post_draft_mode_ai").callback(first),
        item(view, "post_draft_mode_ai").callback(second),
    )
    assert first.response.edit_message.await_count + second.response.edit_message.await_count == 1
    assert first.response.send_message.await_count + second.response.send_message.await_count == 1
    assert adapter.controller.session.state is PostDraftUISessionState.AI_INPUT
    assert service.calls == 0


@pytest.mark.asyncio
async def test_ai_settings_send_modal_failure_aborts_without_second_response() -> None:
    adapter, service = ui()
    mode = create_post_draft_mode_view(ui=adapter)
    selected = interaction()
    await item(mode, "post_draft_mode_ai").callback(selected)
    settings = selected.response.edit_message.await_args.kwargs["view"]
    attempted = interaction()
    attempted.response.send_modal.side_effect = RuntimeError(CANARY)

    await item(settings, "post_draft_open_ai_input").callback(attempted)

    assert adapter.controller.session.state is PostDraftUISessionState.CANCELLED
    assert adapter._active_component is None
    assert settings.is_finished() and all(child.disabled for child in settings.children)
    assert attempted.response.send_modal.await_count == 1
    assert attempted.response.send_message.await_count == 0
    assert service.calls == 0


@pytest.mark.parametrize("after_response", [False, True])
@pytest.mark.asyncio
async def test_modal_defer_failure_aborts_without_generation_or_retry(
    after_response: bool,
) -> None:
    adapter, service = ui()
    mode = create_post_draft_mode_view(ui=adapter)
    selected = interaction()
    await item(mode, "post_draft_mode_ai").callback(selected)
    settings = selected.response.edit_message.await_args.kwargs["view"]
    opened = interaction()
    await item(settings, "post_draft_open_ai_input").callback(opened)
    modal = opened.response.send_modal.await_args.args[0]
    set_text(modal.purpose, "目的")
    set_text(modal.key_points, "要点")
    submitted = interaction()

    async def fail_defer(**_kwargs: object) -> None:
        submitted.response._done = after_response
        raise RuntimeError(CANARY)

    submitted.response.defer.side_effect = fail_defer
    await modal.on_submit(submitted)

    assert adapter.controller.session.state is PostDraftUISessionState.CANCELLED
    assert adapter._active_component is None
    assert modal.is_finished()
    assert submitted.response.defer.await_count == 1
    assert submitted.response.send_message.await_count == 0
    assert submitted.edit_original_response.await_count == 0
    assert service.calls == 0


@pytest.mark.asyncio
async def test_generating_display_failure_aborts_before_generation() -> None:
    adapter, service = ui()
    await adapter.controller.choose_ai(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    attempted = interaction()
    attempted.edit_original_response.side_effect = RuntimeError(CANARY)

    await adapter.generate(attempted, purpose="目的", key_points="要点")

    assert adapter.controller.session.state is PostDraftUISessionState.CANCELLED
    assert adapter._active_component is None
    assert attempted.response.defer.await_count == 1
    assert attempted.edit_original_response.await_count == 1
    assert service.calls == 0


@pytest.mark.asyncio
async def test_preview_transport_failure_cancels_and_does_not_retry() -> None:
    adapter, service = ui()
    await adapter.controller.choose_ai(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    attempted = interaction()
    attempted.edit_original_response.side_effect = [None, RuntimeError(CANARY)]

    await adapter.generate(attempted, purpose="目的", key_points="要点")

    assert service.calls == 1
    assert attempted.edit_original_response.await_count == 2
    assert adapter.controller.session.state is PostDraftUISessionState.CANCELLED
    assert adapter.controller.session.current_draft() is None
    assert adapter._active_component is None


def test_transport_failure_canary_is_not_retained_by_ui() -> None:
    adapter, _ = ui()
    observed = repr(adapter)
    assert CANARY not in observed
    assert not hasattr(adapter, "interaction")
    assert not hasattr(adapter, "message")
