from alembic.config import Config
from alembic.script import ScriptDirectory

from discord_ai_reminder_bot.infrastructure.database.base import Base


def test_alembic_uses_application_metadata_without_hardcoded_url() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)

    assert config.get_main_option("sqlalchemy.url") is None
    assert set(Base.metadata.tables) == {
        "schedules",
        "schedule_runs",
        "delivery_attempts",
        "operation_logs",
        "notification_logs",
    }
    revisions = list(scripts.walk_revisions())
    assert len(revisions) == 1
    assert revisions[0].revision == "ffc99a7e1d4f"
    assert revisions[0].down_revision is None
