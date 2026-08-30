from pathlib import Path
from typing import Self
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from discord_ai_reminder_bot.infrastructure.database.schema import (
    SchemaRevisionError,
    get_expected_revision,
    verify_schema_revision,
)


class Result:
    def __init__(self, revisions: list[str]) -> None:
        self.revisions = revisions

    def scalars(self) -> list[str]:
        return self.revisions


class Connection:
    def __init__(self, revisions: list[str], error: Exception | None = None) -> None:
        self.revisions = revisions
        self.error = error
        self.statements: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def execute(self, statement):
        self.statements.append(str(statement))
        if self.error:
            raise self.error
        return Result(self.revisions)


class Engine:
    def __init__(self, revisions: list[str], error: Exception | None = None) -> None:
        self.connection = Connection(revisions, error)

    def connect(self) -> Connection:
        return self.connection


def test_reads_single_expected_head_from_alembic_configuration() -> None:
    assert get_expected_revision() == "a41f8c7d2e90"


def test_rejects_multiple_script_heads(monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = MagicMock()
    scripts.get_heads.return_value = ["first", "second"]
    monkeypatch.setattr(
        "discord_ai_reminder_bot.infrastructure.database.schema.ScriptDirectory.from_config",
        lambda config: scripts,
    )
    with pytest.raises(SchemaRevisionError, match="exactly one head"):
        get_expected_revision(Path("alembic.ini"))


@pytest.mark.parametrize("revisions", [[], ["old"], ["unknown"], ["head", "other"]])
@pytest.mark.asyncio
async def test_rejects_missing_old_unknown_or_multiple_database_revisions(
    revisions: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "discord_ai_reminder_bot.infrastructure.database.schema.get_expected_revision",
        lambda path: "head",
    )
    engine = Engine(revisions)
    with pytest.raises(SchemaRevisionError, match="does not match"):
        await verify_schema_revision(engine)  # type: ignore[arg-type]
    assert engine.connection.statements == [
        "SELECT version_num FROM alembic_version ORDER BY version_num"
    ]


@pytest.mark.asyncio
async def test_accepts_current_revision_without_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "discord_ai_reminder_bot.infrastructure.database.schema.get_expected_revision",
        lambda path: "head",
    )
    engine = Engine(["head"])
    assert await verify_schema_revision(engine) == "head"  # type: ignore[arg-type]
    assert all(
        "UPDATE" not in item and "INSERT" not in item for item in engine.connection.statements
    )


@pytest.mark.asyncio
async def test_schema_error_does_not_expose_connection_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "postgresql+psycopg://user:password@localhost/database"
    monkeypatch.setattr(
        "discord_ai_reminder_bot.infrastructure.database.schema.get_expected_revision",
        lambda path: "head",
    )
    error = OperationalError("SELECT", {}, RuntimeError(secret))
    with pytest.raises(SchemaRevisionError) as captured:
        await verify_schema_revision(Engine([], error))  # type: ignore[arg-type]
    assert secret not in str(captured.value)
    assert "password" not in str(captured.value)
