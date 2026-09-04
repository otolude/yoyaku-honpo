from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from discord_ai_reminder_bot.bot.post_draft_runtime import (
    POST_DRAFT_UI_TIMEOUT_SECONDS,
    PostDraftRuntime,
    create_post_draft_runtime,
)
from discord_ai_reminder_bot.bot.post_draft_ui import PostDraftModeView
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.post_draft_config import (
    PostDraftUsageSettingsResult,
    PostDraftUsageSettingsState,
)

NOW = datetime(2026, 9, 4, 3, tzinfo=UTC)


def usage_result(state: PostDraftUsageSettingsState) -> PostDraftUsageSettingsResult:
    return PostDraftUsageSettingsResult(
        state=state,
        policy=None,
        requested_enabled=state is PostDraftUsageSettingsState.CONFIGURED,
    )


def interaction(*, user_id: object = 123, guild_id: object = 100) -> SimpleNamespace:
    response = SimpleNamespace(send_message=AsyncMock())
    return SimpleNamespace(user=SimpleNamespace(id=user_id), guild_id=guild_id, response=response)


def disabled_runtime() -> tuple[PostDraftRuntime, MagicMock]:
    service = MagicMock()
    composition = MagicMock(effective_enabled=False, service=service)
    return PostDraftRuntime(composition=composition, clock=FixedClock(NOW)), service


@pytest.mark.parametrize(
    "state",
    [
        PostDraftUsageSettingsState.DISABLED,
        PostDraftUsageSettingsState.INVALID,
        PostDraftUsageSettingsState.CONFIGURED,
    ],
)
def test_runtime_composes_once_and_remains_effectively_disabled(
    monkeypatch: pytest.MonkeyPatch, state: PostDraftUsageSettingsState
) -> None:
    compose = MagicMock()
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.post_draft_runtime.compose_post_draft_services", compose
    )
    composition = MagicMock(effective_enabled=False)
    compose.return_value = composition
    sessions = MagicMock()
    runtime = create_post_draft_runtime(
        settings=usage_result(state), session_factory=sessions, clock=FixedClock(NOW)
    )
    assert isinstance(runtime, PostDraftRuntime)
    assert runtime.composition is composition
    compose.assert_called_once_with(settings=usage_result(state), session_factory=sessions)
    sessions.assert_not_called()


@pytest.mark.asyncio
async def test_command_creates_distinct_sessions_and_disabled_ephemeral_views() -> None:
    runtime, service = disabled_runtime()
    first, second = interaction(), interaction()
    await runtime.start(first)
    await runtime.start(second)
    assert {field.name for field in fields(runtime)} == {"composition", "clock"}
    assert not hasattr(runtime, "sessions")
    for retained_name in (
        "session_registry",
        "interactions",
        "messages",
        "interaction_tokens",
        "provider_client",
        "openai_client",
        "db_session",
        "background_tasks",
    ):
        assert not hasattr(runtime, retained_name)
    first_call = first.response.send_message.await_args
    second_call = second.response.send_message.await_args
    first_view = first_call.kwargs["view"]
    second_view = second_call.kwargs["view"]
    assert isinstance(first_view, PostDraftModeView)
    assert isinstance(second_view, PostDraftModeView)
    assert first_view is not second_view
    assert first_view.ui.controller.session is not second_view.ui.controller.session
    assert first_call.kwargs["ephemeral"] is True
    assert (
        first_call.kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
    )
    ai = next(child for child in first_view.children if child.custom_id == "post_draft_mode_ai")
    manual = next(
        child for child in first_view.children if child.custom_id == "post_draft_mode_manual"
    )
    cancel = next(child for child in first_view.children if child.custom_id == "post_draft_cancel")
    assert ai.disabled and "準備中" in ai.label
    assert not manual.disabled and not cancel.disabled
    assert "手入力" in first_call.args[0]
    assert service is second_view.ui.controller._generation_service
    assert POST_DRAFT_UI_TIMEOUT_SECONDS > 0


@pytest.mark.parametrize("user_id,guild_id", [(True, 100), (0, 100), (123, None), (123, True)])
@pytest.mark.asyncio
async def test_command_rejects_dm_and_invalid_ids_ephemerally(
    user_id: object, guild_id: object
) -> None:
    runtime, _ = disabled_runtime()
    attempted = interaction(user_id=user_id, guild_id=guild_id)
    await runtime.start(attempted)
    attempted.response.send_message.assert_awaited_once()
    kwargs = attempted.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["view"] is None
