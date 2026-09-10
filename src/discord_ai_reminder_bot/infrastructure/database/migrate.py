"""Official, cross-platform Alembic command wrapper."""

from __future__ import annotations

import argparse
import configparser
from collections.abc import Sequence
from enum import StrEnum

from alembic.config import Config
from alembic.script.revision import ResolutionError, RevisionError
from alembic.util import CommandError
from sqlalchemy.exc import ArgumentError, DBAPIError, IntegrityError, NoSuchModuleError

from alembic import command
from discord_ai_reminder_bot.infrastructure.database.migration_safety import (
    MigrationInvocation,
    MigrationOperation,
    MigrationSafetyError,
    safe_invocation_label,
    select_database_url,
    validate_invocation,
    validate_url_database,
)


class MigrationStage(StrEnum):
    """Bounded progress markers for the managed migration path."""

    INPUT_VALIDATED = "MIGRATION_INPUT_VALIDATED"
    ALEMBIC_CONFIG_CREATED = "ALEMBIC_CONFIG_CREATED"
    COMMAND_ENTERED = "MIGRATION_COMMAND_ENTERED"
    COMMAND_COMPLETED = "MIGRATION_COMMAND_COMPLETED"


class MigrationFailureCategory(StrEnum):
    """Safe failure categories that never include exception-controlled text."""

    MIGRATION_INPUT_ERROR = "migration_input_error"
    ALEMBIC_CONFIGURATION_ERROR = "alembic_configuration_error"
    DATABASE_OPERATIONAL_ERROR = "database_operational_error"
    DATABASE_INTEGRITY_ERROR = "database_integrity_error"
    ALEMBIC_COMMAND_ERROR = "alembic_command_error"
    FILESYSTEM_ERROR = "filesystem_error"
    UNEXPECTED_MIGRATION_ERROR = "unexpected_migration_error"


class AlembicEnvStage(StrEnum):
    """Bounded progress markers inside the Alembic environment."""

    ENTERED = "ALEMBIC_ENV_ENTERED"
    URL_LOADED = "ALEMBIC_URL_LOADED"
    ENGINE_CREATED = "ALEMBIC_ENGINE_CREATED"
    CONNECTION_OPENED = "ALEMBIC_CONNECTION_OPENED"
    DATABASE_IDENTITY_VERIFIED = "ALEMBIC_DATABASE_IDENTITY_VERIFIED"
    CONTEXT_CONFIGURED = "ALEMBIC_CONTEXT_CONFIGURED"
    TRANSACTION_ENTERED = "ALEMBIC_TRANSACTION_ENTERED"
    MIGRATIONS_STARTED = "ALEMBIC_MIGRATIONS_STARTED"
    MIGRATIONS_COMPLETED = "ALEMBIC_MIGRATIONS_COMPLETED"


class MigrationCausalCategory(StrEnum):
    """Safe categories for a bounded nested exception chain."""

    NO_NESTED_CAUSE = "no_nested_cause"
    CONFIGURATION_CAUSE = "configuration_cause"
    URL_CAUSE = "url_cause"
    DIALECT_CAUSE = "dialect_cause"
    DATABASE_OPERATIONAL_CAUSE = "database_operational_cause"
    DATABASE_INTEGRITY_CAUSE = "database_integrity_cause"
    REVISION_RESOLUTION_CAUSE = "revision_resolution_cause"
    FILESYSTEM_CAUSE = "filesystem_cause"
    UNEXPECTED_NESTED_CAUSE = "unexpected_nested_cause"


def classify_migration_failure(error: Exception) -> MigrationFailureCategory:
    """Classify a failure by public exception type without rendering it."""
    if isinstance(error, MigrationSafetyError):
        return MigrationFailureCategory.MIGRATION_INPUT_ERROR
    if isinstance(error, configparser.Error):
        return MigrationFailureCategory.ALEMBIC_CONFIGURATION_ERROR
    if isinstance(error, IntegrityError):
        return MigrationFailureCategory.DATABASE_INTEGRITY_ERROR
    if isinstance(error, DBAPIError):
        return MigrationFailureCategory.DATABASE_OPERATIONAL_ERROR
    if isinstance(error, (CommandError, RevisionError)):
        return MigrationFailureCategory.ALEMBIC_COMMAND_ERROR
    if isinstance(error, OSError):
        return MigrationFailureCategory.FILESYSTEM_ERROR
    return MigrationFailureCategory.UNEXPECTED_MIGRATION_ERROR


def _classify_nested_cause(error: BaseException) -> MigrationCausalCategory | None:
    if isinstance(error, configparser.Error):
        return MigrationCausalCategory.CONFIGURATION_CAUSE
    if isinstance(error, NoSuchModuleError):
        return MigrationCausalCategory.DIALECT_CAUSE
    if isinstance(error, ArgumentError):
        return MigrationCausalCategory.URL_CAUSE
    if isinstance(error, IntegrityError):
        return MigrationCausalCategory.DATABASE_INTEGRITY_CAUSE
    if isinstance(error, DBAPIError):
        return MigrationCausalCategory.DATABASE_OPERATIONAL_CAUSE
    if isinstance(error, (ResolutionError, RevisionError)):
        return MigrationCausalCategory.REVISION_RESOLUTION_CAUSE
    if isinstance(error, OSError):
        return MigrationCausalCategory.FILESYSTEM_CAUSE
    return None


def classify_migration_cause(error: Exception) -> MigrationCausalCategory:
    """Classify at most three nested cause/context levels without rendering them."""
    seen = {id(error)}
    nested_found = False
    current_level: list[BaseException] = [error]
    for _ in range(3):
        next_level: list[BaseException] = []
        for current in current_level:
            for nested in (current.__cause__, current.__context__):
                if nested is None:
                    continue
                nested_found = True
                if id(nested) in seen:
                    continue
                seen.add(id(nested))
                category = _classify_nested_cause(nested)
                if category is not None:
                    return category
                next_level.append(nested)
        if not next_level:
            break
        current_level = next_level
    if nested_found:
        return MigrationCausalCategory.UNEXPECTED_NESTED_CAUSE
    return MigrationCausalCategory.NO_NESTED_CAUSE


def _emit_stage(stage: MigrationStage) -> None:
    print(f"MIGRATION_STAGE={stage.value}")


def _emit_failure_category(category: MigrationFailureCategory) -> None:
    print(f"MIGRATION_FAILURE_CATEGORY={category.value}")


def _emit_alembic_env_stage(stage: AlembicEnvStage) -> None:
    print(stage.value)


def _emit_causal_category(category: MigrationCausalCategory) -> None:
    print(f"MIGRATION_CAUSAL_CATEGORY={category.value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an identity-checked Alembic command")
    parser.add_argument("--target")
    parser.add_argument("--expected-database")
    parser.add_argument("--confirm")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("heads")
    subparsers.add_parser("history")
    subparsers.add_parser("current")
    subparsers.add_parser("check")
    for name in ("upgrade", "downgrade", "stamp"):
        child = subparsers.add_parser(name)
        child.add_argument("revision")
    autogenerate = subparsers.add_parser("autogenerate")
    autogenerate.add_argument("--message", required=True)
    return parser


def _prepare_config(invocation: MigrationInvocation) -> Config:
    database_url = select_database_url(invocation)
    validate_url_database(database_url, invocation.expected_database)
    _emit_stage(MigrationStage.INPUT_VALIDATED)
    config = Config("alembic.ini")
    config.attributes["migration_invocation"] = invocation
    config.attributes["migration_database_url"] = database_url
    return config


def run(arguments: argparse.Namespace) -> None:
    if arguments.command in {"heads", "history"}:
        if any((arguments.target, arguments.expected_database, arguments.confirm)):
            raise MigrationSafetyError("database-free commands do not accept safety credentials")
        _emit_stage(MigrationStage.INPUT_VALIDATED)
        config = Config("alembic.ini")
        _emit_stage(MigrationStage.ALEMBIC_CONFIG_CREATED)
        _emit_stage(MigrationStage.COMMAND_ENTERED)
        if arguments.command == "heads":
            command.heads(config)
        else:
            command.history(config)
        _emit_stage(MigrationStage.COMMAND_COMPLETED)
        return

    operation = MigrationOperation(arguments.command)
    invocation = validate_invocation(
        target=arguments.target,
        expected_database=arguments.expected_database,
        operation=operation.value,
        confirmation=arguments.confirm,
    )
    config = _prepare_config(invocation)
    _emit_stage(MigrationStage.ALEMBIC_CONFIG_CREATED)
    _emit_stage(MigrationStage.COMMAND_ENTERED)
    if operation is MigrationOperation.CURRENT:
        command.current(config)
    elif operation is MigrationOperation.CHECK:
        command.check(config)
    elif operation is MigrationOperation.UPGRADE:
        command.upgrade(config, arguments.revision)
    elif operation is MigrationOperation.DOWNGRADE:
        command.downgrade(config, arguments.revision)
    elif operation is MigrationOperation.STAMP:
        command.stamp(config, arguments.revision)
    elif operation is MigrationOperation.AUTOGENERATE:
        command.revision(config, message=arguments.message, autogenerate=True)
    else:  # pragma: no cover - the closed enum and parser make this unreachable.
        raise MigrationSafetyError("migration operation is not permitted")
    _emit_stage(MigrationStage.COMMAND_COMPLETED)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
        run(arguments)
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001 -- classify without rendering the exception.
        _emit_failure_category(classify_migration_failure(error))
        _emit_causal_category(classify_migration_cause(error))
        operation = getattr(locals().get("arguments"), "command", "unknown")
        target = getattr(locals().get("arguments"), "target", None)
        expected = getattr(locals().get("arguments"), "expected_database", None)
        try:
            invocation = validate_invocation(
                target=target,
                expected_database=expected,
                operation=operation,
                confirmation=getattr(locals().get("arguments"), "confirm", None),
            )
            label = safe_invocation_label(invocation)
        except MigrationSafetyError:
            label = "target=invalid expected_database=invalid operation=invalid"
        print(f"Migrationを安全に実行できませんでした（{label}）。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
