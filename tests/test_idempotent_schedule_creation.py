import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from discord_ai_reminder_bot.application.schedule_creation import (
    IdempotentScheduleCreationCode,
    IdempotentScheduleCreationResult,
    IdempotentScheduleCreationService,
    OnceScheduleCreationFingerprint,
    ScheduleCreationPublicId,
    ScheduleCreationRecord,
    classify_schedule_creation_record,
)

NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)


def fingerprint(*, content: str = "private body", allow_duplicate: bool = False):
    return OnceScheduleCreationFingerprint.create(
        public_id=ScheduleCreationPublicId.create(uuid.uuid7()),
        guild_id=10,
        channel_id=20,
        creator_user_id=30,
        scheduled_for=NOW + timedelta(minutes=5),
        content=content,
        allow_duplicate=allow_duplicate,
        now=NOW,
    )


def matching_record(value: OnceScheduleCreationFingerprint) -> ScheduleCreationRecord:
    return ScheduleCreationRecord(
        public_id=value.public_id,
        guild_id=value.guild_id,
        channel_id=value.channel_id,
        creator_user_id=value.creator_user_id,
        schedule_type=value.schedule_type,
        status=value.status,
        content=value.content,
        next_run_at=value.next_run_at,
        local_time=None,
        weekday=None,
        end_date=None,
        run_count=1,
        pending_run_count=1,
        run_scheduled_for=value.next_run_at,
        run_next_attempt_at=value.next_run_at,
        run_attempt_count=0,
        creation_log_count=1,
        creation_actor_user_id=value.creator_user_id,
        creation_allow_duplicate=value.allow_duplicate,
    )


def test_matching_replay_and_conflicting_content_are_distinct() -> None:
    expected = fingerprint()
    assert classify_schedule_creation_record(expected, matching_record(expected)) is (
        IdempotentScheduleCreationCode.ALREADY_CREATED
    )
    changed = matching_record(expected)
    changed = ScheduleCreationRecord(**{**changed.as_fields(), "content": "different"})
    assert classify_schedule_creation_record(expected, changed) is (
        IdempotentScheduleCreationCode.CONFLICT
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("guild_id", 11),
        ("channel_id", 21),
        ("creator_user_id", 31),
        ("run_count", 0),
        ("pending_run_count", 0),
        ("run_attempt_count", 1),
        ("creation_log_count", 0),
        ("creation_allow_duplicate", True),
    ],
)
def test_fingerprint_includes_owner_inputs_duplicate_policy_and_required_rows(
    field: str, value: object
) -> None:
    expected = fingerprint()
    record = matching_record(expected)
    changed = ScheduleCreationRecord(**{**record.as_fields(), field: value})
    assert classify_schedule_creation_record(expected, changed) is (
        IdempotentScheduleCreationCode.CONFLICT
    )


def test_sensitive_values_are_absent_from_repr_and_fixed_results() -> None:
    expected = fingerprint(content="nonpublic-canary")
    result = IdempotentScheduleCreationResult(IdempotentScheduleCreationCode.UNKNOWN)
    assert "nonpublic-canary" not in repr(expected)
    assert str(expected.public_id.value) not in repr(expected)
    assert repr(result) == (
        "IdempotentScheduleCreationResult(code=<IdempotentScheduleCreationCode.UNKNOWN: 'unknown'>)"
    )


@pytest.mark.asyncio
async def test_application_passes_same_key_and_returns_repository_result() -> None:
    repository = AsyncMock()
    repository.create.return_value = IdempotentScheduleCreationResult(
        IdempotentScheduleCreationCode.CREATED
    )
    key = ScheduleCreationPublicId.create(uuid.uuid7())
    service = IdempotentScheduleCreationService(repository)

    result = await service.create_once(
        public_id=key,
        guild_id=10,
        channel_id=20,
        creator_user_id=30,
        scheduled_for=NOW + timedelta(minutes=5),
        content="body",
        allow_duplicate=False,
        now=NOW,
    )

    assert result.code is IdempotentScheduleCreationCode.CREATED
    passed = repository.create.await_args.args[0]
    assert passed.public_id is key
    assert repository.create.await_args.kwargs == {"operation_at": NOW}


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_unknown() -> None:
    repository = AsyncMock()
    repository.create.side_effect = asyncio.CancelledError
    service = IdempotentScheduleCreationService(repository)

    with pytest.raises(asyncio.CancelledError):
        await service.create_once(
            public_id=ScheduleCreationPublicId.create(uuid.uuid7()),
            guild_id=10,
            channel_id=20,
            creator_user_id=30,
            scheduled_for=NOW + timedelta(minutes=5),
            content="body",
            allow_duplicate=False,
            now=NOW,
        )
