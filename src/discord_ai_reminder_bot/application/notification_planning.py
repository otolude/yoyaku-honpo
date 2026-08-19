"""Draft notification outbox planning in caller-owned transactions."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.domain.enums import (
    NotificationRecipientType,
    NotificationStatus,
    ScheduleStatus,
    ScheduleType,
)
from discord_ai_reminder_bot.domain.notification import (
    notification_deduplication_key,
    plan_draft_notifications,
)
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.infrastructure.database.models import (
    NotificationLog,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.database.notification_repositories import (
    NotificationLogRepository,
)


@dataclass(frozen=True)
class NotificationPlanningResult:
    created_or_existing: int


class NotificationPlanningService:
    """Insert creator-DM routes; never commit or roll back."""

    def __init__(self, session: AsyncSession, *, configured_guild_id: int) -> None:
        self._logs = NotificationLogRepository(session)
        self._guild_id = configured_guild_id

    async def plan_for_run(
        self,
        *,
        schedule: Schedule,
        run: ScheduleRun,
        event_at: datetime,
        excluded_types: frozenset[str] = frozenset(),
    ) -> NotificationPlanningResult:
        event_at = require_utc(event_at)
        if schedule.guild_id != self._guild_id:
            return NotificationPlanningResult(0)
        plans = plan_draft_notifications(
            event_at=event_at,
            scheduled_for=run.scheduled_for,
            schedule_status=ScheduleStatus(schedule.status),
            content=schedule.content,
            schedule_type=ScheduleType(schedule.schedule_type),
            run_status=run.status,
            attempt_count=run.attempt_count,
            next_run_at=schedule.next_run_at,
        )
        for plan in plans:
            if plan.notification_type.value in excluded_types:
                continue
            await self._logs.add_idempotent(
                NotificationLog(
                    schedule_id=schedule.id,
                    schedule_run_id=run.id,
                    notification_type=plan.notification_type.value,
                    recipient_type=NotificationRecipientType.CREATOR_DM.value,
                    recipient_id=schedule.creator_user_id,
                    status=NotificationStatus.PENDING.value,
                    deduplication_key=notification_deduplication_key(
                        event_kind="draft_reminder",
                        schedule_public_id=schedule.public_id,
                        scheduled_for=run.scheduled_for,
                        notification_type=plan.notification_type,
                        recipient_type=NotificationRecipientType.CREATOR_DM,
                    ),
                    error_code=None,
                    error_summary=None,
                    scheduled_at=plan.scheduled_at,
                    next_attempt_at=plan.scheduled_at,
                    attempt_count=0,
                )
            )
        return NotificationPlanningResult(
            sum(plan.notification_type.value not in excluded_types for plan in plans)
        )
