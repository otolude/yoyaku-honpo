from datetime import UTC, datetime, timedelta, timezone

import pytest

from discord_ai_reminder_bot.domain.enums import RunStatus, ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.domain.notification import plan_draft_notifications

NOW = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)


def plan(remaining: timedelta):
    return plan_draft_notifications(
        event_at=NOW,
        scheduled_for=NOW + remaining,
        schedule_status=ScheduleStatus.DRAFT,
        content=None,
        schedule_type=ScheduleType.ONCE,
        run_status=RunStatus.PENDING,
        attempt_count=0,
        next_run_at=NOW + remaining,
    )


@pytest.mark.parametrize(
    ("remaining", "types"),
    [
        (timedelta(hours=24, microseconds=1), ("draft_24h", "draft_1h")),
        (timedelta(hours=24), ("draft_24h", "draft_1h")),
        (timedelta(hours=24) - timedelta(microseconds=1), ("draft_1h",)),
        (timedelta(hours=1, microseconds=1), ("draft_1h",)),
        (timedelta(hours=1), ("draft_1h",)),
        (timedelta(hours=1) - timedelta(microseconds=1), ("draft_immediate",)),
        (timedelta(microseconds=1), ("draft_immediate",)),
        (timedelta(0), ()),
        (-timedelta(microseconds=1), ()),
    ],
)
def test_draft_notification_boundaries(remaining: timedelta, types: tuple[str, ...]) -> None:
    assert tuple(item.notification_type.value for item in plan(remaining)) == types


@pytest.mark.parametrize(
    "changed",
    [
        {"schedule_status": ScheduleStatus.ACTIVE},
        {"schedule_status": ScheduleStatus.PAUSED},
        {"content": "body"},
        {"run_status": RunStatus.PROCESSING},
        {"attempt_count": 1},
        {"next_run_at": NOW + timedelta(hours=3)},
    ],
)
def test_only_consistent_initial_draft_is_planned(changed: dict[str, object]) -> None:
    values = {
        "event_at": NOW,
        "scheduled_for": NOW + timedelta(hours=2),
        "schedule_status": ScheduleStatus.DRAFT,
        "content": None,
        "schedule_type": ScheduleType.DAILY,
        "run_status": RunStatus.PENDING,
        "attempt_count": 0,
        "next_run_at": NOW + timedelta(hours=2),
    }
    values.update(changed)
    assert plan_draft_notifications(**values) == ()


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_at", datetime(2026, 8, 19, 3, 0)),  # noqa: DTZ001 - rejection case
        ("scheduled_for", datetime(2026, 8, 19, 12, 0, tzinfo=timezone(timedelta(hours=9)))),
    ],
)
def test_planning_rejects_non_utc(field: str, value: datetime) -> None:
    values = {
        "event_at": NOW,
        "scheduled_for": NOW + timedelta(hours=2),
        "schedule_status": ScheduleStatus.DRAFT,
        "content": None,
        "schedule_type": ScheduleType.WEEKLY,
        "run_status": RunStatus.PENDING,
        "attempt_count": 0,
        "next_run_at": NOW + timedelta(hours=2),
    }
    values[field] = value
    with pytest.raises(ValueError):
        plan_draft_notifications(**values)
