import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.domain.enums import ScheduleStatus
from discord_ai_reminder_bot.infrastructure.database.exceptions import (
    DuplicateRecordError,
    OptimisticLockError,
    RepositoryNotFoundError,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    OperationLog,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    OperationLogRepository,
    ScheduleRepository,
    ScheduleRunRepository,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)


def make_schedule(
    *,
    guild_id: int = 100,
    creator_user_id: int = 200,
    status: ScheduleStatus = ScheduleStatus.ACTIVE,
    next_run_at: datetime | None = NOW + timedelta(hours=1),
) -> Schedule:
    return Schedule(
        public_id=uuid.uuid7(),
        guild_id=guild_id,
        channel_id=300,
        creator_user_id=creator_user_id,
        schedule_type="once",
        status=status.value,
        content="test content" if status is not ScheduleStatus.DRAFT else None,
        next_run_at=next_run_at,
        version=1,
    )


async def test_schedule_create_get_boundaries_and_no_auto_commit(
    db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    repository = ScheduleRepository(db_session)
    schedule = await repository.add(make_schedule())

    assert schedule.id is not None
    assert await repository.get_by_id(schedule.id) is schedule
    assert await repository.get_by_public_id(guild_id=100, public_id=schedule.public_id) is schedule
    with pytest.raises(RepositoryNotFoundError):
        await repository.get_by_public_id(guild_id=999, public_id=schedule.public_id)

    other_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with other_factory() as other_session:
        count = await other_session.scalar(
            select(func.count()).select_from(Schedule).where(Schedule.id == schedule.id)
        )
    assert count == 0


async def test_schedule_lists_are_scoped_filtered_and_stable(db_session: AsyncSession) -> None:
    repository = ScheduleRepository(db_session)
    later = await repository.add(make_schedule())
    earlier = await repository.add(make_schedule(next_run_at=NOW + timedelta(minutes=30)))
    await repository.add(make_schedule(guild_id=999))
    await repository.add(
        make_schedule(status=ScheduleStatus.DRAFT, next_run_at=NOW + timedelta(minutes=15))
    )

    active = await repository.list_by_guild(guild_id=100, status=ScheduleStatus.ACTIVE, limit=10)
    assert [item.id for item in active] == [earlier.id, later.id]
    creator = await repository.list_by_creator(
        guild_id=100,
        creator_user_id=200,
        status=ScheduleStatus.DRAFT,
        limit=10,
    )
    assert len(creator) == 1
    with pytest.raises(ValueError):
        await repository.list_by_guild(guild_id=100, limit=101)


async def test_optimistic_update_success_conflict_and_not_found(db_session: AsyncSession) -> None:
    repository = ScheduleRepository(db_session)
    schedule = await repository.add(make_schedule())

    updated = await repository.update_with_version(
        guild_id=100,
        schedule_id=schedule.id,
        expected_version=1,
        changes={"content": "updated"},
    )
    assert updated.content == "updated"
    assert updated.version == 2

    with pytest.raises(OptimisticLockError):
        await repository.update_with_version(
            guild_id=100,
            schedule_id=schedule.id,
            expected_version=1,
            changes={"content": "stale"},
        )
    with pytest.raises(RepositoryNotFoundError):
        await repository.update_with_version(
            guild_id=999,
            schedule_id=schedule.id,
            expected_version=2,
            changes={"content": "other guild"},
        )


async def test_schedule_run_duplicate_and_stable_list(db_session: AsyncSession) -> None:
    schedule = await ScheduleRepository(db_session).add(make_schedule())
    repository = ScheduleRunRepository(db_session)
    first = await repository.add(
        ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=NOW,
            status="pending",
            attempt_count=0,
            next_attempt_at=NOW,
        )
    )
    second = await repository.add(
        ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=NOW + timedelta(days=1),
            status="pending",
            attempt_count=0,
            next_attempt_at=NOW + timedelta(days=1),
        )
    )
    assert [item.id for item in await repository.list_by_schedule(schedule_id=schedule.id)] == [
        second.id,
        first.id,
    ]

    with pytest.raises(DuplicateRecordError):
        async with db_session.begin_nested():
            await repository.add(
                ScheduleRun(
                    schedule_id=schedule.id,
                    scheduled_for=NOW,
                    status="pending",
                    attempt_count=0,
                    next_attempt_at=NOW,
                )
            )


async def test_operation_log_save_and_stable_list(db_session: AsyncSession) -> None:
    schedule = await ScheduleRepository(db_session).add(make_schedule())
    repository = OperationLogRepository(db_session)
    first = await repository.add(
        OperationLog(
            schedule_id=schedule.id,
            action="created",
            actor_type="user",
            actor_user_id=200,
            created_at=NOW,
        )
    )
    second = await repository.add(
        OperationLog(
            schedule_id=schedule.id,
            action="edited",
            actor_type="user",
            actor_user_id=200,
            created_at=NOW + timedelta(minutes=1),
        )
    )
    assert [item.id for item in await repository.list_by_schedule(schedule_id=schedule.id)] == [
        second.id,
        first.id,
    ]
