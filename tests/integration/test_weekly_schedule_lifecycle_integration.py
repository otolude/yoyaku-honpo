from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
    IdempotentScheduleCreationCode,
    ScheduleCreationPublicId,
    ScheduleCreationWaitLimit,
)
from discord_ai_reminder_bot.application.post_draft_schedule import PostDraftScheduleScope
from discord_ai_reminder_bot.application.schedule_creation import IdempotentScheduleCreationService
from discord_ai_reminder_bot.application.worker import PollingWorker
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.domain.enums import ScheduleType
from discord_ai_reminder_bot.infrastructure.database.idempotent_schedule_creation_repository import (
    PostgreSQLIdempotentScheduleCreationRepository,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    NameGenerationJob,
    NotificationAttempt,
    NotificationLog,
    OperationLog,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.discord.gateway import DiscordMessageGateway

pytestmark = pytest.mark.asyncio

TOKYO = ZoneInfo("Asia/Tokyo")
DELIVERY_AT = datetime(2026, 9, 20, 12, 30, tzinfo=UTC)
CREATED_AT = DELIVERY_AT - timedelta(minutes=5)
NEXT_DELIVERY_AT = datetime(2026, 9, 27, 12, 30, tzinfo=UTC)
LOCAL_TIME = time(21, 30)
WEEKDAY = 6
BODY = "synthetic weekly lifecycle body"
GUILD_ID = 51_001
CHANNEL_ID = 51_002
OWNER_USER_ID = 51_003
MESSAGE_ID = 51_004


def _session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def lifecycle_creation_key(
    test_engine: AsyncEngine,
) -> AsyncIterator[ScheduleCreationPublicId]:
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    yield key

    sessions = _session_factory(test_engine)
    async with sessions() as session, session.begin():
        schedule_ids = select(Schedule.id).where(Schedule.public_id == key.value)
        run_ids = select(ScheduleRun.id).where(ScheduleRun.schedule_id.in_(schedule_ids))
        notification_ids = select(NotificationLog.id).where(
            NotificationLog.schedule_id.in_(schedule_ids)
        )
        await session.execute(
            delete(NotificationAttempt).where(
                NotificationAttempt.notification_log_id.in_(notification_ids)
            )
        )
        await session.execute(
            delete(NotificationLog).where(NotificationLog.id.in_(notification_ids))
        )
        await session.execute(
            delete(DeliveryAttempt).where(DeliveryAttempt.schedule_run_id.in_(run_ids))
        )
        await session.execute(
            delete(NameGenerationJob).where(NameGenerationJob.schedule_id.in_(schedule_ids))
        )
        await session.execute(
            delete(OperationLog).where(OperationLog.schedule_id.in_(schedule_ids))
        )
        await session.execute(delete(ScheduleRun).where(ScheduleRun.id.in_(run_ids)))
        await session.execute(delete(Schedule).where(Schedule.id.in_(schedule_ids)))


def _creation_service(engine: AsyncEngine) -> IdempotentScheduleCreationService:
    repository = PostgreSQLIdempotentScheduleCreationRepository(
        _session_factory(engine),
        wait_limit=ScheduleCreationWaitLimit.create(0.5),
    )
    return IdempotentScheduleCreationService(repository)


def _discord_gateway() -> tuple[DiscordMessageGateway, MagicMock]:
    client = MagicMock(spec=discord.Client)
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD_ID
    guild.me = MagicMock(spec=discord.Member)
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild = guild
    permissions = MagicMock(spec=discord.Permissions)
    permissions.view_channel = True
    permissions.send_messages = True
    channel.permissions_for.return_value = permissions
    channel.send = AsyncMock(return_value=MagicMock(id=MESSAGE_ID))
    guild.get_channel.return_value = channel
    client.get_guild.return_value = guild
    return (
        DiscordMessageGateway(
            client=client,
            configured_guild_id=GUILD_ID,
            clock=FixedClock(DELIVERY_AT),
        ),
        channel,
    )


def _same_body(actual: str) -> bool:
    return secrets.compare_digest(actual, BODY)


def _same_scope(actual: tuple[int, int, int], expected: PostDraftScheduleScope) -> bool:
    return actual == (expected.guild_id, expected.channel_id, expected.owner_user_id)


async def test_weekly_sunday_delivery_creates_next_pending_run(
    test_engine: AsyncEngine,
    lifecycle_creation_key: ScheduleCreationPublicId,
) -> None:
    baseline_tasks = {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}
    scope = PostDraftScheduleScope(
        owner_user_id=OWNER_USER_ID,
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
    )
    creator = _creation_service(test_engine)
    creation_arguments = {
        "public_id": lifecycle_creation_key,
        "guild_id": scope.guild_id,
        "channel_id": scope.channel_id,
        "creator_user_id": scope.owner_user_id,
        "schedule_type": ScheduleType.WEEKLY,
        "local_time": LOCAL_TIME,
        "weekday": WEEKDAY,
        "end_date": None,
        "content": BODY,
        "allow_duplicate": False,
        "now": CREATED_AT,
    }
    created = await creator.create_recurring(**creation_arguments)
    replayed = await creator.create_recurring(**creation_arguments)
    assert created.code is IdempotentScheduleCreationCode.CREATED
    assert replayed.code is IdempotentScheduleCreationCode.ALREADY_CREATED

    gateway, channel = _discord_gateway()
    result = await PollingWorker(
        session_factory=_session_factory(test_engine),
        gateway=gateway,
        clock=FixedClock(DELIVERY_AT),
        worker_id=uuid.uuid7(),
        batch_size=1,
        max_concurrency=1,
        lease_timeout=timedelta(minutes=2),
        logger=logging.getLogger("test.weekly-lifecycle"),
    ).poll_once()
    assert (
        result.claimed,
        result.succeeded,
        result.retry_scheduled,
        result.failed,
        result.unknown,
        result.skipped,
        result.internal_errors,
    ) == (1, 1, 0, 0, 0, 0, 0)

    sessions = _session_factory(test_engine)
    async with sessions() as session:
        schedule_count = await session.scalar(
            select(func.count())
            .select_from(Schedule)
            .where(Schedule.public_id == lifecycle_creation_key.value)
        )
        schedule = (
            await session.execute(
                select(
                    Schedule.id,
                    Schedule.schedule_type,
                    Schedule.status,
                    Schedule.weekday,
                    Schedule.local_time,
                    Schedule.end_date,
                    Schedule.guild_id,
                    Schedule.channel_id,
                    Schedule.creator_user_id,
                ).where(Schedule.public_id == lifecycle_creation_key.value)
            )
        ).one()
        runs = (
            await session.execute(
                select(
                    ScheduleRun.id,
                    ScheduleRun.scheduled_for,
                    ScheduleRun.status,
                    ScheduleRun.attempt_count,
                    ScheduleRun.next_attempt_at,
                    ScheduleRun.result_code,
                )
                .where(ScheduleRun.schedule_id == schedule.id)
                .order_by(ScheduleRun.scheduled_for)
            )
        ).all()
        run_ids = [run.id for run in runs]
        attempts = (
            await session.execute(
                select(
                    DeliveryAttempt.schedule_run_id,
                    DeliveryAttempt.status,
                    DeliveryAttempt.attempt_number,
                    DeliveryAttempt.error_kind,
                    DeliveryAttempt.error_code,
                ).where(DeliveryAttempt.schedule_run_id.in_(run_ids))
            )
        ).all()
        operations = (
            (
                await session.execute(
                    select(OperationLog.action).where(OperationLog.schedule_id == schedule.id)
                )
            )
            .scalars()
            .all()
        )

    first_run, next_run = runs
    assert schedule_count == 1  # 1
    assert schedule_count - 1 == 0  # 2
    assert schedule.schedule_type == ScheduleType.WEEKLY.value  # 3
    assert schedule.status == "active"  # 4
    assert schedule.weekday == WEEKDAY  # 5
    assert schedule.local_time == LOCAL_TIME  # 6
    assert schedule.end_date is None  # 7
    assert len(runs) == 2  # 8
    assert first_run.status == "succeeded"  # 9
    assert next_run.status == "pending"  # 10
    assert next_run.scheduled_for == NEXT_DELIVERY_AT  # 11
    assert len(attempts) == 1  # 12
    assert attempts[0].status == "succeeded"  # 13
    assert (
        result.retry_scheduled == 0
        and first_run.next_attempt_at is None
        and attempts[0].attempt_number == 1
        and attempts[0].error_kind is None
        and attempts[0].error_code is None
    )  # 14
    assert len(operations) == 1  # 15
    assert operations == ["created"]  # 16
    assert "completed" not in operations  # 17
    assert channel.send.await_count == 1  # 18
    assert channel.send.await_count - 1 == 0  # 19
    sent_args, sent_kwargs = channel.send.await_args
    mentions = sent_kwargs["allowed_mentions"]
    assert (
        mentions.everyone is False
        and mentions.roles is False
        and mentions.users is False
        and mentions.replied_user is False
    )  # 20
    assert _same_body(sent_args[0])  # 21
    assert _same_scope(
        (schedule.guild_id, schedule.channel_id, schedule.creator_user_id), scope
    )  # 22

    next_local = next_run.scheduled_for.astimezone(TOKYO)
    assert (next_local.weekday(), next_local.time().replace(tzinfo=None)) == (WEEKDAY, LOCAL_TIME)
    assert next_run.scheduled_for > first_run.scheduled_for
    assert next_run.attempt_count == 0
    assert next_run.next_attempt_at == next_run.scheduled_for
    assert first_run.result_code == "succeeded"
    assert test_engine.pool.checkedout() == 0
    assert {
        task for task in asyncio.all_tasks() if task is not asyncio.current_task()
    } == baseline_tasks
