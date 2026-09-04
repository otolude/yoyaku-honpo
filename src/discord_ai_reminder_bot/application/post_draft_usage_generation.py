"""Coordinate one post-draft usage reservation with one generation call."""

from __future__ import annotations

import asyncio

from discord_ai_reminder_bot.application.post_draft_generation import (
    GeneratePostDraftService,
    PostDraftDisabledError,
)
from discord_ai_reminder_bot.application.post_draft_usage import (
    PostDraftUsageRepository,
    PostDraftUsageReservation,
)
from discord_ai_reminder_bot.domain.post_draft_generation import (
    GeneratedPostDraft,
    PostDraftGenerationRequest,
)
from discord_ai_reminder_bot.domain.post_draft_usage import (
    PostDraftUsageReservationCode,
    PostDraftUsageReservationResult,
)


class PostDraftUsageError(Exception):
    """A fixed content-free usage reservation failure."""

    def __init__(self, usage_code: PostDraftUsageReservationCode) -> None:
        self.usage_code = usage_code
        super().__init__(usage_code.value)

    def __repr__(self) -> str:
        return f"PostDraftUsageError(usage_code={self.usage_code.value!r})"


class GeneratePostDraftWithUsageService:
    """Reserve usage before allowing one provider-neutral generation call."""

    __slots__ = ("_enabled", "_generation_service", "_semaphore", "_usage_repository")

    def __init__(
        self,
        *,
        usage_repository: PostDraftUsageRepository,
        generation_service: GeneratePostDraftService,
        enabled: bool,
    ) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self._usage_repository = usage_repository
        self._generation_service = generation_service
        self._enabled = enabled
        self._semaphore = asyncio.Semaphore(1)

    def __repr__(self) -> str:
        return "GeneratePostDraftWithUsageService()"

    async def generate(
        self,
        request: PostDraftGenerationRequest,
        reservation: PostDraftUsageReservation,
    ) -> GeneratedPostDraft:
        if not self._enabled:
            raise PostDraftDisabledError

        async with self._semaphore:
            try:
                usage_result = await self._usage_repository.reserve(reservation)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - persistence details stay behind this boundary
                raise PostDraftUsageError(PostDraftUsageReservationCode.USAGE_UNAVAILABLE) from None

            if not isinstance(usage_result, PostDraftUsageReservationResult):
                raise PostDraftUsageError(PostDraftUsageReservationCode.USAGE_UNAVAILABLE)

            match usage_result.code:
                case PostDraftUsageReservationCode.RESERVED:
                    return await self._generation_service.generate(request)
                case PostDraftUsageReservationCode.ALREADY_RESERVED:
                    raise PostDraftUsageError(PostDraftUsageReservationCode.ALREADY_RESERVED)
                case PostDraftUsageReservationCode.USER_RATE_LIMITED:
                    raise PostDraftUsageError(PostDraftUsageReservationCode.USER_RATE_LIMITED)
                case PostDraftUsageReservationCode.GUILD_RATE_LIMITED:
                    raise PostDraftUsageError(PostDraftUsageReservationCode.GUILD_RATE_LIMITED)
                case PostDraftUsageReservationCode.GLOBAL_DAILY_EXHAUSTED:
                    raise PostDraftUsageError(PostDraftUsageReservationCode.GLOBAL_DAILY_EXHAUSTED)
                case PostDraftUsageReservationCode.GLOBAL_MONTHLY_EXHAUSTED:
                    raise PostDraftUsageError(
                        PostDraftUsageReservationCode.GLOBAL_MONTHLY_EXHAUSTED
                    )
                case PostDraftUsageReservationCode.GLOBAL_COST_EXHAUSTED:
                    raise PostDraftUsageError(PostDraftUsageReservationCode.GLOBAL_COST_EXHAUSTED)
                case PostDraftUsageReservationCode.PRICE_UNKNOWN:
                    raise PostDraftUsageError(PostDraftUsageReservationCode.PRICE_UNKNOWN)
                case PostDraftUsageReservationCode.INVALID_POLICY:
                    raise PostDraftUsageError(PostDraftUsageReservationCode.INVALID_POLICY)
                case PostDraftUsageReservationCode.USAGE_UNAVAILABLE:
                    raise PostDraftUsageError(PostDraftUsageReservationCode.USAGE_UNAVAILABLE)
                case _:
                    raise PostDraftUsageError(PostDraftUsageReservationCode.USAGE_UNAVAILABLE)
