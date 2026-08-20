from datetime import UTC, datetime, timedelta, timezone

import pytest

from discord_ai_reminder_bot.domain.cleanup import (
    RETENTION_PERIOD,
    is_global_notification_due,
    is_schedule_due,
    retention_cutoff,
    validate_cleanup_batch_size,
)
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError

NOW = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)
CUTOFF = NOW - timedelta(days=30)


def test_retention_cutoff_is_exactly_thirty_utc_days() -> None:
    assert RETENTION_PERIOD == timedelta(days=30)
    assert retention_cutoff(NOW) == CUTOFF


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(microseconds=1), False),
        (timedelta(), True),
        (-timedelta(microseconds=1), True),
    ],
)
def test_schedule_boundary_is_inclusive(delta: timedelta, expected: bool) -> None:
    assert (
        is_schedule_due(status="completed", terminal_at=CUTOFF + delta, cutoff=CUTOFF) is expected
    )


@pytest.mark.parametrize("status", ["completed", "ended", "deleted"])
def test_terminal_schedule_statuses_are_due(status: str) -> None:
    assert is_schedule_due(status=status, terminal_at=CUTOFF, cutoff=CUTOFF)


@pytest.mark.parametrize("status", ["failed", "draft", "active", "paused"])
def test_non_cleanup_schedule_statuses_are_excluded(status: str) -> None:
    assert not is_schedule_due(status=status, terminal_at=CUTOFF, cutoff=CUTOFF)


def test_schedule_without_terminal_at_is_excluded() -> None:
    assert not is_schedule_due(status="completed", terminal_at=None, cutoff=CUTOFF)


@pytest.mark.parametrize(
    "value", [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=9)))]
)
def test_cleanup_cutoff_rejects_naive_and_non_utc(value: datetime) -> None:
    with pytest.raises(InvalidDateTimeError):
        retention_cutoff(value)


@pytest.mark.parametrize("size", [1, 100])
def test_cleanup_batch_size_accepts_bounds(size: int) -> None:
    assert validate_cleanup_batch_size(size) == size


@pytest.mark.parametrize("size", [0, 101, True, False, 1.0])
def test_cleanup_batch_size_rejects_invalid_values(size: object) -> None:
    with pytest.raises(ValueError):
        validate_cleanup_batch_size(size)  # type: ignore[arg-type]


@pytest.mark.parametrize("status", ["succeeded", "failed", "unknown", "cancelled"])
def test_global_terminal_notification_uses_finished_at(status: str) -> None:
    assert is_global_notification_due(
        status=status,
        schedule_id=None,
        schedule_run_id=None,
        finished_at=CUTOFF,
        cutoff=CUTOFF,
    )


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_global_in_flight_notification_is_excluded(status: str) -> None:
    assert not is_global_notification_due(
        status=status,
        schedule_id=None,
        schedule_run_id=None,
        finished_at=CUTOFF,
        cutoff=CUTOFF,
    )


def test_global_requires_both_foreign_keys_null_and_finished_at() -> None:
    common = {"status": "succeeded", "finished_at": CUTOFF, "cutoff": CUTOFF}
    assert not is_global_notification_due(schedule_id=1, schedule_run_id=None, **common)
    assert not is_global_notification_due(schedule_id=None, schedule_run_id=1, **common)
    assert not is_global_notification_due(
        status="succeeded",
        schedule_id=None,
        schedule_run_id=None,
        finished_at=None,
        cutoff=CUTOFF,
    )


def test_global_finished_at_boundary_is_inclusive() -> None:
    assert not is_global_notification_due(
        status="unknown",
        schedule_id=None,
        schedule_run_id=None,
        finished_at=CUTOFF + timedelta(microseconds=1),
        cutoff=CUTOFF,
    )
