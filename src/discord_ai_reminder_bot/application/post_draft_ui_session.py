"""Discord-neutral in-memory state boundary for one post-draft UI session."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from discord_ai_reminder_bot.application.post_draft_generation import (
    PostDraftDisabledError,
    PostDraftInvalidResponseError,
    PostDraftTimeoutError,
    PostDraftUnavailableError,
)
from discord_ai_reminder_bot.application.post_draft_usage import PostDraftUsageReservation
from discord_ai_reminder_bot.application.post_draft_usage_generation import PostDraftUsageError
from discord_ai_reminder_bot.domain.post_draft_generation import (
    GeneratedPostDraft,
    PostDraftGenerationRequest,
)
from discord_ai_reminder_bot.domain.post_draft_usage import PostDraftUsageReservationCode

MAX_DISCORD_ID = 2**63 - 1


class PostDraftUISessionState(StrEnum):
    MODE_SELECTION = "mode_selection"
    MANUAL_ENTRY = "manual_entry"
    AI_INPUT = "ai_input"
    GENERATING = "generating"
    PREVIEW = "preview"
    EDITING = "editing"
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PostDraftUIErrorCode(StrEnum):
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"
    ALREADY_RESERVED = "already_reserved"
    USER_RATE_LIMITED = "user_rate_limited"
    GUILD_RATE_LIMITED = "guild_rate_limited"
    GLOBAL_DAILY_EXHAUSTED = "global_daily_exhausted"
    GLOBAL_MONTHLY_EXHAUSTED = "global_monthly_exhausted"
    GLOBAL_COST_EXHAUSTED = "global_cost_exhausted"
    USAGE_UNAVAILABLE = "usage_unavailable"
    INVALID_TRANSITION = "invalid_transition"
    NOT_OWNER = "not_owner"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PostDraftUISessionError(Exception):
    """Content-free failure classification for a future Discord presenter."""

    def __init__(self, code: PostDraftUIErrorCode) -> None:
        if not isinstance(code, PostDraftUIErrorCode):
            raise TypeError("invalid post draft UI error")
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"PostDraftUISessionError(code={self.code.value!r})"


class PostDraftUsageGenerationService(Protocol):
    async def generate(
        self,
        request: PostDraftGenerationRequest,
        reservation: PostDraftUsageReservation,
    ) -> GeneratedPostDraft: ...


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("post draft UI time must be timezone-aware")
    return value.astimezone(UTC)


def _discord_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_DISCORD_ID:
        raise ValueError("invalid post draft UI identity")
    return value


@dataclass(slots=True, repr=False)
class PostDraftUISession:
    """Ephemeral state only; Python object memory erasure is not guaranteed."""

    owner_user_id: int = field(repr=False)
    guild_id: int = field(repr=False)
    created_at: datetime = field(repr=False)
    expires_at: datetime = field(repr=False)
    state: PostDraftUISessionState = PostDraftUISessionState.MODE_SELECTION
    request: PostDraftGenerationRequest | None = field(default=None, repr=False)
    _draft: GeneratedPostDraft | None = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _generation_token: int = field(default=0, repr=False)
    _active_generation_token: int | None = field(default=None, repr=False)

    @classmethod
    def create(
        cls,
        *,
        owner_user_id: object,
        guild_id: object,
        created_at: object,
        expires_at: object,
    ) -> PostDraftUISession:
        owner = _discord_id(owner_user_id)
        guild = _discord_id(guild_id)
        created = _aware_utc(created_at)
        expires = _aware_utc(expires_at)
        if expires <= created:
            raise ValueError("invalid post draft UI expiry")
        return cls(
            owner_user_id=owner,
            guild_id=guild,
            created_at=created,
            expires_at=expires,
        )

    def __repr__(self) -> str:
        return f"PostDraftUISession(state={self.state.value!r})"

    def current_draft(self) -> GeneratedPostDraft | None:
        return self._draft


_TERMINAL_STATES = frozenset(
    {
        PostDraftUISessionState.ACCEPTED,
        PostDraftUISessionState.CANCELLED,
        PostDraftUISessionState.EXPIRED,
    }
)


class PostDraftUISessionController:
    """Serialize and validate all mutations of one ephemeral session."""

    __slots__ = ("_generation_service", "session")

    def __init__(
        self,
        *,
        session: PostDraftUISession,
        generation_service: PostDraftUsageGenerationService,
    ) -> None:
        if not isinstance(session, PostDraftUISession):
            raise TypeError("invalid post draft UI session")
        self.session = session
        self._generation_service = generation_service

    def __repr__(self) -> str:
        return "PostDraftUISessionController()"

    def _clear_payload(self) -> None:
        self.session.request = None
        self.session._draft = None

    def _invalidate_generation(self) -> None:
        self.session._active_generation_token = None

    def _authorize(self, *, owner_user_id: object, guild_id: object, now: object) -> None:
        owner = _discord_id(owner_user_id)
        guild = _discord_id(guild_id)
        instant = _aware_utc(now)
        if owner != self.session.owner_user_id or guild != self.session.guild_id:
            raise PostDraftUISessionError(PostDraftUIErrorCode.NOT_OWNER)
        if instant >= self.session.expires_at:
            if self.session.state not in _TERMINAL_STATES:
                self._invalidate_generation()
                self._clear_payload()
                self.session.state = PostDraftUISessionState.EXPIRED
            raise PostDraftUISessionError(PostDraftUIErrorCode.EXPIRED)
        if self.session.state in _TERMINAL_STATES:
            raise PostDraftUISessionError(PostDraftUIErrorCode.INVALID_TRANSITION)

    def _require(self, *states: PostDraftUISessionState) -> None:
        if self.session.state not in states:
            raise PostDraftUISessionError(PostDraftUIErrorCode.INVALID_TRANSITION)

    async def choose_manual(self, *, owner_user_id: object, guild_id: object, now: object) -> None:
        async with self.session._lock:
            self._authorize(owner_user_id=owner_user_id, guild_id=guild_id, now=now)
            self._require(PostDraftUISessionState.MODE_SELECTION)
            self.session.state = PostDraftUISessionState.MANUAL_ENTRY

    async def choose_ai(self, *, owner_user_id: object, guild_id: object, now: object) -> None:
        async with self.session._lock:
            self._authorize(owner_user_id=owner_user_id, guild_id=guild_id, now=now)
            self._require(PostDraftUISessionState.MODE_SELECTION)
            self.session.state = PostDraftUISessionState.AI_INPUT

    async def submit_manual(
        self,
        *,
        text: object,
        owner_user_id: object,
        guild_id: object,
        now: object,
    ) -> GeneratedPostDraft:
        async with self.session._lock:
            self._authorize(owner_user_id=owner_user_id, guild_id=guild_id, now=now)
            self._require(PostDraftUISessionState.MANUAL_ENTRY)
            draft = self._validated_draft(text)
            self.session._draft = draft
            self.session.state = PostDraftUISessionState.PREVIEW
            return draft

    async def generate(
        self,
        *,
        request: PostDraftGenerationRequest,
        reservation: PostDraftUsageReservation,
        owner_user_id: object,
        guild_id: object,
        now: object,
    ) -> GeneratedPostDraft:
        async with self.session._lock:
            self._authorize(owner_user_id=owner_user_id, guild_id=guild_id, now=now)
            self._require(PostDraftUISessionState.AI_INPUT, PostDraftUISessionState.PREVIEW)
            if not isinstance(request, PostDraftGenerationRequest):
                raise PostDraftUISessionError(PostDraftUIErrorCode.INVALID_RESPONSE)
            self.session._generation_token += 1
            token = self.session._generation_token
            self.session._active_generation_token = token
            self.session.request = request
            self.session._draft = None
            self.session.state = PostDraftUISessionState.GENERATING

        failure: PostDraftUIErrorCode | None = None
        generated: GeneratedPostDraft | None = None
        cancellation: asyncio.CancelledError | None = None
        try:
            generated = await self._generation_service.generate(request, reservation)
        except asyncio.CancelledError as error:
            cancellation = error
        except PostDraftDisabledError:
            failure = PostDraftUIErrorCode.DISABLED
        except PostDraftUnavailableError:
            failure = PostDraftUIErrorCode.UNAVAILABLE
        except PostDraftTimeoutError:
            failure = PostDraftUIErrorCode.TIMEOUT
        except PostDraftInvalidResponseError:
            failure = PostDraftUIErrorCode.INVALID_RESPONSE
        except PostDraftUsageError as error:
            failure = _usage_error_code(error.usage_code)
        except Exception:  # noqa: BLE001 - details must not cross this boundary
            failure = PostDraftUIErrorCode.UNKNOWN

        stale: PostDraftUIErrorCode | None = None
        async with self.session._lock:
            if (
                self.session._active_generation_token != token
                or self.session.state is not PostDraftUISessionState.GENERATING
            ):
                stale = _stale_generation_code(self.session.state)
            elif cancellation is not None:
                self._invalidate_generation()
                self.session.request = None
                self.session._draft = None
                self.session.state = PostDraftUISessionState.AI_INPUT
            elif failure is not None:
                self._invalidate_generation()
                self._clear_payload()
                self.session.state = PostDraftUISessionState.AI_INPUT
            elif not isinstance(generated, GeneratedPostDraft):
                self._invalidate_generation()
                self._clear_payload()
                self.session.state = PostDraftUISessionState.AI_INPUT
                failure = PostDraftUIErrorCode.INVALID_RESPONSE
            else:
                self._invalidate_generation()
                self.session._draft = generated
                self.session.state = PostDraftUISessionState.PREVIEW

        if cancellation is not None:
            raise cancellation
        if stale is not None:
            raise PostDraftUISessionError(stale)
        if failure is not None:
            raise PostDraftUISessionError(failure)
        if generated is None:
            raise PostDraftUISessionError(PostDraftUIErrorCode.INVALID_RESPONSE)
        return generated

    async def begin_edit(self, *, owner_user_id: object, guild_id: object, now: object) -> None:
        async with self.session._lock:
            self._authorize(owner_user_id=owner_user_id, guild_id=guild_id, now=now)
            self._require(PostDraftUISessionState.PREVIEW)
            self.session.state = PostDraftUISessionState.EDITING

    async def confirm_edit(
        self,
        *,
        text: object,
        owner_user_id: object,
        guild_id: object,
        now: object,
    ) -> GeneratedPostDraft:
        async with self.session._lock:
            self._authorize(owner_user_id=owner_user_id, guild_id=guild_id, now=now)
            self._require(PostDraftUISessionState.EDITING)
            draft = self._validated_draft(text)
            self.session._draft = draft
            self.session.state = PostDraftUISessionState.PREVIEW
            return draft

    async def accept(
        self, *, owner_user_id: object, guild_id: object, now: object
    ) -> GeneratedPostDraft:
        async with self.session._lock:
            self._authorize(owner_user_id=owner_user_id, guild_id=guild_id, now=now)
            self._require(PostDraftUISessionState.PREVIEW)
            draft = self.session._draft
            if not isinstance(draft, GeneratedPostDraft):
                raise PostDraftUISessionError(PostDraftUIErrorCode.INVALID_RESPONSE)
            self.session.request = None
            self.session.state = PostDraftUISessionState.ACCEPTED
            return draft

    def accepted_draft(self) -> GeneratedPostDraft:
        if self.session.state is not PostDraftUISessionState.ACCEPTED:
            raise PostDraftUISessionError(PostDraftUIErrorCode.INVALID_TRANSITION)
        draft = self.session._draft
        if not isinstance(draft, GeneratedPostDraft):
            raise PostDraftUISessionError(PostDraftUIErrorCode.INVALID_RESPONSE)
        return draft

    async def cancel(self, *, owner_user_id: object, guild_id: object, now: object) -> None:
        async with self.session._lock:
            self._authorize(owner_user_id=owner_user_id, guild_id=guild_id, now=now)
            self._invalidate_generation()
            self._clear_payload()
            self.session.state = PostDraftUISessionState.CANCELLED

    async def expire(self, *, owner_user_id: object, guild_id: object, now: object) -> None:
        async with self.session._lock:
            owner = _discord_id(owner_user_id)
            guild = _discord_id(guild_id)
            instant = _aware_utc(now)
            if owner != self.session.owner_user_id or guild != self.session.guild_id:
                raise PostDraftUISessionError(PostDraftUIErrorCode.NOT_OWNER)
            if self.session.state in _TERMINAL_STATES:
                raise PostDraftUISessionError(PostDraftUIErrorCode.INVALID_TRANSITION)
            if instant < self.session.expires_at:
                raise PostDraftUISessionError(PostDraftUIErrorCode.INVALID_TRANSITION)
            self._invalidate_generation()
            self._clear_payload()
            self.session.state = PostDraftUISessionState.EXPIRED

    @staticmethod
    def _validated_draft(text: object) -> GeneratedPostDraft:
        draft: GeneratedPostDraft | None = None
        try:
            draft = GeneratedPostDraft(text)  # type: ignore[arg-type]
        except TypeError, ValueError:
            pass
        if draft is None:
            raise PostDraftUISessionError(PostDraftUIErrorCode.INVALID_RESPONSE)
        return draft


def _usage_error_code(code: PostDraftUsageReservationCode) -> PostDraftUIErrorCode:
    mapping = {
        PostDraftUsageReservationCode.ALREADY_RESERVED: PostDraftUIErrorCode.ALREADY_RESERVED,
        PostDraftUsageReservationCode.USER_RATE_LIMITED: PostDraftUIErrorCode.USER_RATE_LIMITED,
        PostDraftUsageReservationCode.GUILD_RATE_LIMITED: PostDraftUIErrorCode.GUILD_RATE_LIMITED,
        PostDraftUsageReservationCode.GLOBAL_DAILY_EXHAUSTED: (
            PostDraftUIErrorCode.GLOBAL_DAILY_EXHAUSTED
        ),
        PostDraftUsageReservationCode.GLOBAL_MONTHLY_EXHAUSTED: (
            PostDraftUIErrorCode.GLOBAL_MONTHLY_EXHAUSTED
        ),
        PostDraftUsageReservationCode.GLOBAL_COST_EXHAUSTED: (
            PostDraftUIErrorCode.GLOBAL_COST_EXHAUSTED
        ),
        PostDraftUsageReservationCode.USAGE_UNAVAILABLE: PostDraftUIErrorCode.USAGE_UNAVAILABLE,
        PostDraftUsageReservationCode.PRICE_UNKNOWN: PostDraftUIErrorCode.USAGE_UNAVAILABLE,
        PostDraftUsageReservationCode.INVALID_POLICY: PostDraftUIErrorCode.USAGE_UNAVAILABLE,
    }
    return mapping.get(code, PostDraftUIErrorCode.USAGE_UNAVAILABLE)


def _stale_generation_code(state: PostDraftUISessionState) -> PostDraftUIErrorCode:
    if state is PostDraftUISessionState.CANCELLED:
        return PostDraftUIErrorCode.CANCELLED
    if state is PostDraftUISessionState.EXPIRED:
        return PostDraftUIErrorCode.EXPIRED
    return PostDraftUIErrorCode.INVALID_TRANSITION
