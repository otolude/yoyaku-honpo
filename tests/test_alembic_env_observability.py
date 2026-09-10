import configparser
import logging
import runpy
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from alembic.script.revision import RevisionError
from alembic.util import CommandError
from pydantic import SecretStr
from sqlalchemy.exc import (
    ArgumentError,
    IntegrityError,
    NoSuchModuleError,
    OperationalError,
)
from sqlalchemy.ext import asyncio as sqlalchemy_asyncio

from alembic import context
from discord_ai_reminder_bot.infrastructure.database import migrate, migration_safety
from discord_ai_reminder_bot.infrastructure.database.migration_safety import validate_invocation

ENV_STAGES = (
    "ALEMBIC_ENV_ENTERED",
    "ALEMBIC_URL_LOADED",
    "ALEMBIC_ENGINE_CREATED",
    "ALEMBIC_CONNECTION_OPENED",
    "ALEMBIC_DATABASE_IDENTITY_VERIFIED",
    "ALEMBIC_CONTEXT_CONFIGURED",
    "ALEMBIC_TRANSACTION_ENTERED",
    "ALEMBIC_MIGRATIONS_STARTED",
    "ALEMBIC_MIGRATIONS_COMPLETED",
)


def _nested(outer: Exception, *causes: Exception) -> Exception:
    current = outer
    for cause in causes:
        current.__cause__ = cause
        current = cause
    return outer


@pytest.mark.parametrize(
    ("cause", "expected"),
    [
        (configparser.Error("private"), "configuration_cause"),
        (ArgumentError("private"), "url_cause"),
        (NoSuchModuleError("private"), "dialect_cause"),
        (
            OperationalError("private", None, RuntimeError("private")),
            "database_operational_cause",
        ),
        (
            IntegrityError("private", None, RuntimeError("private")),
            "database_integrity_cause",
        ),
        (RevisionError("private"), "revision_resolution_cause"),
        (OSError("private"), "filesystem_cause"),
        (RuntimeError("private"), "unexpected_nested_cause"),
    ],
)
def test_nested_causes_are_classified_by_allowlisted_type(cause, expected: str) -> None:
    classifier = getattr(migrate, "classify_migration_cause", None)
    assert classifier is not None
    assert classifier(_nested(CommandError("bounded"), cause)).value == expected


def test_failure_without_nested_cause_is_classified() -> None:
    classifier = getattr(migrate, "classify_migration_cause", None)
    assert classifier is not None
    assert classifier(CommandError("bounded")).value == "no_nested_cause"


def test_nested_cause_search_is_limited_to_three_levels() -> None:
    classifier = getattr(migrate, "classify_migration_cause", None)
    assert classifier is not None
    error = _nested(
        CommandError("bounded"),
        RuntimeError("one"),
        RuntimeError("two"),
        RuntimeError("three"),
        OSError("outside-bound"),
    )
    assert classifier(error).value == "unexpected_nested_cause"


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_allowlisted_cause_is_found_at_each_supported_depth(depth: int) -> None:
    classifier = getattr(migrate, "classify_migration_cause", None)
    assert classifier is not None
    causes = [RuntimeError("private") for _ in range(depth - 1)]
    causes.append(OSError("private"))
    assert classifier(_nested(CommandError("bounded"), *causes)).value == "filesystem_cause"


def test_nested_context_is_used_when_explicit_cause_is_absent() -> None:
    classifier = getattr(migrate, "classify_migration_cause", None)
    assert classifier is not None
    error = CommandError("bounded")
    error.__context__ = OSError("private")
    assert classifier(error).value == "filesystem_cause"


def test_explicit_cause_and_implicit_context_are_both_inspected() -> None:
    classifier = getattr(migrate, "classify_migration_cause", None)
    assert classifier is not None
    error = CommandError("bounded")
    error.__cause__ = RuntimeError("private")
    error.__context__ = OSError("private")
    assert classifier(error).value == "filesystem_cause"


def test_nested_cause_cycle_is_bounded() -> None:
    classifier = getattr(migrate, "classify_migration_cause", None)
    assert classifier is not None
    first = RuntimeError("first")
    second = RuntimeError("second")
    first.__cause__ = second
    second.__cause__ = first
    assert classifier(_nested(CommandError("bounded"), first)).value == ("unexpected_nested_cause")


class _FakeConfig:
    config_file_name = None
    config_ini_section = "alembic"
    cmd_opts = SimpleNamespace()

    def __init__(self, calls: Counter[str], failure: str | None) -> None:
        self.calls = calls
        self.failure = failure
        self.attributes = {
            "migration_invocation": validate_invocation(
                target="test",
                expected_database="discord_bot_test",
                operation="upgrade",
                confirmation="test:discord_bot_test:upgrade",
            ),
            "migration_database_url": SecretStr(
                "postgresql+psycopg://test-user:test-password@localhost/discord_bot_test"
            ),
        }
        self.values: dict[str, str] = {}

    def set_main_option(self, key: str, value: str) -> None:
        self.calls["set_main_option"] += 1
        if self.failure == "set_main_option":
            raise configparser.Error("private")
        self.values[key] = value

    def get_section(self, name: str, default: dict[str, str]) -> dict[str, str]:
        self.calls["get_section"] += 1
        return dict(self.values or default)


class _FakeTransaction:
    def __init__(self, calls: Counter[str], failure: str | None) -> None:
        self.calls = calls
        self.failure = failure

    def __enter__(self):
        self.calls["transaction"] += 1
        if self.failure == "transaction":
            raise RuntimeError("private")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class _FakeConnection:
    def __init__(self, calls: Counter[str], failure: str | None) -> None:
        self.calls = calls
        self.failure = failure

    async def commit(self) -> None:
        self.calls["commit"] += 1
        if self.failure == "commit":
            raise RuntimeError("private")

    async def run_sync(self, operation) -> None:
        self.calls["run_sync"] += 1
        operation(object())


class _FakeConnectionContext:
    def __init__(self, calls: Counter[str], failure: str | None) -> None:
        self.calls = calls
        self.failure = failure
        self.connection = _FakeConnection(calls, failure)

    async def __aenter__(self):
        self.calls["connection"] += 1
        if self.failure == "connection":
            raise OperationalError("private", None, RuntimeError("private"))
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _FakeEngine:
    def __init__(self, calls: Counter[str], failure: str | None) -> None:
        self.calls = calls
        self.failure = failure

    def connect(self) -> _FakeConnectionContext:
        return _FakeConnectionContext(self.calls, self.failure)

    async def dispose(self) -> None:
        self.calls["dispose"] += 1
        if self.failure == "dispose":
            raise RuntimeError("private")


def _run_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failure: str | None = None,
    fail_before_marker: str | None = None,
) -> Counter[str]:
    calls: Counter[str] = Counter()
    fake_config = _FakeConfig(calls, failure)
    if failure == "url":
        fake_config.attributes = {}

    monkeypatch.setattr(context, "config", fake_config, raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: False)

    def configure(**values) -> None:
        calls["configure"] += 1
        if failure == "configure":
            raise RuntimeError("private")

    monkeypatch.setattr(context, "configure", configure)
    monkeypatch.setattr(
        context,
        "begin_transaction",
        lambda: _FakeTransaction(calls, failure),
    )

    def run_migrations() -> None:
        calls["run_migrations"] += 1
        if failure == "run_migrations":
            raise RevisionError("private")

    monkeypatch.setattr(context, "run_migrations", run_migrations)

    async def verify(connection, expected_database: str) -> None:
        calls["verify_identity"] += 1
        if failure == "identity":
            raise migration_safety.MigrationSafetyError("private")

    monkeypatch.setattr(migration_safety, "verify_connected_database", verify)

    def engine_factory(*args, **kwargs):
        calls["engine"] += 1
        if failure == "engine":
            raise NoSuchModuleError("private")
        return _FakeEngine(calls, failure)

    monkeypatch.setattr(sqlalchemy_asyncio, "async_engine_from_config", engine_factory)

    if fail_before_marker is not None:

        def emit(stage) -> None:
            if stage.value == fail_before_marker:
                raise RuntimeError("private")
            print(stage.value)

        monkeypatch.setattr(migrate, "_emit_alembic_env_stage", emit, raising=False)

    runpy.run_path(str(Path("alembic/env.py")), run_name="__alembic_observability_test__")
    return calls


def test_environment_success_emits_markers_in_order_and_runs_resources_once(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = logging.getLogger()
    logging_before = (root.level, tuple(root.handlers), root.disabled)

    calls = _run_environment(monkeypatch)

    assert capsys.readouterr().out.splitlines() == list(ENV_STAGES)
    assert calls == Counter(
        {
            "set_main_option": 1,
            "get_section": 1,
            "engine": 1,
            "connection": 1,
            "verify_identity": 1,
            "commit": 1,
            "run_sync": 1,
            "configure": 1,
            "transaction": 1,
            "run_migrations": 1,
            "dispose": 1,
        }
    )
    assert (root.level, tuple(root.handlers), root.disabled) == logging_before


@pytest.mark.parametrize(
    ("failure", "last_marker"),
    [
        ("url", "ALEMBIC_ENV_ENTERED"),
        ("set_main_option", "ALEMBIC_URL_LOADED"),
        ("engine", "ALEMBIC_URL_LOADED"),
        ("connection", "ALEMBIC_ENGINE_CREATED"),
        ("identity", "ALEMBIC_CONNECTION_OPENED"),
        ("commit", "ALEMBIC_DATABASE_IDENTITY_VERIFIED"),
        ("configure", "ALEMBIC_DATABASE_IDENTITY_VERIFIED"),
        ("transaction", "ALEMBIC_CONTEXT_CONFIGURED"),
        ("run_migrations", "ALEMBIC_MIGRATIONS_STARTED"),
        ("dispose", "ALEMBIC_MIGRATIONS_COMPLETED"),
    ],
)
def test_environment_failure_preserves_last_completed_marker(
    failure: str,
    last_marker: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    with pytest.raises((CommandError, configparser.Error, RuntimeError)):
        _run_environment(monkeypatch, failure=failure)

    markers = [line for line in capsys.readouterr().out.splitlines() if line in ENV_STAGES]
    assert markers[-1] == last_marker
    assert markers == list(ENV_STAGES[: ENV_STAGES.index(last_marker) + 1])


@pytest.mark.parametrize("stage", ENV_STAGES)
def test_failure_immediately_before_stage_does_not_emit_that_stage(
    stage: str, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    with pytest.raises((RuntimeError, CommandError)):
        _run_environment(monkeypatch, fail_before_marker=stage)

    markers = [line for line in capsys.readouterr().out.splitlines() if line in ENV_STAGES]
    assert markers == list(ENV_STAGES[: ENV_STAGES.index(stage)])


def test_wrapper_reports_nested_cause_without_reflecting_exception_content(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(
        migrate,
        "select_database_url",
        lambda invocation: SecretStr(
            "postgresql+psycopg://test-user:test-password@localhost/discord_bot_test"
        ),
    )
    monkeypatch.setattr(migrate, "validate_url_database", lambda database_url, expected: None)
    monkeypatch.setattr(migrate, "Config", MagicMock(return_value=MagicMock()))
    secrets = (
        "postgresql+psycopg://private-user:private-password@private-host/private-db",
        "private-password",
        "FORGED_CAUSAL_CATEGORY",
    )
    nested = OperationalError(secrets[0], None, RuntimeError(secrets[1]))
    outer = CommandError(f"{secrets[0]}\nMIGRATION_CAUSAL_CATEGORY={secrets[2]}")
    outer.__cause__ = nested
    monkeypatch.setattr(migrate.command, "upgrade", MagicMock(side_effect=outer))

    result = migrate.main(
        [
            "--target",
            "test",
            "--expected-database",
            "discord_bot_test",
            "--confirm",
            "test:discord_bot_test:upgrade",
            "upgrade",
            "head",
        ]
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "MIGRATION_FAILURE_CATEGORY=alembic_command_error" in output
    assert "MIGRATION_CAUSAL_CATEGORY=database_operational_cause" in output
    assert all(secret not in output for secret in secrets)


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(7)])
def test_causal_observability_does_not_catch_base_exceptions(
    error: BaseException, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(migrate, "run", MagicMock(side_effect=error))

    with pytest.raises(type(error)):
        migrate.main(["heads"])

    output = capsys.readouterr().out
    assert "MIGRATION_CAUSAL_CATEGORY=" not in output
