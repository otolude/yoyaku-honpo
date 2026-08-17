"""Safety validation for explicitly requested PostgreSQL integration tests."""

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from discord_ai_reminder_bot.infrastructure.database.exceptions import UnsafeTestDatabaseError

_LOCAL_TEST_HOSTS = {"127.0.0.1", "localhost", "::1"}


def validate_test_database_url(value: str) -> str:
    """Reject development, production-like, and non-test PostgreSQL targets."""
    try:
        url = make_url(value)
    except ArgumentError, TypeError, ValueError:
        raise UnsafeTestDatabaseError("TEST_DATABASE_URL is invalid") from None

    database = (url.database or "").lower()
    if url.drivername != "postgresql+psycopg":
        raise UnsafeTestDatabaseError("test database must use postgresql+psycopg")
    if url.host not in _LOCAL_TEST_HOSTS:
        raise UnsafeTestDatabaseError("test database must run on a local-only host")
    if database == "discord_bot_dev":
        raise UnsafeTestDatabaseError("development database is forbidden for tests")
    if "test" not in database or database in {"postgres", "template0", "template1"}:
        raise UnsafeTestDatabaseError("database name must clearly identify a test database")
    return value
