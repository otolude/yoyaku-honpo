"""Read-only schedule queries for Discord command presentation."""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.infrastructure.database.exceptions import RepositoryNotFoundError
from discord_ai_reminder_bot.infrastructure.database.models import Schedule
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    ScheduleActionDetailRow,
    ScheduleRepository,
)

SCHEDULES_PER_PAGE = 10
# Discord integer command options are bounded to JavaScript's exact integer range.
MAX_PAGE_NUMBER = 9_007_199_254_740_991
MAX_AUTOCOMPLETE_CHOICES = 25
MAX_AUTOCOMPLETE_INPUT = 100


class ScheduleAutocompleteOperation(StrEnum):
    SHOW = "show"
    EDIT = "edit"
    DELETE = "delete"
    PAUSE = "pause"
    RESUME = "resume"


@dataclass(frozen=True)
class ScheduleAutocompleteView:
    public_id: uuid.UUID
    channel_id: int
    creator_user_id: int
    schedule_type: ScheduleType
    status: ScheduleStatus
    display_at: datetime | None


class InvalidScheduleQueryError(ValueError):
    """A public command query failed safe input validation."""


class ScheduleActionReason(StrEnum):
    AVAILABLE = "available"
    STATUS_OR_TYPE = "status_or_type"
    TIME_WINDOW = "time_window"
    RUN_CONFLICT = "run_conflict"
    ATTEMPT_CONFLICT = "attempt_conflict"


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
    version: int

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version <= 0:
            raise ValueError("schedule version must be a positive integer")


@dataclass(frozen=True)
class ScheduleActionAvailability:
    can_edit: bool
    can_pause: bool
    can_resume: bool
    can_delete: bool
    reason_code: ScheduleActionReason
    observed_version: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.observed_version, bool)
            or not isinstance(self.observed_version, int)
            or self.observed_version <= 0
        ):
            raise ValueError("observed version must be a positive integer")


@dataclass(frozen=True)
class ScheduleDetail:
    schedule: ScheduleView
    actions: ScheduleActionAvailability

    def __post_init__(self) -> None:
        if self.schedule.version != self.actions.observed_version:
            raise ValueError("detail versions must match")


@dataclass(frozen=True)
class SchedulePage:
    schedules: tuple[ScheduleView, ...]
    page: int
    total_count: int

    @property
    def total_pages(self) -> int:
        return max(1, (self.total_count + SCHEDULES_PER_PAGE - 1) // SCHEDULES_PER_PAGE)


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

    async def autocomplete_schedules(
        self,
        *,
        guild_id: int,
        requester_user_id: int,
        administrator: bool,
        operation: ScheduleAutocompleteOperation,
        current: str,
        channel_ids: frozenset[int] = frozenset(),
        now: datetime,
        limit: int = MAX_AUTOCOMPLETE_CHOICES,
    ) -> tuple[ScheduleAutocompleteView, ...]:
        _validate_query_ids(guild_id=guild_id, requester_user_id=requester_user_id)
        if not isinstance(operation, ScheduleAutocompleteOperation):
            raise InvalidScheduleQueryError("invalid autocomplete operation")
        if isinstance(limit, bool) or not 1 <= limit <= MAX_AUTOCOMPLETE_CHOICES:
            raise InvalidScheduleQueryError("invalid autocomplete limit")
        if not isinstance(channel_ids, frozenset) or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in channel_ids
        ):
            raise InvalidScheduleQueryError("invalid autocomplete channel boundary")
        search = _parse_autocomplete_input(current)
        if search is None:
            if not channel_ids:
                return ()
            search = {}
        async with self._session_factory() as session:
            rows = await ScheduleRepository(session).autocomplete_schedules(
                guild_id=guild_id,
                creator_user_id=None if administrator else requester_user_id,
                operation=operation.value,
                now=now,
                limit=limit,
                channel_ids=channel_ids,
                **search,
            )
            views = tuple(
                ScheduleAutocompleteView(
                    public_id=row.public_id,
                    channel_id=row.channel_id,
                    creator_user_id=row.creator_user_id,
                    schedule_type=ScheduleType(row.schedule_type),
                    status=ScheduleStatus(row.status),
                    display_at=row.display_at,
                )
                for row in rows
            )
        return views

    async def list_schedules(
        self,
        *,
        guild_id: int,
        requester_user_id: int,
        administrator: bool,
        status: ScheduleStatus | None,
        page: int,
        schedule_type: ScheduleType | None = None,
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
                "schedule_type": schedule_type,
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

    async def get_schedule_page(
        self,
        *,
        guild_id: int,
        requester_user_id: int,
        administrator: bool,
        status: ScheduleStatus | None,
        page: int,
        schedule_type: ScheduleType | None = None,
        clamp: bool = False,
    ) -> SchedulePage:
        _validate_query_ids(guild_id=guild_id, requester_user_id=requester_user_id)
        if isinstance(page, bool) or not 1 <= page <= MAX_PAGE_NUMBER:
            raise InvalidScheduleQueryError("invalid page")
        async with self._session_factory() as session:
            repository = ScheduleRepository(session)
            common = {
                "guild_id": guild_id,
                "status": status,
                "schedule_type": schedule_type,
                "exclude_deleted": status is None,
            }
            if administrator:
                total = await repository.count_by_guild(**common)
            else:
                total = await repository.count_by_creator(
                    creator_user_id=requester_user_id, **common
                )
            total_pages = max(1, (total + SCHEDULES_PER_PAGE - 1) // SCHEDULES_PER_PAGE)
            effective_page = min(page, total_pages) if clamp else page
            list_args = {
                **common,
                "limit": SCHEDULES_PER_PAGE,
                "offset": (effective_page - 1) * SCHEDULES_PER_PAGE,
            }
            if administrator:
                schedules = await repository.list_by_guild(**list_args)
            else:
                schedules = await repository.list_by_creator(
                    creator_user_id=requester_user_id, **list_args
                )
            return SchedulePage(
                schedules=tuple(_to_view(item) for item in schedules),
                page=effective_page,
                total_count=total,
            )

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

    async def get_schedule_detail(
        self,
        *,
        guild_id: int,
        requester_user_id: int,
        administrator: bool,
        public_id: str,
        now: datetime,
    ) -> ScheduleDetail | None:
        """Return one detached detail and a conservative read-only action observation."""
        _validate_query_ids(guild_id=guild_id, requester_user_id=requester_user_id)
        parsed_id = parse_public_id(public_id)
        now = require_utc(now)
        async with self._session_factory() as session:
            try:
                row = await ScheduleRepository(session).get_action_detail(
                    guild_id=guild_id,
                    public_id=parsed_id,
                )
            except RepositoryNotFoundError:
                return None
            if not administrator and row.creator_user_id != requester_user_id:
                return None
            schedule = ScheduleView(
                public_id=row.public_id,
                channel_id=row.channel_id,
                creator_user_id=row.creator_user_id,
                schedule_type=ScheduleType(row.schedule_type),
                status=ScheduleStatus(row.status),
                content=row.content,
                next_run_at=row.next_run_at,
                local_time=row.local_time,
                weekday=row.weekday,
                end_date=row.end_date,
                version=row.version,
            )
            actions = _action_availability(schedule=schedule, row=row, now=now)
        return ScheduleDetail(schedule=schedule, actions=actions)


def _validate_query_ids(*, guild_id: int, requester_user_id: int) -> None:
    for value in (guild_id, requester_user_id):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise InvalidScheduleQueryError("invalid query boundary")


_TYPE_SEARCHES = {
    "単発": ScheduleType.ONCE,
    "毎日": ScheduleType.DAILY,
    "毎週": ScheduleType.WEEKLY,
    **{item.value: item for item in ScheduleType},
}
_STATUS_SEARCHES = {
    "下書き": ScheduleStatus.DRAFT,
    "有効": ScheduleStatus.ACTIVE,
    "一時停止中": ScheduleStatus.PAUSED,
    "失敗": ScheduleStatus.FAILED,
    "完了": ScheduleStatus.COMPLETED,
    "終了済み": ScheduleStatus.ENDED,
    "削除済み": ScheduleStatus.DELETED,
    **{item.value: item for item in ScheduleStatus},
}
_UUID_PREFIX = re.compile(r"[0-9a-f-]{1,36}")


def _parse_autocomplete_input(current: str) -> dict[str, object] | None:
    if not isinstance(current, str) or len(current) > MAX_AUTOCOMPLETE_INPUT:
        return None
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in current):
        return None
    normalized = current.strip().casefold()
    if not normalized:
        return {}
    if schedule_type := _TYPE_SEARCHES.get(normalized):
        return {"schedule_type": schedule_type}
    if status := _STATUS_SEARCHES.get(normalized):
        return {"status": status}
    if normalized.isascii() and normalized.isdecimal() and 17 <= len(normalized) <= 20:
        channel_id = int(normalized)
        return {"channel_id": channel_id} if 0 < channel_id <= 9_223_372_036_854_775_807 else None
    if _UUID_PREFIX.fullmatch(normalized) and _is_canonical_uuid_prefix(normalized):
        if len(normalized) == 36:
            try:
                parse_public_id(normalized)
            except InvalidScheduleQueryError:
                return None
        return {"uuid_prefix": normalized}
    return None


def _is_canonical_uuid_prefix(value: str) -> bool:
    template = "xxxxxxxx-xxxx-7xxx-vxxx-xxxxxxxxxxxx"
    for index, character in enumerate(value):
        if template[index] == "-":
            if character != "-":
                return False
        elif template[index] == "7":
            if character != "7":
                return False
        elif template[index] == "v":
            if character not in "89ab":
                return False
        elif character == "-":
            return False
    return True


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
        version=schedule.version,
    )


def _action_availability(
    *, schedule: ScheduleView, row: ScheduleActionDetailRow, now: datetime
) -> ScheduleActionAvailability:
    """Evaluate display-only action hints; mutation services remain authoritative."""
    recurring = schedule.schedule_type in {ScheduleType.DAILY, ScheduleType.WEEKLY}
    editable_status = schedule.status in {
        ScheduleStatus.DRAFT,
        ScheduleStatus.ACTIVE,
        ScheduleStatus.PAUSED,
    }
    deletable_status = schedule.status in {
        ScheduleStatus.DRAFT,
        ScheduleStatus.ACTIVE,
        ScheduleStatus.PAUSED,
        ScheduleStatus.FAILED,
    }
    attempt_conflict = row.unsafe_attempt_count > 0
    processing_conflict = row.processing_run_count > 0
    current_required = schedule.status in {ScheduleStatus.DRAFT, ScheduleStatus.ACTIVE}
    current_consistent = (
        schedule.next_run_at is not None
        and row.current_run_count == 1
        and row.current_pending_count == 1
    )
    current_conflict = current_required and not current_consistent
    common_conflict = attempt_conflict or processing_conflict or current_conflict

    can_edit = editable_status and not common_conflict
    if schedule.status is ScheduleStatus.PAUSED:
        can_edit = can_edit and recurring and schedule.next_run_at is None
    else:
        can_edit = bool(
            can_edit
            and schedule.next_run_at is not None
            and schedule.next_run_at >= now + timedelta(minutes=5)
        )

    can_pause = bool(
        schedule.status is ScheduleStatus.ACTIVE
        and recurring
        and current_consistent
        and not attempt_conflict
        and not processing_conflict
    )
    can_resume = bool(
        schedule.status is ScheduleStatus.PAUSED
        and recurring
        and schedule.next_run_at is None
        and row.pending_run_count <= 1
        and row.non_pristine_pending_count == 0
        and not attempt_conflict
        and not processing_conflict
    )
    can_delete = deletable_status and not attempt_conflict and not processing_conflict
    if schedule.status is ScheduleStatus.PAUSED:
        can_delete = can_delete and recurring and schedule.next_run_at is None
    elif schedule.status is ScheduleStatus.FAILED:
        can_delete = can_delete and schedule.schedule_type is ScheduleType.ONCE
    if current_required:
        can_delete = can_delete and current_consistent

    if attempt_conflict:
        reason = ScheduleActionReason.ATTEMPT_CONFLICT
    elif (
        processing_conflict
        or current_conflict
        or (
            schedule.status is ScheduleStatus.PAUSED
            and (row.pending_run_count > 1 or row.non_pristine_pending_count > 0)
        )
    ):
        reason = ScheduleActionReason.RUN_CONFLICT
    elif (
        editable_status
        and schedule.status is not ScheduleStatus.PAUSED
        and current_consistent
        and schedule.next_run_at is not None
        and schedule.next_run_at < now + timedelta(minutes=5)
    ):
        reason = ScheduleActionReason.TIME_WINDOW
    elif not any((can_edit, can_pause, can_resume, can_delete)):
        reason = ScheduleActionReason.STATUS_OR_TYPE
    else:
        reason = ScheduleActionReason.AVAILABLE
    return ScheduleActionAvailability(
        can_edit=can_edit,
        can_pause=can_pause,
        can_resume=can_resume,
        can_delete=can_delete,
        reason_code=reason,
        observed_version=schedule.version,
    )
