from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from discord_ai_reminder_bot.infrastructure.database import models as database_models
from discord_ai_reminder_bot.infrastructure.database.base import Base

_ = database_models


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
        "name_generation_jobs",
        "name_generation_budget_buckets",
        "post_draft_operator_budget_buckets",
        "post_draft_rate_limit_buckets",
        "post_draft_usage_reservation_receipts",
    }
    revisions = list(scripts.walk_revisions())
    assert len(revisions) == 6
    assert revisions[0].revision == "c72e91f4b6a3"
    assert revisions[0].down_revision == "a41f8c7d2e90"
    assert revisions[1].revision == "a41f8c7d2e90"
    assert revisions[1].down_revision == "6c9d4e7f2a10"
    assert revisions[2].revision == "6c9d4e7f2a10"
    assert revisions[2].down_revision == "8e5b2f1c4a90"
    assert revisions[3].revision == "8e5b2f1c4a90"
    assert revisions[3].down_revision == "bf82b90bcd5e"
    assert revisions[4].revision == "bf82b90bcd5e"
    assert revisions[4].down_revision == "ffc99a7e1d4f"
    assert revisions[5].revision == "ffc99a7e1d4f"
    assert revisions[5].down_revision is None


def test_alembic_environment_has_connected_database_guard_and_rejects_offline() -> None:
    environment = Path("alembic/env.py").read_text(encoding="utf-8")
    safety = Path(
        "src/discord_ai_reminder_bot/infrastructure/database/migration_safety.py"
    ).read_text(encoding="utf-8")
    assert "SELECT current_database()" in safety
    assert "actual_database != expected_database" in safety
    assert environment.index("await verify_connected_database") < environment.index(
        "connection.run_sync(do_run_migrations)"
    )
    assert "offline migration mode is not permitted" in environment
    assert "invocation_from_environment" in environment


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


def test_schedule_display_name_revision_has_backfill_and_safe_downgrade_guard() -> None:
    revision = Path("alembic/versions/6c9d4e7f2a10_add_schedule_display_names.py").read_text(
        encoding="utf-8"
    )
    assert "display_name = NULL, display_name_source = 'unset'" in revision
    assert "contains persisted display names" in revision
    assert "RAISE EXCEPTION" in revision


def test_name_generation_revision_has_restrict_fk_and_safe_downgrade_guard() -> None:
    revision = Path("alembic/versions/a41f8c7d2e90_add_name_generation_foundation.py").read_text(
        encoding="utf-8"
    )
    assert 'ondelete="RESTRICT"' in revision
    assert "SELECT 1 FROM name_generation_jobs" in revision
    assert "SELECT 1 FROM name_generation_budget_buckets" in revision
    assert "RAISE EXCEPTION" in revision
    assert "50" not in revision
    assert "500" not in revision
    assert "100000000" not in revision
