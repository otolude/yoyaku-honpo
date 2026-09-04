import ast
import importlib
import inspect
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker

from discord_ai_reminder_bot.domain.post_draft_usage import PostDraftUsagePolicy

NOW = datetime(2026, 9, 4, 4, 5, 6, tzinfo=UTC)
MODULE_NAME = "discord_ai_reminder_bot.infrastructure.database.post_draft_usage_cleanup_repository"
MODULE_PATH = Path(
    "src/discord_ai_reminder_bot/infrastructure/database/post_draft_usage_cleanup_repository.py"
)


def repository_module():
    return importlib.import_module(MODULE_NAME)


def repository_type():
    return repository_module().PostgreSQLPostDraftUsageCleanupRepository


def test_cleanup_repository_owns_only_session_factory_dependency() -> None:
    repository = repository_type()(async_sessionmaker())
    assert tuple(inspect.signature(repository_type()).parameters) == ("session_factory",)
    assert inspect.iscoroutinefunction(repository.cleanup)


def test_cleanup_repository_rejects_invalid_session_factory() -> None:
    with pytest.raises(TypeError, match="^invalid post draft usage cleanup session factory$"):
        repository_type()(object())


def test_cleanup_statements_are_scoped_and_use_inclusive_time_boundaries() -> None:
    statements = repository_module()._cleanup_statements(now=NOW, policy=PostDraftUsagePolicy())
    assert tuple(statements) == ("receipt", "user", "guild", "operator")
    sql = {
        name: str(
            statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )
        for name, statement in statements.items()
    }
    assert "expires_at <=" in sql["receipt"]
    assert "scope_type = 'user'" in sql["user"] and "window_type = 'short'" in sql["user"]
    assert "window_start <=" in sql["user"]
    assert "scope_type = 'guild'" in sql["guild"] and "window_type = 'daily'" in sql["guild"]
    assert "window_start <=" in sql["guild"]
    assert "period_type = 'daily'" in sql["operator"]
    assert "period_type = 'monthly'" in sql["operator"]
    assert "period_start <" in sql["operator"]


def test_operator_cutoffs_use_jst_dates_and_protect_current_month() -> None:
    cutoffs = repository_module().post_draft_usage_cleanup_cutoffs(
        NOW, policy=PostDraftUsagePolicy()
    )
    assert cutoffs.operator_daily_before == date(2026, 6, 6)
    assert cutoffs.operator_monthly_before == date(2026, 6, 1)


def test_deleted_count_normalization_handles_none_and_negative() -> None:
    normalize = repository_module()._deleted_count
    assert normalize(None) == 0
    assert normalize(-1) == 0
    assert normalize(0) == 0
    assert normalize(4) == 4


def test_cleanup_repository_has_no_payload_or_forbidden_dependencies() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(
        name == value or name.startswith(f"{value}.")
        for name in imported
        for value in ("logging", "openai", "discord")
    )
    assert not any("schedule" in name or "name_generation" in name for name in imported)
    for forbidden in ("purpose", "key_points", "content", "prompt", "provider", "operation_log"):
        assert forbidden not in source.lower()
    assert "retry" not in source.lower()
