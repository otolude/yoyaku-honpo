from __future__ import annotations

import ast
import asyncio
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import discord
import pytest
from discord_ai_reminder_bot.bot.post_draft_ui import (
    PostDraftAIInputModal,
    PostDraftAISettingsView,
    PostDraftDiscordUI,
    PostDraftEditModal,
    PostDraftManualInputModal,
    PostDraftModeView,
    PostDraftPreviewView,
    create_post_draft_mode_view,
    post_draft_ui_error_message,
)

from discord_ai_reminder_bot.application.post_draft_ui_session import (
    PostDraftUIErrorCode,
    PostDraftUISession,
    PostDraftUISessionController,
    PostDraftUISessionError,
    PostDraftUISessionState,
)
from discord_ai_reminder_bot.application.post_draft_usage import PostDraftUsageReservation
from discord_ai_reminder_bot.domain.post_draft_generation import (
    GeneratedPostDraft,
)

NOW = datetime(2026, 9, 4, 3, tzinfo=UTC)
OWNER = 123
GUILD = 456
CANARY = "discord-ui-private-canary"
MODULE = Path("src/discord_ai_reminder_bot/bot/post_draft_ui.py")


class FakeGenerationService:
    def __init__(self, outcome: object = GeneratedPostDraft("生成本文")) -> None:
        self.outcome = outcome
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def generate(self, _request: object, _reservation: object) -> GeneratedPostDraft:
        self.calls += 1
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
    adapter = PostDraftDiscordUI(
        controller=value,
        now=lambda: NOW,
        reservation_factory=lambda _now: cast(PostDraftUsageReservation, object()),
        timeout_seconds=60,
    )
    return adapter, generation


def item(view: discord.ui.View, custom_id: str) -> discord.ui.Item[object]:
    return next(child for child in view.children if child.custom_id == custom_id)


def set_text(text_input: discord.ui.TextInput[object], value: str) -> None:
    text_input._value = value


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
    assert ai.purpose.label == "文章の目的"
    assert ai.purpose.required and ai.purpose.min_length == 1 and ai.purpose.max_length == 200
    assert ai.key_points.label == "含めたい要点"
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
        )


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
    with pytest.raises(PostDraftUISessionError):
        await item(view, "post_draft_mode_ai").callback(allowed)
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
