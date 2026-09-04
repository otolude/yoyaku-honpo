import asyncio
import importlib
import inspect
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from discord_ai_reminder_bot.domain.post_draft_usage import (
    MAX_POSTGRES_BIGINT,
    PostDraftUsagePolicy,
)

NOW = datetime(2026, 9, 4, 4, 5, 6, tzinfo=UTC)
NAIVE_NOW = datetime(2026, 9, 4)  # noqa: DTZ001 - invalid-input test fixture
ERROR_CANARY = "cleanup-database-private-canary"
MODULE_NAME = "discord_ai_reminder_bot.application.post_draft_usage_cleanup"
MODULE_PATH = Path("src/discord_ai_reminder_bot/application/post_draft_usage_cleanup.py")


def cleanup_module():
    return importlib.import_module(MODULE_NAME)


def result_type():
    return cleanup_module().PostDraftUsageCleanupResult


def service_type():
    return cleanup_module().CleanupPostDraftUsageService


def unavailable_type():
    return cleanup_module().PostDraftUsageCleanupUnavailableError


def cutoff_helper():
    domain = importlib.import_module("discord_ai_reminder_bot.domain.post_draft_usage")
    return domain.post_draft_usage_cleanup_cutoffs


def result(*, receipts: int = 1, users: int = 2, guilds: int = 3, operators: int = 4):
    return result_type()(
        deleted_receipt_count=receipts,
        deleted_user_bucket_count=users,
        deleted_guild_bucket_count=guilds,
        deleted_operator_bucket_count=operators,
    )


class FakeCleanupRepository:
    def __init__(self, outcome: object | None = None) -> None:
        self.outcome = outcome
        self.calls: list[tuple[datetime, PostDraftUsagePolicy]] = []

    async def cleanup(self, *, now: datetime, policy: PostDraftUsagePolicy):
        self.calls.append((now, policy))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome if self.outcome is not None else result()


@pytest.mark.asyncio
@pytest.mark.parametrize("now", [NAIVE_NOW, None, True])
async def test_cleanup_rejects_naive_or_invalid_now_before_repository(now: object) -> None:
    repository = FakeCleanupRepository()
    service = service_type()(repository=repository)
    with pytest.raises(ValueError, match="^invalid post draft usage cleanup request$"):
        await service.cleanup(now=now, policy=PostDraftUsagePolicy())  # type: ignore[arg-type]
    assert repository.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [True, False, object(), None])
async def test_cleanup_rejects_bool_or_invalid_policy_before_repository(policy: object) -> None:
    repository = FakeCleanupRepository()
    service = service_type()(repository=repository)
    with pytest.raises(ValueError, match="^invalid post draft usage cleanup request$"):
        await service.cleanup(now=NOW, policy=policy)  # type: ignore[arg-type]
    assert repository.calls == []


@pytest.mark.asyncio
async def test_cleanup_calls_repository_once_with_normalized_utc_inputs() -> None:
    repository = FakeCleanupRepository()
    service = service_type()(repository=repository)
    policy = PostDraftUsagePolicy()
    assert await service.cleanup(now=NOW, policy=policy) == result()
    assert repository.calls == [(NOW, policy)]


@pytest.mark.asyncio
async def test_cleanup_database_error_becomes_fixed_unavailable_without_canary() -> None:
    repository = FakeCleanupRepository(RuntimeError(ERROR_CANARY))
    with pytest.raises(
        unavailable_type(), match="^post draft usage cleanup unavailable$"
    ) as caught:
        await service_type()(repository=repository).cleanup(now=NOW, policy=PostDraftUsagePolicy())
    assert ERROR_CANARY not in str(caught.value)
    assert caught.value.__cause__ is None
    assert len(repository.calls) == 1


@pytest.mark.asyncio
async def test_cleanup_cancellation_is_propagated() -> None:
    repository = FakeCleanupRepository(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await service_type()(repository=repository).cleanup(now=NOW, policy=PostDraftUsagePolicy())
    assert len(repository.calls) == 1


def test_cleanup_result_has_only_four_fixed_counts() -> None:
    value = result()
    assert tuple(value.__dataclass_fields__) == (
        "deleted_receipt_count",
        "deleted_user_bucket_count",
        "deleted_guild_bucket_count",
        "deleted_operator_bucket_count",
    )
    assert not hasattr(value, "user_id")
    assert not hasattr(value, "content")


def test_cleanup_cutoffs_normalize_utc_and_derive_jst_date_boundaries_from_policy() -> None:
    cutoffs = cutoff_helper()(NOW, policy=PostDraftUsagePolicy())
    assert cutoffs.receipt_expires_at == NOW
    assert cutoffs.user_window_start == datetime(2026, 8, 28, 4, 5, 6, tzinfo=UTC)
    assert cutoffs.guild_window_start == datetime(2026, 8, 5, 4, 5, 6, tzinfo=UTC)
    assert cutoffs.operator_daily_before == date(2026, 6, 6)
    assert cutoffs.operator_monthly_before == date(2026, 6, 1)
    assert tuple(cutoffs.__dataclass_fields__) == (
        "receipt_expires_at",
        "user_window_start",
        "guild_window_start",
        "operator_daily_before",
        "operator_monthly_before",
    )


@pytest.mark.parametrize("invalid", [-1, True, False, MAX_POSTGRES_BIGINT + 1, 1.0, None])
def test_cleanup_result_rejects_invalid_counts(invalid: object) -> None:
    with pytest.raises(ValueError, match="^invalid post draft usage cleanup result$"):
        result(receipts=invalid)  # type: ignore[arg-type]


def test_cleanup_application_boundary_and_privacy() -> None:
    parameters = inspect.signature(service_type()).parameters
    assert tuple(parameters) == ("repository",)
    assert tuple(inspect.signature(service_type().cleanup).parameters) == (
        "self",
        "now",
        "policy",
    )
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "infrastructure" not in source
    assert "sqlalchemy" not in source
    assert "logging" not in source
    assert "retry" not in source.lower()
    assert ERROR_CANARY not in source
