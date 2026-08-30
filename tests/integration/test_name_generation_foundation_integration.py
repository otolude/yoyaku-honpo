import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application.name_generation import (
    NameGenerationRegistrationPolicy,
    NameGenerationResultService,
    OperatorBudgetService,
    register_generation_job,
)
from discord_ai_reminder_bot.application.name_generation_maintenance import (
    NameGenerationRecoveryService,
)
from discord_ai_reminder_bot.application.schedule_creation import OnceScheduleCreationService
from discord_ai_reminder_bot.application.schedule_editing import EditValues, ScheduleEditingService
from discord_ai_reminder_bot.domain.name_generation import BudgetPolicy, GeneratedScheduleName
from discord_ai_reminder_bot.infrastructure.database.models import (
    NameGenerationBudgetBucket,
    NameGenerationJob,
    OperationLog,
    Schedule,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 30, 3, 0, tzinfo=UTC)


async def test_creation_registers_only_enabled_available_content_and_is_idempotent(
    db_session: AsyncSession,
) -> None:
    enabled = NameGenerationRegistrationPolicy(enabled=True, generator_available=True)
    await OnceScheduleCreationService(db_session, name_generation_policy=enabled).create(
        guild_id=71,
        channel_id=72,
        creator_user_id=73,
        scheduled_for=NOW + timedelta(hours=1),
        content="canary-content",
        allow_duplicate=True,
        now=NOW,
    )
    schedule = (
        await db_session.execute(select(Schedule).where(Schedule.guild_id == 71))
    ).scalar_one()
    job = (await db_session.execute(select(NameGenerationJob))).scalar_one()
    assert job.schedule_id == schedule.id
    assert job.expected_schedule_version == 1
    assert job.status == "pending"
    assert "canary-content" not in repr(job.__dict__)
    assert not hasattr(job, "content")
    assert not await register_generation_job(
        session=db_session,
        schedule_id=schedule.id,
        expected_schedule_version=1,
        created_at=NOW,
        policy=enabled,
    )
    assert await db_session.scalar(select(func.count()).select_from(NameGenerationJob)) == 1

    await OnceScheduleCreationService(db_session).create(
        guild_id=81,
        channel_id=82,
        creator_user_id=83,
        scheduled_for=NOW + timedelta(hours=2),
        content="disabled content",
        allow_duplicate=True,
        now=NOW,
    )
    await OnceScheduleCreationService(db_session, name_generation_policy=enabled).create(
        guild_id=91,
        channel_id=92,
        creator_user_id=93,
        scheduled_for=NOW + timedelta(hours=3),
        content=None,
        allow_duplicate=True,
        now=NOW,
    )
    assert await db_session.scalar(select(func.count()).select_from(NameGenerationJob)) == 1


async def test_recovery_abandons_expired_without_refunding_budget(db_session: AsyncSession) -> None:
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=101,
        channel_id=102,
        creator_user_id=103,
        schedule_type="once",
        status="active",
        content="x",
        display_name=None,
        display_name_source="unset",
        next_run_at=NOW + timedelta(hours=1),
        version=1,
    )
    db_session.add(schedule)
    await db_session.flush()
    db_session.add(
        NameGenerationJob(
            schedule_id=schedule.id,
            expected_schedule_version=1,
            status="processing",
            reserved_cost_microunits=123,
            claimed_at=NOW - timedelta(minutes=2),
            started_at=NOW - timedelta(minutes=2),
            lease_expires_at=NOW - timedelta(seconds=1),
            created_at=NOW - timedelta(minutes=3),
            updated_at=NOW - timedelta(minutes=2),
        )
    )
    await db_session.flush()
    assert await NameGenerationRecoveryService(db_session).abandon_expired(now=NOW) == 1
    job = (await db_session.execute(select(NameGenerationJob))).scalar_one()
    assert (job.status, job.result_code, job.reserved_cost_microunits) == (
        "abandoned",
        "startup_abandoned",
        123,
    )


async def test_ai_result_cas_preserves_user_version_updated_at_and_safe_audit(
    db_session: AsyncSession,
) -> None:
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=111,
        channel_id=112,
        creator_user_id=113,
        schedule_type="once",
        status="active",
        content="private-content-canary",
        display_name=None,
        display_name_source="unset",
        next_run_at=NOW + timedelta(hours=1),
        version=7,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(days=1),
    )
    db_session.add(schedule)
    await db_session.flush()
    job = NameGenerationJob(
        schedule_id=schedule.id,
        expected_schedule_version=7,
        status="processing",
        reserved_cost_microunits=50,
        claimed_at=NOW - timedelta(seconds=2),
        started_at=NOW - timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=29),
        created_at=NOW - timedelta(seconds=3),
        updated_at=NOW - timedelta(seconds=1),
    )
    db_session.add(job)
    await db_session.flush()
    previous_updated_at = schedule.updated_at
    assert await NameGenerationResultService(db_session).save_success(
        job_id=job.id, generated=GeneratedScheduleName("private-name-canary"), finished_at=NOW
    )
    await db_session.refresh(schedule)
    operation = (
        await db_session.execute(
            select(OperationLog).where(OperationLog.schedule_id == schedule.id)
        )
    ).scalar_one()
    assert (schedule.display_name, schedule.display_name_source, schedule.version) == (
        "private-name-canary",
        "ai",
        7,
    )
    assert schedule.updated_at == previous_updated_at
    assert operation.actor_type == "system" and operation.actor_user_id is None
    assert "private-name-canary" not in repr(operation.changes)
    assert "private-content-canary" not in repr(operation.changes)


async def test_content_edit_registers_new_version_but_clear_and_non_content_do_not(
    db_session: AsyncSession,
) -> None:
    enabled = NameGenerationRegistrationPolicy(enabled=True, generator_available=True)
    await OnceScheduleCreationService(db_session, name_generation_policy=enabled).create(
        guild_id=131,
        channel_id=132,
        creator_user_id=133,
        scheduled_for=NOW + timedelta(hours=2),
        content="before",
        allow_duplicate=True,
        now=NOW,
    )
    schedule = (
        await db_session.execute(select(Schedule).where(Schedule.guild_id == 131))
    ).scalar_one()
    service = ScheduleEditingService(db_session, name_generation_policy=enabled)
    await service.edit(
        guild_id=131,
        public_id=str(schedule.public_id),
        actor_user_id=133,
        administrator=False,
        values=EditValues(content="after"),
        edited_at=NOW + timedelta(minutes=1),
        expected_version=1,
    )
    versions = list(
        (
            await db_session.execute(
                select(NameGenerationJob.expected_schedule_version)
                .where(NameGenerationJob.schedule_id == schedule.id)
                .order_by(NameGenerationJob.expected_schedule_version)
            )
        ).scalars()
    )
    assert versions == [1, 2]
    await service.edit(
        guild_id=131,
        public_id=str(schedule.public_id),
        actor_user_id=133,
        administrator=False,
        values=EditValues(channel_id=999),
        edited_at=NOW + timedelta(minutes=2),
        expected_version=2,
    )
    await service.edit(
        guild_id=131,
        public_id=str(schedule.public_id),
        actor_user_id=133,
        administrator=False,
        values=EditValues(clear_content=True),
        edited_at=NOW + timedelta(minutes=3),
        expected_version=3,
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(NameGenerationJob)
            .where(NameGenerationJob.schedule_id == schedule.id)
        )
        == 2
    )


async def test_ai_result_stale_cas_preserves_manual_name(db_session: AsyncSession) -> None:
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=121,
        channel_id=122,
        creator_user_id=123,
        schedule_type="once",
        status="active",
        content="new content",
        display_name="manual name",
        display_name_source="manual",
        next_run_at=NOW + timedelta(hours=1),
        version=8,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(schedule)
    await db_session.flush()
    job = NameGenerationJob(
        schedule_id=schedule.id,
        expected_schedule_version=7,
        status="processing",
        reserved_cost_microunits=50,
        claimed_at=NOW - timedelta(seconds=2),
        started_at=NOW - timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=29),
        created_at=NOW - timedelta(seconds=3),
        updated_at=NOW - timedelta(seconds=1),
    )
    db_session.add(job)
    await db_session.flush()
    assert not await NameGenerationResultService(db_session).save_success(
        job_id=job.id, generated=GeneratedScheduleName("stale name"), finished_at=NOW
    )
    assert (schedule.display_name, schedule.display_name_source, schedule.version) == (
        "manual name",
        "manual",
        8,
    )
    assert (job.status, job.result_code) == ("skipped", "stale_after_generation")
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(OperationLog)
            .where(OperationLog.schedule_id == schedule.id)
        )
        == 0
    )


async def test_ai_result_rechecks_terminal_state_and_discards_result(
    db_session: AsyncSession,
) -> None:
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=125,
        channel_id=126,
        creator_user_id=127,
        schedule_type="once",
        status="completed",
        content="completed content",
        display_name=None,
        display_name_source="unset",
        next_run_at=None,
        terminal_at=NOW,
        version=4,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(schedule)
    await db_session.flush()
    job = NameGenerationJob(
        schedule_id=schedule.id,
        expected_schedule_version=4,
        status="processing",
        reserved_cost_microunits=50,
        claimed_at=NOW - timedelta(seconds=2),
        started_at=NOW - timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=29),
        created_at=NOW - timedelta(seconds=3),
        updated_at=NOW - timedelta(seconds=1),
    )
    db_session.add(job)
    await db_session.flush()
    assert not await NameGenerationResultService(db_session).save_success(
        job_id=job.id,
        generated=GeneratedScheduleName("discarded name"),
        finished_at=NOW,
    )
    assert (schedule.display_name, schedule.display_name_source, schedule.version) == (
        None,
        "unset",
        4,
    )
    assert (job.status, job.result_code) == ("skipped", "ineligible_schedule")


async def test_database_constraints_do_not_embed_temporary_budget_limits(
    db_session: AsyncSession,
) -> None:
    bucket = NameGenerationBudgetBucket(
        period_type="daily",
        period_start=date(2026, 8, 30),
        reserved_request_count=50_000,
        reserved_cost_microunits=900_000_000,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(bucket)
    await db_session.flush()
    assert bucket.reserved_request_count == 50_000
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                NameGenerationBudgetBucket(
                    period_type="monthly",
                    period_start=date(2026, 8, 2),
                    reserved_request_count=0,
                    reserved_cost_microunits=0,
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await db_session.flush()


async def test_operator_budget_reserves_daily_then_monthly_and_fails_closed(
    db_session: AsyncSession,
) -> None:
    service = OperatorBudgetService(db_session, BudgetPolicy(2, 3, 100, "JPY"))
    assert not await service.reserve(maximum_cost_microunits=None, now=NOW)
    assert await service.reserve(maximum_cost_microunits=40, now=NOW)
    assert await service.reserve(maximum_cost_microunits=40, now=NOW)
    assert not await service.reserve(maximum_cost_microunits=1, now=NOW)
    buckets = list(
        (
            await db_session.execute(
                select(NameGenerationBudgetBucket).order_by(NameGenerationBudgetBucket.period_type)
            )
        ).scalars()
    )
    assert [(item.reserved_request_count, item.reserved_cost_microunits) for item in buckets] == [
        (2, 80),
        (2, 80),
    ]
