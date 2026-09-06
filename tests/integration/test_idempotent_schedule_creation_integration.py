import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from discord_ai_reminder_bot.application.schedule_creation import (
    IdempotentScheduleCreationCode,
    IdempotentScheduleCreationService,
    ScheduleCreationPublicId,
)
from discord_ai_reminder_bot.infrastructure.database.idempotent_schedule_creation_repository import (
    PostgreSQLIdempotentScheduleCreationRepository,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    OperationLog,
    Schedule,
    ScheduleRun,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)


def service(engine: AsyncEngine) -> IdempotentScheduleCreationService:
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    return IdempotentScheduleCreationService(
        PostgreSQLIdempotentScheduleCreationRepository(sessions)
    )


def arguments(public_id: ScheduleCreationPublicId, *, content: str = "body") -> dict:
    return {
        "public_id": public_id,
        "guild_id": 91_000,
        "channel_id": 92_000,
        "creator_user_id": 93_000,
        "scheduled_for": NOW + timedelta(hours=1),
        "content": content,
        "allow_duplicate": False,
        "now": NOW,
    }


async def counts(engine: AsyncEngine) -> tuple[int, int, int]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        return (
            int(await session.scalar(select(func.count()).select_from(Schedule)) or 0),
            int(await session.scalar(select(func.count()).select_from(ScheduleRun)) or 0),
            int(await session.scalar(select(func.count()).select_from(OperationLog)) or 0),
        )


async def test_serial_replay_creates_each_business_row_once(test_engine: AsyncEngine) -> None:
    creator = service(test_engine)
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    first = await creator.create_once(**arguments(key))
    second = await creator.create_once(**arguments(key))
    assert (first.code, second.code) == (
        IdempotentScheduleCreationCode.CREATED,
        IdempotentScheduleCreationCode.ALREADY_CREATED,
    )
    assert await counts(test_engine) == (1, 1, 1)


async def test_parallel_replay_has_one_created_and_one_already_created(
    test_engine: AsyncEngine,
) -> None:
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    first, second = await asyncio.wait_for(
        asyncio.gather(
            service(test_engine).create_once(**arguments(key)),
            service(test_engine).create_once(**arguments(key)),
        ),
        timeout=5,
    )
    assert {first.code, second.code} == {
        IdempotentScheduleCreationCode.CREATED,
        IdempotentScheduleCreationCode.ALREADY_CREATED,
    }
    assert await counts(test_engine) == (1, 1, 1)


async def test_same_key_with_different_content_is_conflict_without_update(
    test_engine: AsyncEngine,
) -> None:
    creator = service(test_engine)
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    assert (
        await creator.create_once(**arguments(key))
    ).code is IdempotentScheduleCreationCode.CREATED
    assert (
        await creator.create_once(**arguments(key, content="different"))
    ).code is IdempotentScheduleCreationCode.CONFLICT
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions() as session:
        assert await session.scalar(select(Schedule.content)) == "body"
    assert await counts(test_engine) == (1, 1, 1)


async def test_failed_transaction_leaves_no_partial_business_rows(
    test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = PostgreSQLIdempotentScheduleCreationRepository(
        async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)
    )
    original = repository._add_creation_log

    async def fail_after_schedule(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("fixed-test-failure")

    monkeypatch.setattr(repository, "_add_creation_log", fail_after_schedule)
    result = await IdempotentScheduleCreationService(repository).create_once(
        **arguments(ScheduleCreationPublicId.create(uuid.uuid7()))
    )
    assert result.code is IdempotentScheduleCreationCode.UNKNOWN
    assert await counts(test_engine) == (0, 0, 0)
