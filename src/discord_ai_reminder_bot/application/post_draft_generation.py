"""Provider-neutral one-shot application boundary for AI post drafts."""

from __future__ import annotations

import asyncio
import math
from enum import StrEnum
from typing import Protocol

from discord_ai_reminder_bot.domain.post_draft_generation import (
    GeneratedPostDraft,
    PostDraftGenerationRequest,
)


class PostDraftErrorCode(StrEnum):
    """Closed, user-safe failure classifications."""

    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


class PostDraftGenerationError(Exception):
    """Base failure that retains no provider or payload details."""

    code: PostDraftErrorCode

    def __init__(self, *_discarded_details: object) -> None:
        super().__init__(self.code.value)


class PostDraftUnavailableError(PostDraftGenerationError):
    """The configured generator could not serve this request."""

    code = PostDraftErrorCode.UNAVAILABLE


class PostDraftDisabledError(PostDraftUnavailableError):
    """Post draft generation is disabled by configuration."""

    code = PostDraftErrorCode.DISABLED


class PostDraftTimeoutError(PostDraftGenerationError):
    """The one-shot generation exceeded its outer deadline."""

    code = PostDraftErrorCode.TIMEOUT


class PostDraftInvalidResponseError(PostDraftGenerationError):
    """The returned value was not a valid generated post draft."""

    code = PostDraftErrorCode.INVALID_RESPONSE


class PostDraftUnknownError(PostDraftGenerationError):
    """An unclassified generator failure occurred."""

    code = PostDraftErrorCode.UNKNOWN


class PostDraftGenerator(Protocol):
    """Provider-independent asynchronous one-shot generator Port."""

    async def generate(self, request: PostDraftGenerationRequest) -> GeneratedPostDraft: ...


class DisabledPostDraftGenerator:
    """Safe default implementation that performs no external operation."""

    async def generate(self, request: PostDraftGenerationRequest) -> GeneratedPostDraft:
        del request
        raise PostDraftDisabledError


class GeneratePostDraftService:
    """Call one generator once without persistence, delivery, retry, or payload logging."""

    __slots__ = ("_generator", "_timeout_seconds")

    def __init__(
        self,
        *,
        generator: PostDraftGenerator,
        timeout_seconds: float,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        self._generator = generator
        self._timeout_seconds = float(timeout_seconds)

    def __repr__(self) -> str:
        return "GeneratePostDraftService()"

    async def generate(self, request: PostDraftGenerationRequest) -> GeneratedPostDraft:
        """Generate once and expose only a validated value or fixed typed failure."""
        failure: PostDraftErrorCode | None = None
        generated: object = None
        try:
            async with asyncio.timeout(self._timeout_seconds):
                generated = await self._generator.generate(request)
        except asyncio.CancelledError:
            raise
        except PostDraftDisabledError:
            failure = PostDraftErrorCode.DISABLED
        except PostDraftUnavailableError:
            failure = PostDraftErrorCode.UNAVAILABLE
        except PostDraftInvalidResponseError:
            failure = PostDraftErrorCode.INVALID_RESPONSE
        except TimeoutError:
            failure = PostDraftErrorCode.TIMEOUT
        except PostDraftGenerationError:
            failure = PostDraftErrorCode.UNKNOWN
        except Exception:  # noqa: BLE001 - generator details must not cross this boundary
            failure = PostDraftErrorCode.UNKNOWN

        match failure:
            case PostDraftErrorCode.DISABLED:
                raise PostDraftDisabledError
            case PostDraftErrorCode.UNAVAILABLE:
                raise PostDraftUnavailableError
            case PostDraftErrorCode.INVALID_RESPONSE:
                raise PostDraftInvalidResponseError
            case PostDraftErrorCode.TIMEOUT:
                raise PostDraftTimeoutError
            case PostDraftErrorCode.UNKNOWN:
                raise PostDraftUnknownError
            case None:
                pass

        if not isinstance(generated, GeneratedPostDraft):
            raise PostDraftInvalidResponseError
        validated: GeneratedPostDraft | None = None
        try:
            validated = GeneratedPostDraft(generated.value)
        except TypeError, ValueError:
            pass
        if validated is None:
            raise PostDraftInvalidResponseError
        return generated
