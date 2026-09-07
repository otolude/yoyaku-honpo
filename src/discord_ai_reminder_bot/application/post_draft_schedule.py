"""Application boundaries for scheduling an accepted post draft."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Protocol

from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
    IdempotentScheduleCreationResult,
    ScheduleCreationPublicId,
)
from discord_ai_reminder_bot.domain.enums import ScheduleType


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
