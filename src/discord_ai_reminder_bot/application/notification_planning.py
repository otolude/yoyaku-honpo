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
    NotificationAttemptRepository,
    NotificationLogRepository,
)


@dataclass(frozen=True)
class NotificationPlanningResult:
    created_or_existing: int


class NotificationPlanningService:
    """Insert creator-DM routes; never commit or roll back."""

    def __init__(self, session: AsyncSession, *, configured_guild_id: int) -> None:
        self._session = session
        self._logs = NotificationLogRepository(session)
        self._attempts = NotificationAttemptRepository(session)
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
            stored = await self._logs.add_idempotent(
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
                ),
                allow_existing_event_time=True,
            )
            if (
                stored.status == NotificationStatus.CANCELLED.value
                and stored.error_code == "schedule_paused"
                and stored.attempt_count == 0
                and stored.claimed_by is None
                and stored.claimed_at is None
                and stored.lease_expires_at is None
                and stored.started_at is None
                and stored.finished_at is not None
                and stored.sent_at is None
                and await self._attempts.get_latest(notification_id=stored.id) is None
            ):
                stored.status = NotificationStatus.PENDING.value
                stored.scheduled_at = plan.scheduled_at
                stored.next_attempt_at = plan.scheduled_at
                stored.claimed_by = stored.claimed_at = stored.lease_expires_at = None
                stored.started_at = stored.finished_at = stored.sent_at = None
                stored.error_code = stored.error_summary = None
                await self._session.flush()
        return NotificationPlanningResult(
            sum(plan.notification_type.value not in excluded_types for plan in plans)
        )
