import ast
import asyncio
import math
import sys
import uuid
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
    IdempotentScheduleCreationCode,
    IdempotentScheduleCreationResult,
    OnceScheduleCreationFingerprint,
    ScheduleCreationPublicId,
    ScheduleCreationRecord,
    ScheduleCreationWaitLimit,
    classify_schedule_creation_record,
)
from discord_ai_reminder_bot.application.schedule_creation import (
    CreatedOnceSchedule,
    CreatedRecurringSchedule,
    DuplicateScheduleWarning,
)
from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.infrastructure.database.idempotent_schedule_creation_repository import (
    PostgreSQLIdempotentScheduleCreationRepository,
    _Observation,
)

NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)


def test_application_idempotency_module_has_no_sqlalchemy_or_infrastructure_imports() -> None:
    source = Path(
        "src/discord_ai_reminder_bot/application/idempotent_schedule_creation.py"
    ).read_text()
    imported = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    )
    assert not any(name == "sqlalchemy" or name.startswith("sqlalchemy.") for name in imported)
    assert not any(".infrastructure" in name for name in imported)


def test_existing_created_dto_repr_is_unchanged() -> None:
    public_id = uuid.UUID("0198bb00-0000-7000-8000-000000000001")
    once = CreatedOnceSchedule(
        public_id=public_id,
        channel_id=20,
        status=ScheduleStatus.ACTIVE,
        content="body",
        scheduled_for=NOW,
    )
    recurring = CreatedRecurringSchedule(
        public_id=public_id,
        channel_id=20,
        schedule_type=ScheduleType.DAILY,
        status=ScheduleStatus.ACTIVE,
        content="body",
        local_time=NOW.time().replace(tzinfo=None),
        weekday=None,
        end_date=None,
        next_run_at=NOW,
    )
    assert "public_id=UUID(" in repr(once) and "content='body'" in repr(once)
    assert "public_id=UUID(" in repr(recurring) and "content='body'" in repr(recurring)


@pytest.mark.parametrize("value", [False, 0, -1, math.nan, math.inf, 10**1000, "1", None])
def test_wait_limit_rejects_nonpositive_nonfinite_and_invalid_values(value: object) -> None:
    with pytest.raises(ValueError) as captured:
        ScheduleCreationWaitLimit.create(value)
    assert repr(value) not in str(captured.value)


def test_wait_limit_is_finite_hidden_and_not_an_already_created_deadline() -> None:
    limit = ScheduleCreationWaitLimit.create(0.25)
    assert limit.seconds == 0.25
    assert "0.25" not in repr(limit)
    assert "already_created" not in ScheduleCreationWaitLimit.__doc__.lower()


def test_wait_limit_overflow_has_no_input_exception_context() -> None:
    with pytest.raises(ValueError) as captured:
        ScheduleCreationWaitLimit.create(10**1000)

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def complete_fingerprint_and_record():
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    expected = OnceScheduleCreationFingerprint.create(
        public_id=key,
        guild_id=10,
        channel_id=20,
        creator_user_id=30,
        scheduled_for=NOW + timedelta(hours=25),
        content=None,
        allow_duplicate=False,
        now=NOW,
        notification_planning_enabled=True,
        name_generation_enabled=False,
        name_generator_available=False,
    )
    record = ScheduleCreationRecord.matching(expected)
    return expected, record


def test_fingerprint_excludes_observation_timestamp_values() -> None:
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    arguments = {
        "public_id": key,
        "guild_id": 10,
        "channel_id": 20,
        "creator_user_id": 30,
        "scheduled_for": NOW + timedelta(hours=25),
        "content": "body",
        "allow_duplicate": False,
    }
    first = OnceScheduleCreationFingerprint.create(**arguments, now=NOW)
    second = OnceScheduleCreationFingerprint.create(
        **arguments,
        now=NOW + timedelta(microseconds=1),
    )

    assert "operation_at" not in {item.name for item in fields(first)}
    assert first == second
    assert "name_job_created_at" not in {item.name for item in fields(ScheduleCreationRecord)}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schedule_version", 2),
        ("schedule_timestamps_pristine", False),
        ("display_name", "changed"),
        ("display_name_source", "manual"),
        ("deleted_at", NOW),
        ("terminal_at", NOW),
        ("run_status", "processing"),
        ("run_claimed_by_present", True),
        ("run_claimed_at", NOW),
        ("run_lease_expires_at", NOW),
        ("run_started_at", NOW),
        ("run_finished_at", NOW),
        ("run_discord_message_id_present", True),
        ("run_result_code_present", True),
        ("run_error_summary_present", True),
        ("run_timestamps_pristine", False),
        ("creation_log_pristine", False),
        ("notification_attempt_count", 1),
        ("delivery_attempt_count", 1),
        ("name_job_count", 1),
        ("name_job_pristine", False),
        ("creation_notification_planning_enabled", False),
        ("creation_name_generation_enabled", True),
        ("creation_name_generator_available", True),
    ],
)
def test_complete_creation_graph_mismatch_is_conflict(field: str, value: object) -> None:
    expected, record = complete_fingerprint_and_record()
    assert classify_schedule_creation_record(expected, replace(record, **{field: value})) is (
        IdempotentScheduleCreationCode.CONFLICT
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("notification_type", "changed"),
        ("recipient_type", "changed"),
        ("recipient_id", None),
        ("status", "processing"),
        ("deduplication_key", "changed"),
        ("scheduled_at", NOW),
        ("next_attempt_at", None),
        ("attempt_count", 1),
        ("run_linked", False),
        ("pristine", False),
    ],
)
def test_notification_creation_state_mismatch_is_conflict(field: str, value: object) -> None:
    expected, record = complete_fingerprint_and_record()
    first, *remaining = record.notifications
    changed_notification = replace(first, **{field: value})
    changed = replace(record, notifications=(changed_notification, *remaining))
    assert classify_schedule_creation_record(expected, changed) is (
        IdempotentScheduleCreationCode.CONFLICT
    )


def test_result_contract_is_content_free_and_unknown_is_not_retryable() -> None:
    result = IdempotentScheduleCreationResult(IdempotentScheduleCreationCode.UNKNOWN)
    assert tuple(result.__dataclass_fields__) == ("code",)
    assert "must not" in type(result).__doc__.lower()


def test_production_adapter_has_no_test_coordination_hooks() -> None:
    source = Path(
        "src/discord_ai_reminder_bot/infrastructure/database/"
        "idempotent_schedule_creation_repository.py"
    ).read_text()
    names = {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert "_after_preflight_missing" not in names
    assert "_after_graph_flushed" not in names


def test_operation_log_projection_does_not_filter_out_extra_actions() -> None:
    source = Path(
        "src/discord_ai_reminder_bot/infrastructure/database/"
        "idempotent_schedule_creation_repository.py"
    ).read_text()
    projection = source.split("creation_logs = list(", maxsplit=1)[1].split(
        "notifications = list(", maxsplit=1
    )[0]

    assert "OperationLog.schedule_id == schedule.id" in projection
    assert "OperationLog.action ==" not in projection


def repository_without_database() -> PostgreSQLIdempotentScheduleCreationRepository:
    return PostgreSQLIdempotentScheduleCreationRepository(
        async_sessionmaker(), wait_limit=ScheduleCreationWaitLimit.create(0.01)
    )


@pytest.mark.asyncio
async def test_write_failure_reconciles_after_leaving_raw_exception_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected, _ = complete_fingerprint_and_record()
    repository = repository_without_database()
    observe = AsyncMock(return_value=_Observation.MISSING)
    create_graph = AsyncMock(side_effect=RuntimeError("database-canary"))
    reconciliation_contexts: list[BaseException | None] = []

    async def reconcile(*args: object, **kwargs: object) -> IdempotentScheduleCreationResult:
        del args, kwargs
        reconciliation_contexts.append(sys.exception())
        return IdempotentScheduleCreationResult(IdempotentScheduleCreationCode.UNKNOWN)

    monkeypatch.setattr(repository, "_observe", observe)
    monkeypatch.setattr(repository, "_set_local_wait_limit", AsyncMock())
    monkeypatch.setattr(repository, "_create_graph", create_graph)
    monkeypatch.setattr(repository, "_reconcile_after_failure", reconcile)

    result = await repository.create(expected, operation_at=NOW)

    assert result.code is IdempotentScheduleCreationCode.UNKNOWN
    assert reconciliation_contexts == [None]
    assert observe.await_count == 1
    assert create_graph.await_count == 1
    assert "database-canary" not in repr(result)


@pytest.mark.asyncio
async def test_cancelled_error_is_same_object_without_write_exception_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected, _ = complete_fingerprint_and_record()
    repository = repository_without_database()
    cancellation = _contextualized_cancellation()
    monkeypatch.setattr(repository, "_observe", AsyncMock(return_value=_Observation.MISSING))
    monkeypatch.setattr(repository, "_set_local_wait_limit", AsyncMock())
    monkeypatch.setattr(repository, "_create_graph", AsyncMock(side_effect=cancellation))

    with pytest.raises(asyncio.CancelledError) as captured:
        await repository.create(expected, operation_at=NOW)

    assert captured.value is cancellation
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert captured.value.__traceback__ is not None


def _contextualized_cancellation() -> asyncio.CancelledError:
    cancellation = asyncio.CancelledError()
    try:
        raise RuntimeError("database-canary")
    except RuntimeError:
        try:
            raise cancellation
        except asyncio.CancelledError as caught:
            return caught


class _RollbackCancellationTransaction:
    def __init__(self, cancellation: asyncio.CancelledError) -> None:
        self._cancellation = cancellation

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        del exc_type, exc, traceback
        raise self._cancellation


class _RollbackCancellationSession:
    def __init__(self, cancellation: asyncio.CancelledError) -> None:
        self._cancellation = cancellation

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        del exc_type, exc, traceback
        return False

    def begin(self) -> _RollbackCancellationTransaction:
        return _RollbackCancellationTransaction(self._cancellation)


@pytest.mark.asyncio
async def test_rollback_cancellation_is_same_detached_object_without_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected, _ = complete_fingerprint_and_record()
    repository = repository_without_database()
    cancellation = _contextualized_cancellation()
    observe = AsyncMock(return_value=_Observation.MISSING)
    create_graph = AsyncMock(side_effect=RuntimeError("database-canary"))
    monkeypatch.setattr(repository, "_observe", observe)
    monkeypatch.setattr(repository, "_sessions", lambda: _RollbackCancellationSession(cancellation))
    monkeypatch.setattr(repository, "_set_local_wait_limit", AsyncMock())
    monkeypatch.setattr(repository, "_create_graph", create_graph)

    with pytest.raises(asyncio.CancelledError) as captured:
        await repository.create(expected, operation_at=NOW)

    assert captured.value is cancellation
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert observe.await_count == 1
    assert create_graph.await_count == 1


@pytest.mark.asyncio
async def test_reconciliation_cancellation_is_detached_without_another_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected, _ = complete_fingerprint_and_record()
    repository = repository_without_database()
    cancellation = _contextualized_cancellation()
    observe = AsyncMock(side_effect=[_Observation.MISSING, cancellation])
    create_graph = AsyncMock(side_effect=RuntimeError("database-canary"))
    monkeypatch.setattr(repository, "_observe", observe)
    monkeypatch.setattr(repository, "_set_local_wait_limit", AsyncMock())
    monkeypatch.setattr(repository, "_create_graph", create_graph)

    with pytest.raises(asyncio.CancelledError) as captured:
        await repository.create(expected, operation_at=NOW)

    assert captured.value is cancellation
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert observe.await_count == 2
    assert create_graph.await_count == 1


@pytest.mark.asyncio
async def test_unavailable_reconciliation_returns_unknown_without_second_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected, _ = complete_fingerprint_and_record()
    repository = repository_without_database()
    observe = AsyncMock(side_effect=[_Observation.MISSING, _Observation.UNAVAILABLE])
    create_graph = AsyncMock(side_effect=RuntimeError("database-canary"))
    monkeypatch.setattr(repository, "_observe", observe)
    monkeypatch.setattr(repository, "_set_local_wait_limit", AsyncMock())
    monkeypatch.setattr(repository, "_create_graph", create_graph)

    result = await repository.create(expected, operation_at=NOW)

    assert result.code is IdempotentScheduleCreationCode.UNKNOWN
    assert observe.await_count == 2
    assert create_graph.await_count == 1
    assert "database-canary" not in repr(result)


@pytest.mark.asyncio
async def test_business_duplicate_without_matching_key_is_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected, _ = complete_fingerprint_and_record()
    repository = repository_without_database()
    observe = AsyncMock(side_effect=[_Observation.MISSING, _Observation.MISSING])
    create_graph = AsyncMock(side_effect=DuplicateScheduleWarning)
    monkeypatch.setattr(repository, "_observe", observe)
    monkeypatch.setattr(repository, "_set_local_wait_limit", AsyncMock())
    monkeypatch.setattr(repository, "_create_graph", create_graph)

    result = await repository.create(expected, operation_at=NOW)

    assert result.code is IdempotentScheduleCreationCode.CONFLICT
    assert observe.await_count == 2
    assert create_graph.await_count == 1
