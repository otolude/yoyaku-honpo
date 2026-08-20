import logging
import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from discord_ai_reminder_bot.application.notification_events import NotificationEventService
from discord_ai_reminder_bot.application.notification_gateway import (
    NotificationPermanentError,
    NotificationRateLimitError,
    NotificationTransientError,
    NotificationUnknownError,
)
from discord_ai_reminder_bot.application.notification_recovery import NotificationRecoveryService
from discord_ai_reminder_bot.application.notification_worker import NotificationWorker
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.domain.enums import (
    NotificationRecipientType,
    NotificationStatus,
    NotificationType,
)
from discord_ai_reminder_bot.domain.notification import global_notification_deduplication_key
from discord_ai_reminder_bot.infrastructure.database.models import (
    NotificationAttempt,
    NotificationLog,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.database.notification_repositories import (
    NotificationAttemptRepository,
    NotificationLogRepository,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)


class FakeGateway:
    def __init__(self, result=9001) -> None:
        self.result = result
        self.calls = 0

    async def send(self, message):
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def pending(*, event_id: uuid.UUID, route=NotificationRecipientType.OPERATOR_CHANNEL):
    recipient = {
        NotificationRecipientType.OPERATOR_CHANNEL: 400,
        NotificationRecipientType.OPERATOR_DM: 300,
        NotificationRecipientType.LOG: None,
    }[route]
    return NotificationLog(
        schedule_id=None,
        schedule_run_id=None,
        notification_type=NotificationType.RECOVERY.value,
        recipient_type=route.value,
        recipient_id=recipient,
        status=NotificationStatus.PENDING.value,
        deduplication_key=global_notification_deduplication_key(
            event_kind="recovery_required",
            event_public_id=event_id,
            occurred_at=NOW,
            notification_type=NotificationType.RECOVERY,
            recipient_type=route,
        ),
        error_code=None,
        error_summary=None,
        scheduled_at=NOW,
        next_attempt_at=NOW,
        attempt_count=0,
        claimed_by=None,
        claimed_at=None,
        lease_expires_at=None,
        started_at=None,
        finished_at=None,
        sent_at=None,
    )


def worker(factory, gateway, *, clock=None):
    return NotificationWorker(
        session_factory=factory,
        gateway=gateway,
        clock=clock or FixedClock(NOW),
        worker_id=uuid.uuid7(),
        configured_guild_id=100,
        operator_channel_id=400,
        operator_user_id=300,
        batch_size=20,
        max_concurrency=5,
        lease_timeout=timedelta(minutes=2),
        logger=logging.getLogger("test.notification.worker"),
    )


async def seed(factory, row):
    async with factory() as session, session.begin():
        stored = await NotificationLogRepository(session).add_idempotent(row)
        return stored.id


async def cleanup(factory, ids):
    async with factory() as session, session.begin():
        await session.execute(
            delete(NotificationAttempt).where(NotificationAttempt.notification_log_id.in_(ids))
        )
        await session.execute(delete(NotificationLog).where(NotificationLog.id.in_(ids)))


@pytest.mark.parametrize(
    ("gateway_result", "status", "counter"),
    [
        (9001, "succeeded", "succeeded"),
        (NotificationTransientError(), "pending", "retry_scheduled"),
        (NotificationRateLimitError(NOW + timedelta(minutes=3)), "pending", "retry_scheduled"),
        (NotificationUnknownError(), "unknown", "unknown"),
    ],
)
async def test_worker_result_lifecycle(
    test_engine: AsyncEngine, gateway_result, status: str, counter: str
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    notification_id = await seed(factory, pending(event_id=uuid.uuid7()))
    gateway = FakeGateway(gateway_result)
    try:
        result = await worker(factory, gateway).poll_once()
        assert result.claimed == 1 and getattr(result, counter) == 1
        async with factory() as session:
            log = await session.get(NotificationLog, notification_id)
            attempt = await session.scalar(
                select(NotificationAttempt).where(
                    NotificationAttempt.notification_log_id == notification_id
                )
            )
            assert log is not None and log.status == status
            assert attempt is not None
            if status == "succeeded":
                assert attempt.status == "succeeded" and attempt.discord_message_id == 9001
            elif status == "unknown":
                assert attempt.status == "unknown"
            else:
                assert attempt.status == "failed" and log.next_attempt_at > NOW
    finally:
        await cleanup(factory, [notification_id])


async def test_permanent_failure_creates_separate_fallback_atomically(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    source_id = await seed(factory, pending(event_id=uuid.uuid7()))
    try:
        result = await worker(factory, FakeGateway(NotificationPermanentError())).poll_once()
        assert result.failed == 1 and result.fallbacks_created == 1
        async with factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(NotificationLog).order_by(NotificationLog.id.asc())
                    )
                ).scalars()
            )
            source = next(row for row in rows if row.id == source_id)
            fallback = next(row for row in rows if row.id != source_id)
            assert source.status == "failed"
            assert fallback.status == "pending"
            assert fallback.recipient_type == "operator_dm" and fallback.recipient_id == 300
            ids = [row.id for row in rows]
    finally:
        await cleanup(factory, ids if "ids" in locals() else [source_id])


async def test_gateway_runs_without_notification_row_lock(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    notification_id = await seed(factory, pending(event_id=uuid.uuid7()))

    class LockProbeGateway(FakeGateway):
        async def send(self, message):
            async with factory() as probe, probe.begin():
                locked = await probe.scalar(
                    select(NotificationLog)
                    .where(NotificationLog.id == notification_id)
                    .with_for_update(nowait=True)
                )
                assert locked is not None
            return await super().send(message)

    try:
        result = await worker(factory, LockProbeGateway()).poll_once()
        assert result.succeeded == 1
    finally:
        await cleanup(factory, [notification_id])


@pytest.mark.parametrize(("sending", "expected"), [(False, "pending"), (True, "unknown")])
async def test_notification_lease_recovery(
    test_engine: AsyncEngine, sending: bool, expected: str
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    notification_id = await seed(factory, pending(event_id=uuid.uuid7()))
    worker_id = uuid.uuid7()
    async with factory() as session, session.begin():
        claimed = await NotificationLogRepository(session).claim_due(
            now=NOW,
            worker_id=worker_id,
            batch_size=1,
            lease_timeout=timedelta(seconds=1),
        )
        attempt_id = claimed[0].attempt.id
        if sending:
            await NotificationAttemptRepository(session).mark_sending(
                attempt_id=attempt_id,
                worker_id=worker_id,
                now=NOW + timedelta(microseconds=1),
            )
    try:
        async with factory() as session, session.begin():
            result = await NotificationRecoveryService(
                session, operator_channel_id=400, operator_user_id=300
            ).recover_expired(recovered_at=NOW + timedelta(seconds=2), batch_size=20)
        assert result.selected == 1
        async with factory() as session:
            log = await session.get(NotificationLog, notification_id)
            attempt = await session.get(NotificationAttempt, attempt_id)
            assert log is not None and log.status == expected
            assert attempt is not None and attempt.status == ("unknown" if sending else "failed")
    finally:
        await cleanup(factory, [notification_id])


async def _seed_business_event(
    factory,
    *,
    notification_type: NotificationType,
    run_status: str,
    result_code: str,
) -> tuple[int, int, int]:
    async with factory() as session, session.begin():
        schedule = Schedule(
            public_id=uuid.uuid7(),
            guild_id=100,
            channel_id=200,
            creator_user_id=300,
            schedule_type="once",
            status="active",
            content="private body",
            next_run_at=NOW,
            version=1,
        )
        session.add(schedule)
        await session.flush()
        run = ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=NOW,
            status=run_status,
            attempt_count=1,
            next_attempt_at=NOW if run_status == "pending" else None,
            result_code=result_code,
            finished_at=NOW if run_status in {"failed", "skipped", "succeeded"} else None,
        )
        session.add(run)
        await session.flush()
        log = await NotificationEventService(
            session, configured_guild_id=100, operator_channel_id=400
        ).add_run_event(
            schedule=schedule,
            run=run,
            notification_type=notification_type,
            event_at=NOW,
        )
        assert log is not None
        return schedule.id, run.id, log.id


async def _cleanup_business(factory, schedule_id: int) -> None:
    async with factory() as session, session.begin():
        log_ids = select(NotificationLog.id).where(NotificationLog.schedule_id == schedule_id)
        await session.execute(
            delete(NotificationAttempt).where(NotificationAttempt.notification_log_id.in_(log_ids))
        )
        await session.execute(
            delete(NotificationLog).where(NotificationLog.schedule_id == schedule_id)
        )
        await session.execute(delete(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id))
        await session.execute(delete(Schedule).where(Schedule.id == schedule_id))


@pytest.mark.parametrize(
    ("result_code", "expected"),
    [("draft_without_content", "succeeded"), ("schedule_paused", "cancelled")],
)
async def test_run_skipped_revalidation(
    test_engine: AsyncEngine, result_code: str, expected: str
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id, _run_id, log_id = await _seed_business_event(
        factory,
        notification_type=NotificationType.RUN_SKIPPED,
        run_status="skipped",
        result_code=result_code,
    )
    gateway = FakeGateway()
    try:
        result = await worker(factory, gateway).poll_once()
        assert getattr(result, expected) == 1
        assert gateway.calls == (1 if expected == "succeeded" else 0)
        async with factory() as session:
            log = await session.get(NotificationLog, log_id)
            assert log is not None and log.status == expected
    finally:
        await _cleanup_business(factory, schedule_id)


async def test_run_delayed_is_cancelled_when_run_failed_exists(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id, run_id, delayed_id = await _seed_business_event(
        factory,
        notification_type=NotificationType.RUN_DELAYED,
        run_status="pending",
        result_code="startup_delayed",
    )
    async with factory() as session, session.begin():
        schedule = await session.get(Schedule, schedule_id)
        run = await session.get(ScheduleRun, run_id)
        assert schedule is not None and run is not None
        run.status = "failed"
        run.next_attempt_at = None
        run.result_code = "delivery_failed"
        run.finished_at = NOW
        failed = await NotificationEventService(
            session, configured_guild_id=100, operator_channel_id=400
        ).add_run_event(
            schedule=schedule,
            run=run,
            notification_type=NotificationType.RUN_FAILED,
            event_at=NOW,
        )
        assert failed is not None
        failed_id = failed.id
    gateway = FakeGateway()
    try:
        result = await worker(factory, gateway).poll_once()
        assert result.cancelled == 1 and result.succeeded == 1
        assert gateway.calls == 1
        async with factory() as session:
            delayed = await session.get(NotificationLog, delayed_id)
            failed = await session.get(NotificationLog, failed_id)
            assert delayed is not None and delayed.status == "cancelled"
            assert failed is not None and failed.status == "succeeded"
    finally:
        await _cleanup_business(factory, schedule_id)


async def test_run_failed_is_cancelled_after_return_to_retry_pending(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id, _run_id, log_id = await _seed_business_event(
        factory,
        notification_type=NotificationType.RUN_FAILED,
        run_status="pending",
        result_code="retry_pending",
    )
    gateway = FakeGateway()
    try:
        result = await worker(factory, gateway).poll_once()
        assert result.cancelled == 1 and gateway.calls == 0
        async with factory() as session:
            log = await session.get(NotificationLog, log_id)
            assert log is not None and log.status == "cancelled"
    finally:
        await _cleanup_business(factory, schedule_id)


async def test_recurring_missed_schedule_event_is_revalidated(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        schedule = Schedule(
            public_id=uuid.uuid7(),
            guild_id=100,
            channel_id=200,
            creator_user_id=300,
            schedule_type="daily",
            status="active",
            content="private body",
            next_run_at=NOW + timedelta(days=1),
            local_time=time(12, 0),
            version=1,
        )
        session.add(schedule)
        await session.flush()
        log = await NotificationEventService(
            session, configured_guild_id=100, operator_channel_id=400
        ).add_recurring_missed(schedule=schedule, recovery_cutoff=NOW)
        assert log is not None
        schedule_id, log_id = schedule.id, log.id
    gateway = FakeGateway()
    try:
        result = await worker(factory, gateway).poll_once()
        assert result.succeeded == 1 and gateway.calls == 1
        async with factory() as session:
            persisted = await session.get(NotificationLog, log_id)
            assert persisted is not None and persisted.status == "succeeded"
    finally:
        await _cleanup_business(factory, schedule_id)
