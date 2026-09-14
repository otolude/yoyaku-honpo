from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

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
from discord_ai_reminder_bot.application.post_draft_schedule import (
    PostDraftOnceScheduleInput,
    PostDraftScheduleComposition,
    PostDraftScheduleScope,
)
from discord_ai_reminder_bot.application.schedule_creation import (
    IdempotentScheduleCreationService,
)
from discord_ai_reminder_bot.application.worker import PollingWorker
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.domain.enums import ScheduleType
from discord_ai_reminder_bot.domain.post_draft_generation import (
    MAX_GENERATED_POST_CHARACTERS,
    GeneratedPostDraft,
)
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

DELIVERY_AT = datetime(2026, 9, 20, 12, 30, tzinfo=UTC)
CREATED_AT = DELIVERY_AT - timedelta(minutes=5)
BODY = "界" * MAX_GENERATED_POST_CHARACTERS
GUILD_ID = 52_001
CHANNEL_ID = 52_002
OWNER_USER_ID = 52_003
MESSAGE_ID = 52_004


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


def _same_body(actual: object) -> bool:
    return isinstance(actual, str) and actual == BODY


async def test_two_thousand_character_body_is_stored_and_delivered_once(
    test_engine: AsyncEngine,
    lifecycle_creation_key: ScheduleCreationPublicId,
) -> None:
    baseline_tasks = {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}
    scope = PostDraftScheduleScope(
        owner_user_id=OWNER_USER_ID,
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
    )

    assert len(BODY) == 2_000  # 1
    draft = GeneratedPostDraft(BODY)
    assert _same_body(draft.value) and len(draft.value) == 2_000  # 2

    controller = PostDraftScheduleComposition(
        port=_creation_service(test_engine),
        public_id_factory=lambda: lifecycle_creation_key,
    ).start(scope=scope, accepted_draft=draft)
    controller.session.select_type(ScheduleType.ONCE)
    controller.session.set_validated_input(PostDraftOnceScheduleInput(scheduled_at=DELIVERY_AT))
    created = await controller.confirm(now=CREATED_AT)
    assert created.code is IdempotentScheduleCreationCode.CREATED  # 3

    sessions = _session_factory(test_engine)
    async with sessions() as session:
        initial_schedule_count = await session.scalar(
            select(func.count())
            .select_from(Schedule)
            .where(Schedule.public_id == lifecycle_creation_key.value)
        )
        initial_schedule = (
            await session.execute(
                select(
                    Schedule.id,
                    Schedule.schedule_type,
                    Schedule.status,
                    Schedule.content,
                    func.char_length(Schedule.content).label("content_length"),
                ).where(Schedule.public_id == lifecycle_creation_key.value)
            )
        ).one()
        initial_runs = (
            await session.execute(
                select(
                    ScheduleRun.id,
                    ScheduleRun.status,
                    ScheduleRun.attempt_count,
                    ScheduleRun.next_attempt_at,
                    ScheduleRun.scheduled_for,
                ).where(ScheduleRun.schedule_id == initial_schedule.id)
            )
        ).all()
        initial_operations = (
            (
                await session.execute(
                    select(OperationLog.action)
                    .where(OperationLog.schedule_id == initial_schedule.id)
                    .order_by(OperationLog.id)
                )
            )
            .scalars()
            .all()
        )

    assert (
        initial_schedule_count == 1
        and initial_schedule.schedule_type == ScheduleType.ONCE.value
        and initial_schedule.status == "active"
    )  # 4
    assert len(initial_runs) == 1  # 5
    assert len(initial_operations) == 1  # 6
    assert initial_operations == ["created"]  # 7
    assert _same_body(initial_schedule.content)  # 8
    assert initial_schedule.content_length == 2_000  # 9
    assert (
        initial_runs[0].status == "pending"
        and initial_runs[0].attempt_count == 0
        and initial_runs[0].scheduled_for == DELIVERY_AT
        and initial_runs[0].next_attempt_at == DELIVERY_AT
    )  # 10

    gateway, channel = _discord_gateway()
    worker_result = await PollingWorker(
        session_factory=sessions,
        gateway=gateway,
        clock=FixedClock(DELIVERY_AT),
        worker_id=uuid.uuid7(),
        batch_size=1,
        max_concurrency=1,
        lease_timeout=timedelta(minutes=2),
        logger=logging.getLogger("test.post-draft-2000-character-lifecycle"),
    ).poll_once()
    assert (
        worker_result.claimed,
        worker_result.succeeded,
        worker_result.failed,
        worker_result.unknown,
        worker_result.skipped,
        worker_result.internal_errors,
    ) == (1, 1, 0, 0, 0, 0)  # 11
    assert worker_result.retry_scheduled == 0  # 12
    assert channel.send.await_count == 1  # 13
    assert channel.send.await_count - 1 == 0  # 14
    sent_args, sent_kwargs = channel.send.await_args
    assert _same_body(sent_args[0])  # 15
    assert len(sent_args[0]) == 2_000  # 16
    assert len(sent_args) == 1 and "content" not in sent_kwargs  # 17
    assert set(sent_kwargs) == {"allowed_mentions"}  # 18
    mentions = sent_kwargs["allowed_mentions"]
    assert (
        mentions.everyone is False
        and mentions.users is False
        and mentions.roles is False
        and mentions.replied_user is False
    )  # 19

    async with sessions() as session:
        final_schedule_count = await session.scalar(
            select(func.count())
            .select_from(Schedule)
            .where(Schedule.public_id == lifecycle_creation_key.value)
        )
        final_schedule = (
            await session.execute(
                select(
                    Schedule.id,
                    Schedule.status,
                    Schedule.content,
                    Schedule.next_run_at,
                ).where(Schedule.public_id == lifecycle_creation_key.value)
            )
        ).one()
        final_runs = (
            await session.execute(
                select(
                    ScheduleRun.id,
                    ScheduleRun.status,
                    ScheduleRun.next_attempt_at,
                ).where(ScheduleRun.schedule_id == final_schedule.id)
            )
        ).all()
        run_ids = [run.id for run in final_runs]
        attempts = (
            await session.execute(
                select(
                    DeliveryAttempt.status,
                    DeliveryAttempt.attempt_number,
                ).where(DeliveryAttempt.schedule_run_id.in_(run_ids))
            )
        ).all()
        final_operations = (
            (
                await session.execute(
                    select(OperationLog.action)
                    .where(OperationLog.schedule_id == final_schedule.id)
                    .order_by(OperationLog.id)
                )
            )
            .scalars()
            .all()
        )

    assert (
        final_schedule_count == 1
        and final_schedule.status == "completed"
        and _same_body(final_schedule.content)
        and final_schedule.next_run_at is None
    )  # 20
    assert (
        len(final_runs) == 1
        and final_runs[0].status == "succeeded"
        and final_runs[0].next_attempt_at is None
    )  # 21
    assert (
        len(attempts) == 1 and attempts[0].status == "succeeded" and attempts[0].attempt_number == 1
    )  # 22
    assert final_operations == ["created", "completed"]  # 23

    assert test_engine.pool.checkedout() == 0
    assert {
        task for task in asyncio.all_tasks() if task is not asyncio.current_task()
    } == baseline_tasks
