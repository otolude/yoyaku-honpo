"""Alembic environment configured for SQLAlchemy asyncio."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic.util import CommandError
from pydantic import SecretStr
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from discord_ai_reminder_bot.infrastructure.database import models as database_models
from discord_ai_reminder_bot.infrastructure.database.base import Base
from discord_ai_reminder_bot.infrastructure.database.migration_safety import (
    MigrationInvocation,
    MigrationOperation,
    MigrationSafetyError,
    invocation_from_environment,
    select_database_url,
    validate_invocation,
    validate_url_database,
    verify_connected_database,
)

_ = database_models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _direct_cli_operation() -> MigrationOperation:
    """Classify a direct Alembic CLI command without trusting environment claims."""
    options = config.cmd_opts
    command_spec = getattr(options, "cmd", None)
    if not isinstance(command_spec, tuple) or not command_spec or not callable(command_spec[0]):
        raise MigrationSafetyError("migration operation cannot be determined")
    command_name = command_spec[0].__name__
    if command_name == "revision":
        if getattr(options, "autogenerate", False) is not True:
            raise MigrationSafetyError("only autogenerate revision execution is permitted")
        command_name = "autogenerate"
    try:
        return MigrationOperation(command_name)
    except ValueError as error:
        raise MigrationSafetyError("migration operation is not permitted") from error


def get_migration_context() -> tuple[MigrationInvocation, SecretStr]:
    """Independently validate wrapper attributes or guarded direct-CLI settings."""
    wrapped = config.attributes.get("migration_invocation")
    wrapped_url = config.attributes.get("migration_database_url")
    if wrapped is not None or wrapped_url is not None:
        if not isinstance(wrapped, MigrationInvocation) or not isinstance(wrapped_url, SecretStr):
            raise MigrationSafetyError("migration wrapper context is invalid")
        invocation = validate_invocation(
            target=wrapped.target.value,
            expected_database=wrapped.expected_database,
            operation=wrapped.operation.value,
            confirmation=wrapped.confirmation,
        )
        database_url = wrapped_url
    else:
        operation = _direct_cli_operation()
        invocation = invocation_from_environment(operation)
        database_url = select_database_url(invocation)
    validate_url_database(database_url, invocation.expected_database)
    return invocation, database_url


def run_migrations_offline() -> None:
    """Reject offline mode because it cannot verify the real database identity."""
    raise CommandError("offline migration mode is not permitted")


def do_run_migrations(connection: Connection) -> None:
    """Run migrations using the synchronous facade of an async connection."""
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Verify the connected database before creating a migration context."""
    try:
        invocation, database_url = get_migration_context()
    except MigrationSafetyError:
        raise CommandError("migration safety verification failed") from None
    escaped_url = database_url.get_secret_value().replace("%", "%%")
    config.set_main_option("sqlalchemy.url", escaped_url)
    try:
        connectable = async_engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
            echo=False,
            hide_parameters=True,
        )
    except Exception:  # noqa: BLE001 -- configuration errors may contain credentials.
        raise CommandError("migration database configuration failed safely") from None

    try:
        try:
            async with connectable.connect() as connection:
                await verify_connected_database(connection, invocation.expected_database)
                # End the read-only identity query before Alembic owns its DDL transaction.
                await connection.commit()
                await connection.run_sync(do_run_migrations)
        except CommandError:
            raise
        except MigrationSafetyError:
            raise CommandError("migration database identity verification failed") from None
        except Exception:  # noqa: BLE001 -- driver details may contain credentials.
            raise CommandError("migration database operation failed safely") from None
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations through SQLAlchemy's async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
