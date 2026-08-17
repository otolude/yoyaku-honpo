"""Clock abstraction for deterministic domain decisions."""

from datetime import datetime
from typing import Protocol

from discord_ai_reminder_bot.domain.recurrence import require_utc


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current timezone-aware UTC datetime."""
        ...


class FixedClock:
    """A clock fixed at a UTC instant, primarily for deterministic tests."""

    def __init__(self, current: datetime) -> None:
        self._current = require_utc(current)

    def now(self) -> datetime:
        return self._current
