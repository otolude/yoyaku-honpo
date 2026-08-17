from __future__ import annotations

import logging
from typing import Self

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from discord_ai_reminder_bot.infrastructure.database.health import (
    check_database_connection,
    verify_database_connection,
)
from discord_ai_reminder_bot.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)

TEST_DATABASE_URL = "postgresql+psycopg://test_user:test-password@localhost/test_database"


def test_creates_async_engine_with_safe_settings() -> None:
    engine = create_database_engine(SecretStr(TEST_DATABASE_URL))

    assert isinstance(engine, AsyncEngine)
    assert engine.echo is False
    assert engine.sync_engine.hide_parameters is True
    assert engine.sync_engine.pool._pre_ping is True
    assert "test-password" not in repr(engine)


@pytest.mark.asyncio
async def test_session_factory_creates_independent_async_sessions() -> None:
    engine = create_database_engine(SecretStr(TEST_DATABASE_URL))
    session_factory = create_session_factory(engine)

    first_session = session_factory()
    second_session = session_factory()
    try:
        assert isinstance(first_session, AsyncSession)
        assert isinstance(second_session, AsyncSession)
        assert first_session is not second_session
    finally:
        await first_session.close()
        await second_session.close()
        await engine.dispose()


class FakeResult:
    def scalar_one(self) -> int:
        return 1


class FakeConnection:
    def __init__(self) -> None:
        self.statement = ""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, statement: object) -> FakeResult:
        self.statement = str(statement)
        return FakeResult()


class FakeEngine:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.disposed = False

    def connect(self) -> FakeConnection:
        return self.connection

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_connection_check_executes_select_one() -> None:
    engine = FakeEngine()

    await check_database_connection(engine)  # type: ignore[arg-type]

    assert engine.connection.statement == "SELECT 1"


@pytest.mark.asyncio
async def test_connection_verification_disposes_engine_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    monkeypatch.setattr(
        "discord_ai_reminder_bot.infrastructure.database.health.create_database_engine",
        lambda database_url: engine,
    )

    await verify_database_connection(SecretStr(TEST_DATABASE_URL))

    assert engine.disposed is True


@pytest.mark.asyncio
async def test_connection_verification_disposes_engine_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()

    async def fail_connection_check(unused_engine: object) -> None:
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(
        "discord_ai_reminder_bot.infrastructure.database.health.create_database_engine",
        lambda database_url: engine,
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.infrastructure.database.health.check_database_connection",
        fail_connection_check,
    )

    with pytest.raises(ConnectionError, match="database unavailable"):
        await verify_database_connection(SecretStr(TEST_DATABASE_URL))

    assert engine.disposed is True


def test_database_url_is_not_written_to_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        engine = create_database_engine(SecretStr(TEST_DATABASE_URL))

    assert TEST_DATABASE_URL not in caplog.text
    assert "test-password" not in caplog.text
    assert "test-password" not in repr(engine)
