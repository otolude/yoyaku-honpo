"""Read-only schedule queries for Discord command presentation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.infrastructure.database.exceptions import RepositoryNotFoundError
from discord_ai_reminder_bot.infrastructure.database.models import Schedule
from discord_ai_reminder_bot.infrastructure.database.repositories import ScheduleRepository

SCHEDULES_PER_PAGE = 10
# Discord integer command options are bounded to JavaScript's exact integer range.
MAX_PAGE_NUMBER = 9_007_199_254_740_991


class InvalidScheduleQueryError(ValueError):
    """A public command query failed safe input validation."""


@dataclass(frozen=True)
class ScheduleView:
    public_id: uuid.UUID
    channel_id: int
    creator_user_id: int
    schedule_type: ScheduleType
    status: ScheduleStatus
    content: str | None
    next_run_at: datetime | None
    local_time: time | None
    weekday: int | None
    end_date: date | None


def parse_public_id(value: str) -> uuid.UUID:
    """Accept only canonical UUIDv7 identifiers used by schedules."""
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise InvalidScheduleQueryError("invalid public schedule id") from error
    if str(parsed) != value.lower() or parsed.version != 7:
        raise InvalidScheduleQueryError("invalid public schedule id")
    return parsed


class ScheduleQueryService:
    """Open a short read-only Session and return ORM-independent values."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_schedules(
        self,
        *,
        guild_id: int,
        requester_user_id: int,
        administrator: bool,
        status: ScheduleStatus | None,
        page: int,
    ) -> list[ScheduleView]:
        _validate_query_ids(guild_id=guild_id, requester_user_id=requester_user_id)
        if isinstance(page, bool) or not 1 <= page <= MAX_PAGE_NUMBER:
            raise InvalidScheduleQueryError("invalid page")
        offset = (page - 1) * SCHEDULES_PER_PAGE
        async with self._session_factory() as session:
            repository = ScheduleRepository(session)
            arguments = {
                "guild_id": guild_id,
                "status": status,
                "limit": SCHEDULES_PER_PAGE,
                "offset": offset,
                "exclude_deleted": status is None,
            }
            if administrator:
                schedules = await repository.list_by_guild(**arguments)
            else:
                schedules = await repository.list_by_creator(
                    creator_user_id=requester_user_id,
                    **arguments,
                )
            return [_to_view(schedule) for schedule in schedules]

    async def show_schedule(
        self,
        *,
        guild_id: int,
        requester_user_id: int,
        administrator: bool,
        public_id: str,
    ) -> ScheduleView | None:
        _validate_query_ids(guild_id=guild_id, requester_user_id=requester_user_id)
        parsed_id = parse_public_id(public_id)
        async with self._session_factory() as session:
            try:
                schedule = await ScheduleRepository(session).get_by_public_id(
                    guild_id=guild_id,
                    public_id=parsed_id,
                )
            except RepositoryNotFoundError:
                return None
            if not administrator and schedule.creator_user_id != requester_user_id:
                return None
            return _to_view(schedule)


def _validate_query_ids(*, guild_id: int, requester_user_id: int) -> None:
    for value in (guild_id, requester_user_id):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise InvalidScheduleQueryError("invalid query boundary")


def _to_view(schedule: Schedule) -> ScheduleView:
    return ScheduleView(
        public_id=schedule.public_id,
        channel_id=schedule.channel_id,
        creator_user_id=schedule.creator_user_id,
        schedule_type=ScheduleType(schedule.schedule_type),
        status=ScheduleStatus(schedule.status),
        content=schedule.content,
        next_run_at=schedule.next_run_at,
        local_time=schedule.local_time,
        weekday=schedule.weekday,
        end_date=schedule.end_date,
    )
