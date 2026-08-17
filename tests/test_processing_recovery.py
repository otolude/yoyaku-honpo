from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql

from discord_ai_reminder_bot.domain.enums import DeliveryAttemptStatus
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.domain.recovery import (
    InterruptedAttemptAction,
    classify_interrupted_attempt,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    build_expired_processing_statement,
)

RECOVERED_AT = datetime(2026, 8, 17, 3, 5, tzinfo=UTC)


def test_expired_processing_statement_uses_postgresql_skip_locked() -> None:
    statement = build_expired_processing_statement(recovered_at=RECOVERED_AT, batch_size=20)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled).upper()
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT" in sql
    assert "SCHEDULE_RUNS.STATUS =" in sql
    assert "processing" in compiled.params.values()
    assert "LEASE_EXPIRES_AT" in sql
    assert "CLAIMED_BY IS NOT NULL" in sql
    assert "CLAIMED_AT IS NOT NULL" in sql
    assert "ORDER BY" in sql


@pytest.mark.parametrize("batch_size", [0, 21, True])
def test_expired_processing_statement_rejects_invalid_batch(batch_size: int) -> None:
    with pytest.raises(ValueError):
        build_expired_processing_statement(recovered_at=RECOVERED_AT, batch_size=batch_size)


def test_expired_processing_statement_rejects_naive_time() -> None:
    with pytest.raises(InvalidDateTimeError):
        build_expired_processing_statement(
            recovered_at=datetime(2026, 8, 17, 3, 5),  # noqa: DTZ001
            batch_size=1,
        )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (DeliveryAttemptStatus.CLAIMED, InterruptedAttemptAction.RETURN_TO_PENDING),
        (DeliveryAttemptStatus.SENDING, InterruptedAttemptAction.FAIL_WITH_UNKNOWN_RESULT),
        (DeliveryAttemptStatus.UNKNOWN, InterruptedAttemptAction.FAIL_WITH_UNKNOWN_RESULT),
        (DeliveryAttemptStatus.SUCCEEDED, InterruptedAttemptAction.NO_RECOVERY),
        (DeliveryAttemptStatus.FAILED, InterruptedAttemptAction.NO_RECOVERY),
    ],
)
def test_attempt_recovery_classification_is_safe(
    status: DeliveryAttemptStatus, expected: InterruptedAttemptAction
) -> None:
    assert classify_interrupted_attempt(status) is expected
