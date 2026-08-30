from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from discord_ai_reminder_bot.domain.enums import DisplayNameSource, ScheduleType
from discord_ai_reminder_bot.domain.schedule_naming import (
    normalize_manual_display_name,
    schedule_display_name,
    validate_display_name,
)


@pytest.mark.parametrize("source", [DisplayNameSource.AI, DisplayNameSource.MANUAL])
@pytest.mark.parametrize("value", ["名", "x" * 32, "  予約名  "])
def test_validate_display_name_accepts_and_trims_named_values(
    source: DisplayNameSource, value: str
) -> None:
    name, actual_source = validate_display_name(value, source)
    assert name == value.strip()
    assert actual_source is source


@pytest.mark.parametrize(
    "value",
    ["", " ", "x" * 33, "改行\n名", "復帰\r名", "nul\x00名", "format\u200b名", "surrogate\ud800名"],
)
def test_validate_display_name_rejects_empty_long_and_unicode_control_values(value: str) -> None:
    with pytest.raises(ValueError):
        validate_display_name(value, DisplayNameSource.MANUAL)


def test_validate_display_name_enforces_source_pair() -> None:
    assert validate_display_name(None, DisplayNameSource.UNSET) == (
        None,
        DisplayNameSource.UNSET,
    )
    with pytest.raises(ValueError):
        validate_display_name("name", DisplayNameSource.UNSET)
    with pytest.raises(TypeError):
        validate_display_name(None, DisplayNameSource.AI)


def test_empty_manual_submission_becomes_unset() -> None:
    assert normalize_manual_display_name("  ") == (None, DisplayNameSource.UNSET)


@pytest.mark.parametrize(
    ("schedule_type", "next_run_at", "local_time", "weekday", "expected"),
    [
        (
            ScheduleType.ONCE,
            datetime(2026, 8, 30, 15, 30, tzinfo=UTC),
            None,
            None,
            "単発予約 8/31 00:30",
        ),
        (ScheduleType.DAILY, None, time(9, 5), None, "毎日予約 09:05"),
        (ScheduleType.WEEKLY, None, time(21, 0), 6, "毎週予約 日 21:00"),
        (ScheduleType.ONCE, None, None, None, "名称未設定"),
    ],
)
def test_schedule_display_name_fallbacks_are_deterministic_and_jst(
    schedule_type: ScheduleType,
    next_run_at: datetime | None,
    local_time: time | None,
    weekday: int | None,
    expected: str,
) -> None:
    assert (
        schedule_display_name(
            display_name=None,
            source=DisplayNameSource.UNSET,
            schedule_type=schedule_type,
            next_run_at=next_run_at,
            local_time=local_time,
            weekday=weekday,
        )
        == expected
    )


def test_schedule_display_name_prefers_persisted_name() -> None:
    assert (
        schedule_display_name(
            display_name="手動名",
            source=DisplayNameSource.MANUAL,
            schedule_type=ScheduleType.ONCE,
            next_run_at=None,
            local_time=None,
            weekday=None,
        )
        == "手動名"
    )
