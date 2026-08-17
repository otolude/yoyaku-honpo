import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    MAX_CLAIM_BATCH_SIZE,
    ClaimedScheduleRun,
    ScheduleRunRepository,
    build_due_runs_claim_statement,
)

NOW = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)


def test_claim_statement_compiles_to_postgresql_locking_query() -> None:
    statement = build_due_runs_claim_statement(now=NOW, batch_size=20)
    sql = str(statement.compile(dialect=postgresql.dialect())).upper()

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT" in sql
    assert "STATUS" in sql
    assert "SCHEDULED_FOR" in sql
    assert "NEXT_ATTEMPT_AT" in sql
    assert "ATTEMPT_COUNT" in sql
    assert "ORDER BY" in sql


@pytest.mark.parametrize("batch_size", [0, MAX_CLAIM_BATCH_SIZE + 1, True])
def test_claim_statement_rejects_invalid_batch_size(batch_size: int) -> None:
    with pytest.raises(ValueError):
        build_due_runs_claim_statement(now=NOW, batch_size=batch_size)


def test_claim_statement_rejects_naive_datetime() -> None:
    with pytest.raises(InvalidDateTimeError):
        build_due_runs_claim_statement(
            now=datetime(2026, 8, 17, 3, 0),  # noqa: DTZ001 - intentional boundary input
            batch_size=1,
        )


@pytest.mark.asyncio
async def test_claim_api_rejects_invalid_lease_and_worker() -> None:
    repository = ScheduleRunRepository(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await repository.claim_due(
            now=NOW,
            worker_id=uuid.uuid7(),
            batch_size=1,
            lease_timeout=timedelta(0),
        )
    with pytest.raises(TypeError):
        await repository.claim_due(
            now=NOW,
            worker_id="worker",  # type: ignore[arg-type]
            batch_size=1,
            lease_timeout=timedelta(seconds=1),
        )


def test_claimed_result_type_is_frozen() -> None:
    assert ClaimedScheduleRun.__dataclass_params__.frozen is True
