from __future__ import annotations

import pytest
from pydantic import ValidationError

from discord_ai_reminder_bot.config import Settings

ENVIRONMENT_KEYS = (
    "APP_ENV",
    "LOG_LEVEL",
    "TIMEZONE",
    "DISCORD_BOT_TOKEN",
    "DISCORD_GUILD_ID",
    "DISCORD_ALLOWED_ROLE_IDS",
    "DISCORD_OPERATOR_USER_ID",
    "DISCORD_OPERATOR_CHANNEL_ID",
    "DATABASE_URL",
    "SCHEDULER_POLL_INTERVAL_SECONDS",
    "SCHEDULER_BATCH_SIZE",
    "SCHEDULER_MAX_CONCURRENCY",
    "SCHEDULER_PROCESSING_TIMEOUT_SECONDS",
)


@pytest.fixture
def valid_environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    for key in ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)

    values = {
        "APP_ENV": "test",
        "LOG_LEVEL": "INFO",
        "TIMEZONE": "Asia/Tokyo",
        "DISCORD_BOT_TOKEN": "test-token-never-use-in-production",
        "DISCORD_GUILD_ID": "100000000000000001",
        "DISCORD_ALLOWED_ROLE_IDS": "200000000000000001,200000000000000002",
        "DISCORD_OPERATOR_USER_ID": "300000000000000001",
        "DISCORD_OPERATOR_CHANNEL_ID": "400000000000000001",
        "DATABASE_URL": (
            "postgresql+psycopg://discord_bot:test-password@localhost:5432/discord_bot_test"
        ),
        "SCHEDULER_POLL_INTERVAL_SECONDS": "10",
        "SCHEDULER_BATCH_SIZE": "20",
        "SCHEDULER_MAX_CONCURRENCY": "5",
        "SCHEDULER_PROCESSING_TIMEOUT_SECONDS": "120",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def load_without_env_file() -> Settings:
    return Settings(_env_file=None)


def test_loads_valid_settings(valid_environment: dict[str, str]) -> None:
    settings = load_without_env_file()

    assert settings.app_env == "test"
    assert settings.timezone == "Asia/Tokyo"
    assert settings.discord_guild_id == 100000000000000001
    assert settings.scheduler_poll_interval_seconds == 10


def test_detects_missing_required_value(
    valid_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISCORD_GUILD_ID")

    with pytest.raises(ValidationError) as error:
        load_without_env_file()

    assert "DISCORD_GUILD_ID" in str(error.value)


@pytest.mark.parametrize("invalid_id", ["not-a-number", "0", "-1"])
def test_rejects_invalid_discord_id(
    valid_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch, invalid_id: str
) -> None:
    monkeypatch.setenv("DISCORD_GUILD_ID", invalid_id)

    with pytest.raises(ValidationError):
        load_without_env_file()


def test_rejects_timezone_other_than_asia_tokyo(
    valid_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIMEZONE", "UTC")

    with pytest.raises(ValidationError):
        load_without_env_file()


def test_loads_multiple_allowed_role_ids(valid_environment: dict[str, str]) -> None:
    settings = load_without_env_file()

    assert settings.discord_allowed_role_ids == (
        200000000000000001,
        200000000000000002,
    )


def test_masks_secrets_in_settings_display(valid_environment: dict[str, str]) -> None:
    settings = load_without_env_file()
    displayed = repr(settings)

    assert valid_environment["DISCORD_BOT_TOKEN"] not in displayed
    assert "test-password" not in displayed
    assert "**********" in displayed


def test_masks_secret_in_validation_error(
    valid_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_url = "mysql://discord_bot:do-not-expose@localhost/discord_bot_test"
    monkeypatch.setenv("DATABASE_URL", secret_url)

    with pytest.raises(ValidationError) as error:
        load_without_env_file()

    assert secret_url not in str(error.value)
    assert "do-not-expose" not in str(error.value)
