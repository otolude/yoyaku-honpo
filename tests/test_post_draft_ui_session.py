from __future__ import annotations

import ast
import asyncio
import logging
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from discord_ai_reminder_bot.application.post_draft_generation import (
    PostDraftDisabledError,
    PostDraftInvalidResponseError,
    PostDraftTimeoutError,
    PostDraftUnavailableError,
    PostDraftUnknownError,
)
from discord_ai_reminder_bot.application.post_draft_ui_session import (
    PostDraftUIErrorCode,
    PostDraftUISession,
    PostDraftUISessionController,
    PostDraftUISessionError,
    PostDraftUISessionState,
)
from discord_ai_reminder_bot.application.post_draft_usage import PostDraftUsageReservation
from discord_ai_reminder_bot.application.post_draft_usage_generation import PostDraftUsageError
from discord_ai_reminder_bot.domain.post_draft_generation import (
    GeneratedPostDraft,
    PostDraftGenerationRequest,
    PostLength,
    PostTone,
)
from discord_ai_reminder_bot.domain.post_draft_usage import PostDraftUsageReservationCode

NOW = datetime(2026, 9, 4, 3, tzinfo=UTC)
OWNER = 111
GUILD = 222
CANARY = "ui-private-canary"
MODULE = Path("src/discord_ai_reminder_bot/application/post_draft_ui_session.py")


def request(*, purpose: str = "告知") -> PostDraftGenerationRequest:
    return PostDraftGenerationRequest(
        purpose=purpose,
        key_points="9月開催",
        tone=PostTone.POLITE,
        length=PostLength.SHORT,
    )


def reservation() -> PostDraftUsageReservation:
    return cast(PostDraftUsageReservation, object())


class FakeGenerationService:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.outcomes = outcomes or [GeneratedPostDraft("生成本文")]
        self.calls = 0
        self.active = 0
        self.maximum_active = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def generate(self, _request: object, _reservation: object) -> GeneratedPostDraft:
        self.calls += 1
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.entered.set()
        try:
            if self.block:
                await self.release.wait()
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return cast(GeneratedPostDraft, outcome)
        finally:
            self.active -= 1


def session() -> PostDraftUISession:
    return PostDraftUISession.create(
        owner_user_id=OWNER,
        guild_id=GUILD,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )


def controller(
    fake: FakeGenerationService | None = None,
) -> tuple[PostDraftUISessionController, FakeGenerationService]:
    service = fake or FakeGenerationService()
    return PostDraftUISessionController(session=session(), generation_service=service), service


async def choose_ai(value: PostDraftUISessionController) -> None:
    await value.choose_ai(owner_user_id=OWNER, guild_id=GUILD, now=NOW)


async def generate(value: PostDraftUISessionController) -> GeneratedPostDraft:
    return await value.generate(
        request=request(),
        reservation=reservation(),
        owner_user_id=OWNER,
        guild_id=GUILD,
        now=NOW,
    )


@pytest.mark.asyncio
async def test_initial_and_complete_ai_state_flow() -> None:
    value, fake = controller()
    assert value.session.state is PostDraftUISessionState.MODE_SELECTION
    await choose_ai(value)
    assert value.session.state is PostDraftUISessionState.AI_INPUT
    generated = await generate(value)
    assert generated.value == "生成本文"
    assert value.session.state is PostDraftUISessionState.PREVIEW
    await value.begin_edit(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    assert value.session.state is PostDraftUISessionState.EDITING
    await value.confirm_edit(text="編集本文", owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    accepted = await value.accept(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    assert accepted.value == "編集本文"
    assert value.session.state is PostDraftUISessionState.ACCEPTED
    assert value.accepted_draft().value == "編集本文"
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_manual_flow_never_calls_generation_service() -> None:
    value, fake = controller()
    await value.choose_manual(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    assert value.session.state is PostDraftUISessionState.MANUAL_ENTRY
    draft = await value.submit_manual(
        text="手入力本文", owner_user_id=OWNER, guild_id=GUILD, now=NOW
    )
    assert draft.value == "手入力本文"
    assert value.session.state is PostDraftUISessionState.PREVIEW
    assert (await value.accept(owner_user_id=OWNER, guild_id=GUILD, now=NOW)).value == "手入力本文"
    assert fake.calls == 0


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (PostDraftDisabledError(), PostDraftUIErrorCode.DISABLED),
        (PostDraftUnavailableError(), PostDraftUIErrorCode.UNAVAILABLE),
        (PostDraftTimeoutError(), PostDraftUIErrorCode.TIMEOUT),
        (PostDraftInvalidResponseError(), PostDraftUIErrorCode.INVALID_RESPONSE),
        (PostDraftUnknownError(), PostDraftUIErrorCode.UNKNOWN),
        (
            PostDraftUsageError(PostDraftUsageReservationCode.USER_RATE_LIMITED),
            PostDraftUIErrorCode.USER_RATE_LIMITED,
        ),
        (
            PostDraftUsageError(PostDraftUsageReservationCode.GUILD_RATE_LIMITED),
            PostDraftUIErrorCode.GUILD_RATE_LIMITED,
        ),
        (
            PostDraftUsageError(PostDraftUsageReservationCode.GLOBAL_DAILY_EXHAUSTED),
            PostDraftUIErrorCode.GLOBAL_DAILY_EXHAUSTED,
        ),
        (
            PostDraftUsageError(PostDraftUsageReservationCode.GLOBAL_MONTHLY_EXHAUSTED),
            PostDraftUIErrorCode.GLOBAL_MONTHLY_EXHAUSTED,
        ),
        (
            PostDraftUsageError(PostDraftUsageReservationCode.GLOBAL_COST_EXHAUSTED),
            PostDraftUIErrorCode.GLOBAL_COST_EXHAUSTED,
        ),
        (
            PostDraftUsageError(PostDraftUsageReservationCode.USAGE_UNAVAILABLE),
            PostDraftUIErrorCode.USAGE_UNAVAILABLE,
        ),
        (RuntimeError(CANARY), PostDraftUIErrorCode.UNKNOWN),
    ],
)
@pytest.mark.asyncio
async def test_generation_failures_are_fixed_and_restore_ai_input(
    error: BaseException, code: PostDraftUIErrorCode, caplog: pytest.LogCaptureFixture
) -> None:
    value, fake = controller(FakeGenerationService([error]))
    await choose_ai(value)
    with caplog.at_level(logging.DEBUG), pytest.raises(PostDraftUISessionError) as caught:
        await generate(value)
    assert caught.value.code is code
    assert value.session.state is PostDraftUISessionState.AI_INPUT
    assert fake.calls == 1
    observed = " ".join(
        (
            str(caught.value),
            repr(caught.value),
            repr(caught.value.args),
            repr(vars(caught.value)),
            "".join(traceback.format_exception(caught.value)),
            caplog.text,
        )
    )
    assert CANARY not in observed
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_generation_cancellation_is_same_object_and_restores_state() -> None:
    cancelled = asyncio.CancelledError()
    value, fake = controller(FakeGenerationService([cancelled]))
    await choose_ai(value)
    with pytest.raises(asyncio.CancelledError) as caught:
        await generate(value)
    assert caught.value is cancelled
    assert value.session.state is PostDraftUISessionState.AI_INPUT
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_regeneration_replaces_current_draft_without_history() -> None:
    value, fake = controller(
        FakeGenerationService([GeneratedPostDraft("最初"), GeneratedPostDraft("置換後")])
    )
    await choose_ai(value)
    await generate(value)
    second = await generate(value)
    assert second.value == "置換後"
    assert value.session.current_draft().value == "置換後"
    assert fake.calls == 2
    assert not hasattr(value.session, "history")


@pytest.mark.asyncio
async def test_same_session_operations_are_serialized_and_double_generate_rejected() -> None:
    value, fake = controller()
    fake.block = True
    await choose_ai(value)
    first = asyncio.create_task(generate(value))
    await fake.entered.wait()
    second = asyncio.create_task(generate(value))
    await asyncio.sleep(0)
    assert fake.calls == 1
    assert fake.maximum_active == 1
    fake.release.set()
    assert (await first).value == "生成本文"
    with pytest.raises(PostDraftUISessionError) as caught:
        await second
    assert caught.value.code is PostDraftUIErrorCode.INVALID_TRANSITION
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_double_accept_and_terminal_operations_are_rejected() -> None:
    value, _ = controller()
    await choose_ai(value)
    await generate(value)
    await value.accept(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    for operation in (
        lambda: value.accept(owner_user_id=OWNER, guild_id=GUILD, now=NOW),
        lambda: value.cancel(owner_user_id=OWNER, guild_id=GUILD, now=NOW),
        lambda: value.begin_edit(owner_user_id=OWNER, guild_id=GUILD, now=NOW),
    ):
        with pytest.raises(PostDraftUISessionError) as caught:
            await operation()
        assert caught.value.code is PostDraftUIErrorCode.INVALID_TRANSITION


@pytest.mark.asyncio
async def test_wrong_owner_and_guild_are_fixed_errors() -> None:
    value, _ = controller()
    with pytest.raises(PostDraftUISessionError) as owner_error:
        await value.choose_ai(owner_user_id=999, guild_id=GUILD, now=NOW)
    assert owner_error.value.code is PostDraftUIErrorCode.NOT_OWNER
    with pytest.raises(PostDraftUISessionError) as guild_error:
        await value.choose_ai(owner_user_id=OWNER, guild_id=999, now=NOW)
    assert guild_error.value.code is PostDraftUIErrorCode.NOT_OWNER
    assert "999" not in repr(owner_error.value)
    assert "999" not in repr(guild_error.value)


@pytest.mark.parametrize(
    "owner,guild", [(True, GUILD), (0, GUILD), (-1, GUILD), (OWNER, True), (OWNER, 0), (OWNER, -1)]
)
def test_owner_and_guild_ids_are_strict_positive_integers(owner: object, guild: object) -> None:
    with pytest.raises(ValueError):
        PostDraftUISession.create(
            owner_user_id=owner,
            guild_id=guild,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=1),
        )


@pytest.mark.asyncio
async def test_expiry_boundary_and_payload_release() -> None:
    value, fake = controller()
    await value.choose_ai(
        owner_user_id=OWNER, guild_id=GUILD, now=NOW + timedelta(minutes=14, seconds=59)
    )
    with pytest.raises(PostDraftUISessionError) as caught:
        await value.choose_ai(owner_user_id=OWNER, guild_id=GUILD, now=NOW + timedelta(minutes=15))
    assert caught.value.code is PostDraftUIErrorCode.EXPIRED
    assert value.session.state is PostDraftUISessionState.EXPIRED
    assert value.session.current_draft() is None
    assert value.session.request is None
    assert fake.calls == 0
    elapsed, _ = controller()
    with pytest.raises(PostDraftUISessionError) as elapsed_error:
        await elapsed.choose_ai(
            owner_user_id=OWNER,
            guild_id=GUILD,
            now=NOW + timedelta(minutes=15, seconds=1),
        )
    assert elapsed_error.value.code is PostDraftUIErrorCode.EXPIRED


def test_naive_session_and_operation_times_are_rejected() -> None:
    with pytest.raises(ValueError):
        PostDraftUISession.create(
            owner_user_id=OWNER,
            guild_id=GUILD,
            created_at=NOW.replace(tzinfo=None),
            expires_at=NOW,
        )


@pytest.mark.asyncio
async def test_naive_operation_time_is_rejected() -> None:
    value, _ = controller()
    with pytest.raises(ValueError):
        await value.choose_ai(
            owner_user_id=OWNER,
            guild_id=GUILD,
            now=NOW.replace(tzinfo=None),
        )


@pytest.mark.asyncio
async def test_cancel_releases_payload_and_is_terminal() -> None:
    value, _ = controller()
    await choose_ai(value)
    await value.cancel(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
    assert value.session.state is PostDraftUISessionState.CANCELLED
    assert value.session.request is None
    assert value.session.current_draft() is None


def test_repr_excludes_payload_ids_and_lock() -> None:
    value = session()
    observed = repr(value)
    assert str(OWNER) not in observed
    assert str(GUILD) not in observed
    assert CANARY not in observed
    assert "Lock" not in observed


def imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                modules.add(f"relative:{node.level}:{node.module or ''}")
            else:
                modules.add(node.module or "")
    return modules


def test_application_module_imports_are_strictly_allowlisted() -> None:
    allowed = {
        "__future__",
        "asyncio",
        "dataclasses",
        "datetime",
        "enum",
        "typing",
        "discord_ai_reminder_bot.application.post_draft_generation",
        "discord_ai_reminder_bot.application.post_draft_usage",
        "discord_ai_reminder_bot.application.post_draft_usage_generation",
        "discord_ai_reminder_bot.domain.post_draft_generation",
        "discord_ai_reminder_bot.domain.post_draft_usage",
    }
    assert imported_modules(MODULE.read_text(encoding="utf-8")) <= allowed


@pytest.mark.parametrize(
    "source",
    [
        "import discord",
        "import sqlalchemy as sa",
        "from openai import AsyncOpenAI",
        "from discord_ai_reminder_bot.infrastructure.database import models",
        "from . import post_draft_composition",
        "from discord import Interaction as I",
    ],
)
def test_import_guard_rejects_forbidden_import_canaries(source: str) -> None:
    allowed = {"asyncio"}
    assert not imported_modules(source) <= allowed


def test_import_guard_ignores_non_import_text() -> None:
    source = '''"""import discord"""\n# from openai import OpenAI\nVALUE = "import sqlalchemy"\nimport asyncio\n'''
    assert imported_modules(source) == {"asyncio"}


def test_module_has_no_background_task_creation_or_persistence() -> None:
    source = MODULE.read_text(encoding="utf-8")
    assert "create_task" not in source
    assert "ensure_future" not in source
    assert "pickle" not in source
    assert "json" not in imported_modules(source)
    assert "logging" not in imported_modules(source)


def test_discord_py_version_and_public_types_are_not_runtime_dependencies() -> None:
    import discord

    assert discord.__version__ == "2.7.1"
    source = MODULE.read_text(encoding="utf-8")
    assert "discord" not in imported_modules(source)


async def start_blocked_generation(
    outcomes: list[object] | None = None,
) -> tuple[PostDraftUISessionController, FakeGenerationService, asyncio.Task[GeneratedPostDraft]]:
    value, fake = controller(FakeGenerationService(outcomes))
    fake.block = True
    await choose_ai(value)
    task = asyncio.create_task(generate(value))
    await fake.entered.wait()
    assert value.session.state is PostDraftUISessionState.GENERATING
    return value, fake, task


async def observe_before_release(operation: object) -> tuple[bool, object]:
    task = asyncio.create_task(cast(object, operation))  # type: ignore[arg-type]
    done, _ = await asyncio.wait({task}, timeout=0.05)
    return bool(done), task


@pytest.mark.parametrize(
    "outcome",
    [
        GeneratedPostDraft("遅延成功"),
        PostDraftUnavailableError(),
        RuntimeError(CANARY),
    ],
)
@pytest.mark.asyncio
async def test_cancel_completes_during_generation_and_discards_late_outcome(
    outcome: object, caplog: pytest.LogCaptureFixture
) -> None:
    value, fake, generation = await start_blocked_generation([outcome])
    with caplog.at_level(logging.DEBUG):
        completed, cancel_task = await observe_before_release(
            value.cancel(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
        )
        state_before_release = value.session.state
        fake.release.set()
        with pytest.raises(PostDraftUISessionError) as caught:
            await generation
        await cast(asyncio.Task[None], cancel_task)
    assert completed
    assert state_before_release is PostDraftUISessionState.CANCELLED
    assert value.session.state is PostDraftUISessionState.CANCELLED
    assert caught.value.code is PostDraftUIErrorCode.CANCELLED
    assert value.session.request is None
    assert value.session.current_draft() is None
    assert fake.calls == 1
    observed = " ".join(
        (
            str(caught.value),
            repr(caught.value),
            repr(caught.value.args),
            repr(vars(caught.value)),
            "".join(traceback.format_exception(caught.value)),
            caplog.text,
        )
    )
    assert CANARY not in observed
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "outcome", [GeneratedPostDraft("遅延成功"), PostDraftUnavailableError(), RuntimeError(CANARY)]
)
@pytest.mark.asyncio
async def test_expire_completes_during_generation_and_discards_late_outcome(
    outcome: object,
) -> None:
    value, fake, generation = await start_blocked_generation([outcome])
    completed, expire_task = await observe_before_release(
        value.expire(
            owner_user_id=OWNER,
            guild_id=GUILD,
            now=NOW + timedelta(minutes=15),
        )
    )
    state_before_release = value.session.state
    fake.release.set()
    with pytest.raises(PostDraftUISessionError) as caught:
        await generation
    await cast(asyncio.Task[None], expire_task)
    assert completed
    assert state_before_release is PostDraftUISessionState.EXPIRED
    assert value.session.state is PostDraftUISessionState.EXPIRED
    assert caught.value.code is PostDraftUIErrorCode.EXPIRED
    assert value.session.request is None
    assert value.session.current_draft() is None
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_second_generate_is_rejected_before_first_is_released() -> None:
    value, fake, first = await start_blocked_generation()
    second = asyncio.create_task(generate(value))
    done, _ = await asyncio.wait({second}, timeout=0.05)
    fake.release.set()
    assert (await first).value == "生成本文"
    with pytest.raises(PostDraftUISessionError) as caught:
        await second
    assert done
    assert caught.value.code is PostDraftUIErrorCode.INVALID_TRANSITION
    assert fake.calls == 1


@pytest.mark.parametrize("operation", ["edit", "accept", "manual", "ai", "manual_mode"])
@pytest.mark.asyncio
async def test_other_operations_are_rejected_while_generation_is_blocked(
    operation: str,
) -> None:
    value, fake, generation = await start_blocked_generation()
    operations = {
        "edit": value.begin_edit(owner_user_id=OWNER, guild_id=GUILD, now=NOW),
        "accept": value.accept(owner_user_id=OWNER, guild_id=GUILD, now=NOW),
        "manual": value.submit_manual(text="本文", owner_user_id=OWNER, guild_id=GUILD, now=NOW),
        "ai": value.choose_ai(owner_user_id=OWNER, guild_id=GUILD, now=NOW),
        "manual_mode": value.choose_manual(owner_user_id=OWNER, guild_id=GUILD, now=NOW),
    }
    selected = operations.pop(operation)
    for unused in operations.values():
        unused.close()  # type: ignore[attr-defined]
    completed, operation_task = await observe_before_release(selected)
    state_before_release = value.session.state
    fake.release.set()
    await generation
    with pytest.raises(PostDraftUISessionError) as caught:
        await cast(asyncio.Task[object], operation_task)
    assert completed
    assert state_before_release is PostDraftUISessionState.GENERATING
    assert caught.value.code is PostDraftUIErrorCode.INVALID_TRANSITION
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_generation_coroutine_cancellation_restores_ai_input_and_rethrows_same_object() -> (
    None
):
    cancellation = asyncio.CancelledError()
    value, fake, generation = await start_blocked_generation([cancellation])
    fake.release.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await generation
    assert value.session.state is PostDraftUISessionState.AI_INPUT
    assert value.session.request is None
    assert value.session.current_draft() is None
    assert fake.calls == 1
    assert caught.value is cancellation


@pytest.mark.parametrize("terminal", ["cancelled", "expired"])
@pytest.mark.asyncio
async def test_terminal_operation_wins_race_with_generation_coroutine_cancellation(
    terminal: str,
) -> None:
    cancellation = asyncio.CancelledError()
    value, fake, generation = await start_blocked_generation([cancellation])
    if terminal == "cancelled":
        terminal_operation = value.cancel(owner_user_id=OWNER, guild_id=GUILD, now=NOW)
        expected = PostDraftUISessionState.CANCELLED
    else:
        terminal_operation = value.expire(
            owner_user_id=OWNER,
            guild_id=GUILD,
            now=NOW + timedelta(minutes=15),
        )
        expected = PostDraftUISessionState.EXPIRED
    completed, terminal_task = await observe_before_release(terminal_operation)
    fake.release.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await generation
    await cast(asyncio.Task[None], terminal_task)
    assert completed
    assert value.session.state is expected
    assert value.session.request is None
    assert value.session.current_draft() is None
    assert fake.calls == 1
    assert caught.value is cancellation


def test_cancelled_error_code_is_unique_and_background_apis_remain_absent() -> None:
    assert PostDraftUIErrorCode.CANCELLED.value == "cancelled"
    assert len({code.value for code in PostDraftUIErrorCode}) == len(PostDraftUIErrorCode)
    source = MODULE.read_text(encoding="utf-8")
    assert "create_task" not in source
    assert "ensure_future" not in source
    assert "TaskGroup" not in source
