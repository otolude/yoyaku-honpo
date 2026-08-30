"""Provider-independent schedule-name generation policy primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from discord_ai_reminder_bot.domain.enums import (
    BudgetPeriodType,
    DisplayNameSource,
    NameGenerationJobStatus,
)
from discord_ai_reminder_bot.domain.schedule_naming import validate_display_name

MAX_POSTGRES_BIGINT = 9_223_372_036_854_775_807
TOKYO = ZoneInfo("Asia/Tokyo")
TERMINAL_JOB_STATUSES = frozenset(
    {
        NameGenerationJobStatus.SUCCEEDED,
        NameGenerationJobStatus.FAILED,
        NameGenerationJobStatus.SKIPPED,
        NameGenerationJobStatus.ABANDONED,
    }
)


def _positive_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")  # noqa: TRY004
    if not 1 <= value <= MAX_POSTGRES_BIGINT:
        raise ValueError(f"{field} is outside the supported range")
    return value


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """Mutable-by-configuration operator safety limits, immutable per process."""

    daily_request_limit: int
    monthly_request_limit: int
    monthly_cost_limit_microunits: int
    cost_currency: str

    def __post_init__(self) -> None:
        daily = _positive_integer(self.daily_request_limit, field="daily_request_limit")
        monthly = _positive_integer(self.monthly_request_limit, field="monthly_request_limit")
        _positive_integer(
            self.monthly_cost_limit_microunits,
            field="monthly_cost_limit_microunits",
        )
        if monthly < daily:
            raise ValueError("monthly_request_limit must be at least daily_request_limit")
        if self.cost_currency != "JPY":
            raise ValueError("cost_currency must be JPY")


@dataclass(frozen=True, slots=True)
class NameGenerationRequest:
    content: str
    max_length: int = 32
    locale: str = "ja-JP"
    single_line: bool = True
    prohibit_control_characters: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content:
            raise ValueError("content is required")
        if self.max_length != 32 or self.locale != "ja-JP":
            raise ValueError("unsupported generation constraints")
        if not self.single_line or not self.prohibit_control_characters:
            raise ValueError("unsafe generation constraints")


@dataclass(frozen=True, slots=True)
class GeneratedScheduleName:
    value: str

    def __post_init__(self) -> None:
        normalized, source = validate_display_name(self.value, DisplayNameSource.AI)
        if normalized is None:
            raise ValueError("generated name is required")
        object.__setattr__(self, "value", normalized)
        if source is not DisplayNameSource.AI:
            raise AssertionError("display-name validator returned an unexpected source")


def budget_period_start(period_type: BudgetPeriodType, at: datetime) -> date:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at must be timezone-aware")
    local_date = at.astimezone(TOKYO).date()
    if period_type is BudgetPeriodType.MONTHLY:
        return local_date.replace(day=1)
    return local_date


def budget_period_end(period_type: BudgetPeriodType, period_start: date) -> date:
    if period_type is BudgetPeriodType.DAILY:
        return period_start + timedelta(days=1)
    if period_start.day != 1:
        raise ValueError("monthly period_start must be the first day")
    return (period_start.replace(day=28) + timedelta(days=4)).replace(day=1)


def can_transition_job(current: NameGenerationJobStatus, target: NameGenerationJobStatus) -> bool:
    allowed = {
        NameGenerationJobStatus.PENDING: {
            NameGenerationJobStatus.PROCESSING,
            NameGenerationJobStatus.SKIPPED,
        },
        NameGenerationJobStatus.PROCESSING: TERMINAL_JOB_STATUSES,
    }
    return target in allowed.get(current, frozenset())


def terminal_job_is_due(
    *,
    status: NameGenerationJobStatus,
    finished_at: datetime | None,
    now: datetime,
    retention_days: int,
) -> bool:
    _positive_integer(retention_days, field="retention_days")
    if status not in TERMINAL_JOB_STATUSES or finished_at is None:
        return False
    if finished_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return finished_at <= now - timedelta(days=retention_days)


def budget_bucket_is_due(
    *, period_type: BudgetPeriodType, period_start: date, today_jst: date, retention_days: int
) -> bool:
    _positive_integer(retention_days, field="retention_days")
    return budget_period_end(period_type, period_start) <= today_jst - timedelta(
        days=retention_days
    )
