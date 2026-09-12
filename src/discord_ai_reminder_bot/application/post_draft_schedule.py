"""Application boundaries for scheduling an accepted post draft."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Protocol

from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
    IdempotentScheduleCreationCode,
    IdempotentScheduleCreationResult,
    ScheduleCreationPublicId,
)
from discord_ai_reminder_bot.domain.enums import ScheduleType
from discord_ai_reminder_bot.domain.post_draft_generation import GeneratedPostDraft


class PostDraftScheduleState(StrEnum):
    SCHEDULE_TYPE_SELECTION = "schedule_type_selection"
    SCHEDULE_INPUT = "schedule_input"
    FINAL_CONFIRMATION = "final_confirmation"
    SAVING = "saving"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True, repr=False)
class PostDraftOnceScheduleInput:
    scheduled_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.scheduled_at, datetime) or self.scheduled_at.tzinfo is None:
            raise ValueError("scheduled_at must be timezone-aware")


@dataclass(frozen=True, slots=True, repr=False)
class PostDraftDailyScheduleInput:
    local_time: time = field(repr=False)
    end_date: date | None = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.local_time, time) or self.local_time.tzinfo is not None:
            raise ValueError("local_time must be timezone-naive")
        if self.local_time.second or self.local_time.microsecond:
            raise ValueError("local_time must have minute precision")
        if self.end_date is not None and not isinstance(self.end_date, date):
            raise ValueError("end_date must be a date")


@dataclass(frozen=True, slots=True, repr=False)
class PostDraftWeeklyScheduleInput:
    local_time: time = field(repr=False)
    weekday: int = field(repr=False)
    end_date: date | None = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.local_time, time) or self.local_time.tzinfo is not None:
            raise ValueError("local_time must be timezone-naive")
        if self.local_time.second or self.local_time.microsecond:
            raise ValueError("local_time must have minute precision")
        if (
            isinstance(self.weekday, bool)
            or not isinstance(self.weekday, int)
            or not 0 <= self.weekday <= 6
        ):
            raise ValueError("weekday must be between 0 and 6")
        if self.end_date is not None and not isinstance(self.end_date, date):
            raise ValueError("end_date must be a date")


@dataclass(frozen=True, slots=True, repr=False)
class PostDraftScheduleSnapshot:
    state: str = field(repr=False)
    schedule_type: ScheduleType | None = field(repr=False)
    validated_input: object | None = field(repr=False)
    timezone: str = field(repr=False)
    allow_duplicate: bool = field(repr=False)
    confirmation_revision: int = field(repr=False)

    def __repr__(self) -> str:
        return "PostDraftScheduleSnapshot()"


_TERMINAL_SCHEDULE_STATES = {
    PostDraftScheduleState.COMPLETED,
    PostDraftScheduleState.CANCELLED,
    PostDraftScheduleState.CONFLICT,
    PostDraftScheduleState.UNKNOWN,
}


class PostDraftSchedulePort(Protocol):
    async def create_once(
        self,
        *,
        public_id: ScheduleCreationPublicId,
        guild_id: int,
        channel_id: int,
        creator_user_id: int,
        scheduled_for: datetime,
        content: str | None,
        allow_duplicate: bool,
        now: datetime,
    ) -> IdempotentScheduleCreationResult: ...

    async def create_recurring(
        self,
        *,
        public_id: ScheduleCreationPublicId,
        guild_id: int,
        channel_id: int,
        creator_user_id: int,
        schedule_type: ScheduleType,
        local_time: time,
        weekday: int | None,
        end_date: date | None,
        content: str | None,
        allow_duplicate: bool,
        now: datetime,
    ) -> IdempotentScheduleCreationResult: ...


@dataclass(frozen=True, slots=True)
class PostDraftScheduleScope:
    owner_user_id: int = field(repr=False)
    guild_id: int = field(repr=False)
    channel_id: int = field(repr=False)

    def __post_init__(self) -> None:
        for value in (self.owner_user_id, self.guild_id, self.channel_id):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("invalid post draft schedule scope")

    def __repr__(self) -> str:
        return "PostDraftScheduleScope()"


class PostDraftScheduleSession:
    __slots__ = (
        "_accepted_draft",
        "_confirmation_revision",
        "_schedule_type",
        "_scope",
        "_state",
        "_validated_input",
    )

    def __init__(
        self, *, scope: PostDraftScheduleScope, accepted_draft: GeneratedPostDraft
    ) -> None:
        if not isinstance(scope, PostDraftScheduleScope):
            raise TypeError("scope must be a PostDraftScheduleScope")
        if not isinstance(accepted_draft, GeneratedPostDraft):
            raise TypeError("accepted_draft must be a GeneratedPostDraft")
        self._scope = scope
        self._accepted_draft = accepted_draft
        self._state = PostDraftScheduleState.SCHEDULE_TYPE_SELECTION
        self._schedule_type: ScheduleType | None = None
        self._validated_input: object | None = None
        self._confirmation_revision = 0

    @property
    def scope(self) -> PostDraftScheduleScope:
        return self._scope

    @property
    def accepted_draft(self) -> GeneratedPostDraft:
        return self._accepted_draft

    def snapshot(self) -> PostDraftScheduleSnapshot:
        return PostDraftScheduleSnapshot(
            state=self._state,
            schedule_type=self._schedule_type,
            validated_input=self._validated_input,
            timezone="Asia/Tokyo",
            allow_duplicate=False,
            confirmation_revision=self._confirmation_revision,
        )

    def select_type(self, schedule_type: ScheduleType) -> None:
        self._require_state(PostDraftScheduleState.SCHEDULE_TYPE_SELECTION)
        if not isinstance(schedule_type, ScheduleType):
            raise TypeError("schedule_type must be a ScheduleType")
        self._schedule_type = schedule_type
        self._state = PostDraftScheduleState.SCHEDULE_INPUT

    def set_validated_input(self, value: object) -> None:
        self._require_state(PostDraftScheduleState.SCHEDULE_INPUT)
        expected = {
            ScheduleType.ONCE: PostDraftOnceScheduleInput,
            ScheduleType.DAILY: PostDraftDailyScheduleInput,
            ScheduleType.WEEKLY: PostDraftWeeklyScheduleInput,
        }[self._schedule_type]
        if not isinstance(value, expected):
            raise TypeError("schedule input does not match selected type")
        self._validated_input = value
        self._confirmation_revision += 1
        self._state = PostDraftScheduleState.FINAL_CONFIRMATION

    def replace_validated_input(
        self, new_input: object, *, expected_revision: int
    ) -> PostDraftScheduleSnapshot:
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise TypeError("invalid confirmation revision")
        if expected_revision < 0:
            raise ValueError("invalid confirmation revision")
        self._require_state(PostDraftScheduleState.FINAL_CONFIRMATION)
        if self._validated_input is None or expected_revision != self._confirmation_revision:
            raise ValueError("stale confirmation revision")
        expected = {
            ScheduleType.ONCE: PostDraftOnceScheduleInput,
            ScheduleType.DAILY: PostDraftDailyScheduleInput,
            ScheduleType.WEEKLY: PostDraftWeeklyScheduleInput,
        }[self._schedule_type]
        if not isinstance(new_input, expected):
            raise TypeError("schedule input does not match selected type")
        self._validated_input = new_input
        self._confirmation_revision += 1
        return self.snapshot()

    def edit_schedule_input(self) -> None:
        self._require_state(PostDraftScheduleState.FINAL_CONFIRMATION)
        self._state = PostDraftScheduleState.SCHEDULE_INPUT

    def begin_saving(self) -> None:
        self._require_state(PostDraftScheduleState.FINAL_CONFIRMATION)
        if self._validated_input is None:
            raise ValueError("validated schedule input is required")
        self._state = PostDraftScheduleState.SAVING

    def mark_completed(self) -> None:
        self._mark_result(PostDraftScheduleState.COMPLETED)

    def mark_conflict(self) -> None:
        self._mark_result(PostDraftScheduleState.CONFLICT)

    def mark_unknown(self) -> None:
        self._mark_result(PostDraftScheduleState.UNKNOWN)

    def cancel(self) -> None:
        self._require_state(
            PostDraftScheduleState.SCHEDULE_TYPE_SELECTION,
            PostDraftScheduleState.SCHEDULE_INPUT,
            PostDraftScheduleState.FINAL_CONFIRMATION,
        )
        self._state = PostDraftScheduleState.CANCELLED

    def _mark_result(self, state: str) -> None:
        self._require_state(PostDraftScheduleState.SAVING)
        self._state = state

    def _require_state(self, *allowed: str) -> None:
        if self._state not in allowed:
            raise ValueError("invalid schedule session state transition")


class PostDraftScheduleConfirmationError(Exception):
    """Fixed application error for an inconclusive schedule confirmation."""

    def __init__(self) -> None:
        super().__init__("post draft schedule confirmation failed")


class PostDraftScheduleController:
    __slots__ = ("_lock", "_port", "_public_id_factory", "_session")

    def __init__(
        self,
        *,
        session: PostDraftScheduleSession,
        port: PostDraftSchedulePort,
        public_id_factory: Callable[[], ScheduleCreationPublicId],
    ) -> None:
        if not isinstance(session, PostDraftScheduleSession):
            raise TypeError("session must be a PostDraftScheduleSession")
        self._session = session
        self._port = port
        self._public_id_factory = public_id_factory
        self._lock = asyncio.Lock()

    def snapshot(self) -> PostDraftScheduleSnapshot:
        return self._session.snapshot()

    @property
    def session(self) -> PostDraftScheduleSession:
        return self._session

    async def confirm(self, *, now: datetime) -> IdempotentScheduleCreationResult:
        async with self._lock:
            self._session.begin_saving()
            try:
                public_id = self._public_id_factory()
                if not isinstance(public_id, ScheduleCreationPublicId):
                    raise TypeError("public_id_factory returned an invalid value")
            except Exception:  # noqa: BLE001
                self._session.mark_unknown()
                raise PostDraftScheduleConfirmationError from None

            try:
                result = await self._call_port(public_id=public_id, now=now)
            except asyncio.CancelledError:
                self._session.mark_unknown()
                raise
            except Exception:  # noqa: BLE001
                self._session.mark_unknown()
                raise PostDraftScheduleConfirmationError from None

            if not isinstance(result, IdempotentScheduleCreationResult):
                self._session.mark_unknown()
                raise PostDraftScheduleConfirmationError
            match result.code:
                case (
                    IdempotentScheduleCreationCode.CREATED
                    | IdempotentScheduleCreationCode.ALREADY_CREATED
                ):
                    self._session.mark_completed()
                case IdempotentScheduleCreationCode.CONFLICT:
                    self._session.mark_conflict()
                case IdempotentScheduleCreationCode.UNKNOWN:
                    self._session.mark_unknown()
                case _:
                    self._session.mark_unknown()
                    raise PostDraftScheduleConfirmationError
            return result

    async def _call_port(
        self, *, public_id: ScheduleCreationPublicId, now: datetime
    ) -> IdempotentScheduleCreationResult:
        snapshot = self._session.snapshot()
        scope = self._session.scope
        common = {
            "public_id": public_id,
            "guild_id": scope.guild_id,
            "channel_id": scope.channel_id,
            "creator_user_id": scope.owner_user_id,
            "content": self._session.accepted_draft.value,
            "allow_duplicate": snapshot.allow_duplicate,
            "now": now,
        }
        if snapshot.schedule_type is ScheduleType.ONCE:
            value = snapshot.validated_input
            if not isinstance(value, PostDraftOnceScheduleInput):
                raise TypeError("invalid once schedule input")
            return await self._port.create_once(
                **common,
                scheduled_for=value.scheduled_at.astimezone(UTC),
            )
        value = snapshot.validated_input
        if snapshot.schedule_type is ScheduleType.DAILY and isinstance(
            value, PostDraftDailyScheduleInput
        ):
            return await self._port.create_recurring(
                **common,
                schedule_type=ScheduleType.DAILY,
                local_time=value.local_time,
                weekday=None,
                end_date=value.end_date,
            )
        if snapshot.schedule_type is ScheduleType.WEEKLY and isinstance(
            value, PostDraftWeeklyScheduleInput
        ):
            return await self._port.create_recurring(
                **common,
                schedule_type=ScheduleType.WEEKLY,
                local_time=value.local_time,
                weekday=value.weekday,
                end_date=value.end_date,
            )
        raise TypeError("invalid recurring schedule input")


class PostDraftScheduleComposition:
    __slots__ = ("_port", "_public_id_factory")

    def __init__(
        self,
        *,
        port: PostDraftSchedulePort,
        public_id_factory: Callable[[], ScheduleCreationPublicId],
    ) -> None:
        if port is None or not callable(getattr(port, "create_once", None)):
            raise TypeError("invalid post draft schedule port")
        if not callable(getattr(port, "create_recurring", None)):
            raise TypeError("invalid post draft schedule port")
        if public_id_factory is None or not callable(public_id_factory):
            raise TypeError("invalid post draft schedule public id factory")
        self._port = port
        self._public_id_factory = public_id_factory

    def start(
        self,
        *,
        scope: PostDraftScheduleScope,
        accepted_draft: GeneratedPostDraft,
    ) -> PostDraftScheduleController:
        if not isinstance(scope, PostDraftScheduleScope):
            raise TypeError("invalid post draft schedule scope")
        if not isinstance(accepted_draft, GeneratedPostDraft):
            raise TypeError("invalid post draft accepted draft")
        session = PostDraftScheduleSession(scope=scope, accepted_draft=accepted_draft)
        return PostDraftScheduleController(
            session=session,
            port=self._port,
            public_id_factory=self._public_id_factory,
        )
