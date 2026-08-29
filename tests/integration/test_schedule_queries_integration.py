import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.schedule_queries import (
    MAX_PAGE_NUMBER,
    ScheduleAutocompleteOperation,
    ScheduleQueryService,
)
from discord_ai_reminder_bot.domain.enums import (
    DeliveryAttemptStatus,
    RunStatus,
    ScheduleStatus,
    ScheduleType,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    Schedule,
    ScheduleRun,
)

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


def autocomplete_schedule(
    *,
    status: ScheduleStatus = ScheduleStatus.ACTIVE,
    schedule_type: ScheduleType = ScheduleType.DAILY,
    creator_user_id: int = CREATOR_ID,
    guild_id: int = GUILD_ID,
    channel_id: int = 8_300,
    next_run_at: datetime | None = NOW + timedelta(hours=2),
) -> Schedule:
    terminal = status in {
        ScheduleStatus.COMPLETED,
        ScheduleStatus.ENDED,
        ScheduleStatus.DELETED,
    }
    if status in {
        ScheduleStatus.PAUSED,
        ScheduleStatus.FAILED,
        ScheduleStatus.COMPLETED,
        ScheduleStatus.ENDED,
        ScheduleStatus.DELETED,
    }:
        next_run_at = None
    return Schedule(
        public_id=uuid.uuid7(),
        guild_id=guild_id,
        channel_id=channel_id,
        creator_user_id=creator_user_id,
        schedule_type=schedule_type.value,
        status=status.value,
        content=None if status is ScheduleStatus.DRAFT else "never returned body",
        next_run_at=next_run_at,
        local_time=time(12, 0) if schedule_type is not ScheduleType.ONCE else None,
        weekday=0 if schedule_type is ScheduleType.WEEKLY else None,
        version=1,
        deleted_at=NOW if status is ScheduleStatus.DELETED else None,
        terminal_at=NOW if terminal else None,
    )


async def add_current_run(
    session: AsyncSession,
    schedule: Schedule,
    *,
    status: RunStatus = RunStatus.PENDING,
    attempt_status: DeliveryAttemptStatus | None = None,
) -> ScheduleRun:
    await session.flush()
    scheduled_for = schedule.next_run_at or NOW + timedelta(hours=1)
    processing = status is RunStatus.PROCESSING
    terminal = status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.SKIPPED}
    worker_id = uuid.uuid4()
    run = ScheduleRun(
        schedule_id=schedule.id,
        scheduled_for=scheduled_for,
        status=status.value,
        attempt_count=1 if processing or attempt_status else 0,
        next_attempt_at=scheduled_for if status is RunStatus.PENDING else None,
        claimed_by=worker_id if processing else None,
        claimed_at=NOW if processing else None,
        lease_expires_at=NOW + timedelta(minutes=5) if processing else None,
        result_code="test_terminal" if terminal else None,
        discord_message_id=1 if status is RunStatus.SUCCEEDED else None,
        finished_at=NOW if terminal else None,
        started_at=NOW if processing or terminal else None,
    )
    session.add(run)
    await session.flush()
    if attempt_status is not None:
        session.add(
            DeliveryAttempt(
                schedule_run_id=run.id,
                attempt_number=1,
                status=attempt_status.value,
                claimed_by=worker_id,
                claimed_at=NOW,
                send_started_at=(NOW if attempt_status is DeliveryAttemptStatus.SENDING else None),
            )
        )
        await session.flush()
    return run


async def autocomplete(
    session: AsyncSession,
    operation: ScheduleAutocompleteOperation,
    *,
    administrator: bool = False,
    current: str = "",
    channel_ids: frozenset[int] = frozenset(),
):
    return await service_for(session).autocomplete_schedules(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=administrator,
        operation=operation,
        current=current,
        channel_ids=channel_ids,
        now=NOW,
    )


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


async def test_autocomplete_owner_admin_guild_deleted_limit_and_stable_order(
    db_session: AsyncSession,
) -> None:
    owned = [
        autocomplete_schedule(next_run_at=NOW + timedelta(minutes=index // 2 + 10))
        for index in range(27)
    ]
    other_owner = autocomplete_schedule(
        creator_user_id=OTHER_CREATOR_ID, next_run_at=NOW + timedelta(minutes=1)
    )
    other_guild = autocomplete_schedule(guild_id=OTHER_GUILD_ID)
    deleted = autocomplete_schedule(status=ScheduleStatus.DELETED, schedule_type=ScheduleType.ONCE)
    db_session.add_all([*owned, other_owner, other_guild, deleted])
    await db_session.flush()

    creator = await autocomplete(db_session, ScheduleAutocompleteOperation.SHOW)
    admin = await autocomplete(db_session, ScheduleAutocompleteOperation.SHOW, administrator=True)
    admin_deleted = await service_for(db_session).autocomplete_schedules(
        guild_id=GUILD_ID,
        requester_user_id=CREATOR_ID,
        administrator=True,
        operation=ScheduleAutocompleteOperation.SHOW,
        current="deleted",
        now=NOW,
    )
    expected = sorted(owned, key=lambda item: (item.next_run_at, item.id))[:25]

    assert [item.public_id for item in creator] == [item.public_id for item in expected]
    assert len(creator) == 25
    assert other_owner.public_id in {item.public_id for item in admin}
    assert other_guild.public_id not in {item.public_id for item in admin}
    assert [item.public_id for item in admin_deleted] == [deleted.public_id]
    assert all(item.creator_user_id == CREATOR_ID for item in creator)


async def test_autocomplete_operation_state_and_run_boundaries(db_session: AsyncSession) -> None:
    editable = autocomplete_schedule()
    missing_current = autocomplete_schedule()
    paused = autocomplete_schedule(status=ScheduleStatus.PAUSED)
    failed = autocomplete_schedule(status=ScheduleStatus.FAILED, schedule_type=ScheduleType.ONCE)
    pause_once = autocomplete_schedule(schedule_type=ScheduleType.ONCE)
    processing = autocomplete_schedule()
    claimed = autocomplete_schedule()
    terminal_current = autocomplete_schedule()
    db_session.add_all(
        [
            editable,
            missing_current,
            paused,
            failed,
            pause_once,
            processing,
            claimed,
            terminal_current,
        ]
    )
    await add_current_run(db_session, editable)
    await add_current_run(db_session, pause_once)
    await add_current_run(db_session, processing, status=RunStatus.PROCESSING)
    await add_current_run(db_session, claimed, attempt_status=DeliveryAttemptStatus.CLAIMED)
    await add_current_run(db_session, terminal_current, status=RunStatus.FAILED)

    edit_ids = {
        item.public_id
        for item in await autocomplete(db_session, ScheduleAutocompleteOperation.EDIT)
    }
    delete_ids = {
        item.public_id
        for item in await autocomplete(db_session, ScheduleAutocompleteOperation.DELETE)
    }
    pause_ids = {
        item.public_id
        for item in await autocomplete(db_session, ScheduleAutocompleteOperation.PAUSE)
    }

    assert editable.public_id in edit_ids
    assert paused.public_id in edit_ids
    assert missing_current.public_id not in edit_ids
    assert processing.public_id not in edit_ids
    assert claimed.public_id not in edit_ids
    assert terminal_current.public_id not in edit_ids
    assert failed.public_id in delete_ids
    assert editable.public_id in pause_ids
    assert pause_once.public_id not in pause_ids


async def test_autocomplete_resume_requires_supported_pristine_pending_run(
    db_session: AsyncSession,
) -> None:
    no_pending = autocomplete_schedule(status=ScheduleStatus.PAUSED)
    pristine = autocomplete_schedule(status=ScheduleStatus.PAUSED)
    multiple = autocomplete_schedule(status=ScheduleStatus.PAUSED)
    attempted = autocomplete_schedule(status=ScheduleStatus.PAUSED)
    db_session.add_all([no_pending, pristine, multiple, attempted])
    await add_current_run(db_session, pristine)
    await add_current_run(db_session, multiple)
    await db_session.flush()
    db_session.add(
        ScheduleRun(
            schedule_id=multiple.id,
            scheduled_for=NOW + timedelta(hours=3),
            status=RunStatus.PENDING.value,
            attempt_count=0,
            next_attempt_at=NOW + timedelta(hours=3),
        )
    )
    await add_current_run(db_session, attempted, attempt_status=DeliveryAttemptStatus.SENDING)
    await db_session.flush()

    ids = {
        item.public_id
        for item in await autocomplete(db_session, ScheduleAutocompleteOperation.RESUME)
    }
    assert ids == {no_pending.public_id, pristine.public_id}


async def test_autocomplete_fixed_searches_and_read_only_snapshot(db_session: AsyncSession) -> None:
    daily = autocomplete_schedule(channel_id=123456789012345678)
    weekly = autocomplete_schedule(schedule_type=ScheduleType.WEEKLY)
    db_session.add_all([daily, weekly])
    await add_current_run(db_session, daily)
    await add_current_run(db_session, weekly)
    daily_id = daily.id
    daily_public_id = daily.public_id
    weekly_public_id = weekly.public_id
    before = (daily.version, daily.updated_at, daily.content)
    await db_session.flush()

    by_prefix = await autocomplete(
        db_session, ScheduleAutocompleteOperation.SHOW, current=str(daily_public_id)[:30]
    )
    by_exact = await autocomplete(
        db_session, ScheduleAutocompleteOperation.SHOW, current=str(daily_public_id)
    )
    by_type = await autocomplete(db_session, ScheduleAutocompleteOperation.SHOW, current="毎週")
    by_status = await autocomplete(db_session, ScheduleAutocompleteOperation.SHOW, current="active")
    by_channel = await autocomplete(
        db_session, ScheduleAutocompleteOperation.SHOW, current="123456789012345678"
    )
    by_channel_names = await autocomplete(
        db_session,
        ScheduleAutocompleteOperation.SHOW,
        current="active",
        channel_ids=frozenset({daily.channel_id, weekly.channel_id}),
    )
    db_session.expire_all()
    reloaded = (
        await db_session.execute(select(Schedule).where(Schedule.id == daily_id))
    ).scalar_one()

    assert [item.public_id for item in by_prefix] == [daily_public_id]
    assert [item.public_id for item in by_exact] == [daily_public_id]
    assert [item.public_id for item in by_type] == [weekly_public_id]
    assert {item.public_id for item in by_status} == {daily_public_id, weekly_public_id}
    assert [item.public_id for item in by_channel] == [daily_public_id]
    assert {item.public_id for item in by_channel_names} == {daily_public_id, weekly_public_id}
    assert len(by_channel_names) == 2
    assert (reloaded.version, reloaded.updated_at, reloaded.content) == before
