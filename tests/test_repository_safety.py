import pytest

from discord_ai_reminder_bot.infrastructure.database.exceptions import UnsafeTestDatabaseError
from discord_ai_reminder_bot.infrastructure.database.repositories import MAX_LIST_LIMIT
from discord_ai_reminder_bot.infrastructure.database.testing import validate_test_database_url


def test_local_test_database_url_is_allowed() -> None:
    value = "postgresql+psycopg://user:password@127.0.0.1:55432/discord_bot_test"
    assert validate_test_database_url(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "postgresql+psycopg://user:password@127.0.0.1:5432/discord_bot_dev",
        "postgresql+psycopg://user:password@db.example.com:5432/discord_bot_test",
        "postgresql+psycopg://user:password@127.0.0.1:5432/discord_bot",
        "sqlite:///discord_bot_test.sqlite3",
    ],
)
def test_unsafe_test_database_url_is_rejected(value: str) -> None:
    with pytest.raises(UnsafeTestDatabaseError):
        validate_test_database_url(value)


def test_repository_list_limit_is_bounded() -> None:
    assert MAX_LIST_LIMIT == 100
