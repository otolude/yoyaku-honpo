import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from discord_ai_reminder_bot.infrastructure.database.testing import validate_test_database_url

EXPECTED_REVISION = "8e5b2f1c4a90"


@pytest.fixture(scope="session")
def test_database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not set; PostgreSQL integration tests are skipped")
    return validate_test_database_url(value)


@pytest_asyncio.fixture(scope="session")
async def test_engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(test_database_url, echo=False, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            database = (await connection.execute(text("SELECT current_database()"))).scalar_one()
            revision = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
            if "test" not in database.lower() or revision != EXPECTED_REVISION:
                pytest.fail("test database identity or Alembic revision is not safe")
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            autoflush=False,
            expire_on_commit=False,
        )
        async with factory() as session:
            yield session
        await transaction.rollback()
