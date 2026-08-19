"""Startup bootstrap for future draft notification plans."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application.notification_planning import NotificationPlanningService
from discord_ai_reminder_bot.domain.enums import RunStatus, ScheduleStatus
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.infrastructure.database.notification_repositories import (
    NotificationLogRepository,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    ScheduleRepository,
    ScheduleRunRepository,
)


@dataclass(frozen=True)
class DraftNotificationBootstrapSummary:
    selected: int = 0
    notifications_cancelled: int = 0
    notifications_planned: int = 0


class DraftNotificationBootstrapService:
    """Process one locked batch; the runtime owns its transaction."""

    def __init__(self, session: AsyncSession, *, configured_guild_id: int) -> None:
        self._runs = ScheduleRunRepository(session)
        self._schedules = ScheduleRepository(session)
        self._logs = NotificationLogRepository(session)
        self._planner = NotificationPlanningService(
            session, configured_guild_id=configured_guild_id
        )
        self._guild_id = configured_guild_id

    async def bootstrap(
        self, *, recovery_cutoff: datetime, batch_size: int
    ) -> DraftNotificationBootstrapSummary:
        recovery_cutoff = require_utc(recovery_cutoff)
        runs = await self._runs.lock_draft_notification_bootstrap(
            recovery_cutoff=recovery_cutoff,
            configured_guild_id=self._guild_id,
            batch_size=batch_size,
        )
        cancelled = planned = 0
        for run in runs:
            schedule = await self._schedules.lock_by_id(run.schedule_id)
            if not self._eligible(schedule, run, recovery_cutoff):
                continue
            existing = await self._logs.list_draft_for_run(run_id=run.id)
            cancelled += await self._logs.cancel_unclaimed_overdue_draft(
                run_id=run.id, cutoff=recovery_cutoff
            )
            result = await self._planner.plan_for_run(
                schedule=schedule,
                run=run,
                event_at=recovery_cutoff,
                excluded_types=frozenset(
                    item.notification_type
                    for item in existing
                    if item.notification_type == "draft_immediate"
                ),
            )
            planned += result.created_or_existing
        return DraftNotificationBootstrapSummary(len(runs), cancelled, planned)

    def _eligible(self, schedule, run, cutoff: datetime) -> bool:
        return (
            schedule.guild_id == self._guild_id
            and schedule.status == ScheduleStatus.DRAFT.value
            and schedule.content is None
            and schedule.next_run_at == run.scheduled_for
            and run.status == RunStatus.PENDING.value
            and run.attempt_count == 0
            and run.scheduled_for > cutoff
        )
