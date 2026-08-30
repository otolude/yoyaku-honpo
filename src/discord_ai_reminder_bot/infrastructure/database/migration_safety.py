"""Fail-closed identity and authorization checks for Alembic commands."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncConnection

from discord_ai_reminder_bot.config import load_database_settings

_DATABASE_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")


class MigrationSafetyError(RuntimeError):
    """The requested migration cannot prove that its target is safe."""


class MigrationTarget(StrEnum):
    TEST = "test"
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class MigrationOperation(StrEnum):
    CURRENT = "current"
    CHECK = "check"
    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    STAMP = "stamp"
    AUTOGENERATE = "autogenerate"

    @property
    def requires_confirmation(self) -> bool:
        return self in {
            self.UPGRADE,
            self.DOWNGRADE,
            self.STAMP,
            self.AUTOGENERATE,
        }


@dataclass(frozen=True, slots=True)
class MigrationInvocation:
    target: MigrationTarget
    expected_database: str
    operation: MigrationOperation
    confirmation: str | None

    @property
    def required_confirmation(self) -> str:
        return f"{self.target.value}:{self.expected_database}:{self.operation.value}"


def _exact_nonempty(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MigrationSafetyError(f"{field} must be explicitly specified")
    return value


def validate_invocation(
    *, target: object, expected_database: object, operation: object, confirmation: object = None
) -> MigrationInvocation:
    """Validate explicit, case-sensitive migration safety inputs."""
    raw_target = _exact_nonempty(target, field="migration target")
    raw_expected = _exact_nonempty(expected_database, field="expected database")
    raw_operation = _exact_nonempty(operation, field="migration operation")
    try:
        parsed_target = MigrationTarget(raw_target)
        parsed_operation = MigrationOperation(raw_operation)
    except ValueError as error:
        raise MigrationSafetyError("migration target or operation is not permitted") from error
    if not _DATABASE_NAME.fullmatch(raw_expected):
        raise MigrationSafetyError("expected database name is invalid")
    if parsed_target is MigrationTarget.TEST and raw_expected != "discord_bot_test":
        raise MigrationSafetyError("test migrations require the dedicated test database")
    if parsed_target is MigrationTarget.DEVELOPMENT and raw_expected != "discord_bot_dev":
        raise MigrationSafetyError("development migrations require the development database")

    raw_confirmation: str | None
    if confirmation is None:
        raw_confirmation = None
    else:
        raw_confirmation = _exact_nonempty(confirmation, field="migration confirmation")
    invocation = MigrationInvocation(
        target=parsed_target,
        expected_database=raw_expected,
        operation=parsed_operation,
        confirmation=raw_confirmation,
    )
    if parsed_operation.requires_confirmation:
        if raw_confirmation != invocation.required_confirmation:
            raise MigrationSafetyError("migration confirmation does not exactly match the request")
    elif raw_confirmation is not None:
        raise MigrationSafetyError("read-only migration commands do not accept confirmation")
    return invocation


def invocation_from_environment(
    operation: MigrationOperation, environ: Mapping[str, str] | None = None
) -> MigrationInvocation:
    """Read direct-CLI safety values from the process environment only."""
    values = os.environ if environ is None else environ
    return validate_invocation(
        target=values.get("MIGRATION_TARGET_ENV"),
        expected_database=values.get("MIGRATION_EXPECTED_DATABASE"),
        operation=operation.value,
        confirmation=values.get("MIGRATION_APPLY_CONFIRMATION"),
    )


def select_database_url(
    invocation: MigrationInvocation, environ: Mapping[str, str] | None = None
) -> SecretStr:
    """Select credentials without ever falling across environment boundaries."""
    values = os.environ if environ is None else environ
    if invocation.target is MigrationTarget.TEST:
        raw_url = values.get("TEST_DATABASE_URL")
    elif invocation.target is MigrationTarget.PRODUCTION:
        raw_url = values.get("DATABASE_URL")
    else:
        # Development deliberately retains the existing DATABASE_URL/.env behavior.
        return load_database_settings().database_url
    if not isinstance(raw_url, str) or not raw_url or raw_url != raw_url.strip():
        raise MigrationSafetyError("database credentials are not explicitly configured")
    return SecretStr(raw_url)


def validate_url_database(database_url: SecretStr, expected_database: str) -> None:
    """Reject an obvious URL mismatch before opening a connection."""
    try:
        url = make_url(database_url.get_secret_value())
    except ArgumentError, TypeError, ValueError:
        raise MigrationSafetyError("database credentials are invalid") from None
    if url.drivername != "postgresql+psycopg" or url.database != expected_database:
        raise MigrationSafetyError("database identity does not match the migration request")


async def verify_connected_database(connection: AsyncConnection, expected_database: str) -> str:
    """Authoritatively compare the server's database identity before any DDL."""
    actual_database = (await connection.execute(text("SELECT current_database()"))).scalar_one()
    if actual_database != expected_database:
        raise MigrationSafetyError("connected database identity does not match")
    return actual_database


def safe_invocation_label(invocation: MigrationInvocation) -> str:
    """Return the only migration context permitted in user-facing output."""
    return (
        f"target={invocation.target.value} "
        f"expected_database={invocation.expected_database} "
        f"operation={invocation.operation.value}"
    )
