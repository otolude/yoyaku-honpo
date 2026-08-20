import asyncio
import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.gateway import (
    OutboundMessage,
    PermanentGatewayError,
    RateLimitGatewayError,
    TransientGatewayError,
    UnknownGatewayError,
)
from discord_ai_reminder_bot.application.notification_events import NotificationEventService
from discord_ai_reminder_bot.application.worker import PollingWorker
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    NotificationLog,
    OperationLog,
    Schedule,
    ScheduleRun,
)

NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)


class FakeGateway:
    def __init__(self, outcome: int | Exception = 9001) -> None:
        self.outcome = outcome
        self.calls: list[OutboundMessage] = []
        self.active = 0
        self.maximum_active = 0

    async def send(self, message: OutboundMessage) -> int:
        self.calls.append(message)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.active -= 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class ConcurrencyGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.two_started = asyncio.Event()

    async def send(self, message: OutboundMessage) -> int:
        self.calls.append(message)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        if self.active == 2:
            self.two_started.set()
        await self.two_started.wait()
        self.active -= 1
        return 9000 + len(self.calls)


class ObservingGateway(FakeGateway):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__()
        self.engine = engine
        self.observed: tuple[str, str] | None = None

    async def send(self, message: OutboundMessage) -> int:
        async with factory(self.engine)() as session, session.begin():
            run = (
                await session.execute(
                    select(ScheduleRun)
                    .where(ScheduleRun.id == message.schedule_run_id)
                    .with_for_update(nowait=True)
                )
            ).scalar_one()
            attempt = (
                await session.execute(
                    select(DeliveryAttempt).where(
                        DeliveryAttempt.schedule_run_id == message.schedule_run_id
                    )
                )
            ).scalar_one()
            self.observed = (run.status, attempt.status)
        return await super().send(message)


class MixedGateway(FakeGateway):
    async def send(self, message: OutboundMessage) -> int:
        self.calls.append(message)
        if len(self.calls) == 1:
            raise RuntimeError("unclassified adapter failure")
        return 9100 + len(self.calls)


@pytest_asyncio.fixture(autouse=True)
async def clean_worker_tables(test_engine: AsyncEngine):
    async def clean() -> None:
        async with test_engine.begin() as connection:
            for model in (NotificationLog, OperationLog, DeliveryAttempt, ScheduleRun, Schedule):
                await connection.execute(delete(model))

    await clean()
    yield
    await clean()


def factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def worker(
    engine: AsyncEngine,
    gateway: FakeGateway,
    *,
    batch_size: int = 20,
    concurrency: int = 5,
    session_factory=None,
) -> PollingWorker:
    return PollingWorker(
        session_factory=session_factory or factory(engine),
        gateway=gateway,
        clock=FixedClock(NOW),
        worker_id=uuid.uuid7(),
        batch_size=batch_size,
        max_concurrency=concurrency,
        lease_timeout=timedelta(seconds=120),
        logger=logging.getLogger("test.worker"),
        configured_guild_id=100,
        operator_channel_id=400,
    )


async def seed(
    engine: AsyncEngine,
    *,
    status: str = "active",
    schedule_type: str = "once",
    count: int = 1,
    attempt_count: int = 0,
) -> list[tuple[int, int]]:
    result = []
    async with factory(engine)() as session, session.begin():
        for index in range(count):
            recurring = schedule_type != "once"
            terminal = status in {"ended", "deleted"}
            scheduled_for = NOW - timedelta(microseconds=index)
            schedule = Schedule(
                public_id=uuid.uuid7(),
                guild_id=100,
                channel_id=200,
                creator_user_id=300,
                schedule_type=schedule_type,
                status=status,
                content=None if status == "draft" else "safe body",
                next_run_at=(None if status in {"paused", "ended", "deleted"} else scheduled_for),
                local_time=time(12, 0) if recurring else None,
                weekday=None,
                end_date=date(2026, 8, 30) if recurring else None,
                version=1,
                deleted_at=NOW if status == "deleted" else None,
                terminal_at=NOW if terminal else None,
            )
            session.add(schedule)
            await session.flush()
            run = ScheduleRun(
                schedule_id=schedule.id,
                scheduled_for=scheduled_for,
                status="pending",
                attempt_count=attempt_count,
                next_attempt_at=NOW,
            )
            session.add(run)
            await session.flush()
            result.append((schedule.id, run.id))
    return result


@pytest.mark.asyncio
async def test_empty_and_batch_size(test_engine: AsyncEngine) -> None:
    gateway = FakeGateway()
    assert (await worker(test_engine, gateway).poll_once()).claimed == 0
    await seed(test_engine, count=3)
    result = await worker(test_engine, gateway, batch_size=2, concurrency=2).poll_once()
    assert result.claimed == result.succeeded == 2
    assert len(gateway.calls) == 2


@pytest.mark.asyncio
async def test_success_commits_sending_and_finalizes_once(test_engine: AsyncEngine) -> None:
    schedule_id, run_id = (await seed(test_engine))[0]
    gateway = FakeGateway()
    result = await worker(test_engine, gateway).poll_once()
    assert result.succeeded == 1
    async with factory(test_engine)() as session:
        schedule = await session.get(Schedule, schedule_id)
        run = await session.get(ScheduleRun, run_id)
        attempt = (await session.execute(select(DeliveryAttempt))).scalar_one()
        assert schedule is not None and schedule.status == "completed"
        assert run is not None and run.status == "succeeded"
        assert attempt.status == "succeeded"
        assert gateway.calls[0].content == "safe body"


@pytest.mark.asyncio
async def test_gateway_observes_committed_sending_without_database_lock(
    test_engine: AsyncEngine,
) -> None:
    await seed(test_engine)
    gateway = ObservingGateway(test_engine)
    result = await worker(test_engine, gateway).poll_once()
    assert result.succeeded == 1
    assert gateway.observed == ("processing", "sending")


@pytest.mark.asyncio
async def test_recurring_success_creates_one_future_run(test_engine: AsyncEngine) -> None:
    schedule_id, _ = (await seed(test_engine, schedule_type="daily"))[0]
    result = await worker(test_engine, FakeGateway()).poll_once()
    assert result.succeeded == 1
    async with factory(test_engine)() as session:
        schedule = await session.get(Schedule, schedule_id)
        runs = (await session.execute(select(ScheduleRun))).scalars().all()
        assert schedule is not None and schedule.status == "active"
        assert len(runs) == 2
        assert sum(item.status == "pending" for item in runs) == 1


@pytest.mark.parametrize(
    ("outcome", "field"),
    [
        (TransientGatewayError(), "retry_scheduled"),
        (RateLimitGatewayError(NOW + timedelta(minutes=9)), "retry_scheduled"),
        (PermanentGatewayError(), "failed"),
        (UnknownGatewayError(), "unknown"),
    ],
)
@pytest.mark.asyncio
async def test_gateway_outcomes(test_engine: AsyncEngine, outcome: Exception, field: str) -> None:
    _, run_id = (await seed(test_engine))[0]
    result = await worker(test_engine, FakeGateway(outcome)).poll_once()
    assert getattr(result, field) == 1
    async with factory(test_engine)() as session:
        run = await session.get(ScheduleRun, run_id)
        assert run is not None
        if field == "retry_scheduled":
            assert run.status == "pending" and run.next_attempt_at > NOW
            assert await session.scalar(select(func.count()).select_from(NotificationLog)) == 0
        else:
            assert run.status == "failed"
            notification = (
                await session.execute(
                    select(NotificationLog).where(NotificationLog.notification_type == "run_failed")
                )
            ).scalar_one()
            assert notification.schedule_run_id == run.id
            assert notification.recipient_type == "operator_channel"
            assert notification.recipient_id == 400
            assert notification.error_code is None and notification.error_summary is None


@pytest.mark.parametrize("status", ["draft", "paused", "deleted", "ended"])
@pytest.mark.asyncio
async def test_non_sendable_schedule_is_skipped(test_engine: AsyncEngine, status: str) -> None:
    schedule_id, run_id = (await seed(test_engine, status=status, schedule_type="daily"))[0]
    gateway = FakeGateway()
    result = await worker(test_engine, gateway).poll_once()
    assert result.skipped == 1 and not gateway.calls
    async with factory(test_engine)() as session:
        schedule = await session.get(Schedule, schedule_id)
        run = await session.get(ScheduleRun, run_id)
        attempt = (await session.execute(select(DeliveryAttempt))).scalar_one()
        assert schedule is not None and schedule.status == status
        assert run is not None and run.status == "skipped"
        assert attempt.status == "failed"
        assert attempt.error_code == "skipped_before_send"
        if status == "draft":
            assert await session.scalar(select(func.count()).select_from(ScheduleRun)) == 2
            notification = (
                await session.execute(
                    select(NotificationLog).where(
                        NotificationLog.notification_type == "run_skipped"
                    )
                )
            ).scalar_one()
            assert notification.schedule_id == schedule.id
            assert notification.schedule_run_id == run.id
            assert notification.error_code is None
        else:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(NotificationLog)
                    .where(NotificationLog.notification_type == "run_skipped")
                )
                == 0
            )


@pytest.mark.asyncio
async def test_fourth_transient_failure_creates_run_failed(test_engine: AsyncEngine) -> None:
    _, run_id = (await seed(test_engine, attempt_count=3))[0]
    result = await worker(test_engine, FakeGateway(TransientGatewayError())).poll_once()
    assert result.failed == 1
    async with factory(test_engine)() as session:
        run = await session.get(ScheduleRun, run_id)
        notification = (
            await session.execute(
                select(NotificationLog).where(NotificationLog.schedule_run_id == run_id)
            )
        ).scalar_one()
        assert run is not None and run.status == "failed" and run.attempt_count == 4
        assert notification.notification_type == "run_failed"


@pytest.mark.asyncio
async def test_max_concurrency_and_independent_errors(test_engine: AsyncEngine) -> None:
    await seed(test_engine, count=5)
    gateway = ConcurrencyGateway()
    result = await worker(test_engine, gateway, concurrency=2).poll_once()
    assert result.succeeded == 5
    assert gateway.maximum_active == 2


@pytest.mark.asyncio
async def test_one_gateway_exception_does_not_cancel_other_items(test_engine: AsyncEngine) -> None:
    await seed(test_engine, count=2)
    result = await worker(test_engine, MixedGateway(), concurrency=2).poll_once()
    assert result.unknown == 1
    assert result.succeeded == 1


class BrokenContext:
    async def __aenter__(self):
        raise RuntimeError("safe database failure")

    async def __aexit__(self, *args):
        return False


class FailingFactory:
    def __init__(self, real_factory, fail_call: int) -> None:
        self.real_factory = real_factory
        self.fail_call = fail_call
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return BrokenContext() if self.calls == self.fail_call else self.real_factory()


@pytest.mark.asyncio
async def test_claim_failure_never_calls_gateway(test_engine: AsyncEngine) -> None:
    await seed(test_engine)
    gateway = FakeGateway()
    result = await worker(
        test_engine,
        gateway,
        session_factory=FailingFactory(factory(test_engine), 1),
    ).poll_once()
    assert result.internal_errors == 1
    assert not gateway.calls


@pytest.mark.asyncio
async def test_success_persist_failure_leaves_sending_without_resend(
    test_engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    _, run_id = (await seed(test_engine))[0]
    gateway = FakeGateway()
    sessions = FailingFactory(factory(test_engine), 3)
    with caplog.at_level(logging.ERROR):
        result = await worker(test_engine, gateway, session_factory=sessions).poll_once()
    assert result.internal_errors == 1 and len(gateway.calls) == 1
    async with factory(test_engine)() as session:
        run = await session.get(ScheduleRun, run_id)
        attempt = (await session.execute(select(DeliveryAttempt))).scalar_one()
        assert run is not None and run.status == "processing"
        assert attempt.status == "sending"
    assert "safe body" not in caplog.text
    assert "postgresql+psycopg" not in caplog.text


@pytest.mark.asyncio
async def test_notification_event_failure_rolls_back_terminal_delivery(
    test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run_id = (await seed(test_engine))[0]

    async def fail_event(*args, **kwargs):
        raise RuntimeError("safe outbox failure")

    monkeypatch.setattr(NotificationEventService, "add_run_event", fail_event)
    result = await worker(test_engine, FakeGateway(PermanentGatewayError())).poll_once()
    assert result.internal_errors == 1
    async with factory(test_engine)() as session:
        run = await session.get(ScheduleRun, run_id)
        attempt = (await session.execute(select(DeliveryAttempt))).scalar_one()
        assert run is not None and run.status == "processing"
        assert attempt.status == "sending"
        assert await session.scalar(select(func.count(NotificationLog.id))) == 0


@pytest.mark.asyncio
async def test_two_workers_send_each_run_only_once(test_engine: AsyncEngine) -> None:
    await seed(test_engine)
    first, second = FakeGateway(), FakeGateway()
    results = await asyncio.gather(
        worker(test_engine, first).poll_once(), worker(test_engine, second).poll_once()
    )
    assert sum(item.succeeded for item in results) == 1
    assert len(first.calls) + len(second.calls) == 1
