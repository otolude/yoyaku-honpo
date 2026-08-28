from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from discord_ai_reminder_bot.application.schedule_queries import (
    MAX_PAGE_NUMBER,
    InvalidScheduleQueryError,
    ScheduleQueryService,
    parse_public_id,
)
from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.infrastructure.database.exceptions import RepositoryNotFoundError
from discord_ai_reminder_bot.infrastructure.database.models import Schedule


def schedule(*, creator_user_id: int = 20, guild_id: int = 10) -> Schedule:
    return Schedule(
        public_id=uuid.uuid7(),
        guild_id=guild_id,
        channel_id=30,
        creator_user_id=creator_user_id,
        schedule_type="once",
        status="active",
        content="本文",
        next_run_at=datetime(2026, 8, 20, 10, 0, tzinfo=UTC),
    )


class FakeSession:
    def __init__(self) -> None:
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


@pytest.mark.asyncio
async def test_creator_list_is_scoped_and_uses_stable_page_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    repository = AsyncMock()
    repository.list_by_creator.return_value = [schedule()]
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_queries.ScheduleRepository",
        lambda unused: repository,
    )
    service = ScheduleQueryService(lambda: session)  # type: ignore[arg-type]

    result = await service.list_schedules(
        guild_id=10,
        requester_user_id=20,
        administrator=False,
        status=None,
        schedule_type=None,
        page=3,
    )

    assert len(result) == 1
    repository.list_by_creator.assert_awaited_once_with(
        guild_id=10,
        creator_user_id=20,
        status=None,
        schedule_type=None,
        limit=10,
        offset=20,
        exclude_deleted=True,
    )
    repository.list_by_guild.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_administrator_list_can_include_explicit_deleted_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AsyncMock()
    repository.list_by_guild.return_value = []
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_queries.ScheduleRepository",
        lambda unused: repository,
    )
    service = ScheduleQueryService(lambda: FakeSession())  # type: ignore[arg-type]
    await service.list_schedules(
        guild_id=10,
        requester_user_id=99,
        administrator=True,
        status=ScheduleStatus.DELETED,
        schedule_type=None,
        page=1,
    )
    repository.list_by_guild.assert_awaited_once_with(
        guild_id=10,
        status=ScheduleStatus.DELETED,
        schedule_type=None,
        limit=10,
        offset=0,
        exclude_deleted=False,
    )


@pytest.mark.asyncio
async def test_page_counts_with_same_creator_filter_and_clamps(monkeypatch) -> None:
    repository = AsyncMock()
    repository.count_by_creator.return_value = 24
    repository.list_by_creator.return_value = [schedule()]
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_queries.ScheduleRepository",
        lambda unused: repository,
    )
    result = await ScheduleQueryService(lambda: FakeSession()).get_schedule_page(  # type: ignore[arg-type]
        guild_id=10,
        requester_user_id=20,
        administrator=False,
        status=ScheduleStatus.PAUSED,
        schedule_type=None,
        page=99,
        clamp=True,
    )
    assert (result.page, result.total_count, result.total_pages) == (3, 24, 3)
    repository.count_by_creator.assert_awaited_once_with(
        guild_id=10,
        creator_user_id=20,
        status=ScheduleStatus.PAUSED,
        schedule_type=None,
        exclude_deleted=False,
    )
    repository.list_by_creator.assert_awaited_once_with(
        guild_id=10,
        creator_user_id=20,
        status=ScheduleStatus.PAUSED,
        schedule_type=None,
        exclude_deleted=False,
        limit=10,
        offset=20,
    )


@pytest.mark.asyncio
async def test_schedule_type_is_identical_for_count_and_page(monkeypatch) -> None:
    repository = AsyncMock()
    repository.count_by_guild.return_value = 11
    repository.list_by_guild.return_value = [schedule()]
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_queries.ScheduleRepository",
        lambda unused: repository,
    )
    result = await ScheduleQueryService(lambda: FakeSession()).get_schedule_page(  # type: ignore[arg-type]
        guild_id=10,
        requester_user_id=20,
        administrator=True,
        status=ScheduleStatus.PAUSED,
        schedule_type=ScheduleType.DAILY,
        page=2,
    )
    assert (result.total_count, result.total_pages) == (11, 2)
    common = {
        "guild_id": 10,
        "status": ScheduleStatus.PAUSED,
        "schedule_type": ScheduleType.DAILY,
        "exclude_deleted": False,
    }
    repository.count_by_guild.assert_awaited_once_with(**common)
    repository.list_by_guild.assert_awaited_once_with(**common, limit=10, offset=10)


@pytest.mark.parametrize("page", [0, -1, MAX_PAGE_NUMBER + 1, True])
@pytest.mark.asyncio
async def test_invalid_page_is_rejected_before_query(page: int) -> None:
    service = ScheduleQueryService(lambda: FakeSession())  # type: ignore[arg-type]
    with pytest.raises(InvalidScheduleQueryError):
        await service.list_schedules(
            guild_id=10,
            requester_user_id=20,
            administrator=False,
            status=None,
            page=page,
        )


@pytest.mark.asyncio
async def test_show_uses_guild_public_id_and_enforces_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = schedule(creator_user_id=77)
    repository = AsyncMock()
    repository.get_by_public_id.return_value = other
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_queries.ScheduleRepository",
        lambda unused: repository,
    )
    service = ScheduleQueryService(lambda: FakeSession())  # type: ignore[arg-type]

    denied = await service.show_schedule(
        guild_id=10,
        requester_user_id=20,
        administrator=False,
        public_id=str(other.public_id),
    )
    allowed = await service.show_schedule(
        guild_id=10,
        requester_user_id=20,
        administrator=True,
        public_id=str(other.public_id),
    )

    assert denied is None
    assert allowed is not None
    repository.get_by_public_id.assert_awaited_with(guild_id=10, public_id=other.public_id)


@pytest.mark.asyncio
async def test_missing_show_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = AsyncMock()
    repository.get_by_public_id.side_effect = RepositoryNotFoundError("missing")
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_queries.ScheduleRepository",
        lambda unused: repository,
    )
    service = ScheduleQueryService(lambda: FakeSession())  # type: ignore[arg-type]
    assert (
        await service.show_schedule(
            guild_id=10,
            requester_user_id=20,
            administrator=False,
            public_id=str(uuid.uuid7()),
        )
        is None
    )


@pytest.mark.parametrize(
    "value", ["not-a-uuid", str(uuid.uuid4()), "{00000000-0000-7000-8000-000000000000}"]
)
def test_public_id_requires_canonical_uuid7(value: str) -> None:
    with pytest.raises(InvalidScheduleQueryError):
        parse_public_id(value)
