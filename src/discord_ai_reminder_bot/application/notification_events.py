"""Create initial notification outbox routes for confirmed business events."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.config import MAX_POSTGRES_BIGINT
from discord_ai_reminder_bot.domain.enums import (
    NotificationRecipientType,
    NotificationStatus,
    NotificationType,
)
from discord_ai_reminder_bot.domain.notification import (
    notification_deduplication_key,
    schedule_notification_deduplication_key,
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

_RUN_EVENT_KINDS = {
    NotificationType.RUN_FAILED: "run_failed",
    NotificationType.RUN_DELAYED: "run_delayed",
    NotificationType.RUN_SKIPPED: "run_skipped",
    NotificationType.RECOVERY: "recovery",
}


class NotificationEventService:
    """Insert only the first route; transaction ownership remains with the caller."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        configured_guild_id: int,
        operator_channel_id: int,
    ) -> None:
        self._logs = NotificationLogRepository(session)
        self._guild_id = _positive_bigint(configured_guild_id, "configured_guild_id")
        self._operator_channel_id = _positive_bigint(operator_channel_id, "operator_channel_id")

    async def add_run_event(
        self,
        *,
        schedule: Schedule,
        run: ScheduleRun,
        notification_type: NotificationType,
        event_at: datetime,
    ) -> NotificationLog | None:
        event_at = require_utc(event_at)
        notification_type = NotificationType(notification_type)
        event_kind = _RUN_EVENT_KINDS.get(notification_type)
        if event_kind is None:
            raise ValueError("notification type is not a run business event")
        if schedule.guild_id != self._guild_id:
            return None
        if run.schedule_id != schedule.id:
            raise ValueError("run does not belong to schedule")
        return await self._logs.add_idempotent(
            NotificationLog(
                schedule_id=schedule.id,
                schedule_run_id=run.id,
                notification_type=notification_type.value,
                recipient_type=NotificationRecipientType.OPERATOR_CHANNEL.value,
                recipient_id=self._operator_channel_id,
                status=NotificationStatus.PENDING.value,
                deduplication_key=notification_deduplication_key(
                    event_kind=event_kind,
                    schedule_public_id=schedule.public_id,
                    scheduled_for=run.scheduled_for,
                    notification_type=notification_type,
                    recipient_type=NotificationRecipientType.OPERATOR_CHANNEL,
                ),
                error_code=None,
                error_summary=None,
                scheduled_at=event_at,
                next_attempt_at=event_at,
                attempt_count=0,
            ),
            allow_existing_event_time=True,
        )

    async def add_recurring_missed(
        self, *, schedule: Schedule, recovery_cutoff: datetime
    ) -> NotificationLog | None:
        recovery_cutoff = require_utc(recovery_cutoff)
        if schedule.guild_id != self._guild_id:
            return None
        return await self._logs.add_idempotent(
            NotificationLog(
                schedule_id=schedule.id,
                schedule_run_id=None,
                notification_type=NotificationType.RUN_SKIPPED.value,
                recipient_type=NotificationRecipientType.OPERATOR_CHANNEL.value,
                recipient_id=self._operator_channel_id,
                status=NotificationStatus.PENDING.value,
                deduplication_key=schedule_notification_deduplication_key(
                    event_kind="startup_recurring_missed",
                    schedule_public_id=schedule.public_id,
                    occurred_at=recovery_cutoff,
                    notification_type=NotificationType.RUN_SKIPPED,
                    recipient_type=NotificationRecipientType.OPERATOR_CHANNEL,
                ),
                error_code=None,
                error_summary=None,
                scheduled_at=recovery_cutoff,
                next_attempt_at=recovery_cutoff,
                attempt_count=0,
            ),
            allow_existing_event_time=True,
        )


def _positive_bigint(value: int, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_POSTGRES_BIGINT
    ):
        raise ValueError(f"{field} must be a positive PostgreSQL BIGINT")
    return value
