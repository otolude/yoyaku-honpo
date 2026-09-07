"""Application boundaries for scheduling an accepted post draft."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import StrEnum
from typing import Protocol

from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
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
    __slots__ = ("_accepted_draft", "_schedule_type", "_scope", "_state", "_validated_input")

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
        self._state = PostDraftScheduleState.FINAL_CONFIRMATION

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
