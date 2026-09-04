from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from discord_ai_reminder_bot.bot.client import ReminderBot
from discord_ai_reminder_bot.bot.post_draft_runtime import (
    POST_DRAFT_UI_TIMEOUT_SECONDS,
    PostDraftRuntime,
    create_post_draft_runtime,
)
from discord_ai_reminder_bot.bot.post_draft_ui import PostDraftModeView
from discord_ai_reminder_bot.config import Settings
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.post_draft_config import (
    PostDraftUsageSettingsResult,
    PostDraftUsageSettingsState,
)

NOW = datetime(2026, 9, 4, 3, tzinfo=UTC)


def core_settings() -> Settings:
    return Settings(
        APP_ENV="test",
        TIMEZONE="Asia/Tokyo",
        DISCORD_BOT_TOKEN="synthetic-token",
        DISCORD_GUILD_ID=100,
        DISCORD_ALLOWED_ROLE_IDS="200",
        DISCORD_OPERATOR_USER_ID=300,
        DISCORD_OPERATOR_CHANNEL_ID=400,
        DATABASE_URL="postgresql+psycopg://synthetic:synthetic@localhost/test",
        SCHEDULER_MAX_CONCURRENCY=1,
    )


def usage_result(state: PostDraftUsageSettingsState) -> PostDraftUsageSettingsResult:
    return PostDraftUsageSettingsResult(
        state=state,
        policy=None,
        requested_enabled=state is PostDraftUsageSettingsState.CONFIGURED,
    )


def interaction(*, user_id: object = 123, guild_id: object = 100) -> SimpleNamespace:
    response = SimpleNamespace(send_message=AsyncMock())
    return SimpleNamespace(user=SimpleNamespace(id=user_id), guild_id=guild_id, response=response)


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
    runtime = create_post_draft_runtime(
        settings=usage_result(PostDraftUsageSettingsState.DISABLED),
        session_factory=MagicMock(),
        clock=FixedClock(NOW),
    )
    first, second = interaction(), interaction()
    await runtime.start(first)
    await runtime.start(second)
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
    assert "手入力" in first_call.kwargs["content"]
    assert runtime.composition.service is second_view.ui.controller._generation_service
    assert POST_DRAFT_UI_TIMEOUT_SECONDS > 0


@pytest.mark.parametrize("user_id,guild_id", [(True, 100), (0, 100), (123, None), (123, True)])
@pytest.mark.asyncio
async def test_command_rejects_dm_and_invalid_ids_ephemerally(
    user_id: object, guild_id: object
) -> None:
    runtime = create_post_draft_runtime(
        settings=usage_result(PostDraftUsageSettingsState.INVALID),
        session_factory=MagicMock(),
        clock=FixedClock(NOW),
    )
    attempted = interaction(user_id=user_id, guild_id=guild_id)
    await runtime.start(attempted)
    attempted.response.send_message.assert_awaited_once()
    kwargs = attempted.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["view"] is None


def test_bot_registers_post_compose_once_without_sync_or_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_runtime = MagicMock()
    runtime = MagicMock(spec=PostDraftRuntime)
    create_runtime.return_value = runtime
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.client.create_post_draft_runtime", create_runtime
    )
    engine = MagicMock()
    sessions = MagicMock()
    bot = ReminderBot(
        settings=core_settings(),
        engine=engine,
        session_factory=sessions,
        clock=FixedClock(NOW),
        worker_id=uuid.uuid7(),
        logger=logging.getLogger("test.post-draft-runtime"),
        post_draft_usage_settings=usage_result(PostDraftUsageSettingsState.DISABLED),
    )
    create_runtime.assert_called_once()
    guild = discord.Object(id=100)
    post = bot.tree.get_command("post", guild=guild)
    assert post is bot.post_commands
    compose = post.get_command("compose")
    assert compose is not None
    assert compose.description == "投稿する文章を作成します"
    assert bot.tree.get_command("post") is None
    assert not hasattr(runtime, "sessions")
    sessions.assert_not_called()
    engine.assert_not_called()
