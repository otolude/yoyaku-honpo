"""Pure usage-limit policy and time-window primitives for AI post drafts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

MAX_POSTGRES_BIGINT = 9_223_372_036_854_775_807
JPY_MICROUNITS_PER_YEN = 1_000_000
TOKYO = ZoneInfo("Asia/Tokyo")

_INVALID_POLICY = "invalid post draft usage policy"


def _positive_bigint(value: object, *, error_message: str = _INVALID_POLICY) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(error_message)  # noqa: TRY004
    if not 1 <= value <= MAX_POSTGRES_BIGINT:
        raise ValueError(error_message)
    return value


def _require_aware(timestamp: datetime) -> datetime:
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    try:
        offset = timestamp.utcoffset()
    except OverflowError, ValueError:
        raise ValueError("timestamp must be timezone-aware") from None
    if offset is None:
        raise ValueError("timestamp must be timezone-aware")
    return timestamp.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PostDraftOperatorBudgetPolicy:
    """Global request and pessimistic-cost ceilings for post drafting."""

    daily_request_limit: int = field(default=50, repr=False)
    monthly_request_limit: int = field(default=500, repr=False)
    monthly_cost_limit_microunits: int = field(
        default=500 * JPY_MICROUNITS_PER_YEN,
        repr=False,
    )
    cost_currency: str = field(default="JPY", repr=False)
    retention_days: int = field(default=90, repr=False)

    def __post_init__(self) -> None:
        daily = _positive_bigint(self.daily_request_limit)
        monthly = _positive_bigint(self.monthly_request_limit)
        _positive_bigint(self.monthly_cost_limit_microunits)
        _positive_bigint(self.retention_days)
        if monthly < daily or self.cost_currency != "JPY":
            raise ValueError(_INVALID_POLICY)


@dataclass(frozen=True, slots=True)
class PostDraftRateLimitPolicy:
    """Fixed-window user and guild limits, independent from persistence."""

    user_request_limit: int = field(default=3, repr=False)
    user_window_minutes: int = field(default=10, repr=False)
    guild_daily_request_limit: int = field(default=30, repr=False)
    global_daily_request_limit: int = field(default=50, repr=False)
    user_retention_days: int = field(default=7, repr=False)
    guild_retention_days: int = field(default=30, repr=False)

    def __post_init__(self) -> None:
        _positive_bigint(self.user_request_limit)
        window_minutes = _positive_bigint(self.user_window_minutes)
        guild_daily = _positive_bigint(self.guild_daily_request_limit)
        global_daily = _positive_bigint(self.global_daily_request_limit)
        _positive_bigint(self.user_retention_days)
        _positive_bigint(self.guild_retention_days)
        if window_minutes != 10 or guild_daily > global_daily:
            raise ValueError(_INVALID_POLICY)


@dataclass(frozen=True, slots=True)
class PostDraftUsagePolicy:
    """Complete fail-closed MVP policy supplied by configuration later."""

    operator_budget: PostDraftOperatorBudgetPolicy = field(
        default_factory=PostDraftOperatorBudgetPolicy,
        repr=False,
    )
    rate_limit: PostDraftRateLimitPolicy = field(
        default_factory=PostDraftRateLimitPolicy,
        repr=False,
    )
    maximum_concurrency: int = field(default=1, repr=False)
    receipt_retention_days: int = field(default=7, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.operator_budget, PostDraftOperatorBudgetPolicy):
            raise ValueError(_INVALID_POLICY)  # noqa: TRY004
        if not isinstance(self.rate_limit, PostDraftRateLimitPolicy):
            raise ValueError(_INVALID_POLICY)  # noqa: TRY004
        maximum_concurrency = _positive_bigint(self.maximum_concurrency)
        _positive_bigint(self.receipt_retention_days)
        if (
            maximum_concurrency != 1
            or self.operator_budget.daily_request_limit
            != self.rate_limit.global_daily_request_limit
        ):
            raise ValueError(_INVALID_POLICY)


class PostDraftUsageReservationCode(StrEnum):
    """Closed outcomes from a future atomic usage reservation."""

    RESERVED = "reserved"
    ALREADY_RESERVED = "already_reserved"
    USER_RATE_LIMITED = "user_rate_limited"
    GUILD_RATE_LIMITED = "guild_rate_limited"
    GLOBAL_DAILY_EXHAUSTED = "global_daily_exhausted"
    GLOBAL_MONTHLY_EXHAUSTED = "global_monthly_exhausted"
    GLOBAL_COST_EXHAUSTED = "global_cost_exhausted"
    PRICE_UNKNOWN = "price_unknown"
    INVALID_POLICY = "invalid_policy"
    USAGE_UNAVAILABLE = "usage_unavailable"


@dataclass(frozen=True, slots=True)
class PostDraftUsageReservationResult:
    """Content-free outcome of a future reservation operation."""

    code: PostDraftUsageReservationCode

    def __post_init__(self) -> None:
        if not isinstance(self.code, PostDraftUsageReservationCode):
            raise ValueError("invalid post draft usage result")  # noqa: TRY004


@dataclass(frozen=True, slots=True)
class PostDraftOperationKey:
    """Opaque idempotency key without Discord or Schedule identifiers."""

    value: UUID = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise ValueError("operation key must be a UUID")  # noqa: TRY004


@dataclass(frozen=True, slots=True)
class PostDraftUserId:
    """Validated Discord user snowflake for a future rate-limit key."""

    value: int = field(repr=False)

    def __post_init__(self) -> None:
        _positive_bigint(self.value, error_message="invalid post draft subject identifier")


@dataclass(frozen=True, slots=True)
class PostDraftGuildId:
    """Validated Discord guild snowflake for a future rate-limit key."""

    value: int = field(repr=False)

    def __post_init__(self) -> None:
        _positive_bigint(self.value, error_message="invalid post draft subject identifier")


def validate_maximum_cost_microunits(value: object) -> int:
    """Validate an already-priced JPY microunit reservation amount."""
    return _positive_bigint(value, error_message="invalid maximum cost")


def user_fixed_window_start(timestamp: datetime) -> datetime:
    """Floor an instant to its fixed ten-minute UTC window."""
    instant = _require_aware(timestamp)
    minute = instant.minute - (instant.minute % 10)
    return instant.replace(minute=minute, second=0, microsecond=0)


def jst_daily_window_start(timestamp: datetime) -> datetime:
    """Return the UTC instant at which the containing JST day began."""
    local = _require_aware(timestamp).astimezone(TOKYO)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def jst_monthly_window_start(timestamp: datetime) -> datetime:
    """Return the UTC instant at which the containing JST month began."""
    local = _require_aware(timestamp).astimezone(TOKYO)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def retention_cutoff(timestamp: datetime, *, retention_days: int) -> datetime:
    """Return an exact UTC cutoff; callers supply one deterministic clock value."""
    instant = _require_aware(timestamp)
    days = _positive_bigint(retention_days)
    return instant - timedelta(days=days)


@dataclass(frozen=True, slots=True)
class PostDraftUsageCleanupCutoffs:
    """Deterministic UTC and JST-date boundaries for one cleanup transaction."""

    receipt_expires_at: datetime
    user_window_start: datetime
    guild_window_start: datetime
    operator_daily_before: date
    operator_monthly_before: date


def post_draft_usage_cleanup_cutoffs(
    timestamp: datetime, *, policy: PostDraftUsagePolicy
) -> PostDraftUsageCleanupCutoffs:
    """Derive every inclusive or strict retention boundary from one supplied instant."""
    if not isinstance(policy, PostDraftUsagePolicy):
        raise ValueError(_INVALID_POLICY)  # noqa: TRY004
    instant = _require_aware(timestamp)
    operator_cutoff_day = instant.astimezone(TOKYO).date() - timedelta(
        days=policy.operator_budget.retention_days
    )
    return PostDraftUsageCleanupCutoffs(
        receipt_expires_at=instant,
        user_window_start=retention_cutoff(
            instant, retention_days=policy.rate_limit.user_retention_days
        ),
        guild_window_start=retention_cutoff(
            instant, retention_days=policy.rate_limit.guild_retention_days
        ),
        operator_daily_before=operator_cutoff_day,
        operator_monthly_before=operator_cutoff_day.replace(day=1),
    )
