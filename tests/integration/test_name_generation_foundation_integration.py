import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application.name_generation import (
    NameGenerationClaimService,
    NameGenerationRegistrationPolicy,
    NameGenerationResultService,
    OperatorBudgetService,
    register_generation_job,
)
from discord_ai_reminder_bot.application.name_generation_maintenance import (
    NameGenerationCleanupService,
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


async def test_ai_result_does_not_overwrite_manual_name_at_same_version(
    db_session: AsyncSession,
) -> None:
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=1231,
        channel_id=1232,
        creator_user_id=1233,
        schedule_type="once",
        status="active",
        content="same version content",
        display_name="manual latest",
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
        expected_schedule_version=8,
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
        job_id=job.id, generated=GeneratedScheduleName("discard me"), finished_at=NOW
    )
    assert (schedule.display_name, schedule.display_name_source, schedule.version) == (
        "manual latest",
        "manual",
        8,
    )
    assert (job.status, job.result_code) == ("skipped", "manual_name")


async def test_startup_recovery_wins_over_delayed_finalize(db_session: AsyncSession) -> None:
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=1241,
        channel_id=1242,
        creator_user_id=1243,
        schedule_type="once",
        status="active",
        content="delayed content",
        display_name=None,
        display_name_source="unset",
        next_run_at=NOW + timedelta(hours=1),
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(schedule)
    await db_session.flush()
    job = NameGenerationJob(
        schedule_id=schedule.id,
        expected_schedule_version=1,
        status="processing",
        reserved_cost_microunits=50,
        claimed_at=NOW - timedelta(minutes=2),
        started_at=NOW - timedelta(minutes=2),
        lease_expires_at=NOW - timedelta(seconds=1),
        created_at=NOW - timedelta(minutes=3),
        updated_at=NOW - timedelta(minutes=2),
    )
    db_session.add(job)
    await db_session.flush()
    assert await NameGenerationRecoveryService(db_session).abandon_expired(now=NOW) == 1
    assert not await NameGenerationResultService(db_session).save_success(
        job_id=job.id, generated=GeneratedScheduleName("late result"), finished_at=NOW
    )
    assert (schedule.display_name, schedule.display_name_source) == (None, "unset")
    assert (job.status, job.result_code) == ("abandoned", "startup_abandoned")


async def test_name_cleanup_preserves_pending_then_deletes_due_terminal(
    db_session: AsyncSession,
) -> None:
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=1251,
        channel_id=1252,
        creator_user_id=1253,
        schedule_type="once",
        status="active",
        content="cleanup content",
        display_name=None,
        display_name_source="unset",
        next_run_at=NOW + timedelta(hours=1),
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(schedule)
    await db_session.flush()
    job = NameGenerationJob(
        schedule_id=schedule.id,
        expected_schedule_version=1,
        status="pending",
        reserved_cost_microunits=0,
        created_at=NOW - timedelta(days=40),
        updated_at=NOW - timedelta(days=40),
    )
    db_session.add(job)
    await db_session.flush()
    cleanup = NameGenerationCleanupService(db_session)
    result = await cleanup.cleanup(now=NOW, job_retention_days=30, budget_retention_days=90)
    assert result.jobs_deleted == 0
    job.status = "failed"
    job.result_code = "generator_error"
    job.finished_at = NOW - timedelta(days=30)
    job.updated_at = NOW
    await db_session.flush()
    result = await cleanup.cleanup(now=NOW, job_retention_days=30, budget_retention_days=90)
    assert result.jobs_deleted == 1


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


async def test_claim_revalidates_and_reserves_before_processing(db_session: AsyncSession) -> None:
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=141,
        channel_id=142,
        creator_user_id=143,
        schedule_type="once",
        status="active",
        content="claim-private-canary",
        display_name=None,
        display_name_source="unset",
        next_run_at=NOW + timedelta(hours=1),
        version=3,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(schedule)
    await db_session.flush()
    job = NameGenerationJob(
        schedule_id=schedule.id,
        expected_schedule_version=3,
        status="pending",
        reserved_cost_microunits=0,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(job)
    await db_session.flush()
    claimed = await NameGenerationClaimService(
        db_session,
        enabled=True,
        generator_available=True,
        maximum_cost_microunits=25,
        budget_policy=BudgetPolicy(50, 500, 100, "JPY"),
        processing_lease_seconds=30,
    ).claim_and_reserve(now=NOW)
    assert claimed is not None and claimed.job_id == job.id
    assert claimed.request.content == "claim-private-canary"
    assert job.status == "processing" and job.reserved_cost_microunits == 25
    buckets = list((await db_session.execute(select(NameGenerationBudgetBucket))).scalars())
    assert len(buckets) == 2
    assert all(item.reserved_request_count == 1 for item in buckets)


async def test_budget_50_500_and_exact_cost_boundaries_are_configuration_driven(
    db_session: AsyncSession,
) -> None:
    db_session.add_all(
        [
            NameGenerationBudgetBucket(
                period_type="daily",
                period_start=date(2026, 8, 30),
                reserved_request_count=49,
                reserved_cost_microunits=90,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            ),
            NameGenerationBudgetBucket(
                period_type="monthly",
                period_start=date(2026, 8, 1),
                reserved_request_count=499,
                reserved_cost_microunits=90,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            ),
        ]
    )
    await db_session.flush()
    temporary = OperatorBudgetService(db_session, BudgetPolicy(50, 500, 100, "JPY"))
    assert await temporary.reserve(maximum_cost_microunits=10, now=NOW)
    assert not await temporary.reserve(maximum_cost_microunits=1, now=NOW)
    await db_session.execute(delete(NameGenerationBudgetBucket))
    db_session.add_all(
        [
            NameGenerationBudgetBucket(
                period_type="daily",
                period_start=date(2026, 8, 30),
                reserved_request_count=50,
                reserved_cost_microunits=100,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            ),
            NameGenerationBudgetBucket(
                period_type="monthly",
                period_start=date(2026, 8, 1),
                reserved_request_count=500,
                reserved_cost_microunits=100,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            ),
        ]
    )
    await db_session.flush()
    changed = OperatorBudgetService(db_session, BudgetPolicy(60, 600, 200, "JPY"))
    assert await changed.reserve(maximum_cost_microunits=1, now=NOW)


async def test_processing_partial_index_allows_only_one_and_rolls_back_second_budget(
    db_session: AsyncSession,
) -> None:
    for offset in (0, 10):
        schedule = Schedule(
            public_id=uuid.uuid7(),
            guild_id=161 + offset,
            channel_id=162 + offset,
            creator_user_id=163 + offset,
            schedule_type="once",
            status="active",
            content="x",
            display_name=None,
            display_name_source="unset",
            next_run_at=NOW + timedelta(hours=1),
            version=1,
            created_at=NOW + timedelta(microseconds=offset),
            updated_at=NOW,
        )
        db_session.add(schedule)
        await db_session.flush()
        db_session.add(
            NameGenerationJob(
                schedule_id=schedule.id,
                expected_schedule_version=1,
                status="pending",
                reserved_cost_microunits=0,
                created_at=NOW + timedelta(microseconds=offset),
                updated_at=NOW + timedelta(microseconds=offset),
            )
        )
    await db_session.flush()
    arguments = {
        "enabled": True,
        "generator_available": True,
        "maximum_cost_microunits": 10,
        "budget_policy": BudgetPolicy(50, 500, 100, "JPY"),
        "processing_lease_seconds": 30,
    }
    assert await NameGenerationClaimService(db_session, **arguments).claim_and_reserve(now=NOW)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await NameGenerationClaimService(db_session, **arguments).claim_and_reserve(now=NOW)
    jobs = list(
        (
            await db_session.execute(select(NameGenerationJob).order_by(NameGenerationJob.id))
        ).scalars()
    )
    assert [item.status for item in jobs] == ["processing", "pending"]
    buckets = list((await db_session.execute(select(NameGenerationBudgetBucket))).scalars())
    assert all(item.reserved_request_count == 1 for item in buckets)


@pytest.mark.parametrize(
    ("enabled", "available", "cost", "source", "content", "status", "code"),
    [
        (False, True, 1, "unset", "x", "active", "generation_disabled"),
        (True, False, 1, "unset", "x", "active", "generator_unavailable"),
        (True, True, None, "unset", "x", "active", "price_unknown"),
        (True, True, -1, "unset", "x", "active", "budget_invalid"),
        (True, True, 1, "manual", "x", "active", "manual_name"),
        (True, True, 1, "unset", None, "draft", "ineligible_schedule"),
        (True, True, 1, "unset", "x", "completed", "ineligible_schedule"),
    ],
)
async def test_claim_skips_without_generator_or_budget(
    db_session: AsyncSession, enabled, available, cost, source, content, status, code
) -> None:
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=151,
        channel_id=152,
        creator_user_id=153,
        schedule_type="once",
        status=status,
        content=content,
        display_name="manual" if source == "manual" else None,
        display_name_source=source,
        next_run_at=(NOW + timedelta(hours=1)) if status in {"active", "draft"} else None,
        terminal_at=NOW if status == "completed" else None,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(schedule)
    await db_session.flush()
    job = NameGenerationJob(
        schedule_id=schedule.id,
        expected_schedule_version=1,
        status="pending",
        reserved_cost_microunits=0,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(job)
    await db_session.flush()
    claimed = await NameGenerationClaimService(
        db_session,
        enabled=enabled,
        generator_available=available,
        maximum_cost_microunits=cost,
        budget_policy=BudgetPolicy(50, 500, 100, "JPY"),
        processing_lease_seconds=30,
    ).claim_and_reserve(now=NOW)
    assert claimed is None
    assert (job.status, job.result_code) == ("skipped", code)
    assert (
        await db_session.scalar(select(func.count()).select_from(NameGenerationBudgetBucket)) == 0
    )
