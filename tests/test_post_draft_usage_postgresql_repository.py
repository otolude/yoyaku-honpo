import ast
import asyncio
import importlib
import inspect
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker

from discord_ai_reminder_bot.application.post_draft_usage import PostDraftUsageReservation
from discord_ai_reminder_bot.domain.post_draft_usage import (
    PostDraftGuildId,
    PostDraftOperationKey,
    PostDraftUsagePolicy,
    PostDraftUserId,
)

MODULE_PATH = Path(
    "src/discord_ai_reminder_bot/infrastructure/database/post_draft_usage_repository.py"
)
OPERATION_CANARY = UUID("dd7bc90a-773f-48b5-b3a9-9c9238979e1c")
USER_CANARY = 8_432_109_876_543_210
GUILD_CANARY = 7_432_109_876_543_210


def reservation() -> PostDraftUsageReservation:
    return PostDraftUsageReservation.create(
        operation_key=PostDraftOperationKey(OPERATION_CANARY),
        user_id=PostDraftUserId(USER_CANARY),
        guild_id=PostDraftGuildId(GUILD_CANARY),
        maximum_cost_microunits=12_345,
        now=datetime(2026, 9, 4, 1, 2, 3, tzinfo=UTC),
        policy=PostDraftUsagePolicy(),
    )


def repository_class():
    module = importlib.import_module(
        "discord_ai_reminder_bot.infrastructure.database.post_draft_usage_repository"
    )
    assert hasattr(module, "PostgreSQLPostDraftUsageRepository")
    return module.PostgreSQLPostDraftUsageRepository


def repository_module():
    return importlib.import_module(
        "discord_ai_reminder_bot.infrastructure.database.post_draft_usage_repository"
    )


def test_repository_implements_port_with_session_factory_dependency() -> None:
    repository_type = repository_class()
    parameters = inspect.signature(repository_type).parameters
    assert tuple(parameters) == ("session_factory",)
    repository = repository_type(async_sessionmaker())
    assert inspect.iscoroutinefunction(repository.reserve)


def test_constructor_rejects_non_session_factory_without_implicit_conversion() -> None:
    repository_type = repository_class()
    with pytest.raises(TypeError, match="^invalid post draft usage session factory$"):
        repository_type(object())


def test_receipt_insert_uses_postgresql_on_conflict_do_nothing() -> None:
    module = repository_module()
    statement = module._receipt_insert_statement(reservation())
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "INSERT INTO post_draft_usage_reservation_receipts" in compiled
    assert "ON CONFLICT (operation_key) DO NOTHING" in compiled
    assert "RETURNING post_draft_usage_reservation_receipts.operation_key" in compiled


def test_fixed_lock_order_is_explicit_and_complete() -> None:
    module = repository_module()
    assert module.POST_DRAFT_USAGE_LOCK_ORDER == (
        "operator_daily",
        "operator_monthly",
        "guild_daily",
        "user_short",
    )


def test_lock_statements_use_select_for_update_and_postgresql_upsert() -> None:
    module = repository_module()
    value = reservation()
    statements = module._bucket_statements(value)
    assert tuple(statements) == module.POST_DRAFT_USAGE_LOCK_ORDER
    for insert_statement, lock_statement in statements.values():
        insert_sql = str(insert_statement.compile(dialect=postgresql.dialect()))
        lock_sql = str(lock_statement.compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT" in insert_sql
        assert "DO NOTHING" in insert_sql
        assert "FOR UPDATE" in lock_sql


def test_repository_source_has_no_forbidden_runtime_dependencies_or_logging() -> None:
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
    assert not any("schedule" in name for name in imported)
    assert "retry" not in source.lower()
    assert "echo=" not in source


def test_repository_source_does_not_define_payload_fields() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "purpose",
        "key_points",
        "content",
        "body",
        "prompt",
        "interaction_id",
        "schedule_id",
        "provider_id",
    )
    assert all(value not in source for value in forbidden)


class EnterFailure:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def __aenter__(self):
        raise self.error

    async def __aexit__(self, *_args: object) -> None:
        return None


class FailingSessionFactory(async_sessionmaker):
    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error

    def __call__(self, **_local_kw: object):
        return EnterFailure(self.error)


@pytest.mark.asyncio
async def test_database_exception_propagates_without_canary_exposure() -> None:
    canary = "db-detail-dd7bc90a-773f-48b5-b3a9-9c9238979e1c"
    repository = repository_class()(FailingSessionFactory(RuntimeError(canary)))
    with pytest.raises(RuntimeError, match=canary):
        await repository.reserve(reservation())


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed() -> None:
    repository = repository_class()(FailingSessionFactory(asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await repository.reserve(reservation())


def test_repository_repr_does_not_expose_reservation_canaries() -> None:
    repository = repository_class()(async_sessionmaker())
    observed = repr(repository)
    assert str(OPERATION_CANARY) not in observed
    assert str(USER_CANARY) not in observed
    assert str(GUILD_CANARY) not in observed
