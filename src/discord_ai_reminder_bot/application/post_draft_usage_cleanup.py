"""Persistence-neutral cleanup boundary for post-draft usage state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from discord_ai_reminder_bot.domain.post_draft_usage import (
    MAX_POSTGRES_BIGINT,
    PostDraftUsagePolicy,
    post_draft_usage_cleanup_cutoffs,
)

_INVALID_REQUEST = "invalid post draft usage cleanup request"
_INVALID_RESULT = "invalid post draft usage cleanup result"
_UNAVAILABLE = "post draft usage cleanup unavailable"


def _validate_deleted_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(_INVALID_RESULT)  # noqa: TRY004
    if not 0 <= value <= MAX_POSTGRES_BIGINT:
        raise ValueError(_INVALID_RESULT)
    return value


@dataclass(frozen=True, slots=True)
class PostDraftUsageCleanupResult:
    """Content-free counts committed by one cleanup transaction."""

    deleted_receipt_count: int
    deleted_user_bucket_count: int
    deleted_guild_bucket_count: int
    deleted_operator_bucket_count: int

    def __post_init__(self) -> None:
        for value in (
            self.deleted_receipt_count,
            self.deleted_user_bucket_count,
            self.deleted_guild_bucket_count,
            self.deleted_operator_bucket_count,
        ):
            _validate_deleted_count(value)


class PostDraftUsageCleanupRepository(Protocol):
    """Delete only expired post-draft usage state in one atomic operation."""

    async def cleanup(
        self, *, now: datetime, policy: PostDraftUsagePolicy
    ) -> PostDraftUsageCleanupResult: ...


class PostDraftUsageCleanupUnavailableError(Exception):
    """Fixed failure when usage cleanup persistence is unavailable."""

    def __init__(self) -> None:
        super().__init__(_UNAVAILABLE)


class CleanupPostDraftUsageService:
    """Validate one cutoff and invoke the cleanup Repository exactly once."""

    def __init__(self, *, repository: PostDraftUsageCleanupRepository) -> None:
        self._repository = repository

    async def cleanup(
        self, *, now: datetime, policy: PostDraftUsagePolicy
    ) -> PostDraftUsageCleanupResult:
        try:
            cutoffs = post_draft_usage_cleanup_cutoffs(now, policy=policy)
        except OverflowError, TypeError, ValueError:
            raise ValueError(_INVALID_REQUEST) from None
        try:
            result = await self._repository.cleanup(
                now=cutoffs.receipt_expires_at,
                policy=policy,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - persistence details stay behind this boundary
            raise PostDraftUsageCleanupUnavailableError from None
        if not isinstance(result, PostDraftUsageCleanupResult):
            raise PostDraftUsageCleanupUnavailableError
        return result
