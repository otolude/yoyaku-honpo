import asyncio
import importlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.post_draft_usage import PostDraftUsageReservation
from discord_ai_reminder_bot.domain.post_draft_usage import (
    JPY_MICROUNITS_PER_YEN,
    PostDraftGuildId,
    PostDraftOperationKey,
    PostDraftOperatorBudgetPolicy,
    PostDraftRateLimitPolicy,
    PostDraftUsagePolicy,
    PostDraftUsageReservationCode,
    PostDraftUserId,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    OperationLog,
    PostDraftOperatorBudgetBucket,
    PostDraftRateLimitBucket,
    PostDraftUsageReservationReceipt,
    Schedule,
)

NOW = datetime(2026, 9, 4, 4, 5, 6, tzinfo=UTC)
USER_ID = 8_111_111_111_111_111
GUILD_ID = 7_111_111_111_111_111
TABLES = (
    "post_draft_usage_reservation_receipts",
    "post_draft_rate_limit_buckets",
    "post_draft_operator_budget_buckets",
)


def repository_type():
    module = importlib.import_module(
        "discord_ai_reminder_bot.infrastructure.database.post_draft_usage_repository"
    )
    assert hasattr(module, "PostgreSQLPostDraftUsageRepository")
    return module.PostgreSQLPostDraftUsageRepository


def policy(
    *,
    user: int = 3,
    guild: int = 30,
    daily: int = 50,
    monthly: int = 500,
    monthly_cost: int = 500 * JPY_MICROUNITS_PER_YEN,
) -> PostDraftUsagePolicy:
    return PostDraftUsagePolicy(
        operator_budget=PostDraftOperatorBudgetPolicy(
            daily_request_limit=daily,
            monthly_request_limit=monthly,
            monthly_cost_limit_microunits=monthly_cost,
        ),
        rate_limit=PostDraftRateLimitPolicy(
            user_request_limit=user,
            guild_daily_request_limit=guild,
            global_daily_request_limit=daily,
        ),
    )


def reservation(
    *,
    operation_key: UUID | None = None,
    user_id: int = USER_ID,
    guild_id: int = GUILD_ID,
    maximum_cost: int = 1_000,
    usage_policy: PostDraftUsagePolicy | None = None,
    now: datetime = NOW,
) -> PostDraftUsageReservation:
    return PostDraftUsageReservation.create(
        operation_key=PostDraftOperationKey(operation_key or uuid4()),
        user_id=PostDraftUserId(user_id),
        guild_id=PostDraftGuildId(guild_id),
        maximum_cost_microunits=maximum_cost,
        now=now,
        policy=usage_policy or policy(),
    )


@pytest_asyncio.fixture(autouse=True)
async def clean_usage_tables(test_engine: AsyncEngine) -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await connection.execute(text("TRUNCATE " + ", ".join(TABLES)))
    yield
    async with test_engine.begin() as connection:
        await connection.execute(text("TRUNCATE " + ", ".join(TABLES)))


def factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def counts(engine: AsyncEngine) -> tuple[int, int, int]:
    async with factory(engine)() as session:
        return tuple(
            int(await session.scalar(select(func.count()).select_from(model)) or 0)
            for model in (
                PostDraftOperatorBudgetBucket,
                PostDraftRateLimitBucket,
                PostDraftUsageReservationReceipt,
            )
        )


async def bucket_values(engine: AsyncEngine) -> tuple[tuple[object, ...], ...]:
    async with factory(engine)() as session:
        operators = (
            await session.execute(
                select(
                    PostDraftOperatorBudgetBucket.period_type,
                    PostDraftOperatorBudgetBucket.reserved_request_count,
                    PostDraftOperatorBudgetBucket.reserved_cost_microunits,
                    PostDraftOperatorBudgetBucket.version,
                ).order_by(PostDraftOperatorBudgetBucket.period_type)
            )
        ).all()
        rates = (
            await session.execute(
                select(
                    PostDraftRateLimitBucket.scope_type,
                    PostDraftRateLimitBucket.scope_id,
                    PostDraftRateLimitBucket.request_count,
                    PostDraftRateLimitBucket.version,
                ).order_by(PostDraftRateLimitBucket.scope_type, PostDraftRateLimitBucket.scope_id)
            )
        ).all()
        return tuple(tuple(row) for row in (*operators, *rates))


@pytest.mark.asyncio
async def test_first_reservation_commits_four_buckets_and_opaque_receipt(
    test_engine: AsyncEngine,
) -> None:
    key = uuid4()
    result = await repository_type()(factory(test_engine)).reserve(reservation(operation_key=key))
    assert result.code is PostDraftUsageReservationCode.RESERVED
    assert await counts(test_engine) == (2, 2, 1)
    async with factory(test_engine)() as session:
        receipt = await session.get(PostDraftUsageReservationReceipt, key)
        assert receipt is not None
        assert receipt.reserved_at == NOW
        assert receipt.expires_at == NOW + timedelta(days=7)


@pytest.mark.asyncio
async def test_repeated_operation_key_is_already_reserved_without_increment(
    test_engine: AsyncEngine,
) -> None:
    key = uuid4()
    repository = repository_type()(factory(test_engine))
    assert (
        await repository.reserve(reservation(operation_key=key))
    ).code is PostDraftUsageReservationCode.RESERVED
    before = await bucket_values(test_engine)
    repeated = reservation(operation_key=key, user_id=USER_ID + 1, guild_id=GUILD_ID + 1)
    assert (
        await repository.reserve(repeated)
    ).code is PostDraftUsageReservationCode.ALREADY_RESERVED
    assert await bucket_values(test_engine) == before
    assert await counts(test_engine) == (2, 2, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit_name", "limit", "expected"),
    [
        ("user", 2, PostDraftUsageReservationCode.USER_RATE_LIMITED),
        ("guild", 2, PostDraftUsageReservationCode.GUILD_RATE_LIMITED),
        ("daily", 2, PostDraftUsageReservationCode.GLOBAL_DAILY_EXHAUSTED),
        ("monthly", 2, PostDraftUsageReservationCode.GLOBAL_MONTHLY_EXHAUSTED),
    ],
)
async def test_exact_request_limit_succeeds_and_limit_plus_one_rolls_back_everything(
    test_engine: AsyncEngine,
    limit_name: str,
    limit: int,
    expected: PostDraftUsageReservationCode,
) -> None:
    values = {"user": 100, "guild": 100, "daily": 100, "monthly": 100}
    values[limit_name] = limit
    if limit_name == "monthly":
        values["daily"] = limit
    values["guild"] = min(values["guild"], values["daily"])
    usage_policy = policy(**values)
    repository = repository_type()(factory(test_engine))
    for offset in range(limit):
        kwargs: dict[str, object] = {"usage_policy": usage_policy}
        if limit_name in {"guild", "daily", "monthly"}:
            kwargs["user_id"] = USER_ID + offset
        if limit_name in {"daily", "monthly"}:
            kwargs["guild_id"] = GUILD_ID + offset
        if limit_name == "monthly":
            kwargs["now"] = NOW + timedelta(days=offset)
        result = await repository.reserve(reservation(**kwargs))  # type: ignore[arg-type]
        assert result.code is PostDraftUsageReservationCode.RESERVED
    before_counts = await counts(test_engine)
    before_buckets = await bucket_values(test_engine)
    rejected_key = uuid4()
    rejected_kwargs: dict[str, object] = {
        "operation_key": rejected_key,
        "usage_policy": usage_policy,
    }
    if limit_name in {"guild", "daily", "monthly"}:
        rejected_kwargs["user_id"] = USER_ID + limit
    if limit_name in {"daily", "monthly"}:
        rejected_kwargs["guild_id"] = GUILD_ID + limit
    if limit_name == "monthly":
        rejected_kwargs["now"] = NOW + timedelta(days=limit)
    result = await repository.reserve(reservation(**rejected_kwargs))  # type: ignore[arg-type]
    assert result.code is expected
    assert await counts(test_engine) == before_counts
    assert await bucket_values(test_engine) == before_buckets


@pytest.mark.asyncio
async def test_exact_monthly_cost_succeeds_and_one_more_rolls_back(
    test_engine: AsyncEngine,
) -> None:
    usage_policy = policy(monthly_cost=2_000)
    repository = repository_type()(factory(test_engine))
    assert (
        await repository.reserve(reservation(maximum_cost=2_000, usage_policy=usage_policy))
    ).code is PostDraftUsageReservationCode.RESERVED
    before = await bucket_values(test_engine)
    result = await repository.reserve(reservation(maximum_cost=1, usage_policy=usage_policy))
    assert result.code is PostDraftUsageReservationCode.GLOBAL_COST_EXHAUSTED
    assert await bucket_values(test_engine) == before
    assert await counts(test_engine) == (2, 2, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["same_user", "same_guild", "different_guild"])
async def test_concurrent_reservations_have_no_lost_update_or_deadlock(
    test_engine: AsyncEngine,
    mode: str,
) -> None:
    usage_policy = policy(user=20, guild=20, daily=20, monthly=20)
    values = []
    for offset in range(10):
        user_id = USER_ID if mode == "same_user" else USER_ID + offset
        guild_id = GUILD_ID + offset if mode == "different_guild" else GUILD_ID
        values.append(reservation(user_id=user_id, guild_id=guild_id, usage_policy=usage_policy))
    repository = repository_type()(factory(test_engine))
    results = await asyncio.wait_for(
        asyncio.gather(*(repository.reserve(value) for value in values)), timeout=10
    )
    assert all(result.code is PostDraftUsageReservationCode.RESERVED for result in results)
    async with factory(test_engine)() as session:
        operator_counts = set(
            (
                await session.scalars(select(PostDraftOperatorBudgetBucket.reserved_request_count))
            ).all()
        )
        assert operator_counts == {10}


@pytest.mark.asyncio
async def test_concurrent_same_operation_key_reserves_once(test_engine: AsyncEngine) -> None:
    key = uuid4()
    repository = repository_type()(factory(test_engine))
    results = await asyncio.wait_for(
        asyncio.gather(*(repository.reserve(reservation(operation_key=key)) for _ in range(8))),
        timeout=10,
    )
    assert [result.code for result in results].count(PostDraftUsageReservationCode.RESERVED) == 1
    assert [result.code for result in results].count(
        PostDraftUsageReservationCode.ALREADY_RESERVED
    ) == 7
    assert await counts(test_engine) == (2, 2, 1)


@pytest.mark.asyncio
async def test_separate_repository_instances_share_committed_limit(
    test_engine: AsyncEngine,
) -> None:
    usage_policy = policy(user=1)
    first = repository_type()(factory(test_engine))
    second = repository_type()(factory(test_engine))
    assert (
        await first.reserve(reservation(usage_policy=usage_policy))
    ).code is PostDraftUsageReservationCode.RESERVED
    assert (
        await second.reserve(reservation(usage_policy=usage_policy))
    ).code is PostDraftUsageReservationCode.USER_RATE_LIMITED


@pytest.mark.asyncio
async def test_completed_reserve_releases_all_row_locks(test_engine: AsyncEngine) -> None:
    repository = repository_type()(factory(test_engine))
    await repository.reserve(reservation())
    async with factory(test_engine)() as session, session.begin():
        await session.execute(select(PostDraftOperatorBudgetBucket).with_for_update(nowait=True))
        await session.execute(select(PostDraftRateLimitBucket).with_for_update(nowait=True))
        await session.execute(select(PostDraftUsageReservationReceipt).with_for_update(nowait=True))


@pytest.mark.asyncio
async def test_rejection_leaves_schedules_and_operation_logs_unchanged(
    test_engine: AsyncEngine,
) -> None:
    async with factory(test_engine)() as session:
        before = (
            int(await session.scalar(select(func.count()).select_from(Schedule)) or 0),
            int(await session.scalar(select(func.count()).select_from(OperationLog)) or 0),
        )
    repository = repository_type()(factory(test_engine))
    usage_policy = policy(user=1)
    await repository.reserve(reservation(usage_policy=usage_policy))
    await repository.reserve(reservation(usage_policy=usage_policy))
    async with factory(test_engine)() as session:
        after = (
            int(await session.scalar(select(func.count()).select_from(Schedule)) or 0),
            int(await session.scalar(select(func.count()).select_from(OperationLog)) or 0),
        )
    assert after == before


@pytest.mark.asyncio
async def test_tables_store_no_payload_columns(test_engine: AsyncEngine) -> None:
    await repository_type()(factory(test_engine)).reserve(reservation())
    async with factory(test_engine)() as session:
        columns = (
            (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = ANY(:tables)"
                    ),
                    {"tables": list(TABLES)},
                )
            )
            .scalars()
            .all()
        )
    forbidden = {"content", "body", "prompt", "interaction_id", "schedule_id", "provider_id"}
    assert forbidden.isdisjoint(columns)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [SQLAlchemyError("fixed database failure"), asyncio.CancelledError()],
    ids=["database_error", "cancellation"],
)
async def test_exception_during_transaction_propagates_and_rolls_back(
    test_engine: AsyncEngine,
    error: BaseException,
) -> None:
    repository_class = repository_type()

    class FailingRepository(repository_class):
        async def _reserve_transaction(
            self, session: AsyncSession, value: PostDraftUsageReservation
        ) -> None:
            await super()._reserve_transaction(session, value)
            raise error

    repository = FailingRepository(factory(test_engine))
    with pytest.raises(type(error), match="fixed database failure" if str(error) else None):
        await repository.reserve(reservation())
    assert await counts(test_engine) == (0, 0, 0)
