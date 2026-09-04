import asyncio
import importlib
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.domain.post_draft_usage import PostDraftUsagePolicy
from discord_ai_reminder_bot.infrastructure.database.models import (
    NameGenerationBudgetBucket,
    OperationLog,
    PostDraftOperatorBudgetBucket,
    PostDraftRateLimitBucket,
    PostDraftUsageReservationReceipt,
    Schedule,
)

NOW = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)
TARGET_TABLES = (
    "post_draft_usage_reservation_receipts",
    "post_draft_rate_limit_buckets",
    "post_draft_operator_budget_buckets",
)


def cleanup_module():
    return importlib.import_module(
        "discord_ai_reminder_bot.infrastructure.database.post_draft_usage_cleanup_repository"
    )


def repository_type():
    return cleanup_module().PostgreSQLPostDraftUsageCleanupRepository


def factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture(autouse=True)
async def clean_usage_tables(test_engine: AsyncEngine) -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await connection.execute(text("TRUNCATE " + ", ".join(TARGET_TABLES)))
    yield
    async with test_engine.begin() as connection:
        await connection.execute(text("TRUNCATE " + ", ".join(TARGET_TABLES)))


async def counts(engine: AsyncEngine) -> tuple[int, int, int]:
    async with factory(engine)() as session:
        models = (
            PostDraftUsageReservationReceipt,
            PostDraftRateLimitBucket,
            PostDraftOperatorBudgetBucket,
        )
        values = [await session.scalar(select(func.count()).select_from(model)) for model in models]
        return tuple(int(value or 0) for value in values)  # type: ignore[return-value]


def receipt(*, expires_at: datetime) -> PostDraftUsageReservationReceipt:
    return PostDraftUsageReservationReceipt(
        operation_key=uuid4(), reserved_at=expires_at - timedelta(days=1), expires_at=expires_at
    )


def rate(*, scope: str, window: str, start: datetime, scope_id: int):
    return PostDraftRateLimitBucket(
        scope_type=scope,
        scope_id=scope_id,
        window_type=window,
        window_start=start,
        request_count=1,
        version=1,
        created_at=start,
        updated_at=start,
    )


def operator(*, period: str, start: date):
    return PostDraftOperatorBudgetBucket(
        period_type=period,
        period_start=start,
        reserved_request_count=1,
        reserved_cost_microunits=1,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


async def run_cleanup(engine: AsyncEngine):
    return await repository_type()(factory(engine)).cleanup(now=NOW, policy=PostDraftUsagePolicy())


@pytest.mark.asyncio
async def test_receipt_cleanup_deletes_before_and_equal_boundary_only(
    test_engine: AsyncEngine,
) -> None:
    async with factory(test_engine).begin() as session:
        session.add_all(
            [
                receipt(expires_at=NOW - timedelta(microseconds=1)),
                receipt(expires_at=NOW),
                receipt(expires_at=NOW + timedelta(microseconds=1)),
            ]
        )
    result = await run_cleanup(test_engine)
    assert result.deleted_receipt_count == 2
    assert await counts(test_engine) == (1, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "window", "days", "field"),
    [
        ("user", "short", 7, "deleted_user_bucket_count"),
        ("guild", "daily", 30, "deleted_guild_bucket_count"),
    ],
)
async def test_rate_bucket_cleanup_uses_inclusive_boundary_and_exact_scope_window(
    test_engine: AsyncEngine, scope: str, window: str, days: int, field: str
) -> None:
    cutoff = NOW - timedelta(days=days)
    async with factory(test_engine).begin() as session:
        session.add_all(
            [
                rate(
                    scope=scope, window=window, start=cutoff - timedelta(microseconds=1), scope_id=1
                ),
                rate(scope=scope, window=window, start=cutoff, scope_id=2),
                rate(
                    scope=scope, window=window, start=cutoff + timedelta(microseconds=1), scope_id=3
                ),
            ]
        )
    result = await run_cleanup(test_engine)
    assert getattr(result, field) == 2
    assert await counts(test_engine) == (0, 1, 0)


@pytest.mark.asyncio
async def test_operator_cleanup_uses_strict_jst_daily_and_monthly_date_boundaries(
    test_engine: AsyncEngine,
) -> None:
    async with factory(test_engine).begin() as session:
        session.add_all(
            [
                operator(period="daily", start=date(2026, 6, 6)),
                operator(period="daily", start=date(2026, 6, 7)),
                operator(period="daily", start=date(2026, 9, 5)),
                operator(period="monthly", start=date(2026, 5, 1)),
                operator(period="monthly", start=date(2026, 6, 1)),
                operator(period="monthly", start=date(2026, 9, 1)),
            ]
        )
    result = await run_cleanup(test_engine)
    assert result.deleted_operator_bucket_count == 2
    assert await counts(test_engine) == (0, 0, 4)


@pytest.mark.asyncio
async def test_all_classifications_commit_once_with_exact_counts_and_other_tables_unchanged(
    test_engine: AsyncEngine,
) -> None:
    async with factory(test_engine).begin() as session:
        session.add_all(
            [
                receipt(expires_at=NOW),
                rate(scope="user", window="short", start=NOW - timedelta(days=7), scope_id=10),
                rate(scope="guild", window="daily", start=NOW - timedelta(days=30), scope_id=11),
                operator(period="daily", start=date(2026, 6, 6)),
                NameGenerationBudgetBucket(
                    period_type="daily",
                    period_start=date(2026, 1, 1),
                    reserved_request_count=1,
                    reserved_cost_microunits=1,
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            ]
        )
    async with factory(test_engine)() as session:
        before = (
            await session.scalar(select(func.count()).select_from(Schedule)),
            await session.scalar(select(func.count()).select_from(OperationLog)),
            await session.scalar(select(func.count()).select_from(NameGenerationBudgetBucket)),
        )
    result = await run_cleanup(test_engine)
    assert (
        result.deleted_receipt_count,
        result.deleted_user_bucket_count,
        result.deleted_guild_bucket_count,
        result.deleted_operator_bucket_count,
    ) == (1, 1, 1, 1)
    async with factory(test_engine)() as session:
        after = (
            await session.scalar(select(func.count()).select_from(Schedule)),
            await session.scalar(select(func.count()).select_from(OperationLog)),
            await session.scalar(select(func.count()).select_from(NameGenerationBudgetBucket)),
        )
    assert after == before


@pytest.mark.asyncio
async def test_cleanup_works_from_a_second_repository_instance(test_engine: AsyncEngine) -> None:
    async with factory(test_engine).begin() as session:
        session.add(receipt(expires_at=NOW))
    first = repository_type()(factory(test_engine))
    second = repository_type()(factory(test_engine))
    assert (
        await first.cleanup(now=NOW - timedelta(seconds=1), policy=PostDraftUsagePolicy())
    ).deleted_receipt_count == 0
    assert (await second.cleanup(now=NOW, policy=PostDraftUsagePolicy())).deleted_receipt_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [SQLAlchemyError("fixed"), asyncio.CancelledError()])
async def test_cleanup_failure_rolls_back_every_classification(
    test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    async with factory(test_engine).begin() as session:
        session.add_all(
            [
                receipt(expires_at=NOW),
                rate(scope="user", window="short", start=NOW - timedelta(days=7), scope_id=20),
            ]
        )
    module = cleanup_module()

    async def fail_after_first_delete(session: AsyncSession, statements: dict[str, object]):
        await session.execute(statements["receipt"])
        raise failure

    monkeypatch.setattr(module, "_execute_cleanup_transaction", fail_after_first_delete)
    with pytest.raises(type(failure)):
        await run_cleanup(test_engine)
    assert await counts(test_engine) == (1, 1, 0)


@pytest.mark.asyncio
async def test_cleanup_keeps_schema_revision_tables_and_payload_boundary(
    test_engine: AsyncEngine,
) -> None:
    async with test_engine.connect() as connection:
        revision_before = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        table_names_before = set(
            await connection.run_sync(lambda sync: inspect(sync).get_table_names(schema="public"))
        )
    await run_cleanup(test_engine)
    async with test_engine.connect() as connection:
        revision_after = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        table_names_after = set(
            await connection.run_sync(lambda sync: inspect(sync).get_table_names(schema="public"))
        )
        columns = {
            row["name"]
            for table in TARGET_TABLES
            for row in await connection.run_sync(
                lambda sync, table=table: inspect(sync).get_columns(table, schema="public")
            )
        }
    assert revision_before == revision_after == "c72e91f4b6a3"
    assert table_names_after == table_names_before
    assert columns.isdisjoint({"content", "purpose", "key_points", "provider", "schedule_id"})
