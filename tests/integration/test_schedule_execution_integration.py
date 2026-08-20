import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.schedule_execution import (
    FinalizationResult,
    ScheduleExecutionService,
)
from discord_ai_reminder_bot.domain.exceptions import (
    InvalidDateTimeError,
    InvalidStateTransitionError,
)
from discord_ai_reminder_bot.infrastructure.database.exceptions import (
    RepositoryStateConflictError,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    NotificationLog,
    OperationLog,
    Schedule,
    ScheduleRun,
)

pytestmark = pytest.mark.asyncio
RUN_AT = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)
FINALIZED_AT = RUN_AT + timedelta(minutes=5)


async def add_terminal_run(
    session: AsyncSession,
    *,
    schedule_type: str = "once",
    schedule_status: str = "active",
    run_status: str = "succeeded",
    end_date: date | None = None,
) -> tuple[Schedule, ScheduleRun]:
    recurring = schedule_type in {"daily", "weekly"}
    terminal_schedule = schedule_status in {"completed", "ended", "deleted"}
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=900,
        channel_id=901,
        creator_user_id=902,
        schedule_type=schedule_type,
        status=schedule_status,
        content=None if schedule_status == "draft" else "finalize me",
        next_run_at=RUN_AT if schedule_status in {"active", "draft"} else None,
        local_time=time(12, 0) if recurring else None,
        weekday=0 if schedule_type == "weekly" else None,
        end_date=end_date,
        version=1,
        deleted_at=FINALIZED_AT if schedule_status == "deleted" else None,
        terminal_at=FINALIZED_AT if terminal_schedule else None,
    )
    session.add(schedule)
    await session.flush()
    run = ScheduleRun(
        schedule_id=schedule.id,
        scheduled_for=RUN_AT,
        status=run_status,
        attempt_count=1 if run_status != "skipped" else 0,
        next_attempt_at=(RUN_AT if run_status == "pending" else None),
        discord_message_id=903 if run_status == "succeeded" else None,
        result_code=run_status if run_status not in {"pending", "processing"} else None,
        started_at=RUN_AT if run_status != "skipped" else None,
        finished_at=FINALIZED_AT if run_status in {"succeeded", "failed", "skipped"} else None,
    )
    session.add(run)
    await session.flush()
    return schedule, run


@pytest.mark.parametrize(
    ("run_status", "schedule_status", "action"),
    [
        ("succeeded", "completed", "completed"),
        ("failed", "failed", "failed"),
        ("skipped", "failed", "failed"),
    ],
)
async def test_once_terminal_run_updates_schedule_and_is_idempotent(
    db_session: AsyncSession,
    run_status: str,
    schedule_status: str,
    action: str,
) -> None:
    schedule, run = await add_terminal_run(db_session, run_status=run_status)
    service = ScheduleExecutionService(db_session)
    first = await service.finalize_run(run_id=run.id, finalized_at=FINALIZED_AT)
    second = await service.finalize_run(run_id=run.id, finalized_at=FINALIZED_AT)

    assert first.result is FinalizationResult.APPLIED
    assert second.result is FinalizationResult.ALREADY_FINALIZED
    assert schedule.status == schedule_status
    assert schedule.next_run_at is None
    assert schedule.version == 2
    assert schedule.updated_at == FINALIZED_AT
    assert schedule.terminal_at == (FINALIZED_AT if schedule_status == "completed" else None)
    operations = list(
        (
            await db_session.execute(
                select(OperationLog).where(OperationLog.schedule_id == schedule.id)
            )
        ).scalars()
    )
    assert [operation.action for operation in operations] == [action]
    assert operations[0].actor_type == "system"
    assert operations[0].actor_user_id is None
    assert operations[0].delete_kind is None
    assert operations[0].delete_reason is None
    assert operations[0].created_at == FINALIZED_AT


async def test_contentless_once_draft_skipped_stays_draft(
    db_session: AsyncSession,
) -> None:
    schedule, run = await add_terminal_run(
        db_session, schedule_status="draft", run_status="skipped"
    )
    result = await ScheduleExecutionService(db_session).finalize_run(
        run_id=run.id, finalized_at=FINALIZED_AT
    )
    assert result.result is FinalizationResult.NO_ACTION
    assert schedule.status == "draft"
    assert schedule.version == 1


@pytest.mark.parametrize(
    ("schedule_type", "expected"),
    [
        ("daily", datetime(2026, 8, 18, 3, 0, tzinfo=UTC)),
        ("weekly", datetime(2026, 8, 24, 3, 0, tzinfo=UTC)),
    ],
)
async def test_recurring_creates_one_initialized_future_run(
    db_session: AsyncSession, schedule_type: str, expected: datetime
) -> None:
    schedule, run = await add_terminal_run(db_session, schedule_type=schedule_type)
    result = await ScheduleExecutionService(db_session).finalize_run(
        run_id=run.id, finalized_at=FINALIZED_AT
    )
    next_run = result.next_run
    assert result.result is FinalizationResult.APPLIED
    assert next_run is not None
    assert next_run.scheduled_for == expected
    assert next_run.scheduled_for > FINALIZED_AT
    assert next_run.status == "pending"
    assert next_run.attempt_count == 0
    assert next_run.next_attempt_at == expected
    assert next_run.claimed_by is None
    assert next_run.claimed_at is None
    assert next_run.lease_expires_at is None
    assert next_run.discord_message_id is None
    assert next_run.result_code is None
    assert next_run.error_summary is None
    assert next_run.started_at is None
    assert next_run.finished_at is None
    assert schedule.status == "active"
    assert schedule.next_run_at == expected
    assert schedule.version == 2


async def test_end_date_last_day_is_included(db_session: AsyncSession) -> None:
    schedule, run = await add_terminal_run(
        db_session, schedule_type="daily", end_date=date(2026, 8, 18)
    )
    result = await ScheduleExecutionService(db_session).finalize_run(
        run_id=run.id, finalized_at=FINALIZED_AT
    )
    assert result.next_run is not None
    assert result.next_run.scheduled_for == datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
    assert schedule.status == "active"


async def test_end_date_exhaustion_ends_schedule_and_logs(db_session: AsyncSession) -> None:
    schedule, run = await add_terminal_run(
        db_session, schedule_type="daily", end_date=date(2026, 8, 17)
    )
    result = await ScheduleExecutionService(db_session).finalize_run(
        run_id=run.id, finalized_at=FINALIZED_AT
    )
    assert result.next_run is None
    assert result.operation_log is not None
    assert result.operation_log.action == "ended"
    assert result.operation_log.actor_type == "system"
    assert schedule.status == "ended"
    assert schedule.next_run_at is None
    assert schedule.terminal_at == FINALIZED_AT
    assert schedule.version == 2


@pytest.mark.parametrize("run_status", ["failed", "skipped"])
async def test_recurring_failure_or_skip_still_creates_next_run(
    db_session: AsyncSession, run_status: str
) -> None:
    schedule, run = await add_terminal_run(db_session, schedule_type="daily", run_status=run_status)
    result = await ScheduleExecutionService(db_session).finalize_run(
        run_id=run.id, finalized_at=FINALIZED_AT
    )
    assert schedule.status == "active"
    assert result.next_run is not None


@pytest.mark.parametrize(
    ("schedule_type", "end_date", "expected"),
    [
        ("daily", None, datetime(2026, 8, 18, 3, 0, tzinfo=UTC)),
        ("daily", date(2026, 8, 18), datetime(2026, 8, 18, 3, 0, tzinfo=UTC)),
        ("weekly", None, datetime(2026, 8, 24, 3, 0, tzinfo=UTC)),
    ],
)
async def test_recurring_draft_skip_keeps_one_future_run(
    db_session: AsyncSession,
    schedule_type: str,
    end_date: date | None,
    expected: datetime,
) -> None:
    schedule, run = await add_terminal_run(
        db_session,
        schedule_type=schedule_type,
        schedule_status="draft",
        run_status="skipped",
        end_date=end_date,
    )
    service = ScheduleExecutionService(db_session)
    first = await service.finalize_run(run_id=run.id, finalized_at=FINALIZED_AT)
    second = await service.finalize_run(run_id=run.id, finalized_at=FINALIZED_AT)
    count = await db_session.scalar(
        select(func.count()).select_from(ScheduleRun).where(ScheduleRun.schedule_id == schedule.id)
    )
    assert first.result is FinalizationResult.APPLIED
    assert second.result is FinalizationResult.ALREADY_FINALIZED
    assert schedule.status == "draft"
    assert schedule.content is None
    assert schedule.next_run_at == expected
    assert schedule.version == 2
    assert first.next_run is second.next_run
    assert count == 2


async def test_recurring_draft_next_run_uses_notification_planning(
    db_session: AsyncSession,
) -> None:
    schedule, run = await add_terminal_run(
        db_session,
        schedule_type="daily",
        schedule_status="draft",
        run_status="skipped",
    )
    result = await ScheduleExecutionService(
        db_session, configured_guild_id=schedule.guild_id
    ).finalize_run(run_id=run.id, finalized_at=FINALIZED_AT)
    notifications = list(
        (
            await db_session.execute(
                select(NotificationLog).where(NotificationLog.schedule_id == schedule.id)
            )
        ).scalars()
    )
    assert result.next_run is not None
    assert len(notifications) == 1
    assert notifications[0].schedule_run_id == result.next_run.id
    assert notifications[0].notification_type == "draft_1h"


async def test_recurring_draft_without_next_run_is_left_unchanged(
    db_session: AsyncSession,
) -> None:
    schedule, run = await add_terminal_run(
        db_session,
        schedule_type="daily",
        schedule_status="draft",
        run_status="skipped",
        end_date=date(2026, 8, 17),
    )
    result = await ScheduleExecutionService(db_session).finalize_run(
        run_id=run.id, finalized_at=FINALIZED_AT
    )
    count = await db_session.scalar(
        select(func.count()).select_from(ScheduleRun).where(ScheduleRun.schedule_id == schedule.id)
    )
    assert result.result is FinalizationResult.NO_ACTION
    assert result.next_run is None
    assert schedule.status == "draft"
    assert schedule.next_run_at == RUN_AT
    assert schedule.version == 1
    assert count == 1


@pytest.mark.parametrize("run_status", ["succeeded", "failed"])
async def test_recurring_draft_rejects_non_skipped_terminal_run(
    db_session: AsyncSession, run_status: str
) -> None:
    _schedule, run = await add_terminal_run(
        db_session,
        schedule_type="daily",
        schedule_status="draft",
        run_status=run_status,
    )
    with pytest.raises(RepositoryStateConflictError):
        await ScheduleExecutionService(db_session).finalize_run(
            run_id=run.id, finalized_at=FINALIZED_AT
        )


@pytest.mark.parametrize("schedule_status", ["paused", "deleted", "ended"])
async def test_inactive_recurring_schedule_does_not_generate(
    db_session: AsyncSession, schedule_status: str
) -> None:
    schedule, run = await add_terminal_run(
        db_session,
        schedule_type="daily",
        schedule_status=schedule_status,
        run_status="skipped",
    )
    result = await ScheduleExecutionService(db_session).finalize_run(
        run_id=run.id, finalized_at=FINALIZED_AT
    )
    assert result.result is FinalizationResult.NO_ACTION
    assert result.next_run is None
    assert schedule.version == 1


async def test_pending_and_processing_runs_are_rejected(db_session: AsyncSession) -> None:
    for status in ("pending", "processing"):
        _schedule, run = await add_terminal_run(db_session, run_status=status)
        with pytest.raises(InvalidStateTransitionError):
            await ScheduleExecutionService(db_session).finalize_run(
                run_id=run.id, finalized_at=FINALIZED_AT
            )


async def test_finalization_rejects_naive_datetime(db_session: AsyncSession) -> None:
    _schedule, run = await add_terminal_run(db_session)
    with pytest.raises(InvalidDateTimeError):
        await ScheduleExecutionService(db_session).finalize_run(
            run_id=run.id,
            finalized_at=datetime(2026, 8, 17, 3, 5),  # noqa: DTZ001
        )


async def test_recurring_inconsistent_schedule_status_is_rejected(
    db_session: AsyncSession,
) -> None:
    schedule, run = await add_terminal_run(db_session, schedule_type="daily")
    schedule.status = "failed"
    with pytest.raises(RepositoryStateConflictError):
        await ScheduleExecutionService(db_session).finalize_run(
            run_id=run.id, finalized_at=FINALIZED_AT
        )


async def test_recurring_second_finalization_has_no_duplicate(
    db_session: AsyncSession,
) -> None:
    schedule, run = await add_terminal_run(db_session, schedule_type="daily")
    service = ScheduleExecutionService(db_session)
    first = await service.finalize_run(run_id=run.id, finalized_at=FINALIZED_AT)
    second = await service.finalize_run(run_id=run.id, finalized_at=FINALIZED_AT)
    count = await db_session.scalar(
        select(func.count()).select_from(ScheduleRun).where(ScheduleRun.schedule_id == schedule.id)
    )
    assert first.result is FinalizationResult.APPLIED
    assert second.result is FinalizationResult.ALREADY_FINALIZED
    assert first.next_run is second.next_run
    assert schedule.version == 2
    assert count == 2


async def test_existing_expected_next_run_is_reused(db_session: AsyncSession) -> None:
    schedule, run = await add_terminal_run(db_session, schedule_type="daily")
    expected = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
    existing = ScheduleRun(
        schedule_id=schedule.id,
        scheduled_for=expected,
        status="pending",
        attempt_count=0,
        next_attempt_at=expected,
    )
    db_session.add(existing)
    await db_session.flush()
    result = await ScheduleExecutionService(db_session).finalize_run(
        run_id=run.id, finalized_at=FINALIZED_AT
    )
    assert result.next_run is existing
    assert schedule.next_run_at == expected
    assert schedule.version == 2


async def test_conflicting_existing_next_run_is_rejected(
    db_session: AsyncSession,
) -> None:
    schedule, run = await add_terminal_run(db_session, schedule_type="daily")
    expected = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
    db_session.add(
        ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=expected,
            status="skipped",
            attempt_count=0,
            next_attempt_at=None,
            result_code="conflict",
            finished_at=FINALIZED_AT,
        )
    )
    await db_session.flush()
    with pytest.raises(RepositoryStateConflictError):
        await ScheduleExecutionService(db_session).finalize_run(
            run_id=run.id, finalized_at=FINALIZED_AT
        )


async def _seed_committed(
    engine: AsyncEngine,
    *,
    schedule_status: str = "active",
    run_status: str = "succeeded",
) -> tuple[int, int]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        schedule, run = await add_terminal_run(
            session,
            schedule_type="daily",
            schedule_status=schedule_status,
            run_status=run_status,
        )
        return schedule.id, run.id


async def _cleanup(engine: AsyncEngine, schedule_id: int) -> None:
    async with engine.begin() as connection:
        run_ids = select(ScheduleRun.id).where(ScheduleRun.schedule_id == schedule_id)
        await connection.execute(
            delete(OperationLog).where(OperationLog.schedule_id == schedule_id)
        )
        await connection.execute(delete(ScheduleRun).where(ScheduleRun.id.in_(run_ids)))
        await connection.execute(delete(Schedule).where(Schedule.id == schedule_id))


@pytest.mark.parametrize(
    ("schedule_status", "run_status"),
    [("active", "succeeded"), ("draft", "skipped")],
)
async def test_concurrent_finalization_creates_one_next_run(
    test_engine: AsyncEngine, schedule_status: str, run_status: str
) -> None:
    schedule_id, run_id = await _seed_committed(
        test_engine, schedule_status=schedule_status, run_status=run_status
    )
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    first_session, second_session = factory(), factory()
    first_tx, second_tx = await first_session.begin(), await second_session.begin()
    try:
        first = await ScheduleExecutionService(first_session).finalize_run(
            run_id=run_id, finalized_at=FINALIZED_AT
        )
        second_task = asyncio.create_task(
            ScheduleExecutionService(second_session).finalize_run(
                run_id=run_id, finalized_at=FINALIZED_AT
            )
        )
        await asyncio.sleep(0.05)
        assert not second_task.done()
        await first_tx.commit()
        second = await asyncio.wait_for(second_task, timeout=1)
        assert first.result is FinalizationResult.APPLIED
        assert second.result is FinalizationResult.ALREADY_FINALIZED
        await second_tx.commit()
    finally:
        if first_tx.is_active:
            await first_tx.rollback()
        if second_tx.is_active:
            await second_tx.rollback()
        await first_session.close()
        await second_session.close()

    async with factory() as verifier:
        count = await verifier.scalar(
            select(func.count())
            .select_from(ScheduleRun)
            .where(ScheduleRun.schedule_id == schedule_id)
        )
        version = await verifier.scalar(select(Schedule.version).where(Schedule.id == schedule_id))
        assert count == 2
        assert version == 2
    await _cleanup(test_engine, schedule_id)


async def test_rollback_and_uncommitted_changes_cover_all_rows(
    test_engine: AsyncEngine,
) -> None:
    schedule_id, run_id = await _seed_committed(test_engine)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory.begin() as seed_session:
        completed_schedule, completed_run = await add_terminal_run(
            seed_session, run_status="succeeded"
        )
        completed_schedule_id = completed_schedule.id
        completed_run_id = completed_run.id
    session = factory()
    transaction = await session.begin()
    try:
        await ScheduleExecutionService(session).finalize_run(
            run_id=run_id, finalized_at=FINALIZED_AT
        )
        await ScheduleExecutionService(session).finalize_run(
            run_id=completed_run_id, finalized_at=FINALIZED_AT
        )
        async with factory() as observer:
            schedule = await observer.get(Schedule, schedule_id)
            completed_schedule = await observer.get(Schedule, completed_schedule_id)
            run_count = await observer.scalar(
                select(func.count())
                .select_from(ScheduleRun)
                .where(ScheduleRun.schedule_id == schedule_id)
            )
            log_count = await observer.scalar(
                select(func.count())
                .select_from(OperationLog)
                .where(OperationLog.schedule_id.in_([schedule_id, completed_schedule_id]))
            )
            assert schedule is not None
            assert completed_schedule is not None
            assert schedule.status == "active"
            assert schedule.version == 1
            assert completed_schedule.status == "active"
            assert completed_schedule.version == 1
            assert run_count == 1
            assert log_count == 0
    finally:
        await transaction.rollback()
        await session.close()

    async with factory() as verifier:
        schedule = await verifier.get(Schedule, schedule_id)
        completed_schedule = await verifier.get(Schedule, completed_schedule_id)
        run_count = await verifier.scalar(
            select(func.count())
            .select_from(ScheduleRun)
            .where(ScheduleRun.schedule_id == schedule_id)
        )
        log_count = await verifier.scalar(
            select(func.count())
            .select_from(OperationLog)
            .where(OperationLog.schedule_id.in_([schedule_id, completed_schedule_id]))
        )
        assert schedule is not None
        assert completed_schedule is not None
        assert schedule.status == "active"
        assert schedule.version == 1
        assert completed_schedule.status == "active"
        assert completed_schedule.version == 1
        assert run_count == 1
        assert log_count == 0
    await _cleanup(test_engine, schedule_id)
    await _cleanup(test_engine, completed_schedule_id)
