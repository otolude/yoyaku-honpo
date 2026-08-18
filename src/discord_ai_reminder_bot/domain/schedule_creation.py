"""Pure validation for one-time schedule creation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.domain.recurrence import require_utc

TOKYO = ZoneInfo("Asia/Tokyo")
DATETIME_FORMAT = "%Y-%m-%d %H:%M"


class InvalidScheduleContentError(ValueError):
    """The supplied post body is not a valid active body or omitted draft body."""


def parse_once_scheduled_at(value: str, *, now: datetime) -> datetime:
    """Parse an exact Tokyo minute and require it to be at least five minutes ahead."""
    try:
        parsed = datetime.strptime(value, DATETIME_FORMAT).replace(tzinfo=TOKYO, fold=0)
    except (TypeError, ValueError) as error:
        raise InvalidDateTimeError("invalid scheduled datetime") from error
    if parsed.strftime(DATETIME_FORMAT) != value:
        raise InvalidDateTimeError("invalid scheduled datetime")
    naive = parsed.replace(tzinfo=None)
    first = parsed
    second = parsed.replace(fold=1)
    if first.utcoffset() != second.utcoffset():
        raise InvalidDateTimeError("ambiguous scheduled datetime")
    scheduled = first.astimezone(UTC)
    if scheduled.astimezone(TOKYO).replace(tzinfo=None) != naive:
        raise InvalidDateTimeError("nonexistent scheduled datetime")
    return validate_once_scheduled_for(scheduled, now=now)


def validate_once_scheduled_for(scheduled: datetime, *, now: datetime) -> datetime:
    scheduled = require_utc(scheduled)
    if scheduled < require_utc(now) + timedelta(minutes=5):
        raise InvalidDateTimeError("scheduled datetime is too soon")
    return scheduled


def validate_create_content(content: str | None) -> str | None:
    """Keep valid content verbatim while reserving None exclusively for drafts."""
    if content is None:
        return None
    if not 1 <= len(content) <= 2_000 or not content.strip():
        raise InvalidScheduleContentError("invalid content")
    lowered = content.lower()
    if "@everyone" in lowered or "@here" in lowered:
        raise InvalidScheduleContentError("forbidden mention")
    return content
