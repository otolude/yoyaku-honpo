"""Atomic PostgreSQL cleanup for expired post-draft usage state."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import Delete, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.post_draft_usage_cleanup import (
    PostDraftUsageCleanupResult,
)
from discord_ai_reminder_bot.domain.post_draft_usage import (
    PostDraftUsagePolicy,
    post_draft_usage_cleanup_cutoffs,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    PostDraftOperatorBudgetBucket,
    PostDraftRateLimitBucket,
    PostDraftUsageReservationReceipt,
)

_INVALID_SESSION_FACTORY = "invalid post draft usage cleanup session factory"


def _cleanup_statements(*, now: datetime, policy: PostDraftUsagePolicy) -> dict[str, Delete]:
    cutoffs = post_draft_usage_cleanup_cutoffs(now, policy=policy)
    return {
        "receipt": delete(PostDraftUsageReservationReceipt).where(
            PostDraftUsageReservationReceipt.expires_at <= cutoffs.receipt_expires_at
        ),
        "user": delete(PostDraftRateLimitBucket).where(
            PostDraftRateLimitBucket.scope_type == "user",
            PostDraftRateLimitBucket.window_type == "short",
            PostDraftRateLimitBucket.window_start <= cutoffs.user_window_start,
        ),
        "guild": delete(PostDraftRateLimitBucket).where(
            PostDraftRateLimitBucket.scope_type == "guild",
            PostDraftRateLimitBucket.window_type == "daily",
            PostDraftRateLimitBucket.window_start <= cutoffs.guild_window_start,
        ),
        "operator": delete(PostDraftOperatorBudgetBucket).where(
            (
                (PostDraftOperatorBudgetBucket.period_type == "daily")
                & (PostDraftOperatorBudgetBucket.period_start < cutoffs.operator_daily_before)
            )
            | (
                (PostDraftOperatorBudgetBucket.period_type == "monthly")
                & (PostDraftOperatorBudgetBucket.period_start < cutoffs.operator_monthly_before)
            )
        ),
    }


def _deleted_count(rowcount: int | None) -> int:
    if isinstance(rowcount, bool) or not isinstance(rowcount, int) or rowcount < 0:
        return 0
    return rowcount


async def _execute_cleanup_transaction(
    session: AsyncSession, statements: Mapping[str, Delete]
) -> PostDraftUsageCleanupResult:
    counts: dict[str, int] = {}
    for name in ("receipt", "user", "guild", "operator"):
        result = await session.execute(statements[name])
        counts[name] = _deleted_count(result.rowcount)
    return PostDraftUsageCleanupResult(
        deleted_receipt_count=counts["receipt"],
        deleted_user_bucket_count=counts["user"],
        deleted_guild_bucket_count=counts["guild"],
        deleted_operator_bucket_count=counts["operator"],
    )


class PostgreSQLPostDraftUsageCleanupRepository:
    """Own one short Session and transaction per cleanup operation."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        if not isinstance(session_factory, async_sessionmaker):
            raise TypeError(_INVALID_SESSION_FACTORY)
        self._sessions = session_factory

    async def cleanup(
        self, *, now: datetime, policy: PostDraftUsagePolicy
    ) -> PostDraftUsageCleanupResult:
        statements = _cleanup_statements(now=now, policy=policy)
        async with self._sessions() as session, session.begin():
            result = await _execute_cleanup_transaction(session, statements)
        return result
