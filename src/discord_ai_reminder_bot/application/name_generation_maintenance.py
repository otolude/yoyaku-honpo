"""Unwired 2B-1 recovery and retention services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.domain.name_generation import TOKYO
from discord_ai_reminder_bot.infrastructure.database.name_generation_repositories import (
    NameGenerationBudgetRepository,
    NameGenerationJobRepository,
)


@dataclass(frozen=True, slots=True)
class NameGenerationCleanupResult:
    jobs_deleted: int
    budget_buckets_deleted: int


class NameGenerationRecoveryService:
    def __init__(self, session: AsyncSession) -> None:
        self._jobs = NameGenerationJobRepository(session)

    async def abandon_expired(self, *, now: datetime) -> int:
        return await self._jobs.abandon_expired_processing(now=now)


class NameGenerationCleanupService:
    def __init__(self, session: AsyncSession) -> None:
        self._jobs = NameGenerationJobRepository(session)
        self._budgets = NameGenerationBudgetRepository(session)

    async def cleanup(
        self, *, now: datetime, job_retention_days: int, budget_retention_days: int
    ) -> NameGenerationCleanupResult:
        jobs = await self._jobs.delete_terminal_due(cutoff=now - timedelta(days=job_retention_days))
        today = now.astimezone(TOKYO).date()
        buckets = await self._budgets.delete_due(
            daily_before=today - timedelta(days=budget_retention_days),
            monthly_before=(today - timedelta(days=budget_retention_days)).replace(day=1),
        )
        return NameGenerationCleanupResult(jobs, buckets)
