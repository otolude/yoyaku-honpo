"""Persistence operations for provider-independent name generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.domain.enums import (
    BudgetPeriodType,
    NameGenerationJobStatus,
    NameGenerationResultCode,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    NameGenerationBudgetBucket,
    NameGenerationJob,
    Schedule,
)


@dataclass(frozen=True, slots=True)
class PendingNameGenerationJob:
    id: int
    schedule_id: int
    expected_schedule_version: int
    schedule_version: int
    content: str | None
    display_name: str | None
    display_name_source: str
    schedule_status: str


class NameGenerationJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_pending_idempotent(
        self, *, schedule_id: int, expected_schedule_version: int, created_at: datetime
    ) -> bool:
        statement = (
            pg_insert(NameGenerationJob)
            .values(
                schedule_id=schedule_id,
                expected_schedule_version=expected_schedule_version,
                status=NameGenerationJobStatus.PENDING.value,
                reserved_cost_microunits=0,
                created_at=created_at,
                updated_at=created_at,
            )
            .on_conflict_do_nothing(index_elements=["schedule_id", "expected_schedule_version"])
            .returning(NameGenerationJob.id)
        )
        return (await self._session.scalar(statement)) is not None

    async def result_code(self, *, job_id: int) -> NameGenerationResultCode | None:
        value = await self._session.scalar(
            select(NameGenerationJob.result_code).where(NameGenerationJob.id == job_id)
        )
        return NameGenerationResultCode(value) if value is not None else None

    async def lock_schedule_then_pending(self) -> PendingNameGenerationJob | None:
        candidate = (
            await self._session.execute(
                select(NameGenerationJob.id, NameGenerationJob.schedule_id)
                .where(NameGenerationJob.status == NameGenerationJobStatus.PENDING.value)
                .order_by(NameGenerationJob.created_at, NameGenerationJob.id)
                .limit(1)
            )
        ).one_or_none()
        if candidate is None:
            return None
        job_id, schedule_id = candidate
        schedule = (
            await self._session.execute(
                select(Schedule).where(Schedule.id == schedule_id).with_for_update()
            )
        ).scalar_one_or_none()
        if schedule is None:
            return None
        job = await self._session.scalar(
            select(NameGenerationJob)
            .where(
                NameGenerationJob.id == job_id,
                NameGenerationJob.status == NameGenerationJobStatus.PENDING.value,
            )
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        return PendingNameGenerationJob(
            job.id,
            job.schedule_id,
            job.expected_schedule_version,
            schedule.version,
            schedule.content,
            schedule.display_name,
            schedule.display_name_source,
            schedule.status,
        )

    async def mark_pending_terminal(
        self,
        *,
        job_id: int,
        status: NameGenerationJobStatus,
        result_code: NameGenerationResultCode,
        finished_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(NameGenerationJob)
            .where(
                NameGenerationJob.id == job_id,
                NameGenerationJob.status == NameGenerationJobStatus.PENDING.value,
            )
            .values(
                status=status.value,
                result_code=result_code.value,
                finished_at=finished_at,
                updated_at=finished_at,
            )
        )
        return bool(result.rowcount)

    async def mark_processing(
        self,
        *,
        job_id: int,
        reserved_cost_microunits: int,
        claimed_at: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(NameGenerationJob)
            .where(
                NameGenerationJob.id == job_id,
                NameGenerationJob.status == NameGenerationJobStatus.PENDING.value,
            )
            .values(
                status=NameGenerationJobStatus.PROCESSING.value,
                reserved_cost_microunits=reserved_cost_microunits,
                claimed_at=claimed_at,
                started_at=claimed_at,
                lease_expires_at=lease_expires_at,
                updated_at=claimed_at,
            )
        )
        return bool(result.rowcount)

    async def abandon_expired_processing(self, *, now: datetime) -> int:
        result = await self._session.execute(
            update(NameGenerationJob)
            .where(
                NameGenerationJob.status == NameGenerationJobStatus.PROCESSING.value,
                NameGenerationJob.lease_expires_at <= now,
            )
            .values(
                status=NameGenerationJobStatus.ABANDONED.value,
                result_code=NameGenerationResultCode.STARTUP_ABANDONED.value,
                finished_at=now,
                updated_at=now,
            )
        )
        return int(result.rowcount or 0)

    async def delete_terminal_due(self, *, cutoff: datetime) -> int:
        result = await self._session.execute(
            delete(NameGenerationJob).where(
                NameGenerationJob.status.in_(
                    tuple(
                        status.value
                        for status in (
                            NameGenerationJobStatus.SUCCEEDED,
                            NameGenerationJobStatus.FAILED,
                            NameGenerationJobStatus.SKIPPED,
                            NameGenerationJobStatus.ABANDONED,
                        )
                    )
                ),
                NameGenerationJob.finished_at <= cutoff,
            )
        )
        return int(result.rowcount or 0)


class NameGenerationBudgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_then_lock(
        self, *, period_type: BudgetPeriodType, period_start: date, now: datetime
    ) -> NameGenerationBudgetBucket:
        await self._session.execute(
            pg_insert(NameGenerationBudgetBucket)
            .values(
                period_type=period_type.value,
                period_start=period_start,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["period_type", "period_start"])
        )
        return (
            await self._session.execute(
                select(NameGenerationBudgetBucket)
                .where(
                    NameGenerationBudgetBucket.period_type == period_type.value,
                    NameGenerationBudgetBucket.period_start == period_start,
                )
                .with_for_update()
            )
        ).scalar_one()

    async def lock_daily_then_monthly(
        self, *, daily_start: date, monthly_start: date, now: datetime
    ) -> tuple[NameGenerationBudgetBucket, NameGenerationBudgetBucket]:
        daily = await self.upsert_then_lock(
            period_type=BudgetPeriodType.DAILY, period_start=daily_start, now=now
        )
        monthly = await self.upsert_then_lock(
            period_type=BudgetPeriodType.MONTHLY, period_start=monthly_start, now=now
        )
        return daily, monthly

    async def delete_due(self, *, daily_before: date, monthly_before: date) -> int:
        result = await self._session.execute(
            delete(NameGenerationBudgetBucket).where(
                (
                    (NameGenerationBudgetBucket.period_type == BudgetPeriodType.DAILY.value)
                    & (NameGenerationBudgetBucket.period_start < daily_before)
                )
                | (
                    (NameGenerationBudgetBucket.period_type == BudgetPeriodType.MONTHLY.value)
                    & (NameGenerationBudgetBucket.period_start < monthly_before)
                )
            )
        )
        return int(result.rowcount or 0)
