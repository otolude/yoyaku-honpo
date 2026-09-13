"""DB-independent support boundary for manual-acceptance read-only audits."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import SecretStr
from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncEngine

from discord_ai_reminder_bot.infrastructure.database.session import create_database_engine
from discord_ai_reminder_bot.infrastructure.database.testing import validate_test_database_url

_CONDITION_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


class AuditStage(StrEnum):
    """Only stages retained by the audit result."""

    VALIDATE_URL = "VALIDATE_URL"
    CREATE_ENGINE = "CREATE_ENGINE"
    CONNECT = "CONNECT"
    APPLY_OPTIONS = "APPLY_OPTIONS"
    BEGIN = "BEGIN"
    READ_ONLY_VERIFY = "READ_ONLY_VERIFY"
    IDENTITY_VERIFY = "IDENTITY_VERIFY"
    QUERY = "QUERY"
    ROLLBACK = "ROLLBACK"
    DISPOSE = "DISPOSE"
    COMPLETE = "COMPLETE"


class ConditionState(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class HarnessErrorCategory(StrEnum):
    """Fixed categories that cannot contain driver or private details."""

    URL_VALIDATION_FAILED = "URL_VALIDATION_FAILED"
    ENGINE_CREATION_FAILED = "ENGINE_CREATION_FAILED"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    CONNECTION_CLOSE_FAILED = "CONNECTION_CLOSE_FAILED"
    AUTOBEGIN_DETECTED = "AUTOBEGIN_DETECTED"
    OPTIONS_FAILED = "OPTIONS_FAILED"
    BEGIN_FAILED = "BEGIN_FAILED"
    READ_ONLY_VERIFICATION_FAILED = "READ_ONLY_VERIFICATION_FAILED"
    IDENTITY_VERIFICATION_FAILED = "IDENTITY_VERIFICATION_FAILED"
    QUERY_FAILED = "QUERY_FAILED"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    DISPOSE_FAILED = "DISPOSE_FAILED"


@dataclass(frozen=True, slots=True)
class ConditionResult:
    name: str
    state: ConditionState


@dataclass(frozen=True, slots=True)
class CleanupFailure:
    stage: AuditStage
    category: HarnessErrorCategory


@dataclass(frozen=True, slots=True)
class AuditResult:
    """Sanitized result containing no URL, SQL, row, exception, or private value."""

    conditions: tuple[ConditionResult, ...]
    stage_sequence: tuple[AuditStage, ...]
    first_failed_stage: AuditStage | None
    harness_error: HarnessErrorCategory | None
    cleanup_failures: tuple[CleanupFailure, ...]

    @property
    def harness_valid(self) -> bool:
        return self.harness_error is None and not self.cleanup_failures

    def state_for(self, name: str) -> ConditionState:
        for item in self.conditions:
            if item.name == name:
                return item.state
        raise KeyError(name)


class ConditionRecorder:
    """Record independent condition outcomes while retaining unrecorded ones as UNKNOWN."""

    __slots__ = ("_names", "_states")

    def __init__(self, names: Sequence[str]) -> None:
        normalized = tuple(names)
        if (
            not normalized
            or len(set(normalized)) != len(normalized)
            or any(
                not isinstance(name, str) or not _CONDITION_NAME.fullmatch(name)
                for name in normalized
            )
        ):
            raise ValueError("invalid audit condition names")
        self._names = normalized
        self._states = {name: ConditionState.UNKNOWN for name in normalized}

    def record(self, name: str, value: bool) -> None:
        if name not in self._states or type(value) is not bool:
            raise ValueError("invalid audit condition result")
        self._states[name] = ConditionState.TRUE if value else ConditionState.FALSE

    def snapshot(self) -> tuple[ConditionResult, ...]:
        return tuple(ConditionResult(name, self._states[name]) for name in self._names)


AuditQuery = Callable[[Any, ConditionRecorder], Awaitable[None]]
UrlValidator = Callable[[str], str]
EngineFactory = Callable[[SecretStr], Any]


def _default_engine_factory(database_url: SecretStr) -> AsyncEngine:
    return create_database_engine(database_url)


async def run_read_only_database_audit(
    *,
    condition_names: Sequence[str],
    test_database_url: str,
    expected_database: str,
    query: AuditQuery,
    url_validator: UrlValidator = validate_test_database_url,
    engine_factory: EngineFactory = _default_engine_factory,
) -> AuditResult:
    """Run one Core-only read-only transaction and retain sanitized stage evidence."""
    recorder = ConditionRecorder(condition_names)
    stages: list[AuditStage] = []
    cleanup_failures: list[CleanupFailure] = []
    first_failed_stage: AuditStage | None = None
    harness_error: HarnessErrorCategory | None = None
    engine: Any = None
    connection_context: Any = None
    connection: Any = None
    transaction: Any = None
    connection_entered = False
    proceed = True

    def fail(stage: AuditStage, category: HarnessErrorCategory) -> None:
        nonlocal first_failed_stage, harness_error, proceed
        if first_failed_stage is None:
            first_failed_stage = stage
            harness_error = category
        proceed = False

    stages.append(AuditStage.VALIDATE_URL)
    validated_url: str | None = None
    try:
        if not isinstance(expected_database, str) or not expected_database:
            raise ValueError
        validated_url = url_validator(test_database_url)
    except Exception:  # noqa: BLE001 - convert private failures to a fixed category
        fail(AuditStage.VALIDATE_URL, HarnessErrorCategory.URL_VALIDATION_FAILED)

    if proceed:
        stages.append(AuditStage.CREATE_ENGINE)
        try:
            engine = engine_factory(SecretStr(validated_url))
        except Exception:  # noqa: BLE001 - convert private failures to a fixed category
            fail(AuditStage.CREATE_ENGINE, HarnessErrorCategory.ENGINE_CREATION_FAILED)

    if proceed:
        stages.append(AuditStage.CONNECT)
        try:
            connection_context = engine.connect()
            connection = await connection_context.__aenter__()
            connection_entered = True
        except Exception:  # noqa: BLE001 - convert private failures to a fixed category
            fail(AuditStage.CONNECT, HarnessErrorCategory.CONNECTION_FAILED)

    if proceed:
        stages.append(AuditStage.APPLY_OPTIONS)
        try:
            if connection.in_transaction():
                fail(AuditStage.APPLY_OPTIONS, HarnessErrorCategory.AUTOBEGIN_DETECTED)
            else:
                connection = await connection.execution_options(
                    isolation_level="READ COMMITTED",
                    postgresql_readonly=True,
                )
                if connection.in_transaction():
                    fail(AuditStage.APPLY_OPTIONS, HarnessErrorCategory.AUTOBEGIN_DETECTED)
        except Exception:  # noqa: BLE001 - convert private failures to a fixed category
            fail(AuditStage.APPLY_OPTIONS, HarnessErrorCategory.OPTIONS_FAILED)

    if proceed:
        stages.append(AuditStage.BEGIN)
        try:
            transaction = await connection.begin()
        except Exception:  # noqa: BLE001 - convert private failures to a fixed category
            fail(AuditStage.BEGIN, HarnessErrorCategory.BEGIN_FAILED)

    if proceed:
        stages.append(AuditStage.READ_ONLY_VERIFY)
        try:
            statement = select(func.current_setting(literal("transaction_read_only")))
            read_only = (await connection.execute(statement)).scalar_one()
            if read_only not in {True, "on"}:
                fail(
                    AuditStage.READ_ONLY_VERIFY,
                    HarnessErrorCategory.READ_ONLY_VERIFICATION_FAILED,
                )
        except Exception:  # noqa: BLE001 - convert private failures to a fixed category
            fail(
                AuditStage.READ_ONLY_VERIFY,
                HarnessErrorCategory.READ_ONLY_VERIFICATION_FAILED,
            )

    if proceed:
        stages.append(AuditStage.IDENTITY_VERIFY)
        try:
            identity = (await connection.execute(select(func.current_database()))).scalar_one()
            if identity != expected_database:
                fail(
                    AuditStage.IDENTITY_VERIFY,
                    HarnessErrorCategory.IDENTITY_VERIFICATION_FAILED,
                )
        except Exception:  # noqa: BLE001 - convert private failures to a fixed category
            fail(
                AuditStage.IDENTITY_VERIFY,
                HarnessErrorCategory.IDENTITY_VERIFICATION_FAILED,
            )

    if proceed:
        stages.append(AuditStage.QUERY)
        try:
            await query(connection, recorder)
        except Exception:  # noqa: BLE001 - convert private failures to a fixed category
            fail(AuditStage.QUERY, HarnessErrorCategory.QUERY_FAILED)

    if transaction is not None:
        stages.append(AuditStage.ROLLBACK)
        try:
            await transaction.rollback()
        except Exception:  # noqa: BLE001 - retain cleanup failure without private detail
            failure = CleanupFailure(
                AuditStage.ROLLBACK,
                HarnessErrorCategory.ROLLBACK_FAILED,
            )
            cleanup_failures.append(failure)
            if first_failed_stage is None:
                first_failed_stage = failure.stage
                harness_error = failure.category

    if connection_entered:
        try:
            await connection_context.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 - retain cleanup failure without private detail
            failure = CleanupFailure(
                AuditStage.CONNECT,
                HarnessErrorCategory.CONNECTION_CLOSE_FAILED,
            )
            cleanup_failures.append(failure)
            if first_failed_stage is None:
                first_failed_stage = failure.stage
                harness_error = failure.category

    if engine is not None:
        stages.append(AuditStage.DISPOSE)
        try:
            await engine.dispose()
        except Exception:  # noqa: BLE001 - retain cleanup failure without private detail
            failure = CleanupFailure(
                AuditStage.DISPOSE,
                HarnessErrorCategory.DISPOSE_FAILED,
            )
            cleanup_failures.append(failure)
            if first_failed_stage is None:
                first_failed_stage = failure.stage
                harness_error = failure.category

    stages.append(AuditStage.COMPLETE)
    return AuditResult(
        conditions=recorder.snapshot(),
        stage_sequence=tuple(stages),
        first_failed_stage=first_failed_stage,
        harness_error=harness_error,
        cleanup_failures=tuple(cleanup_failures),
    )
