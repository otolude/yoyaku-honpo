from pathlib import Path

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
        "notification_attempts",
    }
    revisions = list(scripts.walk_revisions())
    assert len(revisions) == 3
    assert revisions[0].revision == "8e5b2f1c4a90"
    assert revisions[0].down_revision == "bf82b90bcd5e"
    assert revisions[1].revision == "bf82b90bcd5e"
    assert revisions[1].down_revision == "ffc99a7e1d4f"
    assert revisions[2].revision == "ffc99a7e1d4f"
    assert revisions[2].down_revision is None


def test_completed_action_revision_has_safe_downgrade_guard() -> None:
    revision = Path("alembic/versions/bf82b90bcd5e_add_completed_operation_action.py").read_text(
        encoding="utf-8"
    )
    assert "'completed', 'ended', 'failed'" in revision
    assert "WHERE action = 'completed'" in revision
    assert "RAISE EXCEPTION" in revision


def test_notification_outbox_revision_has_safe_backfill_and_downgrade_guard() -> None:
    revision = Path("alembic/versions/8e5b2f1c4a90_add_notification_outbox.py").read_text(
        encoding="utf-8"
    )
    assert "scheduled_at = created_at" in revision
    assert "SELECT 1 FROM notification_attempts" in revision
    assert "contains non-legacy lifecycle data" in revision
    assert "RAISE EXCEPTION" in revision
