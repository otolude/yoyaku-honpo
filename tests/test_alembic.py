from alembic.config import Config
from alembic.script import ScriptDirectory

from discord_ai_reminder_bot.infrastructure.database.base import Base


def test_alembic_uses_application_metadata_without_hardcoded_url() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)

    assert config.get_main_option("sqlalchemy.url") is None
    assert Base.metadata.tables == {}
    assert list(scripts.walk_revisions()) == []
