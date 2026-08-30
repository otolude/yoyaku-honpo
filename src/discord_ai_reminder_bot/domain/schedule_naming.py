"""Pure validation and deterministic presentation for schedule names."""

from __future__ import annotations

import unicodedata
from datetime import datetime, time

from discord_ai_reminder_bot.domain.enums import DisplayNameSource, ScheduleType
from discord_ai_reminder_bot.domain.recurrence import TOKYO

MAX_DISPLAY_NAME_LENGTH = 32
_FORBIDDEN_CATEGORIES = frozenset(("Cc", "Cf", "Cs"))
_WEEKDAYS = ("月", "火", "水", "木", "金", "土", "日")


def validate_display_name(
    display_name: str | None, source: DisplayNameSource
) -> tuple[str | None, DisplayNameSource]:
    """Validate and normalize the persisted name/source pair."""
    if not isinstance(source, DisplayNameSource):
        raise TypeError("invalid display name source")
    if source is DisplayNameSource.UNSET:
        if display_name is not None:
            raise ValueError("unset display name must be null")
        return None, source
    if not isinstance(display_name, str):
        raise TypeError("named display name must be text")
    normalized = display_name.strip()
    if not 1 <= len(normalized) <= MAX_DISPLAY_NAME_LENGTH:
        raise ValueError("display name must be between 1 and 32 characters")
    if any(unicodedata.category(character) in _FORBIDDEN_CATEGORIES for character in normalized):
        raise ValueError("display name contains a forbidden character")
    return normalized, source


def normalize_manual_display_name(value: str) -> tuple[str | None, DisplayNameSource]:
    """Turn an empty manual submission into unset; otherwise validate it."""
    if not isinstance(value, str):
        raise TypeError("display name must be text")
    if not value.strip():
        return None, DisplayNameSource.UNSET
    return validate_display_name(value, DisplayNameSource.MANUAL)


def schedule_display_name(
    *,
    display_name: str | None,
    source: DisplayNameSource,
    schedule_type: ScheduleType,
    next_run_at: datetime | None,
    local_time: time | None,
    weekday: int | None,
) -> str:
    """Return a stored name or a deterministic, non-persisted JST fallback."""
    stored, _ = validate_display_name(display_name, source)
    if stored is not None:
        return stored
    if schedule_type is ScheduleType.ONCE and next_run_at is not None:
        if next_run_at.tzinfo is None or next_run_at.utcoffset() is None:
            return "名称未設定"
        local = next_run_at.astimezone(TOKYO)
        return f"単発予約 {local.month}/{local.day} {local:%H:%M}"
    if schedule_type is ScheduleType.DAILY and local_time is not None:
        return f"毎日予約 {local_time:%H:%M}"
    if (
        schedule_type is ScheduleType.WEEKLY
        and local_time is not None
        and weekday is not None
        and not isinstance(weekday, bool)
        and 0 <= weekday < len(_WEEKDAYS)
    ):
        return f"毎週予約 {_WEEKDAYS[weekday]} {local_time:%H:%M}"
    return "名称未設定"
