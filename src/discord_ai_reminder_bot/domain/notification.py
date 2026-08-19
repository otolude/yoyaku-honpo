"""Pure notification outbox identity and retry rules."""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from discord_ai_reminder_bot.domain.enums import NotificationRecipientType, NotificationType
from discord_ai_reminder_bot.domain.recurrence import require_utc

MAX_NOTIFICATION_ATTEMPTS = 3
NOTIFICATION_RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5))
_EVENT_PATTERN = re.compile(r"[a-z0-9_]{1,32}\Z")


class NotificationOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


class NotificationDecisionAction(StrEnum):
    RETRY = "retry"
    FINAL_FAILED_WITH_FALLBACK_ALLOWED = "final_failed_with_fallback_allowed"
    UNKNOWN_WITHOUT_FALLBACK = "unknown_without_fallback"
    SUCCEEDED = "succeeded"


@dataclass(frozen=True)
class NotificationDecision:
    action: NotificationDecisionAction
    next_attempt_at: datetime | None


def notification_deduplication_key(
    *,
    event_kind: str,
    schedule_public_id: uuid.UUID,
    scheduled_for: datetime,
    notification_type: NotificationType,
    recipient_type: NotificationRecipientType,
) -> str:
    """Return an unambiguous key without content or internal database IDs."""
    event_kind = _require_event_kind(event_kind)
    public_id = _require_uuid7(schedule_public_id, field="schedule_public_id")
    scheduled_for = require_utc(scheduled_for)
    instant = scheduled_for.strftime("%Y%m%dT%H%M%S.%fZ")
    key = (
        f"v1|{event_kind}|{public_id}|{instant}|"
        f"{NotificationType(notification_type).value}|{NotificationRecipientType(recipient_type).value}"
    )
    if len(key) > 160:
        raise ValueError("deduplication key exceeds 160 characters")
    return key


def global_notification_deduplication_key(
    *,
    event_kind: str,
    event_public_id: uuid.UUID,
    occurred_at: datetime,
    notification_type: NotificationType,
    recipient_type: NotificationRecipientType,
) -> str:
    """Return a key for a global event that has no Schedule."""
    event_kind = _require_event_kind(event_kind)
    public_id = _require_uuid7(event_public_id, field="event_public_id")
    occurred_at = require_utc(occurred_at)
    instant = occurred_at.strftime("%Y%m%dT%H%M%S.%fZ")
    key = (
        f"v1g|{event_kind}|{public_id}|{instant}|"
        f"{NotificationType(notification_type).value}|{NotificationRecipientType(recipient_type).value}"
    )
    if len(key) > 160:
        raise ValueError("deduplication key exceeds 160 characters")
    return key


def fallback_notification_deduplication_key(
    original_key: str, recipient_type: NotificationRecipientType
) -> str:
    """Keep a logical event identity while selecting a distinct fallback route."""
    if not isinstance(original_key, str) or not 1 <= len(original_key) <= 160:
        raise ValueError("original notification key is invalid")
    parts = original_key.split("|")
    if len(parts) != 6 or parts[0] not in {"v1", "v1g"}:
        raise ValueError("original notification key is not canonical")
    NotificationRecipientType(parts[-1])
    route = NotificationRecipientType(recipient_type).value
    key = "|".join((*parts[:-1], route))
    if len(key) > 160:
        raise ValueError("fallback notification key exceeds 160 characters")
    return key


def decide_notification_result(
    *,
    attempt_number: int,
    outcome: NotificationOutcome,
    decided_at: datetime,
    retry_at: datetime | None = None,
) -> NotificationDecision:
    decided_at = require_utc(decided_at)
    if isinstance(attempt_number, bool) or not 1 <= attempt_number <= MAX_NOTIFICATION_ATTEMPTS:
        raise ValueError("attempt_number must be between 1 and 3")
    outcome = NotificationOutcome(outcome)
    if retry_at is not None:
        retry_at = require_utc(retry_at)
        if retry_at <= decided_at:
            raise ValueError("retry_at must be after decided_at")
    if outcome is NotificationOutcome.SUCCEEDED:
        if retry_at is not None:
            raise ValueError("succeeded outcome cannot have retry_at")
        return NotificationDecision(NotificationDecisionAction.SUCCEEDED, None)
    if outcome is NotificationOutcome.UNKNOWN:
        if retry_at is not None:
            raise ValueError("unknown outcome cannot have retry_at")
        return NotificationDecision(NotificationDecisionAction.UNKNOWN_WITHOUT_FALLBACK, None)
    if outcome is NotificationOutcome.PERMANENT:
        if retry_at is not None:
            raise ValueError("terminal outcome cannot have retry_at")
        return NotificationDecision(
            NotificationDecisionAction.FINAL_FAILED_WITH_FALLBACK_ALLOWED, None
        )
    if outcome is NotificationOutcome.RATE_LIMITED:
        if retry_at is None:
            raise ValueError("rate_limited outcome requires retry_at")
        if attempt_number == MAX_NOTIFICATION_ATTEMPTS:
            return NotificationDecision(
                NotificationDecisionAction.FINAL_FAILED_WITH_FALLBACK_ALLOWED, None
            )
        return NotificationDecision(NotificationDecisionAction.RETRY, retry_at)
    if retry_at is not None:
        raise ValueError("only rate_limited outcome accepts retry_at")
    if attempt_number == MAX_NOTIFICATION_ATTEMPTS:
        return NotificationDecision(
            NotificationDecisionAction.FINAL_FAILED_WITH_FALLBACK_ALLOWED, None
        )
    return NotificationDecision(
        NotificationDecisionAction.RETRY,
        decided_at + NOTIFICATION_RETRY_DELAYS[attempt_number - 1],
    )


def _require_event_kind(value: str) -> str:
    if not isinstance(value, str) or _EVENT_PATTERN.fullmatch(value) is None:
        raise ValueError("event_kind must use 1-32 lowercase ASCII letters, digits, or underscores")
    return value


def _require_uuid7(value: uuid.UUID, *, field: str) -> uuid.UUID:
    if (
        not isinstance(value, uuid.UUID)
        or value.version != 7
        or str(value) != str(uuid.UUID(str(value)))
    ):
        raise ValueError(f"{field} must be a canonical UUIDv7")
    return value
