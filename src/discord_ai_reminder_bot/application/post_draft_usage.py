"""Persistence-neutral reservation contract for AI post-draft usage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from discord_ai_reminder_bot.domain.post_draft_usage import (
    PostDraftGuildId,
    PostDraftOperationKey,
    PostDraftUsagePolicy,
    PostDraftUsageReservationResult,
    PostDraftUserId,
    jst_daily_window_start,
    jst_monthly_window_start,
    user_fixed_window_start,
    validate_maximum_cost_microunits,
)

_INVALID_RESERVATION = "invalid post draft usage reservation"


@dataclass(frozen=True, slots=True, init=False)
class PostDraftUsageReservation:
    """Content-free input for one atomic usage reservation attempt."""

    operation_key: PostDraftOperationKey = field(repr=False)
    user_id: PostDraftUserId = field(repr=False)
    guild_id: PostDraftGuildId = field(repr=False)
    user_window_start: datetime = field(repr=False)
    daily_window_start: datetime = field(repr=False)
    monthly_window_start: datetime = field(repr=False)
    maximum_cost_microunits: int = field(repr=False)
    now: datetime = field(repr=False)
    policy: PostDraftUsagePolicy = field(repr=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise ValueError(_INVALID_RESERVATION)

    @classmethod
    def create(
        cls,
        *,
        operation_key: PostDraftOperationKey,
        user_id: PostDraftUserId,
        guild_id: PostDraftGuildId,
        maximum_cost_microunits: int,
        now: datetime,
        policy: PostDraftUsagePolicy,
    ) -> PostDraftUsageReservation:
        """Validate inputs and derive all bucket boundaries from one supplied instant."""
        if (
            not isinstance(operation_key, PostDraftOperationKey)
            or not isinstance(user_id, PostDraftUserId)
            or not isinstance(guild_id, PostDraftGuildId)
            or not isinstance(policy, PostDraftUsagePolicy)
            or not isinstance(now, datetime)
        ):
            raise ValueError(_INVALID_RESERVATION)  # noqa: TRY004
        try:
            maximum_cost = validate_maximum_cost_microunits(maximum_cost_microunits)
            user_start = user_fixed_window_start(now)
            daily_start = jst_daily_window_start(now)
            monthly_start = jst_monthly_window_start(now)
            normalized_now = now.astimezone(UTC)
        except OverflowError, TypeError, ValueError:
            raise ValueError(_INVALID_RESERVATION) from None

        value = object.__new__(cls)
        object.__setattr__(value, "operation_key", operation_key)
        object.__setattr__(value, "user_id", user_id)
        object.__setattr__(value, "guild_id", guild_id)
        object.__setattr__(value, "user_window_start", user_start)
        object.__setattr__(value, "daily_window_start", daily_start)
        object.__setattr__(value, "monthly_window_start", monthly_start)
        object.__setattr__(value, "maximum_cost_microunits", maximum_cost)
        object.__setattr__(value, "now", normalized_now)
        object.__setattr__(value, "policy", policy)
        return value


class PostDraftUsageRepository(Protocol):
    """Atomically reserve every usage bucket and its opaque receipt.

    A first operation key either increments every applicable bucket once or increments none.
    A repeated key returns ``already_reserved`` without incrementing any bucket. That result is
    not authorization to call a generator again. Infrastructure errors carry no details in a
    result, while cancellation remains available for the caller to propagate.
    """

    async def reserve(
        self, reservation: PostDraftUsageReservation
    ) -> PostDraftUsageReservationResult: ...
