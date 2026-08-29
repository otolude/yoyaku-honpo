import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from discord_ai_reminder_bot.application.schedule_deletion import (
    DeleteReasonRequired,
    ScheduleDeletionService,
    ScheduleDeletionUnavailable,
    ScheduleDeletionVersionConflict,
)
from discord_ai_reminder_bot.domain.enums import DeleteKind, ScheduleStatus
from discord_ai_reminder_bot.domain.schedule_deletion import (
    MISSING_DELETE_REASON,
    InvalidDeleteReasonError,
    deletion_kind,
    validate_delete_reason,
    validate_required_delete_reason,
)
from discord_ai_reminder_bot.infrastructure.database.models import Schedule, ScheduleRun
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    build_deletion_runs_statement,
)

NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
GUILD_ID = 100
CREATOR_ID = 200


def schedule(*, status: ScheduleStatus = ScheduleStatus.ACTIVE) -> Schedule:
    next_run_at = (
        NOW + timedelta(hours=1)
        if status in {ScheduleStatus.ACTIVE, ScheduleStatus.DRAFT}
        else None
    )
    value = Schedule(
        public_id=uuid.uuid7(),
        guild_id=GUILD_ID,
        channel_id=300,
        creator_user_id=CREATOR_ID,
        schedule_type="once",
        status=status.value,
        content=None if status is ScheduleStatus.DRAFT else "body",
        next_run_at=next_run_at,
        version=4,
    )
    value.id = 10
    return value


def pending_run(value: Schedule, *, run_id: int = 20) -> ScheduleRun:
    run = ScheduleRun(
        schedule_id=value.id,
        scheduled_for=value.next_run_at,
        status="pending",
        attempt_count=0,
        next_attempt_at=value.next_run_at,
    )
    run.id = run_id
    return run


def test_delete_reason_rejects_too_long() -> None:
    with pytest.raises(InvalidDeleteReasonError):
        validate_delete_reason("x" * 501)


@pytest.mark.parametrize("value", [None, "", " ", "\n\t"])
def test_missing_delete_reason_uses_fixed_database_value(value: str | None) -> None:
    assert validate_delete_reason(value) == MISSING_DELETE_REASON


def test_delete_reason_is_trimmed_and_preserved() -> None:
    assert validate_delete_reason("x") == "x"
    assert validate_delete_reason("  理由\n ") == "理由"
    assert validate_delete_reason("x" * 500) == "x" * 500


@pytest.mark.parametrize("value", [None, "", " ", "　", "\t", "\n", " \t\n　 ", "x" * 501])
def test_required_delete_reason_rejects_missing_whitespace_and_too_long(
    value: str | None,
) -> None:
    with pytest.raises(InvalidDeleteReasonError):
        validate_required_delete_reason(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("x", "x"),
        ("  有効な理由  ", "有効な理由"),
        ("x" * 500, "x" * 500),
        ("内部 空白\nを維持", "内部 空白\nを維持"),
        ("日本語理由", "日本語理由"),
    ],
)
def test_required_delete_reason_trims_edges_and_preserves_valid_content(
    value: str, expected: str
) -> None:
    assert validate_required_delete_reason(value) == expected


@pytest.mark.asyncio
async def test_admin_deleting_other_creator_requires_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = schedule()
    schedules = AsyncMock()
    schedules.get_by_public_id.return_value = value
    runs = AsyncMock()
    runs.list_for_deletion.return_value = [pending_run(value)]
    operations = AsyncMock()
    _patch_repositories(monkeypatch, schedules, runs, operations)
    with pytest.raises(DeleteReasonRequired):
        await ScheduleDeletionService(AsyncMock()).preview(
            guild_id=GUILD_ID,
            public_id=str(value.public_id),
            actor_user_id=CREATOR_ID + 1,
            administrator=True,
            reason=None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [None, "", " ", "　", "\t", "\n", " \t\n　 ", "x" * 501])
async def test_admin_other_required_reason_is_application_final_defense(
    monkeypatch: pytest.MonkeyPatch, reason: str | None
) -> None:
    value = schedule()
    schedules = AsyncMock()
    schedules.get_by_public_id.return_value = value
    runs = AsyncMock()
    runs.list_for_deletion.return_value = [pending_run(value)]
    operations = AsyncMock()
    _patch_repositories(monkeypatch, schedules, runs, operations)

    with pytest.raises(DeleteReasonRequired):
        await ScheduleDeletionService(AsyncMock()).preview(
            guild_id=GUILD_ID,
            public_id=str(value.public_id),
            actor_user_id=CREATOR_ID + 1,
            administrator=True,
            reason=reason,
        )
    operations.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_rejects_stale_expected_version_before_run_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = schedule()
    schedules = AsyncMock()
    schedules.get_by_public_id.return_value = value
    runs = AsyncMock()
    operations = AsyncMock()
    _patch_repositories(monkeypatch, schedules, runs, operations)

    with pytest.raises(ScheduleDeletionVersionConflict):
        await ScheduleDeletionService(AsyncMock()).preview(
            guild_id=GUILD_ID,
            public_id=str(value.public_id),
            actor_user_id=CREATOR_ID,
            administrator=False,
            reason=None,
            expected_version=value.version - 1,
        )

    runs.list_for_deletion.assert_not_awaited()


@pytest.mark.parametrize(
    ("actor", "creator", "administrator", "status", "expected"),
    [
        (1, 1, False, ScheduleStatus.ACTIVE, DeleteKind.CREATOR_DELETED),
        (1, 1, True, ScheduleStatus.FAILED, DeleteKind.CREATOR_DELETED),
        (2, 1, True, ScheduleStatus.ACTIVE, DeleteKind.ADMIN_DELETED),
        (2, 1, True, ScheduleStatus.FAILED, DeleteKind.OPERATOR_RESOLVED_FAILED),
    ],
)
def test_delete_kind_prioritizes_creator_ownership(
    actor: int,
    creator: int,
    administrator: bool,
    status: ScheduleStatus,
    expected: DeleteKind,
) -> None:
    assert (
        deletion_kind(
            actor_user_id=actor,
            creator_user_id=creator,
            administrator=administrator,
            status=status,
        )
        is expected
    )


def test_deletion_run_statement_is_stable_and_lockable() -> None:
    sql = str(
        build_deletion_runs_statement(
            schedule_id=10,
            current_scheduled_for=NOW,
            lock=True,
        ).compile(dialect=postgresql.dialect())
    ).upper()
    assert "ORDER BY SCHEDULE_RUNS.ID ASC" in sql
    assert "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_preview_is_read_only_and_trims_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    value = schedule()
    run = pending_run(value)
    schedules = AsyncMock()
    schedules.get_by_public_id.return_value = value
    runs = AsyncMock()
    runs.list_for_deletion.return_value = [run]
    operations = AsyncMock()
    _patch_repositories(monkeypatch, schedules, runs, operations)
    result = await ScheduleDeletionService(AsyncMock()).preview(
        guild_id=GUILD_ID,
        public_id=str(value.public_id),
        actor_user_id=CREATOR_ID,
        administrator=False,
        reason="  planned  ",
    )
    assert result.reason == "planned"
    schedules.flush_execution_update.assert_not_awaited()
    runs.skip_pending_for_deleted_schedule.assert_not_awaited()
    operations.add.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_status", "actor", "administrator", "expected_kind"),
    [
        (ScheduleStatus.DRAFT, CREATOR_ID, False, "creator_deleted"),
        (ScheduleStatus.ACTIVE, CREATOR_ID, False, "creator_deleted"),
        (ScheduleStatus.PAUSED, CREATOR_ID + 1, True, "admin_deleted"),
        (ScheduleStatus.FAILED, CREATOR_ID + 1, True, "operator_resolved_failed"),
        (ScheduleStatus.FAILED, CREATOR_ID, True, "creator_deleted"),
    ],
)
async def test_delete_updates_schedule_pending_run_and_operation(
    monkeypatch: pytest.MonkeyPatch,
    initial_status: ScheduleStatus,
    actor: int,
    administrator: bool,
    expected_kind: str,
) -> None:
    value = schedule(status=initial_status)
    locked_runs = [pending_run(value)] if value.next_run_at is not None else []
    schedules = AsyncMock()
    schedules.get_by_public_id.return_value = value
    schedules.lock_by_id_for_deletion.return_value = value
    runs = AsyncMock()
    runs.list_for_deletion.return_value = locked_runs
    operations = AsyncMock()
    _patch_repositories(monkeypatch, schedules, runs, operations)
    result = await ScheduleDeletionService(AsyncMock()).delete(
        guild_id=GUILD_ID,
        public_id=str(value.public_id),
        actor_user_id=actor,
        administrator=administrator,
        reason=" reason ",
        deleted_at=NOW,
    )
    assert value.status == "deleted"
    assert value.next_run_at is None
    assert value.deleted_at == value.terminal_at == value.updated_at == NOW
    assert value.version == 5
    assert result.previous_status is initial_status
    operation = operations.add.await_args.args[0]
    assert operation.delete_kind == expected_kind
    assert operation.delete_reason == "reason"
    assert operation.created_at == NOW
    assert "body" not in str(operation.changes)
    if locked_runs:
        runs.skip_pending_for_deleted_schedule.assert_awaited_once_with(
            runs=locked_runs, deleted_at=NOW
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [ScheduleStatus.COMPLETED, ScheduleStatus.ENDED, ScheduleStatus.DELETED]
)
async def test_terminal_schedule_and_redelete_are_rejected_without_log(
    monkeypatch: pytest.MonkeyPatch, status: ScheduleStatus
) -> None:
    value = schedule(status=status)
    schedules = AsyncMock()
    schedules.get_by_public_id.return_value = value
    schedules.lock_by_id_for_deletion.return_value = value
    runs = AsyncMock()
    runs.list_for_deletion.return_value = []
    operations = AsyncMock()
    _patch_repositories(monkeypatch, schedules, runs, operations)
    with pytest.raises(ScheduleDeletionUnavailable):
        await ScheduleDeletionService(AsyncMock()).delete(
            guild_id=GUILD_ID,
            public_id=str(value.public_id),
            actor_user_id=CREATOR_ID,
            administrator=False,
            reason="reason",
            deleted_at=NOW,
        )
    operations.add.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("run_status", ["processing", "succeeded", "failed", "skipped"])
async def test_processing_or_current_terminal_run_is_rejected(
    monkeypatch: pytest.MonkeyPatch, run_status: str
) -> None:
    value = schedule()
    run = pending_run(value)
    run.status = run_status
    schedules = AsyncMock()
    schedules.get_by_public_id.return_value = value
    schedules.lock_by_id_for_deletion.return_value = value
    runs = AsyncMock()
    runs.list_for_deletion.return_value = [run]
    operations = AsyncMock()
    _patch_repositories(monkeypatch, schedules, runs, operations)
    with pytest.raises(ScheduleDeletionUnavailable):
        await ScheduleDeletionService(AsyncMock()).delete(
            guild_id=GUILD_ID,
            public_id=str(value.public_id),
            actor_user_id=CREATOR_ID,
            administrator=False,
            reason="reason",
            deleted_at=NOW,
        )
    runs.skip_pending_for_deleted_schedule.assert_not_awaited()
    operations.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_version_change_after_run_lock_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unlocked = schedule()
    locked = schedule()
    locked.id = unlocked.id
    locked.public_id = unlocked.public_id
    locked.version = unlocked.version + 1
    schedules = AsyncMock()
    schedules.get_by_public_id.return_value = unlocked
    schedules.lock_by_id_for_deletion.return_value = locked
    runs = AsyncMock()
    runs.list_for_deletion.return_value = [pending_run(unlocked)]
    operations = AsyncMock()
    _patch_repositories(monkeypatch, schedules, runs, operations)
    with pytest.raises(ScheduleDeletionUnavailable):
        await ScheduleDeletionService(AsyncMock()).delete(
            guild_id=GUILD_ID,
            public_id=str(unlocked.public_id),
            actor_user_id=CREATOR_ID,
            administrator=False,
            reason="reason",
            deleted_at=NOW,
        )
    operations.add.assert_not_awaited()


def _patch_repositories(
    monkeypatch: pytest.MonkeyPatch,
    schedules: AsyncMock,
    runs: AsyncMock,
    operations: AsyncMock,
) -> None:
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_deletion.ScheduleRepository",
        lambda unused: schedules,
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_deletion.ScheduleRunRepository",
        lambda unused: runs,
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_deletion.OperationLogRepository",
        lambda unused: operations,
    )
