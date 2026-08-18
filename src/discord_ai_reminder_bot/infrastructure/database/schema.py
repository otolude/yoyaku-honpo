"""Read-only verification that the database is at the sole Alembic head."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

ALEMBIC_CONFIG_PATH = Path(__file__).resolve().parents[4] / "alembic.ini"


class SchemaRevisionError(RuntimeError):
    """The migration graph or database revision is unsafe for startup."""


def get_expected_revision(config_path: Path = ALEMBIC_CONFIG_PATH) -> str:
    """Return the only migration head without reading database credentials."""
    config = Config(str(config_path))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise SchemaRevisionError("Alembic migration graph must have exactly one head")
    return heads[0]


async def verify_schema_revision(
    engine: AsyncEngine, *, config_path: Path = ALEMBIC_CONFIG_PATH
) -> str:
    """Compare the read-only database revision with the sole script head."""
    expected = get_expected_revision(config_path)
    try:
        async with engine.connect() as connection:
            rows = list(
                (
                    await connection.execute(
                        text("SELECT version_num FROM alembic_version ORDER BY version_num")
                    )
                ).scalars()
            )
    except SQLAlchemyError as error:
        raise SchemaRevisionError("Database schema revision could not be verified") from error
    if len(rows) != 1 or rows[0] != expected:
        raise SchemaRevisionError("Database schema revision does not match the application")
    return expected
