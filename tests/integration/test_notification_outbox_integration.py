import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.domain.enums import NotificationErrorKind
from discord_ai_reminder_bot.infrastructure.database.exceptions import (
    RepositoryStateConflictError,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    NotificationAttempt,
    NotificationLog,
)
from discord_ai_reminder_bot.infrastructure.database.notification_repositories import (
    NotificationAttemptRepository,
    NotificationLogRepository,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)
LEASE = timedelta(minutes=2)


def pending(*, key: str, scheduled_at: datetime = NOW) -> NotificationLog:
    return NotificationLog(
        schedule_id=None,
        schedule_run_id=None,
        notification_type="recovery",
        recipient_type="log",
        recipient_id=None,
        status="pending",
        deduplication_key=key,
        error_code=None,
        error_summary=None,
        scheduled_at=scheduled_at,
        next_attempt_at=scheduled_at,
        attempt_count=0,
        claimed_by=None,
        claimed_at=None,
        lease_expires_at=None,
        started_at=None,
        finished_at=None,
        sent_at=None,
    )


async def claim(
    session: AsyncSession, *, key: str = "notification-claim"
) -> tuple[NotificationLog, NotificationAttempt, uuid.UUID]:
    repository = NotificationLogRepository(session)
    notification = await repository.add_idempotent(pending(key=key))
    worker = uuid.uuid7()
    claimed = await repository.claim_due(
        now=NOW, worker_id=worker, batch_size=1, lease_timeout=LEASE
    )
    assert len(claimed) == 1
    return notification, claimed[0].attempt, worker


async def test_idempotent_add_returns_existing_and_rejects_conflict(
    db_session: AsyncSession,
) -> None:
    repository = NotificationLogRepository(db_session)
    first = await repository.add_idempotent(pending(key="same-key"))
    second = await repository.add_idempotent(pending(key="same-key"))
    assert first.id == second.id
    conflict = pending(key="same-key")
    conflict.recipient_type = "operator_dm"
    conflict.recipient_id = 123
    with pytest.raises(RepositoryStateConflictError):
        await repository.add_idempotent(conflict)
    assert await db_session.scalar(select(func.count(NotificationLog.id))) == 1


async def test_database_rejects_invalid_pending_lifecycle(db_session: AsyncSession) -> None:
    invalid = pending(key="invalid-lifecycle")
    invalid.next_attempt_at = None
    with pytest.raises(IntegrityError), db_session.no_autoflush:
        async with db_session.begin_nested():
            db_session.add(invalid)
            await db_session.flush()


async def test_partial_outbox_indexes_exist(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND indexname IN "
                "('ix_notification_logs_pending_due', "
                "'ix_notification_logs_processing_lease', "
                "'ix_notification_attempts_log_number')"
            )
        )
    ).all()
    definitions = {name: definition for name, definition in rows}
    assert (
        "WHERE ((status)::text = 'pending'::text)"
        in definitions["ix_notification_logs_pending_due"]
    )
    assert (
        "WHERE ((status)::text = 'processing'::text)"
        in definitions["ix_notification_logs_processing_lease"]
    )
    assert (
        "notification_log_id, attempt_number" in definitions["ix_notification_attempts_log_number"]
    )


async def test_claim_creates_attempt_and_stable_state(db_session: AsyncSession) -> None:
    notification, attempt, worker = await claim(db_session)
    assert notification.status == "processing"
    assert notification.attempt_count == 1
    assert notification.claimed_by == worker
    assert notification.claimed_at == NOW
    assert notification.lease_expires_at == NOW + LEASE
    assert notification.started_at == NOW
    assert attempt.status == "claimed" and attempt.attempt_number == 1


async def test_claimed_sending_succeeded_lifecycle(db_session: AsyncSession) -> None:
    notification, attempt, worker = await claim(db_session, key="success")
    attempts = NotificationAttemptRepository(db_session)
    logs = NotificationLogRepository(db_session)
    sending_at = NOW + timedelta(seconds=1)
    await attempts.mark_sending(attempt_id=attempt.id, worker_id=worker, now=sending_at)
    await logs.mark_sending_started(
        notification_id=notification.id, worker_id=worker, now=sending_at
    )
    finished_at = NOW + timedelta(seconds=2)
    await attempts.mark_succeeded(
        attempt_id=attempt.id,
        worker_id=worker,
        now=finished_at,
        message_id=999,
    )
    await logs.mark_succeeded(notification_id=notification.id, worker_id=worker, now=finished_at)
    assert attempt.status == "succeeded" and attempt.discord_message_id == 999
    assert notification.status == "succeeded"
    assert notification.sent_at == finished_at
    assert notification.finished_at == finished_at
    assert notification.claimed_by is None


@pytest.mark.parametrize(("retry", "expected_status"), [(True, "pending"), (False, "failed")])
async def test_failed_attempt_can_retry_or_finish(
    db_session: AsyncSession, retry: bool, expected_status: str
) -> None:
    notification, attempt, worker = await claim(db_session, key=f"failure-{retry}")
    attempts = NotificationAttemptRepository(db_session)
    logs = NotificationLogRepository(db_session)
    failed_at = NOW + timedelta(seconds=1)
    await attempts.mark_failed(
        attempt_id=attempt.id,
        worker_id=worker,
        now=failed_at,
        error_kind=NotificationErrorKind.TRANSIENT,
        error_code="temporary_failure",
        error_summary="Temporary notification delivery failure",
    )
    if retry:
        await logs.return_to_pending(
            notification_id=notification.id,
            worker_id=worker,
            now=failed_at,
            retry_at=failed_at + timedelta(minutes=1),
            error_code="temporary_failure",
            error_summary="Temporary notification delivery failure",
        )
    else:
        await logs.mark_failed(
            notification_id=notification.id,
            worker_id=worker,
            now=failed_at,
            error_code="delivery_failed",
            error_summary="Notification delivery failed",
        )
    assert attempt.status == "failed"
    assert notification.status == expected_status
    assert notification.next_attempt_at == (failed_at + timedelta(minutes=1) if retry else None)


async def test_sending_unknown_is_terminal(db_session: AsyncSession) -> None:
    notification, attempt, worker = await claim(db_session, key="unknown")
    attempts = NotificationAttemptRepository(db_session)
    logs = NotificationLogRepository(db_session)
    await attempts.mark_sending(
        attempt_id=attempt.id, worker_id=worker, now=NOW + timedelta(seconds=1)
    )
    await attempts.mark_unknown(
        attempt_id=attempt.id,
        worker_id=worker,
        now=NOW + timedelta(seconds=2),
        error_code="delivery_result_unknown",
        error_summary="Notification delivery result is unknown",
    )
    await logs.mark_unknown(
        notification_id=notification.id,
        worker_id=worker,
        now=NOW + timedelta(seconds=2),
        error_code="delivery_result_unknown",
        error_summary="Notification delivery result is unknown",
    )
    assert attempt.status == "unknown" and attempt.error_kind == "unknown"
    assert notification.status == "unknown" and notification.next_attempt_at is None


async def test_cancelled_is_not_claimed(db_session: AsyncSession) -> None:
    repository = NotificationLogRepository(db_session)
    notification = await repository.add_idempotent(pending(key="cancel"))
    await repository.cancel(
        notification_id=notification.id,
        now=NOW,
        error_code="schedule_changed",
        error_summary="Notification is no longer applicable",
    )
    assert notification.status == "cancelled"
    assert not await repository.claim_due(
        now=NOW, worker_id=uuid.uuid7(), batch_size=20, lease_timeout=LEASE
    )


async def test_expired_processing_is_locked_for_recovery(db_session: AsyncSession) -> None:
    notification, _attempt, _worker = await claim(db_session, key="expired")
    rows = await NotificationLogRepository(db_session).lock_expired(now=NOW + LEASE, batch_size=20)
    assert [row.id for row in rows] == [notification.id]


async def test_rollback_restores_log_and_attempt(db_session: AsyncSession) -> None:
    repository = NotificationLogRepository(db_session)
    notification = await repository.add_idempotent(pending(key="rollback"))
    with pytest.raises(RuntimeError, match="force rollback"):
        async with db_session.begin_nested():
            await repository.claim_due(
                now=NOW, worker_id=uuid.uuid7(), batch_size=1, lease_timeout=LEASE
            )
            raise RuntimeError("force rollback")
    await db_session.refresh(notification)
    assert notification.status == "pending" and notification.attempt_count == 0
    assert await db_session.scalar(select(func.count(NotificationAttempt.id))) == 0


async def test_uncommitted_claim_is_invisible_to_other_session(
    db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    notification, _attempt, _worker = await claim(db_session, key="uncommitted")
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as observer:
        assert await observer.get(NotificationLog, notification.id) is None


async def test_two_sessions_skip_locked_without_overlap(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as seed, seed.begin():
        repository = NotificationLogRepository(seed)
        first = await repository.add_idempotent(pending(key="concurrent-1"))
        second = await repository.add_idempotent(
            pending(key="concurrent-2", scheduled_at=NOW + timedelta(microseconds=1))
        )
        ids = [first.id, second.id]
    try:
        async with factory() as one, factory() as two:
            tx_one = await one.begin()
            tx_two = await two.begin()
            rows_one = await NotificationLogRepository(one).claim_due(
                now=NOW + timedelta(seconds=1),
                worker_id=uuid.uuid7(),
                batch_size=1,
                lease_timeout=LEASE,
            )
            rows_two = await NotificationLogRepository(two).claim_due(
                now=NOW + timedelta(seconds=1),
                worker_id=uuid.uuid7(),
                batch_size=1,
                lease_timeout=LEASE,
            )
            assert rows_one[0].notification.id != rows_two[0].notification.id
            await tx_one.rollback()
            await tx_two.rollback()
    finally:
        async with factory() as cleanup, cleanup.begin():
            await cleanup.execute(delete(NotificationLog).where(NotificationLog.id.in_(ids)))
