from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Self

import pytest
from sqlalchemy import literal, select
from support.read_only_database_audit import (
    AuditStage,
    CleanupFailure,
    ConditionState,
    HarnessErrorCategory,
    run_read_only_database_audit,
)

pytestmark = pytest.mark.asyncio

CONDITIONS = ("FIRST_CONDITION", "SECOND_CONDITION", "THIRD_CONDITION")
EXPECTED_DATABASE = "isolated_test_database"
SECRET_URL_CANARY = "postgresql+psycopg://audit-secret@localhost/isolated_test_database"
SECRET_MESSAGE_CANARY = "raw-driver-message-must-not-be-retained"
CONTENT_CANARY = "private-body-must-not-be-retained"
IDENTIFIER_CANARY = "987654321098765432"


class CanaryError(RuntimeError):
    pass


class FakeScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one(self) -> object:
        return self._value


class FakeTransaction:
    def __init__(self, calls: list[str], *, fail_rollback: bool = False) -> None:
        self._calls = calls
        self._fail_rollback = fail_rollback
        self.rollback_count = 0

    async def rollback(self) -> None:
        self._calls.append("rollback")
        self.rollback_count += 1
        if self._fail_rollback:
            raise CanaryError(SECRET_MESSAGE_CANARY)


class FakeConnectionContext:
    def __init__(self, connection: FakeConnection, *, fail_connect: bool = False) -> None:
        self._connection = connection
        self._fail_connect = fail_connect
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self) -> FakeConnection:
        self._connection.calls.append("connect")
        self.enter_count += 1
        if self._fail_connect:
            raise CanaryError(SECRET_MESSAGE_CANARY)
        return self._connection

    async def __aexit__(self, *args: object) -> None:
        self._connection.calls.append("close")
        self.exit_count += 1
        if self._connection.fail_close:
            raise CanaryError(SECRET_MESSAGE_CANARY)


class FakeConnection:
    def __init__(
        self,
        *,
        failure: str | None = None,
        initially_in_transaction: bool = False,
        autobegin_after_options: bool = False,
        read_only_value: object = "on",
        identity_value: object = EXPECTED_DATABASE,
        fail_rollback: bool = False,
        fail_close: bool = False,
    ) -> None:
        self.failure = failure
        self._in_transaction = initially_in_transaction
        self._autobegin_after_options = autobegin_after_options
        self._read_only_value = read_only_value
        self._identity_value = identity_value
        self.fail_close = fail_close
        self.calls: list[str] = []
        self.options: dict[str, object] | None = None
        self.begin_count = 0
        self.execute_count = 0
        self.transaction = FakeTransaction(self.calls, fail_rollback=fail_rollback)

    def in_transaction(self) -> bool:
        return self._in_transaction

    async def execution_options(self, **options: object) -> Self:
        self.calls.append("options")
        if self.failure == "options":
            raise CanaryError(SECRET_MESSAGE_CANARY)
        self.options = options
        self._in_transaction = self._autobegin_after_options
        return self

    async def begin(self) -> FakeTransaction:
        self.calls.append("begin")
        self.begin_count += 1
        if self.failure == "begin":
            raise CanaryError(SECRET_MESSAGE_CANARY)
        self._in_transaction = True
        return self.transaction

    async def execute(self, statement: object) -> FakeScalarResult:
        del statement
        self.execute_count += 1
        if self.execute_count == 1:
            self.calls.append("read_only_verify")
            if self.failure == "read_only_verify":
                raise CanaryError(SECRET_MESSAGE_CANARY)
            return FakeScalarResult(self._read_only_value)
        if self.execute_count == 2:
            self.calls.append("identity_verify")
            if self.failure == "identity_verify":
                raise CanaryError(SECRET_MESSAGE_CANARY)
            return FakeScalarResult(self._identity_value)
        self.calls.append("query_execute")
        return FakeScalarResult(1)


class FakeEngine:
    def __init__(
        self,
        connection: FakeConnection,
        *,
        fail_connect: bool = False,
        fail_dispose: bool = False,
    ) -> None:
        self.connection = connection
        self.context = FakeConnectionContext(connection, fail_connect=fail_connect)
        self.fail_dispose = fail_dispose
        self.connect_count = 0
        self.dispose_count = 0

    def connect(self) -> FakeConnectionContext:
        self.connect_count += 1
        return self.context

    async def dispose(self) -> None:
        self.connection.calls.append("dispose")
        self.dispose_count += 1
        if self.fail_dispose:
            raise CanaryError(SECRET_MESSAGE_CANARY)


def validator(value: str) -> str:
    if value != SECRET_URL_CANARY:
        raise CanaryError(SECRET_MESSAGE_CANARY)
    return value


def factory_for(engine: FakeEngine) -> Callable[[object], FakeEngine]:
    def factory(secret: object) -> FakeEngine:
        assert SECRET_URL_CANARY not in repr(secret)
        return engine

    return factory


async def successful_query(connection: FakeConnection, recorder: Any) -> None:
    connection.calls.append("query")
    await connection.execute(select(literal(1)))
    recorder.record("FIRST_CONDITION", True)
    recorder.record("SECOND_CONDITION", False)


async def test_successful_boundary_has_exact_order_and_separates_condition_false() -> None:
    connection = FakeConnection()
    engine = FakeEngine(connection)

    result = await run_read_only_database_audit(
        condition_names=CONDITIONS,
        test_database_url=SECRET_URL_CANARY,
        expected_database=EXPECTED_DATABASE,
        query=successful_query,
        url_validator=validator,
        engine_factory=factory_for(engine),
    )

    assert result.stage_sequence == tuple(AuditStage)
    assert connection.calls == [
        "connect",
        "options",
        "begin",
        "read_only_verify",
        "identity_verify",
        "query",
        "query_execute",
        "rollback",
        "close",
        "dispose",
    ]
    assert connection.options == {
        "isolation_level": "READ COMMITTED",
        "postgresql_readonly": True,
    }
    assert connection.begin_count == 1
    assert connection.transaction.rollback_count == 1
    assert engine.context.exit_count == 1
    assert engine.dispose_count == 1
    assert result.state_for("FIRST_CONDITION") is ConditionState.TRUE
    assert result.state_for("SECOND_CONDITION") is ConditionState.FALSE
    assert result.state_for("THIRD_CONDITION") is ConditionState.UNKNOWN
    assert result.first_failed_stage is None
    assert result.harness_error is None
    assert result.cleanup_failures == ()
    assert result.harness_valid is True


@pytest.mark.parametrize("after_options", [False, True])
async def test_autobegin_is_detected_before_begin(after_options: bool) -> None:
    connection = FakeConnection(
        initially_in_transaction=not after_options,
        autobegin_after_options=after_options,
    )
    engine = FakeEngine(connection)

    result = await run_read_only_database_audit(
        condition_names=CONDITIONS,
        test_database_url=SECRET_URL_CANARY,
        expected_database=EXPECTED_DATABASE,
        query=successful_query,
        url_validator=validator,
        engine_factory=factory_for(engine),
    )

    assert result.first_failed_stage is AuditStage.APPLY_OPTIONS
    assert result.harness_error is HarnessErrorCategory.AUTOBEGIN_DETECTED
    assert connection.begin_count == 0
    assert connection.transaction.rollback_count == 0
    assert engine.context.exit_count == 1
    assert engine.dispose_count == 1
    assert all(item.state is ConditionState.UNKNOWN for item in result.conditions)


@pytest.mark.parametrize(
    ("failure", "expected_stage", "expected_category"),
    [
        ("validate", AuditStage.VALIDATE_URL, HarnessErrorCategory.URL_VALIDATION_FAILED),
        ("create", AuditStage.CREATE_ENGINE, HarnessErrorCategory.ENGINE_CREATION_FAILED),
        ("connect", AuditStage.CONNECT, HarnessErrorCategory.CONNECTION_FAILED),
        ("options", AuditStage.APPLY_OPTIONS, HarnessErrorCategory.OPTIONS_FAILED),
        ("begin", AuditStage.BEGIN, HarnessErrorCategory.BEGIN_FAILED),
        (
            "read_only_verify",
            AuditStage.READ_ONLY_VERIFY,
            HarnessErrorCategory.READ_ONLY_VERIFICATION_FAILED,
        ),
        (
            "identity_verify",
            AuditStage.IDENTITY_VERIFY,
            HarnessErrorCategory.IDENTITY_VERIFICATION_FAILED,
        ),
        ("query", AuditStage.QUERY, HarnessErrorCategory.QUERY_FAILED),
    ],
)
async def test_each_primary_failure_has_a_fixed_stage_and_category(
    failure: str,
    expected_stage: AuditStage,
    expected_category: HarnessErrorCategory,
) -> None:
    connection_failure = failure if failure in {
        "options",
        "begin",
        "read_only_verify",
        "identity_verify",
    } else None
    connection = FakeConnection(failure=connection_failure)
    engine = FakeEngine(connection, fail_connect=failure == "connect")

    def failing_validator(value: str) -> str:
        if failure == "validate":
            raise CanaryError(SECRET_MESSAGE_CANARY)
        return validator(value)

    def failing_factory(secret: object) -> FakeEngine:
        if failure == "create":
            raise CanaryError(SECRET_MESSAGE_CANARY)
        return factory_for(engine)(secret)

    async def query(fake_connection: FakeConnection, recorder: Any) -> None:
        fake_connection.calls.append("query")
        if failure == "query":
            raise CanaryError(SECRET_MESSAGE_CANARY)
        recorder.record("FIRST_CONDITION", True)

    result = await run_read_only_database_audit(
        condition_names=CONDITIONS,
        test_database_url=SECRET_URL_CANARY,
        expected_database=EXPECTED_DATABASE,
        query=query,
        url_validator=failing_validator,
        engine_factory=failing_factory,
    )

    assert result.first_failed_stage is expected_stage
    assert result.harness_error is expected_category
    assert result.harness_valid is False
    assert all(item.state is ConditionState.UNKNOWN for item in result.conditions)
    if failure in {"read_only_verify", "identity_verify", "query"}:
        assert connection.transaction.rollback_count == 1
    if failure not in {"validate", "create"}:
        assert engine.dispose_count == 1


async def test_read_only_false_and_identity_mismatch_are_harness_failures() -> None:
    for connection, expected_stage, category in (
        (
            FakeConnection(read_only_value="off"),
            AuditStage.READ_ONLY_VERIFY,
            HarnessErrorCategory.READ_ONLY_VERIFICATION_FAILED,
        ),
        (
            FakeConnection(identity_value="different_test_database"),
            AuditStage.IDENTITY_VERIFY,
            HarnessErrorCategory.IDENTITY_VERIFICATION_FAILED,
        ),
    ):
        engine = FakeEngine(connection)
        result = await run_read_only_database_audit(
            condition_names=CONDITIONS,
            test_database_url=SECRET_URL_CANARY,
            expected_database=EXPECTED_DATABASE,
            query=successful_query,
            url_validator=validator,
            engine_factory=factory_for(engine),
        )

        assert result.first_failed_stage is expected_stage
        assert result.harness_error is category
        assert connection.transaction.rollback_count == 1
        assert engine.dispose_count == 1


async def test_query_failure_retains_prior_condition_results() -> None:
    connection = FakeConnection()
    engine = FakeEngine(connection)

    async def partial_query(fake_connection: FakeConnection, recorder: Any) -> None:
        fake_connection.calls.append("query")
        recorder.record("FIRST_CONDITION", True)
        recorder.record("SECOND_CONDITION", False)
        raise CanaryError(SECRET_MESSAGE_CANARY)

    result = await run_read_only_database_audit(
        condition_names=CONDITIONS,
        test_database_url=SECRET_URL_CANARY,
        expected_database=EXPECTED_DATABASE,
        query=partial_query,
        url_validator=validator,
        engine_factory=factory_for(engine),
    )

    assert result.state_for("FIRST_CONDITION") is ConditionState.TRUE
    assert result.state_for("SECOND_CONDITION") is ConditionState.FALSE
    assert result.state_for("THIRD_CONDITION") is ConditionState.UNKNOWN
    assert result.first_failed_stage is AuditStage.QUERY
    assert result.harness_error is HarnessErrorCategory.QUERY_FAILED
    assert connection.transaction.rollback_count == 1
    assert engine.dispose_count == 1


async def test_rollback_and_dispose_failures_are_both_retained_without_overwrite() -> None:
    connection = FakeConnection(fail_rollback=True)
    engine = FakeEngine(connection, fail_dispose=True)

    async def failing_query(fake_connection: FakeConnection, recorder: Any) -> None:
        fake_connection.calls.append("query")
        recorder.record("FIRST_CONDITION", True)
        raise CanaryError(SECRET_MESSAGE_CANARY)

    result = await run_read_only_database_audit(
        condition_names=CONDITIONS,
        test_database_url=SECRET_URL_CANARY,
        expected_database=EXPECTED_DATABASE,
        query=failing_query,
        url_validator=validator,
        engine_factory=factory_for(engine),
    )

    assert result.first_failed_stage is AuditStage.QUERY
    assert result.harness_error is HarnessErrorCategory.QUERY_FAILED
    assert result.cleanup_failures == (
        CleanupFailure(AuditStage.ROLLBACK, HarnessErrorCategory.ROLLBACK_FAILED),
        CleanupFailure(AuditStage.DISPOSE, HarnessErrorCategory.DISPOSE_FAILED),
    )
    assert connection.transaction.rollback_count == 1
    assert engine.dispose_count == 1
    assert result.state_for("FIRST_CONDITION") is ConditionState.TRUE
    assert result.state_for("SECOND_CONDITION") is ConditionState.UNKNOWN


async def test_cleanup_failure_becomes_primary_only_without_an_earlier_failure() -> None:
    connection = FakeConnection(fail_rollback=True)
    engine = FakeEngine(connection, fail_dispose=True)

    result = await run_read_only_database_audit(
        condition_names=CONDITIONS,
        test_database_url=SECRET_URL_CANARY,
        expected_database=EXPECTED_DATABASE,
        query=successful_query,
        url_validator=validator,
        engine_factory=factory_for(engine),
    )

    assert result.first_failed_stage is AuditStage.ROLLBACK
    assert result.harness_error is HarnessErrorCategory.ROLLBACK_FAILED
    assert result.cleanup_failures == (
        CleanupFailure(AuditStage.ROLLBACK, HarnessErrorCategory.ROLLBACK_FAILED),
        CleanupFailure(AuditStage.DISPOSE, HarnessErrorCategory.DISPOSE_FAILED),
    )
    assert result.state_for("SECOND_CONDITION") is ConditionState.FALSE


async def test_connection_close_failure_is_retained_as_cleanup_failure() -> None:
    connection = FakeConnection(fail_close=True)
    engine = FakeEngine(connection)

    result = await run_read_only_database_audit(
        condition_names=CONDITIONS,
        test_database_url=SECRET_URL_CANARY,
        expected_database=EXPECTED_DATABASE,
        query=successful_query,
        url_validator=validator,
        engine_factory=factory_for(engine),
    )

    assert result.first_failed_stage is AuditStage.CONNECT
    assert result.harness_error is HarnessErrorCategory.CONNECTION_CLOSE_FAILED
    assert result.cleanup_failures == (
        CleanupFailure(AuditStage.CONNECT, HarnessErrorCategory.CONNECTION_CLOSE_FAILED),
    )
    assert connection.transaction.rollback_count == 1
    assert engine.dispose_count == 1


async def test_result_never_retains_raw_exception_or_private_values() -> None:
    connection = FakeConnection()
    engine = FakeEngine(connection)

    async def private_failure(fake_connection: FakeConnection, recorder: Any) -> None:
        fake_connection.calls.append("query")
        raise CanaryError(
            f"{SECRET_MESSAGE_CANARY} {SECRET_URL_CANARY} "
            f"{CONTENT_CANARY} {IDENTIFIER_CANARY}"
        )

    result = await run_read_only_database_audit(
        condition_names=CONDITIONS,
        test_database_url=SECRET_URL_CANARY,
        expected_database=EXPECTED_DATABASE,
        query=private_failure,
        url_validator=validator,
        engine_factory=factory_for(engine),
    )
    rendered = repr(result)

    assert SECRET_MESSAGE_CANARY not in rendered
    assert SECRET_URL_CANARY not in rendered
    assert CONTENT_CANARY not in rendered
    assert IDENTIFIER_CANARY not in rendered
    assert EXPECTED_DATABASE not in rendered


async def test_boundary_leaves_no_pending_async_tasks() -> None:
    connection = FakeConnection()
    engine = FakeEngine(connection)

    await run_read_only_database_audit(
        condition_names=CONDITIONS,
        test_database_url=SECRET_URL_CANARY,
        expected_database=EXPECTED_DATABASE,
        query=successful_query,
        url_validator=validator,
        engine_factory=factory_for(engine),
    )

    current = asyncio.current_task()
    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not current and not task.done()
    ]
    assert pending == []
