"""Pure validation for one-time schedule creation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.domain.recurrence import require_utc

TOKYO = ZoneInfo("Asia/Tokyo")
DATETIME_FORMAT = "%Y-%m-%d %H:%M"
TIME_FORMAT = "%H:%M"
DATE_FORMAT = "%Y-%m-%d"
_CREATE_PATTERNS = (
    (
        "strict",
        re.compile(
            r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2}) (?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})"
        ),
    ),
    (
        "slash",
        re.compile(
            r"(?P<year>[0-9]{4})/(?P<month>[0-9]{1,2})/(?P<day>[0-9]{1,2}) (?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})"
        ),
    ),
    (
        "month_day",
        re.compile(
            r"(?P<month>[0-9]{1,2})/(?P<day>[0-9]{1,2}) (?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})"
        ),
    ),
    ("today", re.compile(r"今日 ?(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})")),
    ("tomorrow", re.compile(r"明日 ?(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})")),
)
YEAR_SEARCH_LIMIT = 400
_DISCORD_UNICODE_SPACES = frozenset(
    {
        "\u00a0",
        "\u1680",
        "\u2000",
        "\u2001",
        "\u2002",
        "\u2003",
        "\u2004",
        "\u2005",
        "\u2006",
        "\u2007",
        "\u2008",
        "\u2009",
        "\u200a",
        "\u202f",
        "\u205f",
        "\u3000",
    }
)
_FULLWIDTH_DATETIME_CHARACTERS = frozenset("０１２３４５６７８９：／－﹣−‐‑‒–—―")


class OnceInputFormat(StrEnum):
    STRICT = "strict"
    SLASH = "slash"
    MONTH_DAY = "month_day"
    TODAY = "today"
    TOMORROW = "tomorrow"


@dataclass(frozen=True)
class ParsedOnceSchedule:
    scheduled_for: datetime
    local_datetime: datetime
    input_value: str
    input_format: OnceInputFormat


class InvalidScheduleContentError(ValueError):
    """The supplied post body is not a valid active body or omitted draft body."""


class InvalidCreateDateTimeFormatError(InvalidDateTimeError):
    """The create-only date/time does not match a supported input form."""


class FullwidthCreateDateTimeError(InvalidCreateDateTimeFormatError):
    """The create-only date/time contains a non-ASCII digit or separator."""


class CreateDateTimeTooSoonError(InvalidDateTimeError):
    """The create-only date/time is less than five minutes in the future."""


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


def parse_once_create_input(value: str, *, now: datetime) -> ParsedOnceSchedule:
    """Parse one of the deliberately small create-only Tokyo input forms."""
    now = require_utc(now)
    if not isinstance(value, str):
        raise InvalidCreateDateTimeFormatError("invalid scheduled datetime")
    if any(character in _FULLWIDTH_DATETIME_CHARACTERS for character in value):
        raise FullwidthCreateDateTimeError("non-ASCII datetime character")
    if "\t" in value:
        raise InvalidCreateDateTimeFormatError("tab is not supported")
    normalized = _normalize_create_input(value)
    match = None
    kind = None
    for candidate_kind, pattern in _CREATE_PATTERNS:
        candidate = pattern.fullmatch(normalized)
        if candidate is not None:
            kind, match = candidate_kind, candidate
            break
    if match is None or kind is None:
        raise InvalidCreateDateTimeFormatError("invalid scheduled datetime")

    local_now = now.astimezone(TOKYO)
    values = {name: int(item) for name, item in match.groupdict().items() if item is not None}
    try:
        if kind in {"strict", "slash"}:
            local = _safe_tokyo_datetime(**values)
        elif kind in {"today", "tomorrow"}:
            target = local_now.date() + timedelta(days=kind == "tomorrow")
            local = _safe_tokyo_datetime(
                year=target.year, month=target.month, day=target.day, **values
            )
        else:
            local = _next_month_day(values=values, local_now=local_now, now=now)
    except (TypeError, ValueError) as error:
        raise InvalidCreateDateTimeFormatError("invalid scheduled datetime") from error
    try:
        scheduled = validate_once_scheduled_for(local.astimezone(UTC), now=now)
    except InvalidDateTimeError as error:
        raise CreateDateTimeTooSoonError("scheduled datetime is too soon") from error
    return ParsedOnceSchedule(
        scheduled_for=scheduled,
        local_datetime=local,
        input_value=normalized,
        input_format=OnceInputFormat(kind),
    )


def _normalize_create_input(value: str) -> str:
    """Normalize only surrounding and horizontal space used by Discord clients."""
    stripped = value.strip()
    spaces_normalized = "".join(
        " " if character in _DISCORD_UNICODE_SPACES else character for character in stripped
    )
    return re.sub(" +", " ", spaces_normalized)


def _next_month_day(*, values: dict[str, int], local_now: datetime, now: datetime) -> datetime:
    for offset in range(YEAR_SEARCH_LIMIT + 1):
        try:
            candidate = _safe_tokyo_datetime(year=local_now.year + offset, **values)
        except ValueError:
            continue
        if candidate.astimezone(UTC) >= now + timedelta(minutes=5):
            return candidate
    raise InvalidCreateDateTimeFormatError("no valid date within search limit")


def _safe_tokyo_datetime(*, year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    naive = datetime(year, month, day, hour, minute)  # noqa: DTZ001 - validated before zone attach
    first = naive.replace(tzinfo=TOKYO, fold=0)
    second = first.replace(fold=1)
    if first.utcoffset() != second.utcoffset():
        raise InvalidDateTimeError("ambiguous scheduled datetime")
    if first.astimezone(UTC).astimezone(TOKYO).replace(tzinfo=None) != naive:
        raise InvalidDateTimeError("nonexistent scheduled datetime")
    return first


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


def parse_local_time(value: str) -> time:
    """Parse an exact zero-padded minute without attaching a timezone."""
    try:
        parsed = time.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise InvalidDateTimeError("invalid local time") from error
    if parsed.strftime(TIME_FORMAT) != value:
        raise InvalidDateTimeError("invalid local time")
    return parsed.replace(second=0, microsecond=0)


def parse_end_date(value: str | None) -> date | None:
    """Parse an optional exact Tokyo-local calendar date."""
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise InvalidDateTimeError("invalid end date") from error
    if parsed.strftime(DATE_FORMAT) != value:
        raise InvalidDateTimeError("invalid end date")
    return parsed
