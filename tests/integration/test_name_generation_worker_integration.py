import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from discord_ai_reminder_bot.application.name_generation_worker import NameGenerationWorker
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.domain.name_generation import (
    BudgetPolicy,
    GeneratedScheduleName,
    NameGenerationRequest,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    NameGenerationBudgetBucket,
    NameGenerationJob,
    OperationLog,
    Schedule,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 30, 4, 0, tzinfo=UTC)


class LockCheckingFakeGenerator:
    available = True
    maximum_cost_microunits = 10

    def __init__(self, sessions, schedule_id: int, *, wait: bool = False) -> None:
        self.sessions = sessions
        self.schedule_id = schedule_id
        self.started = asyncio.Event()
        self.release = asyncio.Event() if wait else None
        self.requests: list[NameGenerationRequest] = []

    async def generate(self, request: NameGenerationRequest) -> GeneratedScheduleName:
        self.requests.append(request)
        async with self.sessions() as session, session.begin():
            locked = await session.scalar(
                select(Schedule.id)
                .where(Schedule.id == self.schedule_id)
                .with_for_update(nowait=True)
            )
            assert locked == self.schedule_id
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        return GeneratedScheduleName("隔離生成名")


async def _seed(sessions, guild_id: int) -> tuple[int, int]:
    async with sessions() as session, session.begin():
        schedule = Schedule(
            public_id=uuid.uuid7(),
            guild_id=guild_id,
            channel_id=guild_id + 1,
            creator_user_id=guild_id + 2,
            schedule_type="once",
            status="active",
            content="worker-private-canary",
            display_name=None,
            display_name_source="unset",
            next_run_at=NOW + timedelta(hours=1),
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(schedule)
        await session.flush()
        job = NameGenerationJob(
            schedule_id=schedule.id,
            expected_schedule_version=1,
            status="pending",
            reserved_cost_microunits=0,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(job)
        await session.flush()
        return schedule.id, job.id


async def _clean(sessions, schedule_id: int) -> None:
    async with sessions() as session, session.begin():
        await session.execute(delete(OperationLog).where(OperationLog.schedule_id == schedule_id))
        await session.execute(
            delete(NameGenerationJob).where(NameGenerationJob.schedule_id == schedule_id)
        )
        await session.execute(delete(NameGenerationBudgetBucket))
        await session.execute(delete(Schedule).where(Schedule.id == schedule_id))


async def test_worker_closes_claim_session_before_generator_and_finalizes_success(
    test_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id, job_id = await _seed(sessions, 8_100)
    generator = LockCheckingFakeGenerator(sessions, schedule_id)
    worker = NameGenerationWorker(
        session_factory=sessions,
        generator=generator,
        clock=FixedClock(NOW),
        enabled=True,
        budget_policy=BudgetPolicy(50, 500, 100, "JPY"),
        timeout_seconds=5,
        processing_lease_seconds=30,
    )
    try:
        result = await worker.poll_once()
        assert (result.selected, result.generated, result.result_code) == (1, 1, "generated")
        assert tuple(generator.requests[0].__dataclass_fields__) == (
            "content",
            "max_length",
            "locale",
            "single_line",
            "prohibit_control_characters",
        )
        async with sessions() as session:
            schedule = await session.get(Schedule, schedule_id)
            job = await session.get(NameGenerationJob, job_id)
            operation = await session.scalar(
                select(OperationLog).where(OperationLog.schedule_id == schedule_id)
            )
            assert schedule is not None and (schedule.display_name, schedule.version) == (
                "隔離生成名",
                1,
            )
            assert job is not None and (job.status, job.result_code) == ("succeeded", "generated")
            assert operation is not None
            assert "隔離生成名" not in repr(operation.changes)
            assert "worker-private-canary" not in repr(operation.changes)
    finally:
        await worker.shutdown()
        await _clean(sessions, schedule_id)


async def test_worker_shutdown_cancels_generator_and_marks_unknown_without_refund(
    test_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id, job_id = await _seed(sessions, 8_200)
    generator = LockCheckingFakeGenerator(sessions, schedule_id, wait=True)
    worker = NameGenerationWorker(
        session_factory=sessions,
        generator=generator,
        clock=FixedClock(NOW),
        enabled=True,
        budget_policy=BudgetPolicy(50, 500, 100, "JPY"),
        timeout_seconds=5,
        processing_lease_seconds=30,
    )
    polling = asyncio.create_task(worker.poll_once())
    try:
        await generator.started.wait()
        await worker.shutdown()
        with contextlib.suppress(asyncio.CancelledError):
            await polling
        async with sessions() as session:
            job = await session.get(NameGenerationJob, job_id)
            assert job is not None
            assert (job.status, job.result_code, job.reserved_cost_microunits) == (
                "abandoned",
                "shutdown_unknown",
                10,
            )
        await worker.shutdown()
    finally:
        if not polling.done():
            polling.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await polling
        await _clean(sessions, schedule_id)
