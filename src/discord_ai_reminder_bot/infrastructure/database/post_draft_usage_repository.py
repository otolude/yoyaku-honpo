"""Atomic PostgreSQL usage reservation for AI post drafts."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import Insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.post_draft_usage import PostDraftUsageReservation
from discord_ai_reminder_bot.domain.post_draft_usage import (
    MAX_POSTGRES_BIGINT,
    TOKYO,
    PostDraftUsageReservationCode,
    PostDraftUsageReservationResult,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    PostDraftOperatorBudgetBucket,
    PostDraftRateLimitBucket,
    PostDraftUsageReservationReceipt,
)

POST_DRAFT_USAGE_LOCK_ORDER = (
    "operator_daily",
    "operator_monthly",
    "guild_daily",
    "user_short",
)

_INVALID_SESSION_FACTORY = "invalid post draft usage session factory"


class _ReservationRejected(Exception):
    """Abort every write before returning one fixed capacity outcome."""

    def __init__(self, code: PostDraftUsageReservationCode) -> None:
        super().__init__(code.value)
        self.code = code


def _receipt_insert_statement(reservation: PostDraftUsageReservation) -> Insert:
    return (
        pg_insert(PostDraftUsageReservationReceipt)
        .values(
            operation_key=reservation.operation_key.value,
            reserved_at=reservation.now,
            expires_at=reservation.now + timedelta(days=reservation.policy.receipt_retention_days),
        )
        .on_conflict_do_nothing(index_elements=["operation_key"])
        .returning(PostDraftUsageReservationReceipt.operation_key)
    )


def _operator_statements(
    *, period_type: str, period_start: date, reservation: PostDraftUsageReservation
) -> tuple[Insert, Select]:
    insert_statement = (
        pg_insert(PostDraftOperatorBudgetBucket)
        .values(
            period_type=period_type,
            period_start=period_start,
            created_at=reservation.now,
            updated_at=reservation.now,
        )
        .on_conflict_do_nothing(index_elements=["period_type", "period_start"])
    )
    lock_statement = (
        select(PostDraftOperatorBudgetBucket)
        .where(
            PostDraftOperatorBudgetBucket.period_type == period_type,
            PostDraftOperatorBudgetBucket.period_start == period_start,
        )
        .with_for_update()
    )
    return insert_statement, lock_statement


def _rate_statements(
    *,
    scope_type: str,
    scope_id: int,
    window_type: str,
    window_start: datetime,
    reservation: PostDraftUsageReservation,
) -> tuple[Insert, Select]:
    insert_statement = (
        pg_insert(PostDraftRateLimitBucket)
        .values(
            scope_type=scope_type,
            scope_id=scope_id,
            window_type=window_type,
            window_start=window_start,
            created_at=reservation.now,
            updated_at=reservation.now,
        )
        .on_conflict_do_nothing(
            index_elements=["scope_type", "scope_id", "window_type", "window_start"]
        )
    )
    lock_statement = (
        select(PostDraftRateLimitBucket)
        .where(
            PostDraftRateLimitBucket.scope_type == scope_type,
            PostDraftRateLimitBucket.scope_id == scope_id,
            PostDraftRateLimitBucket.window_type == window_type,
            PostDraftRateLimitBucket.window_start == window_start,
        )
        .with_for_update()
    )
    return insert_statement, lock_statement


def _bucket_statements(
    reservation: PostDraftUsageReservation,
) -> dict[str, tuple[Insert, Select]]:
    daily_date = reservation.daily_window_start.astimezone(TOKYO).date()
    monthly_date = reservation.monthly_window_start.astimezone(TOKYO).date()
    return {
        "operator_daily": _operator_statements(
            period_type="daily", period_start=daily_date, reservation=reservation
        ),
        "operator_monthly": _operator_statements(
            period_type="monthly", period_start=monthly_date, reservation=reservation
        ),
        "guild_daily": _rate_statements(
            scope_type="guild",
            scope_id=reservation.guild_id.value,
            window_type="daily",
            window_start=reservation.daily_window_start,
            reservation=reservation,
        ),
        "user_short": _rate_statements(
            scope_type="user",
            scope_id=reservation.user_id.value,
            window_type="short",
            window_start=reservation.user_window_start,
            reservation=reservation,
        ),
    }


def _exceeds(current: int, increment: int, limit: int) -> bool:
    return current > MAX_POSTGRES_BIGINT - increment or current + increment > limit


class PostgreSQLPostDraftUsageRepository:
    """Own one short Session and transaction per atomic reservation."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        if not isinstance(session_factory, async_sessionmaker):
            raise TypeError(_INVALID_SESSION_FACTORY)
        self._sessions = session_factory

    async def reserve(
        self, reservation: PostDraftUsageReservation
    ) -> PostDraftUsageReservationResult:
        async with self._sessions() as session:
            try:
                async with session.begin():
                    result = await self._reserve_transaction(session, reservation)
            except _ReservationRejected as rejected:
                return PostDraftUsageReservationResult(rejected.code)
        return PostDraftUsageReservationResult(result)

    async def _reserve_transaction(
        self, session: AsyncSession, reservation: PostDraftUsageReservation
    ) -> PostDraftUsageReservationCode:
        inserted = await session.scalar(_receipt_insert_statement(reservation))
        if inserted is None:
            return PostDraftUsageReservationCode.ALREADY_RESERVED

        locked: dict[str, object] = {}
        statements = _bucket_statements(reservation)
        for name in POST_DRAFT_USAGE_LOCK_ORDER:
            insert_statement, lock_statement = statements[name]
            await session.execute(insert_statement)
            locked[name] = (await session.execute(lock_statement)).scalar_one()

        daily = locked["operator_daily"]
        monthly = locked["operator_monthly"]
        guild = locked["guild_daily"]
        user = locked["user_short"]
        if not isinstance(daily, PostDraftOperatorBudgetBucket) or not isinstance(
            monthly, PostDraftOperatorBudgetBucket
        ):
            raise TypeError("invalid post draft operator budget bucket")
        if not isinstance(guild, PostDraftRateLimitBucket) or not isinstance(
            user, PostDraftRateLimitBucket
        ):
            raise TypeError("invalid post draft rate limit bucket")

        policy = reservation.policy
        if _exceeds(user.request_count, 1, policy.rate_limit.user_request_limit):
            raise _ReservationRejected(PostDraftUsageReservationCode.USER_RATE_LIMITED)
        if _exceeds(guild.request_count, 1, policy.rate_limit.guild_daily_request_limit):
            raise _ReservationRejected(PostDraftUsageReservationCode.GUILD_RATE_LIMITED)
        if _exceeds(daily.reserved_request_count, 1, policy.operator_budget.daily_request_limit):
            raise _ReservationRejected(PostDraftUsageReservationCode.GLOBAL_DAILY_EXHAUSTED)
        if _exceeds(
            monthly.reserved_request_count, 1, policy.operator_budget.monthly_request_limit
        ):
            raise _ReservationRejected(PostDraftUsageReservationCode.GLOBAL_MONTHLY_EXHAUSTED)
        cost = reservation.maximum_cost_microunits
        if _exceeds(
            monthly.reserved_cost_microunits,
            cost,
            policy.operator_budget.monthly_cost_limit_microunits,
        ) or _exceeds(daily.reserved_cost_microunits, cost, MAX_POSTGRES_BIGINT):
            raise _ReservationRejected(PostDraftUsageReservationCode.GLOBAL_COST_EXHAUSTED)

        for bucket in (daily, monthly):
            bucket.reserved_request_count += 1
            bucket.reserved_cost_microunits += cost
            bucket.version += 1
            bucket.updated_at = reservation.now
        for bucket in (guild, user):
            bucket.request_count += 1
            bucket.version += 1
            bucket.updated_at = reservation.now
        return PostDraftUsageReservationCode.RESERVED
