import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.schedule_queries import (
    MAX_PAGE_NUMBER,
    ScheduleQueryService,
)
from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.infrastructure.database.models import Schedule

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
GUILD_ID = 8_100
OTHER_GUILD_ID = 8_101
CREATOR_ID = 8_200
OTHER_CREATOR_ID = 8_201


def make_schedule(
    *,
    creator_user_id: int = CREATOR_ID,
    guild_id: int = GUILD_ID,
    status: ScheduleStatus = ScheduleStatus.ACTIVE,
    schedule_type: ScheduleType = ScheduleType.ONCE,
    next_run_at: datetime | None = NOW,
) -> Schedule:
    terminal = status is ScheduleStatus.DELETED
    return Schedule(
        public_id=uuid.uuid7(),
        guild_id=guild_id,
        channel_id=8_300,
        creator_user_id=creator_user_id,
        schedule_type=schedule_type.value,
        status=status.value,
        content="safe integration content",
        next_run_at=None if terminal else next_run_at,
        local_time=time(12, 0) if schedule_type is not ScheduleType.ONCE else None,
        weekday=0 if schedule_type is ScheduleType.WEEKLY else None,
        version=1,
        deleted_at=NOW if terminal else None,
        terminal_at=NOW if terminal else None,
    )


def service_for(db_session: AsyncSession) -> ScheduleQueryService:
    factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    return ScheduleQueryService(factory)


async def test_real_postgres_list_boundaries_order_and_pagination(
    db_session: AsyncSession,
) -> None:
    same_time = NOW + timedelta(minutes=1)
    owned = [
        make_schedule(next_run_at=same_time if index < 2 else NOW + timedelta(minutes=index))
        for index in range(12)
    ]
    other_creator = make_schedule(
        creator_user_id=OTHER_CREATOR_ID,
        next_run_at=NOW - timedelta(minutes=1),
    )
    other_guild = make_schedule(guild_id=OTHER_GUILD_ID, next_run_at=NOW - timedelta(minutes=2))
    deleted = make_schedule(status=ScheduleStatus.DELETED)
    db_session.add_all([*owned, other_creator, other_guild, deleted])
    await db_session.flush()
    expected = sorted(owned, key=lambda item: (item.next_run_at, item.id))
    service = service_for(db_session)

    first = await service.list_schedules(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=False,
        status=None,
        page=1,
    )
    second = await service.list_schedules(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=False,
        status=None,
        page=2,
    )
    administrator = await service.list_schedules(
        guild_id=GUILD_ID,
        requester_user_id=OTHER_CREATOR_ID,
        administrator=True,
        status=None,
        page=1,
    )
    deleted_only = await service.list_schedules(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=False,
        status=ScheduleStatus.DELETED,
        page=1,
    )
    creator_page = await service.get_schedule_page(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=False,
        status=None,
        page=1,
    )
    admin_page = await service.get_schedule_page(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=True,
        status=None,
        page=1,
    )

    assert [item.public_id for item in first] == [item.public_id for item in expected[:10]]
    assert [item.public_id for item in second] == [item.public_id for item in expected[10:]]
    assert administrator[0].public_id == other_creator.public_id
    assert all(item.public_id != other_guild.public_id for item in administrator)
    assert all(item.status is not ScheduleStatus.DELETED for item in administrator)
    assert [item.public_id for item in deleted_only] == [deleted.public_id]
    assert (creator_page.total_count, creator_page.total_pages) == (12, 2)
    assert admin_page.total_count == 13
    assert all(item.public_id != other_guild.public_id for item in admin_page.schedules)


async def test_real_postgres_empty_and_maximum_page_are_safe(db_session: AsyncSession) -> None:
    db_session.add(make_schedule())
    await db_session.flush()
    service = service_for(db_session)

    for page in (2, MAX_PAGE_NUMBER):
        assert (
            await service.list_schedules(
                guild_id=GUILD_ID,
                requester_user_id=CREATOR_ID,
                administrator=False,
                status=None,
                page=page,
            )
            == []
        )


@pytest.mark.parametrize(
    "schedule_type", [ScheduleType.ONCE, ScheduleType.DAILY, ScheduleType.WEEKLY]
)
async def test_real_postgres_schedule_type_filter_count_and_boundaries(
    db_session: AsyncSession, schedule_type: ScheduleType
) -> None:
    matching = make_schedule(schedule_type=schedule_type)
    other_type = ScheduleType.DAILY if schedule_type is ScheduleType.ONCE else ScheduleType.ONCE
    db_session.add_all(
        [
            matching,
            make_schedule(schedule_type=other_type),
            make_schedule(schedule_type=schedule_type, creator_user_id=OTHER_CREATOR_ID),
            make_schedule(schedule_type=schedule_type, guild_id=OTHER_GUILD_ID),
        ]
    )
    await db_session.flush()
    page = await service_for(db_session).get_schedule_page(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=False,
        status=ScheduleStatus.ACTIVE,
        schedule_type=schedule_type,
        page=1,
    )
    assert page.total_count == 1
    assert [item.public_id for item in page.schedules] == [matching.public_id]


async def test_real_postgres_show_deleted_owner_admin_and_guild_boundary(
    db_session: AsyncSession,
) -> None:
    deleted = make_schedule(status=ScheduleStatus.DELETED)
    other_owner = make_schedule(creator_user_id=OTHER_CREATOR_ID)
    other_guild = make_schedule(guild_id=OTHER_GUILD_ID)
    db_session.add_all([deleted, other_owner, other_guild])
    await db_session.flush()
    service = service_for(db_session)

    owned_deleted = await service.show_schedule(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=False,
        public_id=str(deleted.public_id),
    )
    denied = await service.show_schedule(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=False,
        public_id=str(other_owner.public_id),
    )
    admin = await service.show_schedule(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=True,
        public_id=str(other_owner.public_id),
    )
    wrong_guild = await service.show_schedule(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=True,
        public_id=str(other_guild.public_id),
    )
    missing = await service.show_schedule(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=True,
        public_id=str(uuid.uuid7()),
    )

    assert owned_deleted is not None and owned_deleted.status is ScheduleStatus.DELETED
    assert denied is None
    assert admin is not None and admin.public_id == other_owner.public_id
    assert wrong_guild is None
    assert missing is None


async def test_real_postgres_queries_do_not_modify_rows(db_session: AsyncSession) -> None:
    stored = make_schedule()
    db_session.add(stored)
    await db_session.flush()
    before = (
        await db_session.execute(select(Schedule).where(Schedule.public_id == stored.public_id))
    ).scalar_one()
    snapshot = (
        before.status,
        before.content,
        before.next_run_at,
        before.version,
        before.updated_at,
    )
    public_id = stored.public_id
    service = service_for(db_session)

    await service.list_schedules(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=False,
        status=None,
        page=1,
    )
    await service.show_schedule(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=False,
        public_id=str(stored.public_id),
    )
    db_session.expire_all()
    after = (
        await db_session.execute(select(Schedule).where(Schedule.public_id == public_id))
    ).scalar_one()

    assert (
        after.status,
        after.content,
        after.next_run_at,
        after.version,
        after.updated_at,
    ) == snapshot
