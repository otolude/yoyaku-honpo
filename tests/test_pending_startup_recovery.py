from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql

from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    build_startup_pending_statement,
)

CUTOFF = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)


def test_pending_recovery_statement_uses_stable_postgresql_skip_locked() -> None:
    statement = build_startup_pending_statement(recovered_at=CUTOFF, batch_size=20)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled).upper()
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "ORDER BY" in sql
    assert "SCHEDULED_FOR" in sql
    assert "DELIVERY_ATTEMPTS" in sql


@pytest.mark.parametrize("batch_size", [0, 21, True])
def test_pending_recovery_statement_rejects_invalid_batch(batch_size: int) -> None:
    with pytest.raises(ValueError):
        build_startup_pending_statement(recovered_at=CUTOFF, batch_size=batch_size)


def test_pending_recovery_statement_rejects_naive_cutoff() -> None:
    with pytest.raises(InvalidDateTimeError):
        build_startup_pending_statement(
            recovered_at=datetime(2026, 8, 19, 3, 0),  # noqa: DTZ001
            batch_size=1,
        )
