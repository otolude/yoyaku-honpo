from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

import discord_ai_reminder_bot.config as config_module
from discord_ai_reminder_bot.config import DatabaseSettings, Settings, load_database_settings

ENVIRONMENT_KEYS = (
    "APP_ENV",
    "LOG_LEVEL",
    "TIMEZONE",
    "DISCORD_BOT_TOKEN",
    "DISCORD_APPLICATION_ID",
    "DISCORD_GUILD_ID",
    "DISCORD_ALLOWED_ROLE_IDS",
    "DISCORD_OPERATOR_USER_ID",
    "DISCORD_OPERATOR_CHANNEL_ID",
    "DISCORD_GUILD_COMMAND_SYNC_ENABLED",
    "DATABASE_URL",
    "SCHEDULER_POLL_INTERVAL_SECONDS",
    "SCHEDULER_BATCH_SIZE",
    "SCHEDULER_MAX_CONCURRENCY",
    "SCHEDULER_PROCESSING_TIMEOUT_SECONDS",
    "NOTIFICATION_POLL_INTERVAL_SECONDS",
    "NOTIFICATION_BATCH_SIZE",
    "NOTIFICATION_MAX_CONCURRENCY",
    "NOTIFICATION_PROCESSING_TIMEOUT_SECONDS",
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
        "DISCORD_APPLICATION_ID": "900000000000000001",
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
        "NOTIFICATION_POLL_INTERVAL_SECONDS": "10",
        "NOTIFICATION_BATCH_SIZE": "20",
        "NOTIFICATION_MAX_CONCURRENCY": "5",
        "NOTIFICATION_PROCESSING_TIMEOUT_SECONDS": "120",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def load_without_env_file() -> Settings:
    return Settings(_env_file=None)


def load_database_without_env_file() -> DatabaseSettings:
    return DatabaseSettings(_env_file=None)


def test_loads_valid_settings(valid_environment: dict[str, str]) -> None:
    settings = load_without_env_file()

    assert settings.app_env == "test"
    assert settings.timezone == "Asia/Tokyo"
    assert settings.discord_application_id == 900000000000000001
    assert settings.discord_guild_id == 100000000000000001
    assert settings.scheduler_poll_interval_seconds == 10
    assert settings.notification_poll_interval_seconds == 10
    assert settings.notification_batch_size == 20
    assert settings.notification_max_concurrency == 5
    assert settings.notification_processing_timeout_seconds == 120
    assert settings.ai_name_generation_enabled is False
    assert settings.discord_guild_command_sync_enabled is False
    assert settings.name_generation_budget_policy().monthly_cost_limit_microunits == 100_000_000


def test_missing_discord_application_id_is_rejected_before_startup(
    valid_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISCORD_APPLICATION_ID")

    with pytest.raises(ValidationError):
        load_without_env_file()


@pytest.mark.parametrize("value", ["", "0", "-1", "not-an-integer", "1.5"])
def test_rejects_invalid_discord_application_id_strings(
    valid_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DISCORD_APPLICATION_ID", value)

    with pytest.raises(ValidationError):
        load_without_env_file()


def test_rejects_boolean_discord_application_id(valid_environment: dict[str, str]) -> None:
    values: dict[str, object] = dict(valid_environment)
    values["DISCORD_APPLICATION_ID"] = True

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_rejects_discord_application_id_equal_to_guild_id_without_reflection(
    valid_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISCORD_APPLICATION_ID", valid_environment["DISCORD_GUILD_ID"])

    with pytest.raises(ValidationError) as captured:
        load_without_env_file()

    assert valid_environment["DISCORD_GUILD_ID"] not in str(captured.value)


def test_discord_application_id_is_absent_from_repr_and_logs(
    valid_environment: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    configured = load_without_env_file()

    with caplog.at_level(logging.INFO):
        logging.getLogger("test.application-identity").info("identity_configuration_loaded")

    observed = repr(configured) + caplog.text
    assert valid_environment["DISCORD_APPLICATION_ID"] not in observed


def test_malicious_application_id_is_not_reflected(
    valid_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "private-id\nAPPLICATION_COMMANDS_SYNCED=fake"
    monkeypatch.setenv("DISCORD_APPLICATION_ID", canary)

    with pytest.raises(ValidationError) as captured:
        load_without_env_file()

    observed = str(captured.value) + repr(captured.value)
    assert canary not in observed
    assert "APPLICATION_COMMANDS_SYNCED=fake" not in observed


def test_remote_schema_gate_requires_three_matching_application_identities() -> None:
    identity = 900000000000000001

    assert config_module.discord_application_identity_matches(
        configured_application_id=identity,
        oauth_application_id=identity,
        schema_application_id=identity,
    )


@pytest.mark.parametrize(
    ("oauth_application_id", "schema_application_id"),
    [(900000000000000002, 900000000000000001), (900000000000000001, 900000000000000002)],
)
def test_remote_schema_gate_rejects_any_identity_mismatch(
    oauth_application_id: int, schema_application_id: int
) -> None:
    assert not config_module.discord_application_identity_matches(
        configured_application_id=900000000000000001,
        oauth_application_id=oauth_application_id,
        schema_application_id=schema_application_id,
    )


def test_env_example_requires_private_discord_application_id() -> None:
    lines = Path(".env.example").read_text(encoding="utf-8").splitlines()

    assert lines.count("DISCORD_APPLICATION_ID=") == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("True", True),
        ("false", False),
        ("FALSE", False),
        ("False", False),
    ],
)
def test_guild_command_sync_accepts_only_explicit_booleans(
    valid_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("DISCORD_GUILD_COMMAND_SYNC_ENABLED", value)

    assert load_without_env_file().discord_guild_command_sync_enabled is expected


@pytest.mark.parametrize("value", ["", "0", "1", "yes", "no", "on", "off"])
def test_rejects_invalid_guild_command_sync_values(
    valid_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DISCORD_GUILD_COMMAND_SYNC_ENABLED", value)

    with pytest.raises(ValidationError):
        load_without_env_file()


def test_env_example_disables_guild_command_sync() -> None:
    lines = Path(".env.example").read_text(encoding="utf-8").splitlines()

    assert lines.count("DISCORD_GUILD_COMMAND_SYNC_ENABLED=false") == 1


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AI_NAME_GENERATION_ENABLED", "1"),
        ("AI_NAME_GENERATION_MAX_CONCURRENCY", "2"),
        ("AI_NAME_GENERATION_DAILY_REQUEST_LIMIT", "0"),
        ("AI_NAME_GENERATION_MONTHLY_REQUEST_LIMIT", "49"),
        ("AI_NAME_GENERATION_MONTHLY_COST_LIMIT_MICROUNITS", "9223372036854775808"),
        ("AI_NAME_GENERATION_COST_CURRENCY", "USD"),
        ("AI_NAME_GENERATION_PROCESSING_LEASE_SECONDS", "9"),
    ],
)
def test_rejects_unsafe_ai_name_generation_settings(
    valid_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        load_without_env_file()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("NOTIFICATION_POLL_INTERVAL_SECONDS", "0"),
        ("NOTIFICATION_POLL_INTERVAL_SECONDS", "nan"),
        ("NOTIFICATION_BATCH_SIZE", "0"),
        ("NOTIFICATION_BATCH_SIZE", "21"),
        ("NOTIFICATION_MAX_CONCURRENCY", "0"),
        ("NOTIFICATION_MAX_CONCURRENCY", "21"),
        ("NOTIFICATION_PROCESSING_TIMEOUT_SECONDS", "0"),
    ],
)
def test_rejects_invalid_notification_settings(
    valid_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
) -> None:
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        load_without_env_file()


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


def test_loads_database_settings_without_discord_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for key in ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://database_user:database-password@localhost/database_test",
    )
    monkeypatch.chdir(tmp_path)

    settings = load_database_settings()

    assert settings.database_url.get_secret_value().endswith("@localhost/database_test")


def test_database_settings_rejects_invalid_url(monkeypatch: pytest.MonkeyPatch) -> None:
    invalid_url = "mysql://database_user:do-not-expose@localhost/database_test"
    monkeypatch.setenv("DATABASE_URL", invalid_url)

    with pytest.raises(ValidationError) as error:
        load_database_without_env_file()

    assert invalid_url not in str(error.value)
    assert "do-not-expose" not in str(error.value)


def test_database_settings_masks_url_in_display(monkeypatch: pytest.MonkeyPatch) -> None:
    password = "database-password"
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql+psycopg://database_user:{password}@localhost/database_test",
    )

    settings = load_database_without_env_file()

    assert password not in repr(settings)
    assert "**********" in repr(settings)
