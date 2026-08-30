from datetime import UTC, date, datetime, timedelta

import pytest

from discord_ai_reminder_bot.application.name_generation import DisabledNameGenerator, NameGenerator
from discord_ai_reminder_bot.domain.enums import BudgetPeriodType, NameGenerationJobStatus
from discord_ai_reminder_bot.domain.name_generation import (
    BudgetPolicy,
    GeneratedScheduleName,
    NameGenerationRequest,
    budget_bucket_is_due,
    budget_period_start,
    can_transition_job,
    terminal_job_is_due,
)


class FakeNameGenerator:
    """Test-only DI fake; no production setting can select it."""

    available = True

    def __init__(self, result: str) -> None:
        self.result = GeneratedScheduleName(result)
        self.requests: list[NameGenerationRequest] = []

    async def generate(self, request: NameGenerationRequest) -> GeneratedScheduleName:
        self.requests.append(request)
        return self.result


def test_budget_policy_uses_changeable_limits_and_rejects_unsafe_values() -> None:
    assert BudgetPolicy(50, 500, 100_000_000, "JPY").daily_request_limit == 50
    assert BudgetPolicy(200, 1_000, 9_000_000_000, "JPY").daily_request_limit == 200
    for values in (
        (True, 500, 1, "JPY"),
        (0, 500, 1, "JPY"),
        (500, 50, 1, "JPY"),
        (1, 1, 1, "USD"),
    ):
        with pytest.raises(ValueError):
            BudgetPolicy(*values)


def test_jst_bucket_boundaries() -> None:
    before_midnight = datetime(2026, 8, 30, 14, 59, tzinfo=UTC)
    after_midnight = before_midnight + timedelta(minutes=1)
    assert budget_period_start(BudgetPeriodType.DAILY, before_midnight) == date(2026, 8, 30)
    assert budget_period_start(BudgetPeriodType.DAILY, after_midnight) == date(2026, 8, 31)
    assert budget_period_start(BudgetPeriodType.MONTHLY, after_midnight) == date(2026, 8, 1)


def test_job_transitions_and_retention_are_closed() -> None:
    assert can_transition_job(NameGenerationJobStatus.PENDING, NameGenerationJobStatus.PROCESSING)
    assert can_transition_job(NameGenerationJobStatus.PROCESSING, NameGenerationJobStatus.ABANDONED)
    assert not can_transition_job(
        NameGenerationJobStatus.SUCCEEDED, NameGenerationJobStatus.PENDING
    )
    now = datetime(2026, 8, 30, tzinfo=UTC)
    assert terminal_job_is_due(
        status=NameGenerationJobStatus.FAILED,
        finished_at=now - timedelta(days=30),
        now=now,
        retention_days=30,
    )
    assert not terminal_job_is_due(
        status=NameGenerationJobStatus.PENDING, finished_at=None, now=now, retention_days=30
    )
    assert budget_bucket_is_due(
        period_type=BudgetPeriodType.DAILY,
        period_start=date(2026, 5, 31),
        today_jst=date(2026, 8, 30),
        retention_days=90,
    )


@pytest.mark.asyncio
async def test_disabled_generator_never_returns_and_request_has_no_identifiers() -> None:
    request = NameGenerationRequest(content="予約本文")
    assert tuple(request.__dataclass_fields__) == (
        "content",
        "max_length",
        "locale",
        "single_line",
        "prohibit_control_characters",
    )
    generator = DisabledNameGenerator()
    assert generator.available is False
    with pytest.raises(RuntimeError):
        await generator.generate(request)
    assert GeneratedScheduleName(" 予約名 ").value == "予約名"

    fake: NameGenerator = FakeNameGenerator("固定結果")
    assert await fake.generate(request) == GeneratedScheduleName("固定結果")
    assert fake.requests == [request]
