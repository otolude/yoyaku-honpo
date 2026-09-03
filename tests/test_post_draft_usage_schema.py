import importlib.util
import uuid
from pathlib import Path
from unittest.mock import MagicMock, call

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from discord_ai_reminder_bot.infrastructure.database.base import Base
from discord_ai_reminder_bot.infrastructure.database.models import (
    PostDraftOperatorBudgetBucket,
    PostDraftRateLimitBucket,
    PostDraftUsageReservationReceipt,
)

REVISION_ID = "c72e91f4b6a3"
REVISION_PATH = Path("alembic/versions/c72e91f4b6a3_add_post_draft_usage_tables.py")
NEW_TABLES = {
    "post_draft_operator_budget_buckets",
    "post_draft_rate_limit_buckets",
    "post_draft_usage_reservation_receipts",
}
LEGACY_TABLES = {
    "schedules",
    "schedule_runs",
    "delivery_attempts",
    "operation_logs",
    "notification_logs",
    "notification_attempts",
    "name_generation_jobs",
    "name_generation_budget_buckets",
}


def constraint_names(table_name: str) -> set[str]:
    return {
        str(constraint.name)
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, CheckConstraint)
    }


def server_default(table_name: str, column_name: str) -> str | None:
    default = Base.metadata.tables[table_name].c[column_name].server_default
    return None if default is None else str(default.arg)


def test_metadata_adds_exactly_three_post_draft_usage_tables() -> None:
    assert set(Base.metadata.tables) == LEGACY_TABLES | NEW_TABLES
    assert {
        model.__tablename__
        for model in (
            PostDraftOperatorBudgetBucket,
            PostDraftRateLimitBucket,
            PostDraftUsageReservationReceipt,
        )
    } == NEW_TABLES


def test_operator_budget_bucket_schema() -> None:
    table = PostDraftOperatorBudgetBucket.__table__
    assert set(table.c.keys()) == {
        "period_type",
        "period_start",
        "reserved_request_count",
        "reserved_cost_microunits",
        "version",
        "created_at",
        "updated_at",
    }
    assert tuple(table.primary_key.columns.keys()) == ("period_type", "period_start")
    assert isinstance(table.c.period_type.type, String) and table.c.period_type.type.length == 16
    assert isinstance(table.c.period_start.type, Date)
    assert isinstance(table.c.reserved_request_count.type, BigInteger)
    assert isinstance(table.c.reserved_cost_microunits.type, BigInteger)
    assert isinstance(table.c.version.type, Integer)
    assert all(column.nullable is False for column in table.c)
    assert server_default(table.name, "reserved_request_count") == "0"
    assert server_default(table.name, "reserved_cost_microunits") == "0"
    assert server_default(table.name, "version") == "1"
    assert server_default(table.name, "created_at") == "CURRENT_TIMESTAMP"
    assert server_default(table.name, "updated_at") == "CURRENT_TIMESTAMP"
    assert constraint_names(table.name) == {
        "ck_post_draft_operator_budget_buckets_period_type_valid",
        "ck_post_draft_operator_budget_buckets_monthly_period_starts_first",
        "ck_post_draft_operator_budget_buckets_request_count_nonnegative",
        "ck_post_draft_operator_budget_buckets_reserved_cost_nonnegative",
        "ck_post_draft_operator_budget_buckets_version_positive",
        "ck_post_draft_operator_budget_buckets_updated_after_created",
    }
    assert not table.indexes


def test_rate_limit_bucket_schema() -> None:
    table = PostDraftRateLimitBucket.__table__
    assert set(table.c.keys()) == {
        "scope_type",
        "scope_id",
        "window_type",
        "window_start",
        "request_count",
        "version",
        "created_at",
        "updated_at",
    }
    assert tuple(table.primary_key.columns.keys()) == (
        "scope_type",
        "scope_id",
        "window_type",
        "window_start",
    )
    assert isinstance(table.c.scope_type.type, String) and table.c.scope_type.type.length == 16
    assert isinstance(table.c.scope_id.type, BigInteger)
    assert isinstance(table.c.window_type.type, String) and table.c.window_type.type.length == 16
    assert isinstance(table.c.window_start.type, DateTime) and table.c.window_start.type.timezone
    assert isinstance(table.c.request_count.type, BigInteger)
    assert isinstance(table.c.version.type, Integer)
    assert all(column.nullable is False for column in table.c)
    assert server_default(table.name, "request_count") == "0"
    assert server_default(table.name, "version") == "1"
    assert constraint_names(table.name) == {
        "ck_post_draft_rate_limit_buckets_scope_type_valid",
        "ck_post_draft_rate_limit_buckets_window_type_valid",
        "ck_post_draft_rate_limit_buckets_scope_window_valid",
        "ck_post_draft_rate_limit_buckets_scope_id_positive",
        "ck_post_draft_rate_limit_buckets_request_count_nonnegative",
        "ck_post_draft_rate_limit_buckets_version_positive",
        "ck_post_draft_rate_limit_buckets_updated_after_created",
    }
    assert {str(index.name) for index in table.indexes} == {
        "ix_post_draft_rate_limit_buckets_window_start"
    }


def test_receipt_schema_has_only_opaque_idempotency_data() -> None:
    table = PostDraftUsageReservationReceipt.__table__
    assert set(table.c.keys()) == {"operation_key", "reserved_at", "expires_at"}
    assert tuple(table.primary_key.columns.keys()) == ("operation_key",)
    assert isinstance(table.c.operation_key.type, UUID)
    assert isinstance(table.c.reserved_at.type, DateTime) and table.c.reserved_at.type.timezone
    assert isinstance(table.c.expires_at.type, DateTime) and table.c.expires_at.type.timezone
    assert all(column.nullable is False for column in table.c)
    assert constraint_names(table.name) == {
        "ck_post_draft_usage_reservation_receipts_expires_after_reserved"
    }
    forbidden = {
        "user_id",
        "guild_id",
        "interaction_id",
        "content",
        "body",
        "prompt",
        "purpose",
        "key_points",
        "schedule_id",
        "provider_id",
        "maximum_cost_microunits",
        "reserved_cost_microunits",
    }
    assert forbidden.isdisjoint(table.c.keys())


def test_models_do_not_expose_identifiers_in_repr() -> None:
    operation_canary = uuid.UUID("bd45b751-b1d7-4681-a2e7-14d26579a479")
    scope_canary = 8_765_432_109_876_543
    receipt = PostDraftUsageReservationReceipt(operation_key=operation_canary)
    bucket = PostDraftRateLimitBucket(scope_id=scope_canary)
    assert str(operation_canary) not in repr(receipt)
    assert str(scope_canary) not in repr(bucket)


def load_revision():
    spec = importlib.util.spec_from_file_location("post_draft_usage_revision", REVISION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_is_single_linear_child_of_current_head() -> None:
    revision = load_revision()
    assert revision.revision == REVISION_ID
    assert revision.down_revision == "a41f8c7d2e90"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_creates_only_requested_tables_and_cleanup_index(monkeypatch) -> None:
    revision = load_revision()
    create_table = MagicMock()
    create_index = MagicMock()
    monkeypatch.setattr(revision.op, "create_table", create_table)
    monkeypatch.setattr(revision.op, "create_index", create_index)
    revision.upgrade()
    assert [item.args[0] for item in create_table.call_args_list] == [
        "post_draft_operator_budget_buckets",
        "post_draft_rate_limit_buckets",
        "post_draft_usage_reservation_receipts",
    ]
    assert create_index.call_args_list == [
        call(
            "ix_post_draft_rate_limit_buckets_window_start",
            "post_draft_rate_limit_buckets",
            ["window_start"],
        )
    ]


def test_downgrade_guards_all_tables_then_drops_in_reverse_order(monkeypatch) -> None:
    revision = load_revision()
    operations: list[tuple[str, str]] = []
    monkeypatch.setattr(revision.op, "execute", lambda sql: operations.append(("guard", str(sql))))
    monkeypatch.setattr(
        revision.op,
        "drop_index",
        lambda name, **kwargs: operations.append(("index", name)),
    )
    monkeypatch.setattr(revision.op, "drop_table", lambda name: operations.append(("table", name)))
    revision.downgrade()
    assert [kind for kind, _value in operations] == ["guard", "table", "index", "table", "table"]
    guard = operations[0][1]
    assert all(f"SELECT 1 FROM {table}" in guard for table in NEW_TABLES)
    assert "RAISE EXCEPTION 'cannot downgrade: post draft usage data exists'" in guard
    assert [value for kind, value in operations if kind == "table"] == [
        "post_draft_usage_reservation_receipts",
        "post_draft_rate_limit_buckets",
        "post_draft_operator_budget_buckets",
    ]


def test_migration_source_has_no_payload_credentials_or_real_identifiers() -> None:
    source = REVISION_PATH.read_text(encoding="utf-8")
    forbidden = (
        "DATABASE_URL",
        "postgresql://",
        "postgresql+psycopg://",
        "api_key",
        "credential",
        "discord_token",
        "interaction_id",
        "schedule_id",
        "provider_id",
        "purpose",
        "key_points",
        "content",
        "prompt",
        "example.com",
    )
    assert all(value not in source for value in forbidden)
    assert "name_generation_jobs" not in source
    assert "name_generation_budget_buckets" not in source
    assert "operation_logs" not in source
