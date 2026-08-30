"""Official, cross-platform Alembic command wrapper."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from alembic.config import Config

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
    config = Config("alembic.ini")
    config.attributes["migration_invocation"] = invocation
    config.attributes["migration_database_url"] = database_url
    return config


def run(arguments: argparse.Namespace) -> None:
    if arguments.command in {"heads", "history"}:
        if any((arguments.target, arguments.expected_database, arguments.confirm)):
            raise MigrationSafetyError("database-free commands do not accept safety credentials")
        config = Config("alembic.ini")
        if arguments.command == "heads":
            command.heads(config)
        else:
            command.history(config)
        return

    operation = MigrationOperation(arguments.command)
    invocation = validate_invocation(
        target=arguments.target,
        expected_database=arguments.expected_database,
        operation=operation.value,
        confirmation=arguments.confirm,
    )
    config = _prepare_config(invocation)
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
        run(arguments)
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 -- never expose driver errors or credentials from this CLI.
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
