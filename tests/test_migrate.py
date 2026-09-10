import configparser
import logging
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.util import CommandError
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError, OperationalError

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


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (MigrationSafetyError("unsafe input"), "migration_input_error"),
        (configparser.Error("unsafe config"), "alembic_configuration_error"),
        (
            OperationalError("private statement", None, RuntimeError("private connection")),
            "database_operational_error",
        ),
        (
            IntegrityError("private statement", None, RuntimeError("private integrity")),
            "database_integrity_error",
        ),
        (CommandError("private alembic error"), "alembic_command_error"),
        (OSError("private filesystem error"), "filesystem_error"),
        (RuntimeError("private unexpected error"), "unexpected_migration_error"),
    ],
)
def test_migration_failures_are_classified_by_exception_type(error, category: str) -> None:
    assert migrate.classify_migration_failure(error).value == category


def _patch_valid_upgrade_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        migrate,
        "select_database_url",
        lambda invocation: SecretStr(
            "postgresql+psycopg://test-user:test-password@localhost/discord_bot_test"
        ),
    )
    monkeypatch.setattr(migrate, "validate_url_database", lambda database_url, expected: None)


def _upgrade_argv() -> list[str]:
    return [
        "--target",
        "test",
        "--expected-database",
        "discord_bot_test",
        "--confirm",
        "test:discord_bot_test:upgrade",
        "upgrade",
        "head",
    ]


def test_config_creation_failure_reports_last_completed_stage_and_category(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _patch_valid_upgrade_input(monkeypatch)
    monkeypatch.setattr(
        migrate,
        "Config",
        MagicMock(side_effect=configparser.Error("private configuration detail")),
    )

    assert migrate.main(_upgrade_argv()) == 1

    output = capsys.readouterr().out
    assert "MIGRATION_STAGE=MIGRATION_INPUT_VALIDATED" in output
    assert "MIGRATION_STAGE=ALEMBIC_CONFIG_CREATED" not in output
    assert "MIGRATION_FAILURE_CATEGORY=alembic_configuration_error" in output


def test_failure_after_config_before_command_reports_config_stage(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _patch_valid_upgrade_input(monkeypatch)
    monkeypatch.setattr(migrate, "Config", MagicMock(return_value=MagicMock()))
    real_emit = migrate._emit_stage

    def fail_before_command(stage) -> None:
        if stage.value == "MIGRATION_COMMAND_ENTERED":
            raise OSError("private boundary detail")
        real_emit(stage)

    monkeypatch.setattr(migrate, "_emit_stage", fail_before_command)

    assert migrate.main(_upgrade_argv()) == 1

    output = capsys.readouterr().out
    assert "MIGRATION_STAGE=ALEMBIC_CONFIG_CREATED" in output
    assert "MIGRATION_STAGE=MIGRATION_COMMAND_ENTERED" not in output
    assert "MIGRATION_FAILURE_CATEGORY=filesystem_error" in output


def test_command_failure_reports_entered_stage_and_bounded_category(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _patch_valid_upgrade_input(monkeypatch)
    monkeypatch.setattr(migrate, "Config", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(
        migrate.command,
        "upgrade",
        MagicMock(
            side_effect=OperationalError(
                "private statement", None, RuntimeError("private connection detail")
            )
        ),
    )

    assert migrate.main(_upgrade_argv()) == 1

    output = capsys.readouterr().out
    assert "MIGRATION_STAGE=MIGRATION_COMMAND_ENTERED" in output
    assert "MIGRATION_STAGE=MIGRATION_COMMAND_COMPLETED" not in output
    assert "MIGRATION_FAILURE_CATEGORY=database_operational_error" in output


def test_success_reports_all_completed_stages(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _patch_valid_upgrade_input(monkeypatch)
    monkeypatch.setattr(migrate, "Config", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(migrate.command, "upgrade", MagicMock())

    assert migrate.main(_upgrade_argv()) == 0

    assert capsys.readouterr().out.splitlines() == [
        "MIGRATION_STAGE=MIGRATION_INPUT_VALIDATED",
        "MIGRATION_STAGE=ALEMBIC_CONFIG_CREATED",
        "MIGRATION_STAGE=MIGRATION_COMMAND_ENTERED",
        "MIGRATION_STAGE=MIGRATION_COMMAND_COMPLETED",
    ]


def test_exception_content_cannot_forge_markers_or_reflect_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _patch_valid_upgrade_input(monkeypatch)
    monkeypatch.setattr(migrate, "Config", MagicMock(return_value=MagicMock()))
    secrets = (
        "postgresql+psycopg://private-user:private-password@private-host/private-db",
        "private-user",
        "private-password",
        "private-host",
        "forged-category",
    )
    message = (
        f"{secrets[0]}\n"
        "MIGRATION_STAGE=MIGRATION_COMMAND_COMPLETED\n"
        "MIGRATION_FAILURE_CATEGORY=forged-category"
    )
    monkeypatch.setattr(migrate.command, "upgrade", MagicMock(side_effect=RuntimeError(message)))

    assert migrate.main(_upgrade_argv()) == 1

    output = capsys.readouterr().out
    assert "MIGRATION_FAILURE_CATEGORY=unexpected_migration_error" in output
    assert "MIGRATION_STAGE=MIGRATION_COMMAND_COMPLETED" not in output
    assert all(secret not in output for secret in secrets)


def test_failure_output_omits_exception_type_traceback_cause_and_context(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _patch_valid_upgrade_input(monkeypatch)
    monkeypatch.setattr(migrate, "Config", MagicMock(return_value=MagicMock()))
    cause = ValueError("private cause detail")
    error = RuntimeError("private outer detail")
    error.__cause__ = cause
    error.__context__ = cause
    monkeypatch.setattr(migrate.command, "upgrade", MagicMock(side_effect=error))

    assert migrate.main(_upgrade_argv()) == 1

    output = capsys.readouterr().out
    assert "Migrationを安全に実行できませんでした" in output
    assert all(
        value not in output
        for value in (
            "RuntimeError",
            "ValueError",
            "Traceback",
            "private outer detail",
            "private cause detail",
        )
    )


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(7)])
def test_base_exceptions_keep_existing_behavior(
    error: BaseException, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(migrate, "run", MagicMock(side_effect=error))

    with pytest.raises(type(error)):
        migrate.main(["heads"])

    output = capsys.readouterr().out
    assert "MIGRATION_FAILURE_CATEGORY=" not in output
    assert "Migrationを安全に実行できませんでした" not in output


def test_failure_observability_does_not_mutate_global_logging(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = logging.getLogger()
    before = (root.level, tuple(root.handlers), root.disabled)
    monkeypatch.setattr(
        migrate,
        "run",
        MagicMock(side_effect=RuntimeError("private unexpected detail")),
    )

    assert migrate.main(["heads"]) == 1
    capsys.readouterr()

    assert (root.level, tuple(root.handlers), root.disabled) == before
