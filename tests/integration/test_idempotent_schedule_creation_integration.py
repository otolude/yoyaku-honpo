import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
    ScheduleCreationPublicId,
    ScheduleCreationWaitLimit,
)
from discord_ai_reminder_bot.application.name_generation import (
    NameGenerationRegistrationPolicy,
)
from discord_ai_reminder_bot.application.schedule_creation import (
    IdempotentScheduleCreationCode,
    IdempotentScheduleCreationService,
)
from discord_ai_reminder_bot.domain.enums import (
    DisplayNameSource,
    OperationAction,
    RunStatus,
    ScheduleStatus,
    ScheduleType,
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

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
GUILD_ID = 91_000


@pytest_asyncio.fixture
async def track_creation_key(
    test_engine: AsyncEngine,
) -> AsyncIterator[Callable[[ScheduleCreationPublicId], Awaitable[None]]]:
    """Clean exact keys after ordinary pytest failures; process interruption is out of scope."""
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    tracked: set[uuid.UUID] = set()
    sentinel_public_id = uuid.uuid7()
    async with sessions() as session, session.begin():
        sentinel = Schedule(
            public_id=sentinel_public_id,
            guild_id=GUILD_ID,
            channel_id=99_999,
            creator_user_id=98_888,
            schedule_type=ScheduleType.ONCE.value,
            status=ScheduleStatus.ACTIVE.value,
            content="sentinel",
            display_name=None,
            display_name_source=DisplayNameSource.UNSET.value,
            next_run_at=NOW + timedelta(days=7),
            version=1,
        )
        session.add(sentinel)
        await session.flush()
        session.add(
            ScheduleRun(
                schedule_id=sentinel.id,
                scheduled_for=sentinel.next_run_at,
                status=RunStatus.PENDING.value,
                attempt_count=0,
                next_attempt_at=sentinel.next_run_at,
            )
        )
        session.add(
            OperationLog(
                schedule_id=sentinel.id,
                action=OperationAction.CREATED.value,
                actor_type="user",
                actor_user_id=98_888,
                changes={"sentinel": True},
            )
        )

    async def register(key: ScheduleCreationPublicId) -> None:
        async with sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(Schedule).where(Schedule.public_id == key.value)
            )
        assert count == 0
        tracked.add(key.value)

    try:
        yield register
    finally:
        try:
            async with sessions() as session, session.begin():
                sentinel_before = await session.scalar(
                    select(Schedule.content).where(Schedule.public_id == sentinel_public_id)
                )
                sentinel_log_count_before = await session.scalar(
                    select(func.count())
                    .select_from(OperationLog)
                    .join(Schedule, Schedule.id == OperationLog.schedule_id)
                    .where(Schedule.public_id == sentinel_public_id)
                )
                schedule_ids = list(
                    (
                        await session.scalars(
                            select(Schedule.id).where(Schedule.public_id.in_(tracked))
                        )
                    ).all()
                )
                run_ids = list(
                    (
                        await session.scalars(
                            select(ScheduleRun.id).where(ScheduleRun.schedule_id.in_(schedule_ids))
                        )
                    ).all()
                )
                notification_ids = list(
                    (
                        await session.scalars(
                            select(NotificationLog.id).where(
                                NotificationLog.schedule_id.in_(schedule_ids)
                            )
                        )
                    ).all()
                )
                await _delete_exact(
                    session,
                    NotificationAttempt,
                    NotificationAttempt.notification_log_id.in_(notification_ids),
                )
                await _delete_exact(
                    session,
                    NotificationLog,
                    NotificationLog.id.in_(notification_ids),
                )
                await _delete_exact(
                    session,
                    DeliveryAttempt,
                    DeliveryAttempt.schedule_run_id.in_(run_ids),
                )
                await _delete_exact(
                    session,
                    NameGenerationJob,
                    NameGenerationJob.schedule_id.in_(schedule_ids),
                )
                await _delete_exact(
                    session,
                    OperationLog,
                    OperationLog.schedule_id.in_(schedule_ids),
                )
                unexpected_operations = await session.scalar(
                    select(func.count())
                    .select_from(OperationLog)
                    .where(OperationLog.schedule_id.in_(schedule_ids))
                )
                assert unexpected_operations == 0
                await _delete_exact(
                    session,
                    ScheduleRun,
                    ScheduleRun.id.in_(run_ids),
                )
                await _delete_exact(session, Schedule, Schedule.id.in_(schedule_ids))
                sentinel_after = await session.scalar(
                    select(Schedule.content).where(Schedule.public_id == sentinel_public_id)
                )
                sentinel_log_count_after = await session.scalar(
                    select(func.count())
                    .select_from(OperationLog)
                    .join(Schedule, Schedule.id == OperationLog.schedule_id)
                    .where(Schedule.public_id == sentinel_public_id)
                )
                assert sentinel_before == sentinel_after == "sentinel"
                assert sentinel_log_count_before == sentinel_log_count_after == 1
        finally:
            async with sessions() as session, session.begin():
                sentinel_id = await session.scalar(
                    select(Schedule.id).where(Schedule.public_id == sentinel_public_id)
                )
                if sentinel_id is not None:
                    await session.execute(
                        delete(OperationLog).where(OperationLog.schedule_id == sentinel_id)
                    )
                    await session.execute(
                        delete(ScheduleRun).where(ScheduleRun.schedule_id == sentinel_id)
                    )
                    await session.execute(delete(Schedule).where(Schedule.id == sentinel_id))


async def _delete_exact(session, model, condition) -> None:
    expected = int(
        await session.scalar(select(func.count()).select_from(model).where(condition)) or 0
    )
    result = await session.execute(delete(model).where(condition))
    assert result.rowcount == expected


def service(
    engine: AsyncEngine,
    *,
    wait_seconds: float = 0.5,
    expire_on_commit: bool = False,
    configured_guild_id: int | None = None,
    name_generation_policy: NameGenerationRegistrationPolicy | None = None,
    session_type: type[AsyncSession] = AsyncSession,
    session_kwargs: dict[str, object] | None = None,
) -> IdempotentScheduleCreationService:
    sessions = async_sessionmaker(
        engine,
        class_=session_type,
        expire_on_commit=expire_on_commit,
        autoflush=False,
        **(session_kwargs or {}),
    )
    repository = PostgreSQLIdempotentScheduleCreationRepository(
        sessions,
        wait_limit=ScheduleCreationWaitLimit.create(wait_seconds),
    )
    return IdempotentScheduleCreationService(
        repository,
        configured_guild_id=configured_guild_id,
        name_generation_policy=name_generation_policy,
    )


def once_arguments(public_id: ScheduleCreationPublicId, *, content: str | None = "body") -> dict:
    return {
        "public_id": public_id,
        "guild_id": GUILD_ID,
        "channel_id": 92_000,
        "creator_user_id": 93_000,
        "scheduled_for": NOW + timedelta(hours=25),
        "content": content,
        "allow_duplicate": False,
        "now": NOW,
    }


async def graph_counts(engine: AsyncEngine, key: ScheduleCreationPublicId) -> tuple[int, ...]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        schedule_ids = select(Schedule.id).where(Schedule.public_id == key.value)
        run_ids = select(ScheduleRun.id).where(ScheduleRun.schedule_id.in_(schedule_ids))
        notification_ids = select(NotificationLog.id).where(
            NotificationLog.schedule_id.in_(schedule_ids)
        )
        models_and_conditions = (
            (Schedule, Schedule.public_id == key.value),
            (ScheduleRun, ScheduleRun.schedule_id.in_(schedule_ids)),
            (OperationLog, OperationLog.schedule_id.in_(schedule_ids)),
            (NotificationLog, NotificationLog.schedule_id.in_(schedule_ids)),
            (NotificationAttempt, NotificationAttempt.notification_log_id.in_(notification_ids)),
            (DeliveryAttempt, DeliveryAttempt.schedule_run_id.in_(run_ids)),
            (NameGenerationJob, NameGenerationJob.schedule_id.in_(schedule_ids)),
        )
        return tuple(
            int(await session.scalar(select(func.count()).select_from(model).where(condition)) or 0)
            for model, condition in models_and_conditions
        )


async def test_serial_replay_creates_each_business_row_once(
    test_engine: AsyncEngine,
    track_creation_key: Callable[[ScheduleCreationPublicId], Awaitable[None]],
) -> None:
    creator = service(test_engine)
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    await track_creation_key(key)
    first = await creator.create_once(**once_arguments(key))
    second = await creator.create_once(**once_arguments(key))
    assert (first.code, second.code) == (
        IdempotentScheduleCreationCode.CREATED,
        IdempotentScheduleCreationCode.ALREADY_CREATED,
    )
    assert await graph_counts(test_engine, key) == (1, 1, 1, 0, 0, 0, 0)


@dataclass
class _ConcurrencyCoordinator:
    preflight_count: int = 0
    preflight_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    both_preflight_missing: asyncio.Event = field(default_factory=asyncio.Event)
    winner_flushed: asyncio.Event = field(default_factory=asyncio.Event)
    loser_insert_started: asyncio.Event = field(default_factory=asyncio.Event)
    unique_wait_reached: asyncio.Event = field(default_factory=asyncio.Event)
    release_winner: asyncio.Event = field(default_factory=asyncio.Event)
    winner_task: asyncio.Task | None = None
    winner_backend_pid: int | None = None
    loser_backend_pid: int | None = None
    insert_attempt_count: int = 0
    unique_wait_observed: int = 0

    async def preflight(self) -> None:
        async with self.preflight_lock:
            self.preflight_count += 1
            if self.preflight_count == 2:
                self.both_preflight_missing.set()
        await self.both_preflight_missing.wait()


class _CoordinatedSession(AsyncSession):
    def __init__(
        self,
        *args: object,
        coordinator: _ConcurrencyCoordinator,
        target_public_id: uuid.UUID,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._coordinator = coordinator
        self._target_public_id = target_public_id
        self._preflight_observed = False

    async def scalar(self, statement: Any, params: Any = None, **kwargs: Any) -> Any:
        result = await super().scalar(statement, params=params, **kwargs)
        descriptions = getattr(statement, "column_descriptions", ())
        is_schedule_projection = bool(descriptions) and descriptions[0].get("expr") is Schedule
        if not self._preflight_observed and is_schedule_projection and result is None:
            self._preflight_observed = True
            await self._coordinator.preflight()
        return result

    async def flush(self, objects: Any = None) -> None:
        target_insert = any(
            isinstance(item, Schedule) and item.public_id == self._target_public_id
            for item in self.new
        )
        if not target_insert:
            await super().flush(objects)
            return

        backend_pid = int(await super().scalar(text("SELECT pg_backend_pid()")))
        async with self._coordinator.preflight_lock:
            self._coordinator.insert_attempt_count += 1
            is_winner = self._coordinator.winner_task is None
            if is_winner:
                self._coordinator.winner_task = asyncio.current_task()
                self._coordinator.winner_backend_pid = backend_pid
            else:
                self._coordinator.loser_backend_pid = backend_pid

        if is_winner:
            await super().flush(objects)
            self._coordinator.winner_flushed.set()
            await self._coordinator.release_winner.wait()
            return
        await self._coordinator.winner_flushed.wait()
        self._coordinator.loser_insert_started.set()
        await super().flush(objects)


class _CancellationSession(AsyncSession):
    def __init__(self, *args: object, graph_flushed: asyncio.Event, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._graph_flushed = graph_flushed
        self._never_release = asyncio.Event()

    async def flush(self, objects: Any = None) -> None:
        schedule_insert = any(isinstance(item, Schedule) for item in self.new)
        await super().flush(objects)
        if schedule_insert:
            self._graph_flushed.set()
            await self._never_release.wait()


async def _wait_for_unique_index_block(
    engine: AsyncEngine,
    coordinator: _ConcurrencyCoordinator,
) -> None:
    await coordinator.loser_insert_started.wait()
    winner_pid = coordinator.winner_backend_pid
    loser_pid = coordinator.loser_backend_pid
    assert winner_pid is not None and loser_pid is not None and winner_pid != loser_pid
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with asyncio.timeout(2), sessions() as session:
        while True:
            blocked = await session.scalar(
                text("SELECT :winner = ANY(pg_blocking_pids(:loser))"),
                {"winner": winner_pid, "loser": loser_pid},
            )
            if blocked:
                coordinator.unique_wait_observed += 1
                coordinator.unique_wait_reached.set()
                return
            await asyncio.sleep(0)


@asynccontextmanager
async def _conflicting_attempts(
    engine: AsyncEngine,
    key: ScheduleCreationPublicId,
    *,
    wait_seconds: float,
) -> AsyncIterator[tuple[list[asyncio.Task], _ConcurrencyCoordinator]]:
    coordinator = _ConcurrencyCoordinator()
    creators = [
        service(
            engine,
            wait_seconds=wait_seconds,
            session_type=_CoordinatedSession,
            session_kwargs={"coordinator": coordinator, "target_public_id": key.value},
        )
        for _ in range(2)
    ]
    tasks: list[asyncio.Task] = []
    managed: list[asyncio.Task] = []
    try:
        for creator in creators:
            task = asyncio.create_task(creator.create_once(**once_arguments(key)))
            tasks.append(task)
            managed.append(task)
        observer = asyncio.create_task(_wait_for_unique_index_block(engine, coordinator))
        managed.append(observer)
        yield tasks, coordinator
    finally:
        coordinator.release_winner.set()
        for task in managed:
            if not task.done():
                task.cancel()
        await asyncio.gather(*managed, return_exceptions=True)


async def test_parallel_winner_commit_within_limit_reconciles_as_already_created(
    test_engine: AsyncEngine,
    track_creation_key: Callable[[ScheduleCreationPublicId], Awaitable[None]],
) -> None:
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    await track_creation_key(key)
    async with _conflicting_attempts(test_engine, key, wait_seconds=2) as (
        tasks,
        coordinator,
    ):
        await asyncio.wait_for(coordinator.winner_flushed.wait(), timeout=2)
        await asyncio.wait_for(coordinator.unique_wait_reached.wait(), timeout=2)
        coordinator.release_winner.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)
    assert {item.code for item in results} == {
        IdempotentScheduleCreationCode.CREATED,
        IdempotentScheduleCreationCode.ALREADY_CREATED,
    }
    assert coordinator.preflight_count == 2
    assert coordinator.insert_attempt_count == 2
    assert coordinator.unique_wait_observed == 1
    assert await graph_counts(test_engine, key) == (1, 1, 1, 0, 0, 0, 0)


async def test_parallel_uncommitted_winner_at_limit_returns_unknown_without_reinsert(
    test_engine: AsyncEngine,
    track_creation_key: Callable[[ScheduleCreationPublicId], Awaitable[None]],
) -> None:
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    await track_creation_key(key)
    async with _conflicting_attempts(test_engine, key, wait_seconds=0.1) as (
        tasks,
        coordinator,
    ):
        await asyncio.wait_for(coordinator.unique_wait_reached.wait(), timeout=2)
        winner = coordinator.winner_task
        assert winner is not None
        loser = next(item for item in tasks if item is not winner)
        loser_result = await asyncio.wait_for(asyncio.shield(loser), timeout=2)
        assert loser_result.code is IdempotentScheduleCreationCode.UNKNOWN
        coordinator.release_winner.set()
        winner_result = await asyncio.wait_for(winner, timeout=2)
        assert winner_result.code is IdempotentScheduleCreationCode.CREATED
    assert coordinator.preflight_count == 2
    assert coordinator.insert_attempt_count == 2
    assert coordinator.unique_wait_observed == 1
    assert await graph_counts(test_engine, key) == (1, 1, 1, 0, 0, 0, 0)


@pytest.mark.parametrize("expire_on_commit", [False, True])
async def test_replay_projection_is_safe_for_both_expiration_modes(
    test_engine: AsyncEngine,
    track_creation_key: Callable[[ScheduleCreationPublicId], Awaitable[None]],
    expire_on_commit: bool,
) -> None:
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    await track_creation_key(key)
    creator = service(test_engine, expire_on_commit=expire_on_commit)
    assert (
        await creator.create_once(**once_arguments(key))
    ).code is IdempotentScheduleCreationCode.CREATED
    assert (
        await creator.create_once(**once_arguments(key))
    ).code is IdempotentScheduleCreationCode.ALREADY_CREATED


@pytest.mark.parametrize(
    ("schedule_type", "weekday"),
    [(ScheduleType.DAILY, None), (ScheduleType.WEEKLY, 2)],
)
async def test_recurring_types_replay_with_complete_creation_graph(
    test_engine: AsyncEngine,
    track_creation_key: Callable[[ScheduleCreationPublicId], Awaitable[None]],
    schedule_type: ScheduleType,
    weekday: int | None,
) -> None:
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    await track_creation_key(key)
    creator = service(test_engine)
    arguments = {
        "public_id": key,
        "guild_id": GUILD_ID,
        "channel_id": 92_000,
        "creator_user_id": 93_000,
        "schedule_type": schedule_type,
        "local_time": time(14, 30),
        "weekday": weekday,
        "end_date": date(2026, 9, 30),
        "content": "body",
        "allow_duplicate": False,
        "now": NOW,
    }
    assert (
        await creator.create_recurring(**arguments)
    ).code is IdempotentScheduleCreationCode.CREATED
    assert (
        await creator.create_recurring(**arguments)
    ).code is IdempotentScheduleCreationCode.ALREADY_CREATED
    assert await graph_counts(test_engine, key) == (1, 1, 1, 0, 0, 0, 0)


@pytest.mark.parametrize("schedule_type", list(ScheduleType))
@pytest.mark.parametrize(
    ("content", "expected_notifications", "expected_name_jobs"),
    [(None, 2, 0), ("body", 0, 1)],
)
async def test_creation_policies_are_part_of_complete_replay_graph(
    test_engine: AsyncEngine,
    track_creation_key: Callable[[ScheduleCreationPublicId], Awaitable[None]],
    content: str | None,
    expected_notifications: int,
    expected_name_jobs: int,
    schedule_type: ScheduleType,
) -> None:
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    await track_creation_key(key)
    creator = service(
        test_engine,
        configured_guild_id=GUILD_ID,
        name_generation_policy=NameGenerationRegistrationPolicy(
            enabled=True, generator_available=True
        ),
    )
    if schedule_type is ScheduleType.ONCE:
        create = creator.create_once
        arguments = once_arguments(key, content=content)
    else:
        create = creator.create_recurring
        arguments = {
            "public_id": key,
            "guild_id": GUILD_ID,
            "channel_id": 92_000,
            "creator_user_id": 93_000,
            "schedule_type": schedule_type,
            "local_time": time(12, 0),
            "weekday": 1 if schedule_type is ScheduleType.WEEKLY else None,
            "end_date": date(2026, 9, 30),
            "content": content,
            "allow_duplicate": False,
            "now": NOW,
        }
    assert (await create(**arguments)).code is IdempotentScheduleCreationCode.CREATED
    assert (await create(**arguments)).code is IdempotentScheduleCreationCode.ALREADY_CREATED
    assert await graph_counts(test_engine, key) == (
        1,
        1,
        1,
        expected_notifications,
        0,
        0,
        expected_name_jobs,
    )


async def test_same_key_with_different_content_is_conflict_without_update(
    test_engine: AsyncEngine,
    track_creation_key: Callable[[ScheduleCreationPublicId], Awaitable[None]],
) -> None:
    creator = service(test_engine)
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    await track_creation_key(key)
    assert (
        await creator.create_once(**once_arguments(key))
    ).code is IdempotentScheduleCreationCode.CREATED
    assert (
        await creator.create_once(**once_arguments(key, content="different"))
    ).code is IdempotentScheduleCreationCode.CONFLICT
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions() as session:
        assert (
            await session.scalar(select(Schedule.content).where(Schedule.public_id == key.value))
            == "body"
        )
    assert await graph_counts(test_engine, key) == (1, 1, 1, 0, 0, 0, 0)


async def test_extra_operation_action_for_target_makes_replay_conflict(
    test_engine: AsyncEngine,
    track_creation_key: Callable[[ScheduleCreationPublicId], Awaitable[None]],
) -> None:
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    await track_creation_key(key)
    creator = service(test_engine)
    assert (
        await creator.create_once(**once_arguments(key))
    ).code is IdempotentScheduleCreationCode.CREATED
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        schedule_id = await session.scalar(
            select(Schedule.id).where(Schedule.public_id == key.value)
        )
        assert schedule_id is not None
        session.add(
            OperationLog(
                schedule_id=schedule_id,
                action=OperationAction.EDITED.value,
                actor_type="user",
                actor_user_id=93_000,
                changes={"field": "content"},
            )
        )

    assert (
        await creator.create_once(**once_arguments(key))
    ).code is IdempotentScheduleCreationCode.CONFLICT
    assert await graph_counts(test_engine, key) == (1, 1, 2, 0, 0, 0, 0)


async def test_operation_action_for_other_same_guild_schedule_does_not_interfere(
    test_engine: AsyncEngine,
    track_creation_key: Callable[[ScheduleCreationPublicId], Awaitable[None]],
) -> None:
    target = ScheduleCreationPublicId.create(uuid.uuid7())
    other = ScheduleCreationPublicId.create(uuid.uuid7())
    await track_creation_key(target)
    await track_creation_key(other)
    creator = service(test_engine)
    assert (
        await creator.create_once(**once_arguments(target))
    ).code is IdempotentScheduleCreationCode.CREATED
    assert (
        await creator.create_once(
            **{
                **once_arguments(other),
                "scheduled_for": NOW + timedelta(hours=26),
            }
        )
    ).code is IdempotentScheduleCreationCode.CREATED
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        other_id = await session.scalar(
            select(Schedule.id).where(Schedule.public_id == other.value)
        )
        assert other_id is not None
        session.add(
            OperationLog(
                schedule_id=other_id,
                action=OperationAction.PAUSED.value,
                actor_type="user",
                actor_user_id=93_000,
                changes=None,
            )
        )

    assert (
        await creator.create_once(**once_arguments(target))
    ).code is IdempotentScheduleCreationCode.ALREADY_CREATED
    assert await graph_counts(test_engine, target) == (1, 1, 1, 0, 0, 0, 0)
    assert await graph_counts(test_engine, other) == (1, 1, 2, 0, 0, 0, 0)


async def test_failed_transaction_leaves_no_partial_business_rows(
    test_engine: AsyncEngine,
    track_creation_key: Callable[[ScheduleCreationPublicId], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = PostgreSQLIdempotentScheduleCreationRepository(
        async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False),
        wait_limit=ScheduleCreationWaitLimit.create(0.5),
    )
    original = repository._add_creation_log

    async def fail_after_schedule(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("fixed-test-failure")

    monkeypatch.setattr(repository, "_add_creation_log", fail_after_schedule)
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    await track_creation_key(key)
    result = await IdempotentScheduleCreationService(repository).create_once(**once_arguments(key))
    assert result.code is IdempotentScheduleCreationCode.UNKNOWN
    assert await graph_counts(test_engine, key) == (0, 0, 0, 0, 0, 0, 0)


async def test_cancellation_rolls_back_complete_creation_graph(
    test_engine: AsyncEngine,
    track_creation_key: Callable[[ScheduleCreationPublicId], Awaitable[None]],
) -> None:
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    await track_creation_key(key)
    graph_flushed = asyncio.Event()
    creator = service(
        test_engine,
        session_type=_CancellationSession,
        session_kwargs={"graph_flushed": graph_flushed},
    )
    task = asyncio.create_task(creator.create_once(**once_arguments(key)))
    try:
        await asyncio.wait_for(graph_flushed.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert await graph_counts(test_engine, key) == (0, 0, 0, 0, 0, 0, 0)
