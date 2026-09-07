import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import pytest
from sqlalchemy import func, select, table, text
from sqlalchemy.ext.asyncio import AsyncEngine

from discord_ai_reminder_bot.infrastructure.database import migrate
from discord_ai_reminder_bot.infrastructure.database.migration_safety import (
    MigrationSafetyError,
    verify_connected_database,
)

TABLES: Sequence[str] = (
    "schedules",
    "schedule_runs",
    "delivery_attempts",
    "operation_logs",
    "notification_logs",
    "notification_attempts",
    "name_generation_jobs",
    "name_generation_budget_buckets",
)


@contextmanager
def preserve_logging_state() -> Iterator[None]:
    root = logging.getLogger()
    manager = logging.root.manager
    root_state = (
        root.disabled,
        root.level,
        root.propagate,
        list(root.handlers),
        list(root.filters),
    )
    states = {
        name: (
            logger.disabled,
            logger.level,
            logger.propagate,
            list(logger.handlers),
            list(logger.filters),
        )
        for name, logger in manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    try:
        yield
    finally:
        root.disabled, root.level, root.propagate = root_state[:3]
        root.handlers[:] = root_state[3]
        root.filters[:] = root_state[4]
        for name, state in states.items():
            logger = logging.getLogger(name)
            logger.disabled, logger.level, logger.propagate = state[:3]
            logger.handlers[:] = state[3]
            logger.filters[:] = state[4]
        for name, logger in list(manager.loggerDict.items()):
            if name not in states and isinstance(logger, logging.Logger):
                logger.handlers.clear()
                logger.filters.clear()
                logger.disabled = False


async def database_snapshot(engine: AsyncEngine) -> tuple[str, tuple[int, ...], tuple[str, ...]]:
    async with engine.connect() as connection:
        revision = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
        observed_counts = []
        for table_name in TABLES:
            count = (
                await connection.execute(select(func.count()).select_from(table(table_name)))
            ).scalar_one()
            observed_counts.append(int(count))
        counts = tuple(observed_counts)
        schema = tuple(
            (
                await connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' ORDER BY table_name"
                    )
                )
            ).scalars()
        )
    return revision, counts, schema


@pytest.mark.asyncio
async def test_connected_database_mismatch_is_read_only_and_preserves_schema(
    test_engine: AsyncEngine,
) -> None:
    before = await database_snapshot(test_engine)
    async with test_engine.connect() as connection:
        with pytest.raises(MigrationSafetyError, match="identity"):
            await verify_connected_database(connection, "discord_bot_dev")
    after = await database_snapshot(test_engine)
    assert before == after


def test_official_wrapper_current_check_and_noop_upgrade_on_test_database(
    test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", test_database_url)
    common = ["--target", "test", "--expected-database", "discord_bot_test"]
    with preserve_logging_state():
        assert migrate.main([*common, "current"]) == 0
        assert migrate.main([*common, "check"]) == 0
        assert (
            migrate.main(
                [
                    *common,
                    "--confirm",
                    "test:discord_bot_test:upgrade",
                    "upgrade",
                    "head",
                ]
            )
            == 0
        )
