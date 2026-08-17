"""Alembic environment configured for SQLAlchemy asyncio."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from discord_ai_reminder_bot.config import load_database_settings
from discord_ai_reminder_bot.infrastructure.database import models as database_models
from discord_ai_reminder_bot.infrastructure.database.base import Base

_ = database_models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    """Read the secret URL at the Alembic-to-database boundary."""
    return load_database_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Configure offline migration generation without opening a connection."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations using the synchronous facade of an async connection."""
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create a temporary async engine and dispose it after migration."""
    escaped_url = get_database_url().replace("%", "%%")
    config.set_main_option("sqlalchemy.url", escaped_url)
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        echo=False,
        hide_parameters=True,
    )

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations through SQLAlchemy's async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
