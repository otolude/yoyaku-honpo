import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum, auto
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
RACE_ATTEMPTS = 2
LOCK_WAIT_SECONDS = 5.0
BLOCK_OBSERVATION_SECONDS = 2.0
BLOCK_POLL_INTERVAL_SECONDS = 0.01
BLOCK_POLL_LIMIT = 200
RACE_DEADLINE_SECONDS = 10.0
TASK_CLEANUP_SECONDS = 2.0
RACE_ABORT_SECONDS = 3.0
_TASK_DEADLINE_FAILED = "race task deadline exceeded"
_TASK_CLEANUP_FAILED = "race task cleanup failed"
_RACE_CONTRACT_FAILED = "race session contract failed"
_BLOCK_OBSERVATION_FAILED = "unique-index blocking observation failed"
_ATTEMPT_ID: ContextVar[int | None] = ContextVar("schedule_creation_attempt", default=None)


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
        counts: list[int] = []
        for model, condition in models_and_conditions:
            count = await session.scalar(select(func.count()).select_from(model).where(condition))
            counts.append(int(count or 0))
        return tuple(counts)


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


class _SessionRole(Enum):
    PREFLIGHT = auto()
    WRITE = auto()
    RECONCILIATION = auto()


@dataclass
class _ConcurrencyCoordinator:
    preflight_count: int = 0
    reconciliation_count: int = 0
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
    winner_attempt_id: int | None = None
    session_roles: dict[int, list[_SessionRole]] = field(default_factory=dict)
    preflight_attempts: set[int] = field(default_factory=set)
    reconciliation_attempts: set[int] = field(default_factory=set)

    def register_session(self) -> tuple[int, _SessionRole]:
        attempt_id = _ATTEMPT_ID.get()
        if attempt_id is None:
            raise AssertionError(_RACE_CONTRACT_FAILED)
        roles = self.session_roles.setdefault(attempt_id, [])
        try:
            role = (
                _SessionRole.PREFLIGHT,
                _SessionRole.WRITE,
                _SessionRole.RECONCILIATION,
            )[len(roles)]
        except IndexError:
            raise AssertionError(_RACE_CONTRACT_FAILED) from None
        roles.append(role)
        return attempt_id, role

    async def preflight(self, attempt_id: int) -> None:
        async with self.preflight_lock:
            if attempt_id in self.preflight_attempts:
                raise AssertionError(_RACE_CONTRACT_FAILED)
            self.preflight_attempts.add(attempt_id)
            self.preflight_count += 1
            if self.preflight_count == RACE_ATTEMPTS:
                self.both_preflight_missing.set()
        await self.both_preflight_missing.wait()

    def reconciliation(self, attempt_id: int) -> None:
        if attempt_id in self.reconciliation_attempts:
            raise AssertionError(_RACE_CONTRACT_FAILED)
        self.reconciliation_attempts.add(attempt_id)
        self.reconciliation_count += 1

    def assert_complete(self) -> None:
        winner = self.winner_attempt_id
        if winner is None:
            raise AssertionError(_RACE_CONTRACT_FAILED)
        losers = set(self.session_roles) - {winner}
        expected_winner = [_SessionRole.PREFLIGHT, _SessionRole.WRITE]
        expected_loser = [
            _SessionRole.PREFLIGHT,
            _SessionRole.WRITE,
            _SessionRole.RECONCILIATION,
        ]
        all_roles = [role for roles in self.session_roles.values() for role in roles]
        read_session_count = sum(role is not _SessionRole.WRITE for role in all_roles)
        write_session_count = sum(role is _SessionRole.WRITE for role in all_roles)
        valid = (
            len(self.session_roles) == RACE_ATTEMPTS
            and set(self.session_roles) == set(range(RACE_ATTEMPTS))
            and len(losers) == 1
            and self.session_roles[winner] == expected_winner
            and self.session_roles[next(iter(losers))] == expected_loser
            and self.preflight_count == RACE_ATTEMPTS
            and self.preflight_attempts == set(self.session_roles)
            and self.reconciliation_count == 1
            and self.reconciliation_attempts == losers
            and read_session_count == 3
            and write_session_count == 2
            and self.insert_attempt_count == RACE_ATTEMPTS
            and self.unique_wait_observed == 1
            and self.winner_backend_pid is not None
            and self.loser_backend_pid is not None
            and self.winner_backend_pid != self.loser_backend_pid
        )
        if not valid:
            raise AssertionError(_RACE_CONTRACT_FAILED)


def _is_target_schedule_lookup(statement: Any, target_public_id: uuid.UUID) -> bool:
    descriptions = getattr(statement, "column_descriptions", ())
    expected_lookup = select(Schedule).where(Schedule.public_id == target_public_id)
    return bool(
        descriptions
        and descriptions[0].get("expr") is Schedule
        and statement.compare(expected_lookup)
    )


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
        self._attempt_id, self._role = coordinator.register_session()
        self._role_observed = False

    async def scalar(self, statement: Any, params: Any = None, **kwargs: Any) -> Any:
        result = await super().scalar(statement, params=params, **kwargs)
        if (
            self._role in {_SessionRole.PREFLIGHT, _SessionRole.RECONCILIATION}
            and not self._role_observed
        ):
            if not _is_target_schedule_lookup(statement, self._target_public_id):
                raise AssertionError(_RACE_CONTRACT_FAILED)
            self._role_observed = True
            if self._role is _SessionRole.PREFLIGHT:
                if result is not None:
                    raise AssertionError(_RACE_CONTRACT_FAILED)
                await self._coordinator.preflight(self._attempt_id)
            else:
                self._coordinator.reconciliation(self._attempt_id)
        return result

    async def flush(self, objects: Any = None) -> None:
        target_insert = any(
            isinstance(item, Schedule) and item.public_id == self._target_public_id
            for item in self.new
        )
        if not target_insert:
            await super().flush(objects)
            return
        if self._role is not _SessionRole.WRITE:
            raise AssertionError(_RACE_CONTRACT_FAILED)

        backend_pid = int(await super().scalar(text("SELECT pg_backend_pid()")))
        async with self._coordinator.preflight_lock:
            self._coordinator.insert_attempt_count += 1
            is_winner = self._coordinator.winner_task is None
            if is_winner:
                self._coordinator.winner_task = asyncio.current_task()
                self._coordinator.winner_attempt_id = self._attempt_id
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
    failed = False
    try:
        async with asyncio.timeout(BLOCK_OBSERVATION_SECONDS):
            await coordinator.loser_insert_started.wait()
            winner_pid = coordinator.winner_backend_pid
            loser_pid = coordinator.loser_backend_pid
            if winner_pid is None or loser_pid is None or winner_pid == loser_pid:
                failed = True
            else:
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                async with sessions() as session:
                    for _ in range(BLOCK_POLL_LIMIT):
                        blocked = await session.scalar(
                            text("SELECT :winner = ANY(pg_blocking_pids(:loser))"),
                            {"winner": winner_pid, "loser": loser_pid},
                        )
                        if blocked:
                            coordinator.unique_wait_observed += 1
                            coordinator.unique_wait_reached.set()
                            return
                        await asyncio.sleep(BLOCK_POLL_INTERVAL_SECONDS)
                failed = True
    except TimeoutError:
        failed = True
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - database detail must not escape the test boundary
        failed = True
    if failed:
        raise AssertionError(_BLOCK_OBSERVATION_FAILED)


class _ManagedTasks:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def create[ResultT](self, awaitable: Awaitable[ResultT]) -> asyncio.Task[ResultT]:
        task = asyncio.create_task(awaitable)
        self._tasks.add(task)
        return task

    async def results[ResultT](
        self,
        tasks: list[asyncio.Task[ResultT]],
        *,
        deadline: float,
    ) -> list[ResultT]:
        _done, pending = await asyncio.wait(tasks, timeout=deadline)
        if pending:
            raise AssertionError(_TASK_DEADLINE_FAILED)
        return [task.result() for task in tasks]

    async def close(self, *, deadline: float = TASK_CLEANUP_SECONDS) -> None:
        pending = {task for task in self._tasks if not task.done()}
        for task in pending:
            task.cancel()
        remaining: set[asyncio.Task[Any]] = set()
        if pending:
            done, remaining = await asyncio.wait(pending, timeout=deadline)
            self._consume(done)
        self._consume(task for task in self._tasks if task.done())
        for task in remaining:
            task.cancel()
        if remaining:
            raise AssertionError(_TASK_CLEANUP_FAILED)

    @staticmethod
    def _consume(tasks: Any) -> None:
        for task in tasks:
            if not task.cancelled():
                task.exception()


async def _run_as_attempt[ResultT](attempt_id: int, awaitable: Awaitable[ResultT]) -> ResultT:
    token = _ATTEMPT_ID.set(attempt_id)
    try:
        return await awaitable
    finally:
        _ATTEMPT_ID.reset(token)


async def _wait_for_event(event: asyncio.Event) -> None:
    timed_out = False
    try:
        async with asyncio.timeout(BLOCK_OBSERVATION_SECONDS):
            await event.wait()
    except TimeoutError:
        timed_out = True
    if timed_out:
        raise AssertionError(_TASK_DEADLINE_FAILED)


async def _run_race_with_deadline(awaitable: Awaitable[None]) -> None:
    tasks = _ManagedTasks()
    task = tasks.create(awaitable)
    try:
        await tasks.results([task], deadline=RACE_DEADLINE_SECONDS)
    finally:
        await tasks.close(deadline=RACE_ABORT_SECONDS)


async def test_race_coordinator_contract_is_test_local_and_attempt_scoped() -> None:
    coordinator = _ConcurrencyCoordinator()
    target_public_id = uuid.uuid7()
    assert _is_target_schedule_lookup(
        select(Schedule).where(Schedule.public_id == target_public_id), target_public_id
    )
    assert not _is_target_schedule_lookup(
        select(Schedule).where(Schedule.public_id == uuid.uuid7()), target_public_id
    )
    assert not _is_target_schedule_lookup(
        select(Schedule.id).where(Schedule.public_id == target_public_id), target_public_id
    )
    for attempt_id in range(RACE_ATTEMPTS):
        token = _ATTEMPT_ID.set(attempt_id)
        try:
            assert coordinator.register_session() == (attempt_id, _SessionRole.PREFLIGHT)
            assert coordinator.register_session() == (attempt_id, _SessionRole.WRITE)
            assert coordinator.register_session() == (attempt_id, _SessionRole.RECONCILIATION)
            with pytest.raises(AssertionError, match=_RACE_CONTRACT_FAILED):
                coordinator.register_session()
        finally:
            _ATTEMPT_ID.reset(token)
    assert _ATTEMPT_ID.get() is None
    assert all(
        roles
        == [
            _SessionRole.PREFLIGHT,
            _SessionRole.WRITE,
            _SessionRole.RECONCILIATION,
        ]
        for roles in coordinator.session_roles.values()
    )


async def test_race_deadlines_leave_observation_and_cleanup_margin() -> None:
    assert LOCK_WAIT_SECONDS >= 5.0
    assert BLOCK_OBSERVATION_SECONDS <= 2.0
    assert BLOCK_POLL_LIMIT * BLOCK_POLL_INTERVAL_SECONDS <= BLOCK_OBSERVATION_SECONDS
    assert RACE_ABORT_SECONDS > TASK_CLEANUP_SECONDS
    assert RACE_DEADLINE_SECONDS >= LOCK_WAIT_SECONDS + RACE_ABORT_SECONDS


@pytest.mark.parametrize("case_number", range(2))
async def test_managed_race_tasks_are_collected_after_assertion_failure(
    case_number: int,
) -> None:
    del case_number
    managed = _ManagedTasks()
    started = asyncio.Event()
    never_release = asyncio.Event()
    resource_closed = asyncio.Event()

    async def hold_test_resource() -> None:
        started.set()
        try:
            await never_release.wait()
        finally:
            resource_closed.set()

    task = managed.create(hold_test_resource())
    await _wait_for_event(started)
    with pytest.raises(AssertionError, match=_RACE_CONTRACT_FAILED):
        try:
            raise AssertionError(_RACE_CONTRACT_FAILED)
        finally:
            await managed.close()
    assert task.done()
    assert task.cancelled()
    assert resource_closed.is_set()


async def test_managed_tasks_drains_recancelled_tasks_before_reporting_deadline() -> None:
    managed = _ManagedTasks()
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_finished = asyncio.Event()
    never_release = asyncio.Event()

    async def require_second_cancellation() -> None:
        started.set()
        try:
            await never_release.wait()
        finally:
            cleanup_started.set()
            try:
                await never_release.wait()
            except asyncio.CancelledError:
                cleanup_finished.set()

    task = managed.create(require_second_cancellation())
    await _wait_for_event(started)
    try:
        with pytest.raises(AssertionError, match=_TASK_CLEANUP_FAILED):
            await managed.close(deadline=0.01)
        assert task.done()
        assert cleanup_started.is_set()
        assert cleanup_finished.is_set()
        assert not {item for item in managed._tasks if not item.done()}
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_managed_tasks_reports_all_task_failures_after_collection() -> None:
    class FirstFailure(Exception):
        pass

    class SecondFailure(Exception):
        pass

    managed = _ManagedTasks()

    async def fail(error: Exception) -> None:
        raise error

    tasks = [
        managed.create(fail(FirstFailure())),
        managed.create(fail(SecondFailure())),
    ]
    await asyncio.wait(tasks)

    with pytest.raises(ExceptionGroup) as caught:
        await managed.close()

    assert {type(item) for item in caught.value.exceptions} == {FirstFailure, SecondFailure}
    assert all(task.done() for task in tasks)
    assert not {item for item in managed._tasks if not item.done()}


async def test_managed_tasks_reports_observer_failure_after_other_tasks_are_collected() -> None:
    class ObserverFailure(Exception):
        pass

    managed = _ManagedTasks()
    resource_started = asyncio.Event()
    resource_closed = asyncio.Event()
    never_release = asyncio.Event()

    async def fail_observer() -> None:
        raise ObserverFailure

    async def hold_resource() -> None:
        resource_started.set()
        try:
            await never_release.wait()
        finally:
            resource_closed.set()

    observer = managed.create(fail_observer())
    resource = managed.create(hold_resource())
    await _wait_for_event(resource_started)
    await asyncio.wait([observer])
    try:
        with pytest.raises(ObserverFailure):
            await managed.close()
        assert observer.done()
        assert resource.done()
        assert resource_closed.is_set()
    finally:
        if not resource.done():
            resource.cancel()
        await asyncio.gather(observer, resource, return_exceptions=True)


async def test_managed_tasks_consume_returns_failures() -> None:
    class ObserverFailure(Exception):
        pass

    async def fail_observer() -> None:
        raise ObserverFailure

    task = asyncio.create_task(fail_observer())
    await asyncio.wait([task])

    failures = _ManagedTasks._consume([task])

    assert len(failures) == 1
    assert isinstance(failures[0], ObserverFailure)


async def test_cleanup_steps_attempt_graph_and_sentinel_before_aggregating() -> None:
    class GraphCleanupFailure(Exception):
        pass

    class SentinelCleanupFailure(Exception):
        pass

    attempted: list[str] = []

    async def cleanup_graph() -> None:
        attempted.append("graph")
        raise GraphCleanupFailure

    async def cleanup_sentinel() -> None:
        attempted.append("sentinel")
        raise SentinelCleanupFailure

    with pytest.raises(ExceptionGroup) as caught:
        await _run_cleanup_steps(cleanup_graph, cleanup_sentinel)

    assert attempted == ["graph", "sentinel"]
    assert {type(item) for item in caught.value.exceptions} == {
        GraphCleanupFailure,
        SentinelCleanupFailure,
    }


async def test_race_coordinator_exposes_reconciliation_completion() -> None:
    coordinator = _ConcurrencyCoordinator()

    coordinator.reconciliation(1)

    assert coordinator.reconciliation_reached.is_set()


@asynccontextmanager
async def _conflicting_attempts(
    engine: AsyncEngine,
    key: ScheduleCreationPublicId,
    *,
    wait_seconds: float,
) -> AsyncIterator[tuple[list[asyncio.Task[Any]], _ConcurrencyCoordinator, _ManagedTasks]]:
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
    tasks: list[asyncio.Task[Any]] = []
    managed = _ManagedTasks()
    try:
        for attempt_id, creator in enumerate(creators):
            task = managed.create(
                _run_as_attempt(attempt_id, creator.create_once(**once_arguments(key)))
            )
            tasks.append(task)
        managed.create(_wait_for_unique_index_block(engine, coordinator))
        yield tasks, coordinator, managed
    finally:
        coordinator.release_winner.set()
        await managed.close()


async def test_parallel_winner_commit_within_limit_reconciles_as_already_created(
    test_engine: AsyncEngine,
    track_creation_key: Callable[[ScheduleCreationPublicId], Awaitable[None]],
) -> None:
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    await track_creation_key(key)

    async def run() -> None:
        async with _conflicting_attempts(test_engine, key, wait_seconds=LOCK_WAIT_SECONDS) as (
            tasks,
            coordinator,
            managed,
        ):
            await _wait_for_event(coordinator.winner_flushed)
            await _wait_for_event(coordinator.unique_wait_reached)
            coordinator.release_winner.set()
            results = await managed.results(tasks, deadline=LOCK_WAIT_SECONDS)
        assert {item.code for item in results} == {
            IdempotentScheduleCreationCode.CREATED,
            IdempotentScheduleCreationCode.ALREADY_CREATED,
        }
        coordinator.assert_complete()
        assert await graph_counts(test_engine, key) == (1, 1, 1, 0, 0, 0, 0)

    await _run_race_with_deadline(run())


async def test_parallel_uncommitted_winner_at_limit_returns_unknown_without_reinsert(
    test_engine: AsyncEngine,
    track_creation_key: Callable[[ScheduleCreationPublicId], Awaitable[None]],
) -> None:
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    await track_creation_key(key)

    async def run() -> None:
        async with _conflicting_attempts(test_engine, key, wait_seconds=LOCK_WAIT_SECONDS) as (
            tasks,
            coordinator,
            managed,
        ):
            await _wait_for_event(coordinator.unique_wait_reached)
            winner = coordinator.winner_task
            if winner is None:
                raise AssertionError(_RACE_CONTRACT_FAILED)
            loser = next(item for item in tasks if item is not winner)
            loser_result = (await managed.results([loser], deadline=LOCK_WAIT_SECONDS + 1.0))[0]
            assert loser_result.code is IdempotentScheduleCreationCode.UNKNOWN
            coordinator.release_winner.set()
            winner_result = (await managed.results([winner], deadline=2.0))[0]
            assert winner_result.code is IdempotentScheduleCreationCode.CREATED
        coordinator.assert_complete()
        assert await graph_counts(test_engine, key) == (1, 1, 1, 0, 0, 0, 0)

    await _run_race_with_deadline(run())


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
    managed = _ManagedTasks()
    task = managed.create(creator.create_once(**once_arguments(key)))
    try:
        await _wait_for_event(graph_flushed)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await managed.results([task], deadline=TASK_CLEANUP_SECONDS)
    finally:
        await managed.close()
    assert await graph_counts(test_engine, key) == (0, 0, 0, 0, 0, 0, 0)
