import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.delivery import (
    RESULT_FAILED,
    RESULT_RETRY_PENDING,
    RESULT_SUCCEEDED,
    RESULT_UNKNOWN,
    DeliveryService,
)
from discord_ai_reminder_bot.domain.enums import DeliveryErrorKind
from discord_ai_reminder_bot.infrastructure.database.exceptions import (
    RepositoryOwnershipError,
    RepositoryStateConflictError,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    DeliveryAttemptRepository,
    ScheduleRepository,
    ScheduleRunRepository,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)
LEASE = timedelta(minutes=2)


async def claim_attempt(
    session: AsyncSession, *, attempt_number: int = 1
) -> tuple[ScheduleRun, DeliveryAttempt, uuid.UUID]:
    schedule = await ScheduleRepository(session).add(
        Schedule(
            public_id=uuid.uuid7(),
            guild_id=600,
            channel_id=601,
            creator_user_id=602,
            schedule_type="once",
            status="active",
            content="delivery lifecycle",
            next_run_at=NOW + timedelta(days=1),
            version=1,
        )
    )
    run = await ScheduleRunRepository(session).add(
        ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=NOW - timedelta(minutes=1),
            status="pending",
            attempt_count=attempt_number - 1,
            next_attempt_at=NOW,
        )
    )
    worker_id = uuid.uuid7()
    claimed = await ScheduleRunRepository(session).claim_due(
        now=NOW,
        worker_id=worker_id,
        batch_size=1,
        lease_timeout=LEASE,
    )
    assert len(claimed) == 1
    return run, claimed[0].attempt, worker_id


async def test_claimed_to_sending_and_internal_getters(db_session: AsyncSession) -> None:
    run, attempt, worker_id = await claim_attempt(db_session)
    changed = await DeliveryService(db_session).start_sending(
        attempt_id=attempt.id,
        worker_id=worker_id,
        now=NOW + timedelta(seconds=1),
    )

    assert changed.attempt.status == "sending"
    assert changed.attempt.send_started_at == NOW + timedelta(seconds=1)
    assert changed.attempt.finished_at is None
    assert changed.run.id == run.id
    assert changed.run.status == "processing"
    assert changed.run.updated_at == NOW + timedelta(seconds=1)
    repository = DeliveryAttemptRepository(db_session)
    assert await repository.get_by_id(attempt.id) is changed.attempt
    assert (
        await repository.get_by_run_and_number(run_id=run.id, attempt_number=1) is changed.attempt
    )


async def test_start_rejects_other_worker_double_start_and_expired_lease(
    db_session: AsyncSession,
) -> None:
    _run, attempt, worker_id = await claim_attempt(db_session)
    service = DeliveryService(db_session)
    with pytest.raises(RepositoryOwnershipError):
        await service.start_sending(
            attempt_id=attempt.id,
            worker_id=uuid.uuid7(),
            now=NOW + timedelta(seconds=1),
        )
    await service.start_sending(
        attempt_id=attempt.id, worker_id=worker_id, now=NOW + timedelta(seconds=1)
    )
    with pytest.raises(RepositoryStateConflictError):
        await service.start_sending(
            attempt_id=attempt.id,
            worker_id=worker_id,
            now=NOW + timedelta(seconds=2),
        )

    other_run, other_attempt, other_worker = await claim_attempt(db_session)
    with pytest.raises(RepositoryStateConflictError):
        async with db_session.begin_nested():
            await service.start_sending(
                attempt_id=other_attempt.id,
                worker_id=other_worker,
                now=NOW + LEASE + timedelta(seconds=1),
            )
    await db_session.refresh(other_attempt)
    await db_session.refresh(other_run)
    assert other_attempt.status == "claimed"
    assert other_run.status == "processing"


async def test_start_rejects_time_before_claimed_at(db_session: AsyncSession) -> None:
    _run, attempt, worker_id = await claim_attempt(db_session)
    with pytest.raises(RepositoryStateConflictError):
        await DeliveryService(db_session).start_sending(
            attempt_id=attempt.id,
            worker_id=worker_id,
            now=NOW - timedelta(seconds=1),
        )
    await db_session.refresh(attempt)
    assert attempt.status == "claimed"


async def test_sending_to_succeeded_and_rejects_double_completion(
    db_session: AsyncSession,
) -> None:
    _run, attempt, worker_id = await claim_attempt(db_session)
    service = DeliveryService(db_session)
    await service.start_sending(
        attempt_id=attempt.id, worker_id=worker_id, now=NOW + timedelta(seconds=1)
    )
    completed = await service.complete_success(
        attempt_id=attempt.id,
        worker_id=worker_id,
        now=NOW + timedelta(seconds=2),
        message_id=700,
    )
    assert completed.attempt.status == "succeeded"
    assert completed.attempt.finished_at == NOW + timedelta(seconds=2)
    assert completed.attempt.discord_message_id == 700
    assert completed.run.status == "succeeded"
    assert completed.run.discord_message_id == 700
    assert completed.run.result_code == RESULT_SUCCEEDED
    assert completed.run.error_summary is None
    assert completed.run.finished_at == NOW + timedelta(seconds=2)
    assert completed.run.next_attempt_at is None
    assert completed.run.claimed_by is None
    assert completed.run.claimed_at is None
    assert completed.run.lease_expires_at is None

    with pytest.raises(RepositoryStateConflictError):
        await service.complete_success(
            attempt_id=attempt.id,
            worker_id=worker_id,
            now=NOW + timedelta(seconds=3),
            message_id=701,
        )
    with pytest.raises(RepositoryStateConflictError):
        await service.complete_failure(
            attempt_id=attempt.id,
            worker_id=worker_id,
            now=NOW + timedelta(seconds=3),
            error_kind=DeliveryErrorKind.PERMANENT,
            error_code="forbidden",
            error_summary="permission denied",
        )


@pytest.mark.parametrize(
    ("attempt_number", "expected_delay", "expected_status", "expected_code"),
    [
        (1, timedelta(minutes=1), "pending", RESULT_RETRY_PENDING),
        (2, timedelta(minutes=5), "pending", RESULT_RETRY_PENDING),
        (3, timedelta(minutes=15), "pending", RESULT_RETRY_PENDING),
        (4, None, "failed", RESULT_FAILED),
    ],
)
async def test_transient_failure_uses_retry_policy(
    db_session: AsyncSession,
    attempt_number: int,
    expected_delay: timedelta | None,
    expected_status: str,
    expected_code: str,
) -> None:
    _run, attempt, worker_id = await claim_attempt(db_session, attempt_number=attempt_number)
    service = DeliveryService(db_session)
    await service.start_sending(
        attempt_id=attempt.id, worker_id=worker_id, now=NOW + timedelta(seconds=1)
    )
    failed_at = NOW + timedelta(seconds=2)
    changed = await service.complete_failure(
        attempt_id=attempt.id,
        worker_id=worker_id,
        now=failed_at,
        error_kind=DeliveryErrorKind.TRANSIENT,
        error_code="timeout",
        error_summary="temporary timeout",
    )
    assert changed.attempt.status == "failed"
    assert changed.attempt.error_kind == "transient"
    assert changed.attempt.finished_at == failed_at
    assert changed.run.status == expected_status
    assert changed.run.next_attempt_at == (
        failed_at + expected_delay if expected_delay is not None else None
    )
    assert changed.run.finished_at == (None if expected_delay is not None else failed_at)
    assert changed.run.result_code == expected_code
    assert changed.run.claimed_by is None
    assert changed.run.claimed_at is None
    assert changed.run.lease_expires_at is None


async def test_permanent_and_unknown_fail_without_retry(db_session: AsyncSession) -> None:
    _, permanent_attempt, permanent_worker = await claim_attempt(db_session)
    service = DeliveryService(db_session)
    await service.start_sending(
        attempt_id=permanent_attempt.id,
        worker_id=permanent_worker,
        now=NOW + timedelta(seconds=1),
    )
    permanent = await service.complete_failure(
        attempt_id=permanent_attempt.id,
        worker_id=permanent_worker,
        now=NOW + timedelta(seconds=2),
        error_kind=DeliveryErrorKind.PERMANENT,
        error_code="forbidden",
        error_summary="permission denied",
    )
    assert permanent.run.status == "failed"
    assert permanent.run.next_attempt_at is None
    assert permanent.run.result_code == RESULT_FAILED

    _, unknown_attempt, unknown_worker = await claim_attempt(db_session)
    await service.start_sending(
        attempt_id=unknown_attempt.id,
        worker_id=unknown_worker,
        now=NOW + timedelta(seconds=1),
    )
    unknown = await service.complete_unknown(
        attempt_id=unknown_attempt.id,
        worker_id=unknown_worker,
        now=NOW + timedelta(seconds=2),
    )
    assert unknown.attempt.status == "unknown"
    assert unknown.attempt.error_kind == "unknown"
    assert unknown.run.status == "failed"
    assert unknown.run.next_attempt_at is None
    assert unknown.run.result_code == RESULT_UNKNOWN


async def test_unsafe_error_is_rejected_before_storage(db_session: AsyncSession) -> None:
    _, attempt, worker_id = await claim_attempt(db_session)
    service = DeliveryService(db_session)
    await service.start_sending(
        attempt_id=attempt.id, worker_id=worker_id, now=NOW + timedelta(seconds=1)
    )
    with pytest.raises(ValueError):
        await service.complete_failure(
            attempt_id=attempt.id,
            worker_id=worker_id,
            now=NOW + timedelta(seconds=2),
            error_kind=DeliveryErrorKind.PERMANENT,
            error_code="db_error",
            error_summary="postgresql://user:secret@localhost/database",
        )
    await db_session.refresh(attempt)
    assert attempt.status == "sending"
    assert attempt.error_summary is None


async def test_lifecycle_savepoint_rollback_restores_attempt_and_run(
    db_session: AsyncSession,
) -> None:
    run, attempt, worker_id = await claim_attempt(db_session)
    with pytest.raises(RuntimeError, match="force rollback"):
        async with db_session.begin_nested():
            service = DeliveryService(db_session)
            await service.start_sending(
                attempt_id=attempt.id,
                worker_id=worker_id,
                now=NOW + timedelta(seconds=1),
            )
            await service.complete_success(
                attempt_id=attempt.id,
                worker_id=worker_id,
                now=NOW + timedelta(seconds=2),
                message_id=900,
            )
            raise RuntimeError("force rollback")
    await db_session.refresh(attempt)
    await db_session.refresh(run)
    assert attempt.status == "claimed"
    assert attempt.send_started_at is None
    assert run.status == "processing"
    assert run.claimed_by == worker_id


async def test_lifecycle_rollback_and_no_auto_commit(
    db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    run, attempt, worker_id = await claim_attempt(db_session)
    service = DeliveryService(db_session)
    await service.start_sending(
        attempt_id=attempt.id, worker_id=worker_id, now=NOW + timedelta(seconds=1)
    )
    await service.complete_success(
        attempt_id=attempt.id,
        worker_id=worker_id,
        now=NOW + timedelta(seconds=2),
        message_id=800,
    )

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as observer:
        assert (
            await observer.scalar(
                select(func.count()).select_from(ScheduleRun).where(ScheduleRun.id == run.id)
            )
            == 0
        )
        assert (
            await observer.scalar(
                select(func.count())
                .select_from(DeliveryAttempt)
                .where(DeliveryAttempt.id == attempt.id)
            )
            == 0
        )
