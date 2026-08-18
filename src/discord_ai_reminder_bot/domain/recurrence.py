"""Timezone conversion and recurring schedule calculations."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError

TOKYO = ZoneInfo("Asia/Tokyo")


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidDateTimeError("timezone-aware datetime is required")
    return value


def require_utc(value: datetime) -> datetime:
    value = require_aware(value)
    if value.utcoffset() != timedelta(0):
        raise InvalidDateTimeError("UTC datetime is required")
    return value.astimezone(UTC)


def tokyo_to_utc(value: datetime) -> datetime:
    value = require_aware(value)
    if value.tzinfo != TOKYO:
        raise InvalidDateTimeError("Asia/Tokyo datetime is required")
    return value.astimezone(UTC)


def utc_to_tokyo(value: datetime) -> datetime:
    return require_utc(value).astimezone(TOKYO)


def _candidate(day: date, local_time: time) -> datetime:
    if local_time.tzinfo is not None:
        raise InvalidDateTimeError("local schedule time must not contain timezone information")
    naive = datetime.combine(day, local_time.replace(second=0, microsecond=0))
    first = naive.replace(tzinfo=TOKYO, fold=0)
    second = naive.replace(tzinfo=TOKYO, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise InvalidDateTimeError("ambiguous local schedule datetime")
    if first.astimezone(UTC).astimezone(TOKYO).replace(tzinfo=None) != naive:
        raise InvalidDateTimeError("nonexistent local schedule datetime")
    return first


def next_daily_run(
    *, local_time: time, after: datetime, end_date: date | None = None
) -> datetime | None:
    """Return the first daily occurrence strictly after the UTC reference."""
    after_tokyo = utc_to_tokyo(after)
    candidate = _candidate(after_tokyo.date(), local_time)
    if candidate <= after_tokyo:
        candidate += timedelta(days=1)
    if end_date is not None and candidate.date() > end_date:
        return None
    return candidate.astimezone(UTC)


def next_weekly_run(
    *, weekday: int, local_time: time, after: datetime, end_date: date | None = None
) -> datetime | None:
    """Return the first weekly occurrence strictly after the UTC reference."""
    if not 0 <= weekday <= 6:
        raise ValueError("weekday must be between 0 and 6")
    after_tokyo = utc_to_tokyo(after)
    days_ahead = (weekday - after_tokyo.weekday()) % 7
    candidate = _candidate(after_tokyo.date() + timedelta(days=days_ahead), local_time)
    if candidate <= after_tokyo:
        candidate += timedelta(days=7)
    if end_date is not None and candidate.date() > end_date:
        return None
    return candidate.astimezone(UTC)


def first_daily_run(
    *, local_time: time, not_before: datetime, end_date: date | None = None
) -> datetime | None:
    """Return the first daily occurrence at or after an inclusive UTC boundary."""
    return next_daily_run(
        local_time=local_time,
        after=require_utc(not_before) - timedelta(microseconds=1),
        end_date=end_date,
    )


def first_weekly_run(
    *, weekday: int, local_time: time, not_before: datetime, end_date: date | None = None
) -> datetime | None:
    """Return the first weekly occurrence at or after an inclusive UTC boundary."""
    return next_weekly_run(
        weekday=weekday,
        local_time=local_time,
        after=require_utc(not_before) - timedelta(microseconds=1),
        end_date=end_date,
    )
