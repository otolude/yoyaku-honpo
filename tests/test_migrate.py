import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from discord_ai_reminder_bot.infrastructure.database import migrate
from discord_ai_reminder_bot.infrastructure.database.migration_safety import MigrationSafetyError


def arguments(command: str, **values) -> Namespace:
    return Namespace(
        command=command,
        target=values.get("target"),
        expected_database=values.get("expected_database"),
        confirm=values.get("confirm"),
        revision=values.get("revision"),
        message=values.get("message"),
    )


def test_database_free_commands_do_not_load_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = MagicMock(side_effect=AssertionError("credentials must not be loaded"))
    monkeypatch.setattr(migrate, "select_database_url", selected)
    heads = MagicMock()
    history = MagicMock()
    monkeypatch.setattr(migrate.command, "heads", heads)
    monkeypatch.setattr(migrate.command, "history", history)
    migrate.run(arguments("heads"))
    migrate.run(arguments("history"))
    assert not selected.called
    heads.assert_called_once()
    history.assert_called_once()


def test_current_and_check_need_identity_but_not_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        migrate,
        "select_database_url",
        lambda invocation: SecretStr(
            "postgresql+psycopg://test-user:test-password@localhost/discord_bot_test"
        ),
    )
    current = MagicMock()
    check = MagicMock()
    monkeypatch.setattr(migrate.command, "current", current)
    monkeypatch.setattr(migrate.command, "check", check)
    common = {"target": "test", "expected_database": "discord_bot_test"}
    migrate.run(arguments("current", **common))
    migrate.run(arguments("check", **common))
    current.assert_called_once()
    check.assert_called_once()


@pytest.mark.parametrize("operation", ["upgrade", "downgrade", "stamp"])
def test_database_write_commands_require_confirmation_before_loading_url(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = MagicMock(side_effect=AssertionError("must fail before credentials"))
    monkeypatch.setattr(migrate, "select_database_url", selected)
    with pytest.raises(MigrationSafetyError):
        migrate.run(
            arguments(
                operation,
                target="test",
                expected_database="discord_bot_test",
                revision="head",
            )
        )
    assert not selected.called


def test_autogenerate_has_separate_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = MagicMock(side_effect=AssertionError("must fail before credentials"))
    monkeypatch.setattr(migrate, "select_database_url", selected)
    with pytest.raises(MigrationSafetyError):
        migrate.run(
            arguments(
                "autogenerate",
                target="test",
                expected_database="discord_bot_test",
                confirm="test:discord_bot_test:upgrade",
                message="unsafe",
            )
        )
    assert not selected.called


def test_parser_rejects_offline_and_unknown_commands() -> None:
    parser = migrate.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--target", "test", "upgrade", "head", "--sql"])
    with pytest.raises(SystemExit):
        parser.parse_args(["unknown"])


def test_direct_alembic_upgrade_fails_closed_without_printing_credentials() -> None:
    secret = "postgresql+psycopg://private-user:private-password@private-host/discord_bot_test"
    environment = {
        "PATH": "",
        "PYTHONPATH": str(Path.cwd() / "src"),
        "DATABASE_URL": secret,
        "TEST_DATABASE_URL": secret,
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert all(
        value not in output
        for value in (secret, "private-user", "private-password", "private-host")
    )


def test_direct_alembic_connection_failure_is_sanitized() -> None:
    secret = "postgresql+psycopg://private-user:private-password@127.0.0.1:1/discord_bot_test"
    environment = {
        "PATH": "",
        "PYTHONPATH": str(Path.cwd() / "src"),
        "TEST_DATABASE_URL": secret,
        "MIGRATION_TARGET_ENV": "test",
        "MIGRATION_EXPECTED_DATABASE": "discord_bot_test",
        "MIGRATION_APPLY_CONFIRMATION": "test:discord_bot_test:upgrade",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert all(
        value not in output
        for value in (secret, "private-user", "private-password", "127.0.0.1", "port 1")
    )


def test_wrapper_error_does_not_expose_url(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    secret = "postgresql+psycopg://private-user:private-password@private-host/discord_bot_test"
    monkeypatch.setenv("TEST_DATABASE_URL", secret)
    result = migrate.main(["--target", "test", "--expected-database", "discord_bot_dev", "current"])
    assert result == 1
    output = capsys.readouterr().out
    assert all(
        value not in output
        for value in (secret, "private-user", "private-password", "private-host")
    )
