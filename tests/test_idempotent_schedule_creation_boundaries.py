import ast
import math
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
    IdempotentScheduleCreationCode,
    IdempotentScheduleCreationResult,
    OnceScheduleCreationFingerprint,
    ScheduleCreationPublicId,
    ScheduleCreationRecord,
    ScheduleCreationWaitLimit,
    classify_schedule_creation_record,
)
from discord_ai_reminder_bot.application.schedule_creation import (
    CreatedOnceSchedule,
    CreatedRecurringSchedule,
)
from discord_ai_reminder_bot.domain.enums import (
    DisplayNameSource,
    ScheduleStatus,
    ScheduleType,
)

NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)


def test_application_idempotency_module_has_no_sqlalchemy_or_infrastructure_imports() -> None:
    source = Path(
        "src/discord_ai_reminder_bot/application/idempotent_schedule_creation.py"
    ).read_text()
    imported = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    )
    assert not any(name == "sqlalchemy" or name.startswith("sqlalchemy.") for name in imported)
    assert not any(".infrastructure" in name for name in imported)


def test_existing_created_dto_repr_is_unchanged() -> None:
    public_id = uuid.UUID("0198bb00-0000-7000-8000-000000000001")
    once = CreatedOnceSchedule(
        public_id=public_id,
        channel_id=20,
        status=ScheduleStatus.ACTIVE,
        content="body",
        scheduled_for=NOW,
    )
    recurring = CreatedRecurringSchedule(
        public_id=public_id,
        channel_id=20,
        schedule_type=ScheduleType.DAILY,
        status=ScheduleStatus.ACTIVE,
        content="body",
        local_time=NOW.time().replace(tzinfo=None),
        weekday=None,
        end_date=None,
        next_run_at=NOW,
    )
    assert "public_id=UUID(" in repr(once) and "content='body'" in repr(once)
    assert "public_id=UUID(" in repr(recurring) and "content='body'" in repr(recurring)


@pytest.mark.parametrize("value", [False, 0, -1, math.nan, math.inf, "1", None])
def test_wait_limit_rejects_nonpositive_nonfinite_and_invalid_values(value: object) -> None:
    with pytest.raises(ValueError) as captured:
        ScheduleCreationWaitLimit.create(value)
    assert repr(value) not in str(captured.value)


def test_wait_limit_is_finite_hidden_and_not_an_already_created_deadline() -> None:
    limit = ScheduleCreationWaitLimit.create(0.25)
    assert limit.seconds == 0.25
    assert "0.25" not in repr(limit)
    assert "already_created" not in ScheduleCreationWaitLimit.__doc__.lower()


def complete_fingerprint_and_record():
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    expected = OnceScheduleCreationFingerprint.create(
        public_id=key,
        guild_id=10,
        channel_id=20,
        creator_user_id=30,
        scheduled_for=NOW + timedelta(hours=25),
        content=None,
        allow_duplicate=False,
        now=NOW,
        notification_planning_enabled=True,
        name_generation_enabled=False,
        name_generator_available=False,
    )
    record = ScheduleCreationRecord.matching(expected)
    return expected, record


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schedule_version", 2),
        ("display_name", "changed"),
        ("display_name_source", "manual"),
        ("deleted_at", NOW),
        ("terminal_at", NOW),
        ("run_status", "processing"),
        ("run_claimed_by_present", True),
        ("run_claimed_at", NOW),
        ("run_lease_expires_at", NOW),
        ("run_started_at", NOW),
        ("run_finished_at", NOW),
        ("run_discord_message_id_present", True),
        ("run_result_code_present", True),
        ("run_error_summary_present", True),
        ("notification_attempt_count", 1),
        ("delivery_attempt_count", 1),
        ("name_job_count", 1),
    ],
)
def test_complete_creation_graph_mismatch_is_conflict(field: str, value: object) -> None:
    expected, record = complete_fingerprint_and_record()
    assert classify_schedule_creation_record(expected, replace(record, **{field: value})) is (
        IdempotentScheduleCreationCode.CONFLICT
    )


def test_result_contract_is_content_free_and_unknown_is_not_retryable() -> None:
    result = IdempotentScheduleCreationResult(IdempotentScheduleCreationCode.UNKNOWN)
    assert tuple(result.__dataclass_fields__) == ("code",)
    assert "must not" in type(result).__doc__.lower()
