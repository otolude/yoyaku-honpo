from __future__ import annotations

import asyncio
import inspect
import socket
import time
from dataclasses import fields
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from discord_ai_reminder_bot.application.post_draft_generation import DisabledPostDraftGenerator
from discord_ai_reminder_bot.application.post_draft_ui_session import (
    PostDraftUISessionController,
    PostDraftUISessionState,
)
from discord_ai_reminder_bot.application.post_draft_usage_generation import (
    GeneratePostDraftWithUsageService,
)
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


# Exercise the production graph and discord.py dispatch, not a mocked Composition.
CANCEL_CANARY = "synthetic-cancel-private-detail"


class StrictCancelInteraction:
    def __init__(self, events: list[str], failure: str | None = None) -> None:
        self.events = events
        self.failure = failure
        self.user = SimpleNamespace(id=123)
        self.guild_id = 100
        self.data = {}
        self.response = self
        self.done = False
        self.view = None
        self.calls: list[tuple[str, dict]] = []
        self.started = 0.0
        self.defer_started = None

    def is_done(self) -> bool:
        return self.done

    async def _initial(self, method: str, *args, **kwargs) -> None:
        inspect.signature(getattr(discord.InteractionResponse, method)).bind(self, *args, **kwargs)
        self.events.append(method)
        self.calls.append((method, kwargs))
        if method == "defer":
            self.defer_started = time.monotonic()
            assert not kwargs.get("thinking", False)
        if self.done:
            raise discord.InteractionResponded(self)
        if self.failure == method:
            raise RuntimeError(CANCEL_CANARY)
        self.done = True
        if method == "send_message":
            self.view = kwargs.get("view")

    async def send_message(self, *args, **kwargs) -> None:
        await self._initial("send_message", *args, **kwargs)

    async def edit_message(self, *args, **kwargs) -> None:
        await self._initial("edit_message", *args, **kwargs)

    async def defer(self, *args, **kwargs) -> None:
        await self._initial("defer", *args, **kwargs)

    async def edit_original_response(self, **kwargs) -> None:
        inspect.signature(discord.Interaction.edit_original_response).bind(self, **kwargs)
        assert self.done
        self.events.append("render")
        self.calls.append(("render", kwargs))
        if self.failure == "render":
            raise RuntimeError(CANCEL_CANARY)


class CancelLock:
    def __init__(self, events: list[str], name: str) -> None:
        self.events = events
        self.name = name
        self.lock = asyncio.Lock()
        self.entered = asyncio.Event()

    async def __aenter__(self) -> None:
        self.events.append(self.name)
        self.entered.set()
        await self.lock.acquire()

    async def __aexit__(self, *args) -> None:
        self.lock.release()


@pytest_asyncio.fixture
async def initial_cancel(monkeypatch):
    forbidden = MagicMock(side_effect=AssertionError("unexpected side effect"))
    monkeypatch.setattr(async_sessionmaker, "__call__", forbidden)
    monkeypatch.setattr(GeneratePostDraftWithUsageService, "generate", forbidden)
    monkeypatch.setattr(DisabledPostDraftGenerator, "generate", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    runtime = create_post_draft_runtime(
        settings=usage_result(PostDraftUsageSettingsState.DISABLED),
        session_factory=async_sessionmaker(),
        clock=FixedClock(NOW),
    )
    events: list[str] = []
    initial = StrictCancelInteraction(events)
    await runtime.start(initial)
    view = initial.view
    assert isinstance(view, PostDraftModeView)
    assert runtime.composition.effective_enabled is False
    ui = view.ui
    assert ui._active_component is view
    button = next(child for child in view.children if child.custom_id == "post_draft_cancel")
    assert not button.disabled
    ui._ui_lock = CancelLock(events, "ui_lock")
    ui.controller.session._lock = CancelLock(events, "session_lock")
    original = PostDraftUISessionController.cancel
    calls = []

    async def cancel(controller, **kwargs):
        events.append("controller")
        calls.append(None)
        await original(controller, **kwargs)

    monkeypatch.setattr(PostDraftUISessionController, "cancel", cancel)
    events.clear()
    yield SimpleNamespace(view=view, ui=ui, button=button, events=events, cancels=calls)
    view.stop()
    if ui._active_component is not None:
        ui._active_component.stop()
    forbidden.assert_not_called()


async def dispatch_cancel(case, interaction):
    interaction.started = time.monotonic()
    await case.view._scheduled_task(case.button, interaction)


def assert_cancel_log(caplog, *events):
    records = [r for r in caplog.records if r.name.endswith("post_draft_ui")]
    assert [r.getMessage() for r in records] == list(events)
    for record in records:
        assert not record.args
        assert record.exc_info is None
        assert record.exc_text is None
        assert CANCEL_CANARY not in str(record.__dict__)


@pytest.mark.asyncio
async def test_initial_cancel_acknowledges_before_state_and_locks(initial_cancel):
    case = initial_cancel
    session = case.ui.controller.session
    session.request = object()
    session._draft = object()
    session._active_generation_token = object()
    clicked = StrictCancelInteraction(case.events)
    await dispatch_cancel(case, clicked)
    assert case.events == ["defer", "ui_lock", "controller", "session_lock", "ui_lock", "render"]
    assert clicked.defer_started - clicked.started < 0.25
    assert len(case.cancels) == 1
    assert session.state is PostDraftUISessionState.CANCELLED
    assert session.request is session._draft is session._active_generation_token is None
    assert case.view.is_finished() and all(child.disabled for child in case.view.children)
    assert [method for method, _ in clicked.calls] == ["defer", "render"]
    assert clicked.done
    assert clicked.calls[-1][1]["view"] is None


@pytest.mark.parametrize("lock_name", ["ui", "session"])
@pytest.mark.asyncio
async def test_initial_cancel_acknowledges_while_lock_is_blocked(initial_cancel, lock_name):
    case = initial_cancel
    lock = case.ui._ui_lock if lock_name == "ui" else case.ui.controller.session._lock
    await lock.lock.acquire()
    clicked = StrictCancelInteraction(case.events)
    task = asyncio.create_task(dispatch_cancel(case, clicked))
    try:
        await asyncio.wait_for(lock.entered.wait(), timeout=1)
        assert clicked.done, "lock waiting must not begin before acknowledgement"
        assert clicked.defer_started - clicked.started < 0.25
        await asyncio.sleep(3.05)
        assert clicked.done and not task.done()
    finally:
        lock.lock.release()
        try:
            await asyncio.wait_for(task, timeout=1)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert len(case.cancels) == 1


@pytest.mark.asyncio
async def test_initial_cancel_defer_failure_reaches_on_error_without_mutation(
    initial_cancel, caplog
):
    case = initial_cancel
    before = case.ui.controller.session.state
    on_error = AsyncMock(wraps=case.view.on_error)
    case.view.on_error = on_error
    clicked = StrictCancelInteraction(case.events, "defer")
    await dispatch_cancel(case, clicked)
    assert case.cancels == []
    assert case.events == ["defer"]
    assert case.ui.controller.session.state is before
    assert not case.view._consumed and not case.view.is_finished()
    on_error.assert_awaited_once()
    safe_error = on_error.await_args.args[1]
    assert safe_error.__cause__ is safe_error.__context__ is None
    assert CANCEL_CANARY not in repr(safe_error)
    assert_cancel_log(caplog, "cancel_defer_failed", "view_callback_failed")


@pytest.mark.asyncio
async def test_initial_cancel_controller_failure_uses_only_original_response(
    initial_cancel, monkeypatch, caplog
):
    case = initial_cancel

    async def fail(*args, **kwargs):
        raise RuntimeError(CANCEL_CANARY)

    monkeypatch.setattr(PostDraftUISessionController, "cancel", fail)
    clicked = StrictCancelInteraction(case.events)
    await dispatch_cancel(case, clicked)
    assert [method for method, _ in clicked.calls] == ["defer", "render"]
    assert clicked.done
    assert case.ui.controller.session.state is PostDraftUISessionState.MODE_SELECTION
    assert_cancel_log(caplog, "cancel_controller_failed")


@pytest.mark.asyncio
async def test_initial_cancel_render_failure_is_terminal_and_observable(initial_cancel, caplog):
    case = initial_cancel
    clicked = StrictCancelInteraction(case.events, "render")
    await dispatch_cancel(case, clicked)
    assert [method for method, _ in clicked.calls] == ["defer", "render"]
    assert len(case.cancels) == 1
    assert case.ui.controller.session.state is PostDraftUISessionState.CANCELLED
    assert case.view.is_finished()
    assert_cancel_log(caplog, "cancel_render_failed")


@pytest.mark.asyncio
async def test_initial_cancel_stale_does_not_change_current_session(initial_cancel):
    case = initial_cancel
    current = PostDraftModeView(ui=case.ui, timeout=60)
    case.ui.activate_initial(current)
    clicked = StrictCancelInteraction(case.events)
    await dispatch_cancel(case, clicked)
    assert case.cancels == []
    assert case.ui.controller.session.state is PostDraftUISessionState.MODE_SELECTION
    assert case.ui._active_component is current and not current.is_finished()
    assert [method for method, _ in clicked.calls] == ["defer", "render"]


@pytest.mark.asyncio
async def test_initial_cancel_double_click_claims_once(initial_cancel):
    case = initial_cancel
    clicks = [StrictCancelInteraction(case.events), StrictCancelInteraction(case.events)]
    await asyncio.gather(*(dispatch_cancel(case, clicked) for clicked in clicks))
    assert len(case.cancels) == 1
    assert case.ui.controller.session.state is PostDraftUISessionState.CANCELLED
    for clicked in clicks:
        assert [method for method, _ in clicked.calls] == ["defer", "render"]


@pytest.mark.asyncio
async def test_initial_cancel_already_acknowledged_is_not_reprocessed(initial_cancel):
    case = initial_cancel
    clicked = StrictCancelInteraction(case.events)
    clicked.done = True
    await dispatch_cancel(case, clicked)
    assert clicked.calls == [] and case.cancels == []
    assert not case.view._consumed


@pytest.mark.parametrize("done", [False, True])
@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.asyncio
async def test_initial_cancel_view_error_is_safe_and_bounded(initial_cancel, caplog, done, failure):
    case = initial_cancel
    clicked = StrictCancelInteraction(case.events)
    clicked.done = done
    method = "render" if done else "send_message"
    clicked.failure = method if failure else None

    async def callback(_interaction):
        raise RuntimeError(CANCEL_CANARY)

    case.button.callback = callback
    await dispatch_cancel(case, clicked)
    assert [name for name, _ in clicked.calls] == [method]
    expected = ["view_callback_failed"]
    if failure:
        expected.append("view_error_response_failed")
    assert_cancel_log(caplog, *expected)
