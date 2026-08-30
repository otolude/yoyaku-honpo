"""Application boundaries for provider-independent name generation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.domain.enums import (
    ActorType,
    BudgetPeriodType,
    DisplayNameSource,
    NameGenerationJobStatus,
    NameGenerationResultCode,
    OperationAction,
    ScheduleStatus,
)
from discord_ai_reminder_bot.domain.name_generation import (
    MAX_POSTGRES_BIGINT,
    BudgetPolicy,
    GeneratedScheduleName,
    NameGenerationRequest,
    budget_period_start,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    NameGenerationJob,
    OperationLog,
    Schedule,
)
from discord_ai_reminder_bot.infrastructure.database.name_generation_repositories import (
    NameGenerationBudgetRepository,
    NameGenerationJobRepository,
)


class NameGenerator(Protocol):
    @property
    def available(self) -> bool: ...

    async def generate(self, request: NameGenerationRequest) -> GeneratedScheduleName: ...


class DisabledNameGenerator:
    """Safe production default; it never performs I/O."""

    available = False

    async def generate(self, request: NameGenerationRequest) -> GeneratedScheduleName:
        del request
        raise RuntimeError("name generation is disabled")


@dataclass(frozen=True, slots=True)
class NameGenerationRegistrationPolicy:
    """The 2B gate; future entitlement belongs between enabled and budget."""

    enabled: bool = False
    generator_available: bool = False

    @property
    def permits_registration(self) -> bool:
        return self.enabled and self.generator_available


async def register_generation_job(
    *,
    session: AsyncSession,
    schedule_id: int,
    expected_schedule_version: int,
    created_at: datetime,
    policy: NameGenerationRegistrationPolicy,
    logger: logging.Logger | None = None,
) -> bool:
    """Insert idempotently under a savepoint without leaking schedule data."""
    if not policy.permits_registration:
        return False
    try:
        async with session.begin_nested():
            return await NameGenerationJobRepository(session).insert_pending_idempotent(
                schedule_id=schedule_id,
                expected_schedule_version=expected_schedule_version,
                created_at=created_at,
            )
    except asyncio.CancelledError:
        raise
    except SQLAlchemyError:
        if logger is not None:
            logger.warning("name_generation_job_registration_failed")
        return False


class NameGenerationResultService:
    """Persist a generated name with conservative Schedule-version CAS."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_success(
        self, *, job_id: int, generated: GeneratedScheduleName, finished_at: datetime
    ) -> bool:
        reference = (
            await self._session.execute(
                select(NameGenerationJob.schedule_id).where(NameGenerationJob.id == job_id)
            )
        ).scalar_one_or_none()
        if reference is None:
            return False
        schedule = (
            await self._session.execute(
                select(Schedule).where(Schedule.id == reference).with_for_update()
            )
        ).scalar_one_or_none()
        job = (
            await self._session.execute(
                select(NameGenerationJob).where(NameGenerationJob.id == job_id).with_for_update()
            )
        ).scalar_one_or_none()
        if (
            schedule is None
            or job is None
            or job.status != NameGenerationJobStatus.PROCESSING.value
        ):
            return False
        result_code: NameGenerationResultCode | None = None
        if schedule.version != job.expected_schedule_version:
            result_code = NameGenerationResultCode.STALE_AFTER_GENERATION
        elif schedule.display_name_source == DisplayNameSource.MANUAL.value:
            result_code = NameGenerationResultCode.MANUAL_NAME
        elif (
            schedule.display_name_source != DisplayNameSource.UNSET.value
            or schedule.content is None
            or schedule.status
            not in {
                ScheduleStatus.DRAFT.value,
                ScheduleStatus.ACTIVE.value,
                ScheduleStatus.PAUSED.value,
            }
        ):
            result_code = NameGenerationResultCode.INELIGIBLE_SCHEDULE
        if result_code is not None:
            job.status = NameGenerationJobStatus.SKIPPED.value
            job.result_code = result_code.value
            job.finished_at = finished_at
            job.updated_at = finished_at
            await self._session.flush()
            return False

        schedule.display_name = generated.value
        schedule.display_name_source = DisplayNameSource.AI.value
        job.status = NameGenerationJobStatus.SUCCEEDED.value
        job.result_code = NameGenerationResultCode.GENERATED.value
        job.finished_at = finished_at
        job.updated_at = finished_at
        self._session.add(
            OperationLog(
                schedule_id=schedule.id,
                action=OperationAction.NAME_GENERATED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_user_id=None,
                delete_kind=None,
                delete_reason=None,
                changes={
                    "display_name_changed": True,
                    "display_name_source": {
                        "from": DisplayNameSource.UNSET.value,
                        "to": DisplayNameSource.AI.value,
                    },
                },
                created_at=finished_at,
            )
        )
        await self._session.flush()
        return True


class OperatorBudgetService:
    """Atomically reserve pessimistic request cost in daily→monthly lock order."""

    def __init__(self, session: AsyncSession, policy: BudgetPolicy) -> None:
        self._repository = NameGenerationBudgetRepository(session)
        self._policy = policy

    async def reserve(self, *, maximum_cost_microunits: int | None, now: datetime) -> bool:
        if (
            maximum_cost_microunits is None
            or isinstance(maximum_cost_microunits, bool)
            or not isinstance(maximum_cost_microunits, int)
            or not 1 <= maximum_cost_microunits <= MAX_POSTGRES_BIGINT
        ):
            return False
        daily, monthly = await self._repository.lock_daily_then_monthly(
            daily_start=budget_period_start(BudgetPeriodType.DAILY, now),
            monthly_start=budget_period_start(BudgetPeriodType.MONTHLY, now),
            now=now,
        )
        if (
            daily.reserved_request_count + 1 > self._policy.daily_request_limit
            or monthly.reserved_request_count + 1 > self._policy.monthly_request_limit
            or monthly.reserved_cost_microunits + maximum_cost_microunits
            > self._policy.monthly_cost_limit_microunits
            or daily.reserved_cost_microunits > MAX_POSTGRES_BIGINT - maximum_cost_microunits
            or monthly.reserved_cost_microunits > MAX_POSTGRES_BIGINT - maximum_cost_microunits
        ):
            return False
        for bucket in (daily, monthly):
            bucket.reserved_request_count += 1
            bucket.reserved_cost_microunits += maximum_cost_microunits
            bucket.version += 1
            bucket.updated_at = now
        return True
