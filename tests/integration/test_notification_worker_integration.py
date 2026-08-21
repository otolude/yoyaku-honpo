import logging
import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import delete, func, select
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
from discord_ai_reminder_bot.domain.notification import (
    global_notification_deduplication_key,
    notification_deduplication_key,
)
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


async def test_unknown_result_is_terminal_without_reclaim_fallback_or_secret_leak(
    test_engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    notification_id = await seed(factory, pending(event_id=uuid.uuid7()))
    secrets = (
        "private post body",
        "bot-token-secret",
        "postgresql+psycopg://secret",
        "complete exception details",
        "Traceback (most recent call last)",
        "discord response body",
    )

    class UnknownGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.messages = []

        async def send(self, message):
            self.calls += 1
            self.messages.append(message)
            raise RuntimeError(" | ".join(secrets))

    gateway = UnknownGateway()
    notification_worker = worker(factory, gateway)
    try:
        with caplog.at_level(logging.ERROR, logger="test.notification.worker"):
            first = await notification_worker.poll_once()
            second = await notification_worker.poll_once()
        assert first.claimed == first.unknown == 1
        assert first.fallbacks_created == 0
        assert second.claimed == second.fallbacks_created == 0
        assert gateway.calls == 1
        assert len(gateway.messages) == 1
        rendered_message = repr(gateway.messages[0])
        rendered_log = caplog.text
        assert all(secret not in rendered_message for secret in secrets)
        assert all(secret not in rendered_log for secret in secrets)
        assert str(notification_worker._worker_id) not in rendered_message
        async with factory() as session:
            log = await session.get(NotificationLog, notification_id)
            attempts = list(
                (
                    await session.execute(
                        select(NotificationAttempt).where(
                            NotificationAttempt.notification_log_id == notification_id
                        )
                    )
                ).scalars()
            )
            assert log is not None and log.status == "unknown" and log.next_attempt_at is None
            assert len(attempts) == 1 and attempts[0].status == "unknown"
            assert await session.scalar(select(func.count(NotificationLog.id))) == 1
    finally:
        await cleanup(factory, [notification_id])


async def test_recovered_sending_unknown_is_not_reclaimed_or_fallbacked(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    notification_id = await seed(factory, pending(event_id=uuid.uuid7()))
    owner = uuid.uuid7()
    async with factory() as session, session.begin():
        claimed = await NotificationLogRepository(session).claim_due(
            now=NOW,
            worker_id=owner,
            batch_size=1,
            lease_timeout=timedelta(seconds=1),
        )
        attempt_id = claimed[0].attempt.id
        await NotificationAttemptRepository(session).mark_sending(
            attempt_id=attempt_id,
            worker_id=owner,
            now=NOW + timedelta(microseconds=1),
        )
    gateway = FakeGateway()
    try:
        async with factory() as session, session.begin():
            recovered = await NotificationRecoveryService(
                session, operator_channel_id=400, operator_user_id=300
            ).recover_expired(recovered_at=NOW + timedelta(seconds=2), batch_size=20)
        after_recovery = await worker(
            factory, gateway, clock=FixedClock(NOW + timedelta(seconds=3))
        ).poll_once()
        assert recovered.selected == recovered.unknown == 1
        assert recovered.fallbacks_created == 0
        assert after_recovery.claimed == after_recovery.fallbacks_created == 0
        assert gateway.calls == 0
        async with factory() as session:
            log = await session.get(NotificationLog, notification_id)
            attempt = await session.get(NotificationAttempt, attempt_id)
            assert log is not None and log.status == "unknown" and log.next_attempt_at is None
            assert attempt is not None and attempt.status == "unknown"
            assert await session.scalar(select(func.count(NotificationLog.id))) == 1
    finally:
        await cleanup(factory, [notification_id])


async def _expired_notification(factory, *, attempt_number: int = 1):
    row = pending(event_id=uuid.uuid7())
    notification_id = await seed(factory, row)
    worker_id = uuid.uuid7()
    claim_time = NOW
    for prior in range(1, attempt_number):
        async with factory() as session, session.begin():
            claimed = await NotificationLogRepository(session).claim_due(
                now=claim_time,
                worker_id=worker_id,
                batch_size=1,
                lease_timeout=timedelta(seconds=1),
            )
            assert len(claimed) == 1 and claimed[0].attempt.attempt_number == prior
            await NotificationAttemptRepository(session).mark_failed(
                attempt_id=claimed[0].attempt.id,
                worker_id=worker_id,
                now=claim_time + timedelta(microseconds=1),
                error_kind="transient",
                error_code="safe_transient",
                error_summary="Safe transient failure",
            )
            await NotificationLogRepository(session).return_to_pending(
                notification_id=notification_id,
                worker_id=worker_id,
                now=claim_time + timedelta(microseconds=1),
                retry_at=claim_time + timedelta(minutes=1),
                error_code="safe_transient",
                error_summary="Notification will be retried",
            )
        claim_time += timedelta(minutes=10)
    async with factory() as session, session.begin():
        claimed = await NotificationLogRepository(session).claim_due(
            now=claim_time,
            worker_id=worker_id,
            batch_size=1,
            lease_timeout=timedelta(seconds=1),
        )
        assert len(claimed) == 1
        return (
            notification_id,
            claimed[0].attempt.id,
            worker_id,
            claim_time + timedelta(seconds=2),
        )


@pytest.mark.parametrize(
    ("attempt_number", "delay", "expected_status", "fallbacks"),
    [
        (1, timedelta(minutes=1), "pending", 0),
        (2, timedelta(minutes=5), "pending", 0),
        (3, None, "failed", 1),
    ],
)
async def test_notification_recovery_attempt_boundaries_and_fallback_idempotency(
    test_engine: AsyncEngine,
    attempt_number: int,
    delay: timedelta | None,
    expected_status: str,
    fallbacks: int,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    notification_id, attempt_id, _, recovered_at = await _expired_notification(
        factory, attempt_number=attempt_number
    )
    try:
        async with factory() as session, session.begin():
            first = await NotificationRecoveryService(
                session, operator_channel_id=400, operator_user_id=300
            ).recover_expired(recovered_at=recovered_at, batch_size=20)
        async with factory() as session, session.begin():
            second = await NotificationRecoveryService(
                session, operator_channel_id=400, operator_user_id=300
            ).recover_expired(recovered_at=recovered_at, batch_size=20)
        assert first.selected == 1 and second.selected == 0
        assert first.fallbacks_created == fallbacks
        async with factory() as session:
            log = await session.get(NotificationLog, notification_id)
            attempt = await session.get(NotificationAttempt, attempt_id)
            assert log is not None and log.status == expected_status
            assert attempt is not None and attempt.status == "failed"
            assert log.next_attempt_at == (recovered_at + delay if delay is not None else None)
            rows = list((await session.execute(select(NotificationLog))).scalars())
            assert len(rows) == 1 + fallbacks
            if fallbacks:
                fallback = next(row for row in rows if row.id != notification_id)
                assert fallback.recipient_type == "operator_dm"
                assert fallback.status == "pending"
            ids = [row.id for row in rows]
    finally:
        await cleanup(factory, ids if "ids" in locals() else [notification_id])


@pytest.mark.parametrize("inconsistency", ["missing", "number", "worker", "claimed_at", "state"])
async def test_notification_recovery_inconsistency_only_terminalizes_log(
    test_engine: AsyncEngine, inconsistency: str
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    notification_id, attempt_id, _, recovered_at = await _expired_notification(factory)
    original = None
    async with factory() as session, session.begin():
        log = await session.get(NotificationLog, notification_id)
        attempt = await session.get(NotificationAttempt, attempt_id)
        assert log is not None and attempt is not None
        if inconsistency == "missing":
            await session.delete(attempt)
        elif inconsistency == "number":
            attempt.attempt_number = 2
        elif inconsistency == "worker":
            attempt.claimed_by = uuid.uuid7()
        elif inconsistency == "claimed_at":
            attempt.claimed_at = NOW - timedelta(microseconds=1)
        else:
            attempt.status = "failed"
            attempt.finished_at = NOW + timedelta(microseconds=1)
            attempt.error_kind = "permanent"
            attempt.error_code = "safe_failure"
            attempt.error_summary = "Safe failure"
        await session.flush()
        if inconsistency != "missing":
            original = (
                attempt.attempt_number,
                attempt.status,
                attempt.claimed_by,
                attempt.claimed_at,
                attempt.finished_at,
                attempt.error_kind,
                attempt.error_code,
                attempt.error_summary,
            )
    try:
        async with factory() as session, session.begin():
            result = await NotificationRecoveryService(
                session, operator_channel_id=400, operator_user_id=300
            ).recover_expired(recovered_at=recovered_at, batch_size=20)
        assert result.selected == result.unknown == 1
        assert result.fallbacks_created == 0
        async with factory() as session:
            log = await session.get(NotificationLog, notification_id)
            attempt = await session.get(NotificationAttempt, attempt_id)
            assert log is not None and log.status == "unknown" and log.next_attempt_at is None
            assert await session.scalar(select(func.count(NotificationLog.id))) == 1
            if original is None:
                assert attempt is None
            else:
                assert attempt is not None
                assert (
                    attempt.attempt_number,
                    attempt.status,
                    attempt.claimed_by,
                    attempt.claimed_at,
                    attempt.finished_at,
                    attempt.error_kind,
                    attempt.error_code,
                    attempt.error_summary,
                ) == original
    finally:
        await cleanup(factory, [notification_id])


async def test_notification_recovery_skip_locked_changes_are_invisible_and_rollback(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    first_id, _, _, recovered_at = await _expired_notification(factory)
    second_id, _, _, _ = await _expired_notification(factory)
    first, second = factory(), factory()
    first_tx, second_tx = await first.begin(), await second.begin()
    try:
        first_result = await NotificationRecoveryService(
            first, operator_channel_id=400, operator_user_id=300
        ).recover_expired(recovered_at=recovered_at, batch_size=1)
        second_result = await NotificationRecoveryService(
            second, operator_channel_id=400, operator_user_id=300
        ).recover_expired(recovered_at=recovered_at, batch_size=1)
        assert first_result.selected == second_result.selected == 1
        async with factory() as observer:
            observed = list(
                (
                    await observer.execute(
                        select(NotificationLog).where(NotificationLog.id.in_([first_id, second_id]))
                    )
                ).scalars()
            )
            assert all(row.status == "processing" for row in observed)
    finally:
        await first_tx.rollback()
        await second_tx.rollback()
        await first.close()
        await second.close()
    async with factory() as verifier:
        rows = list(
            (
                await verifier.execute(
                    select(NotificationLog).where(NotificationLog.id.in_([first_id, second_id]))
                )
            ).scalars()
        )
        assert all(row.status == "processing" for row in rows)
    await cleanup(factory, [first_id, second_id])


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


async def _seed_due_draft_notification(factory) -> tuple[int, int, int]:
    run_at = NOW + timedelta(hours=25)
    async with factory() as session, session.begin():
        schedule = Schedule(
            public_id=uuid.uuid7(),
            guild_id=100,
            channel_id=200,
            creator_user_id=300,
            schedule_type="daily",
            status="draft",
            content=None,
            next_run_at=run_at,
            local_time=time(12, 0),
            version=1,
        )
        session.add(schedule)
        await session.flush()
        run = ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=run_at,
            status="pending",
            attempt_count=0,
            next_attempt_at=run_at,
        )
        session.add(run)
        await session.flush()
        log = NotificationLog(
            schedule_id=schedule.id,
            schedule_run_id=run.id,
            notification_type="draft_24h",
            recipient_type="creator_dm",
            recipient_id=schedule.creator_user_id,
            status="pending",
            deduplication_key=notification_deduplication_key(
                event_kind="draft_reminder",
                schedule_public_id=schedule.public_id,
                scheduled_for=run.scheduled_for,
                notification_type=NotificationType.DRAFT_24H,
                recipient_type=NotificationRecipientType.CREATOR_DM,
            ),
            scheduled_at=NOW + timedelta(hours=1),
            next_attempt_at=NOW + timedelta(hours=1),
            attempt_count=0,
        )
        session.add(log)
        await session.flush()
        return schedule.id, run.id, log.id


@pytest.mark.parametrize(
    "change",
    ["active", "paused", "deleted", "next_run", "run", "type", "scheduled_at"],
)
async def test_due_draft_notification_cancels_for_each_stale_change(
    test_engine: AsyncEngine, change: str
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id, run_id, log_id = await _seed_due_draft_notification(factory)
    async with factory() as session, session.begin():
        schedule = await session.get(Schedule, schedule_id)
        run = await session.get(ScheduleRun, run_id)
        log = await session.get(NotificationLog, log_id)
        assert schedule is not None and run is not None and log is not None
        if change == "active":
            schedule.status, schedule.content = "active", "safe body"
        elif change == "paused":
            schedule.status, schedule.next_run_at = "paused", None
        elif change == "deleted":
            schedule.status, schedule.next_run_at = "deleted", None
            schedule.deleted_at = schedule.terminal_at = NOW + timedelta(hours=2)
        elif change == "next_run":
            schedule.next_run_at = run.scheduled_for + timedelta(days=1)
        elif change == "run":
            other = ScheduleRun(
                schedule_id=schedule.id,
                scheduled_for=run.scheduled_for + timedelta(days=1),
                status="pending",
                attempt_count=0,
                next_attempt_at=run.scheduled_for + timedelta(days=1),
            )
            session.add(other)
            await session.flush()
            log.schedule_run_id = other.id
        elif change == "type":
            log.notification_type = "draft_1h"
        else:
            log.scheduled_at += timedelta(minutes=30)
            log.next_attempt_at = log.scheduled_at

    gateway = FakeGateway()
    try:
        first = await worker(
            factory, gateway, clock=FixedClock(NOW + timedelta(hours=2))
        ).poll_once()
        second = await worker(
            factory, gateway, clock=FixedClock(NOW + timedelta(hours=2))
        ).poll_once()
        assert first.cancelled == 1 and second.claimed == 0
        assert gateway.calls == 0
        async with factory() as session:
            log = await session.get(NotificationLog, log_id)
            assert log is not None and log.status == "cancelled"
            assert (
                await session.scalar(
                    select(func.count(NotificationLog.id)).where(
                        NotificationLog.schedule_id == schedule_id
                    )
                )
                == 1
            )
    finally:
        await _cleanup_business(factory, schedule_id)


async def test_processing_owned_and_terminal_notifications_are_not_changed_by_other_worker(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    processing_id = await seed(factory, pending(event_id=uuid.uuid7()))
    terminal_id = await seed(factory, pending(event_id=uuid.uuid7()))
    owner = uuid.uuid7()
    async with factory() as session, session.begin():
        claimed = await NotificationLogRepository(session).claim_due(
            now=NOW, worker_id=owner, batch_size=1, lease_timeout=timedelta(minutes=5)
        )
        assert claimed[0].notification.id == processing_id
        terminal = await session.get(NotificationLog, terminal_id)
        assert terminal is not None
        await NotificationLogRepository(session).cancel(
            notification_id=terminal.id,
            now=NOW,
            error_code="stale",
            error_summary="Notification is stale",
        )
    gateway = FakeGateway()
    try:
        result = await worker(factory, gateway).poll_once()
        assert result.claimed == 0 and gateway.calls == 0
        async with factory() as session:
            processing = await session.get(NotificationLog, processing_id)
            terminal = await session.get(NotificationLog, terminal_id)
            assert processing is not None and processing.status == "processing"
            assert processing.claimed_by == owner
            assert terminal is not None and terminal.status == "cancelled"
    finally:
        await cleanup(factory, [processing_id, terminal_id])


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
