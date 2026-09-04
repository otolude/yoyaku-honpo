from __future__ import annotations

import ast
import asyncio
import importlib
import logging
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.post_draft_generation import PostDraftDisabledError
from discord_ai_reminder_bot.domain.post_draft_usage import PostDraftUsagePolicy
from discord_ai_reminder_bot.post_draft_config import (
    PostDraftUsageSettingsResult,
    PostDraftUsageSettingsState,
)

MODULE_NAME = "discord_ai_reminder_bot.post_draft_composition"
CANARIES = (
    "composition-raw-setting-canary",
    "composition-purpose-canary",
    "composition-key-points-canary",
    "composition-body-canary",
    "composition-user-id-canary",
    "composition-guild-id-canary",
    "composition-operation-key-canary",
)
ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "dataclasses",
        "sqlalchemy.ext.asyncio",
        "discord_ai_reminder_bot.application.post_draft_generation",
        "discord_ai_reminder_bot.application.post_draft_usage_generation",
        "discord_ai_reminder_bot.infrastructure.database.post_draft_usage_repository",
        "discord_ai_reminder_bot.post_draft_config",
    }
)


def composition_module():
    return importlib.import_module(MODULE_NAME)


def settings_result(state: PostDraftUsageSettingsState) -> PostDraftUsageSettingsResult:
    return PostDraftUsageSettingsResult(
        state=state,
        policy=None if state is PostDraftUsageSettingsState.INVALID else PostDraftUsagePolicy(),
        requested_enabled=(
            None
            if state is PostDraftUsageSettingsState.INVALID
            else state is PostDraftUsageSettingsState.CONFIGURED
        ),
    )


class CountingSessionFactory(async_sessionmaker[AsyncSession]):
    calls = 0

    def __call__(self, **local_kw: object) -> AsyncSession:
        del local_kw
        self.calls += 1
        raise AssertionError("composition must not start a database session")


class CountingRepository:
    def __init__(self) -> None:
        self.calls = 0

    async def reserve(self, reservation: object) -> object:
        del reservation
        self.calls += 1
        raise AssertionError("disabled composition must not reserve usage")


class CountingGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: object) -> object:
        del request
        self.calls += 1
        raise AssertionError("disabled composition must not call a provider")


@pytest.fixture
def composed_spies(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[object, object]]:
    repository = CountingRepository()
    generator = CountingGenerator()
    module = composition_module()
    monkeypatch.setattr(module, "PostgreSQLPostDraftUsageRepository", lambda _sessions: repository)
    monkeypatch.setattr(module, "DisabledPostDraftGenerator", lambda: generator)
    yield repository, generator


@pytest.mark.parametrize(
    "state",
    [
        PostDraftUsageSettingsState.DISABLED,
        PostDraftUsageSettingsState.INVALID,
        PostDraftUsageSettingsState.CONFIGURED,
    ],
)
@pytest.mark.asyncio
async def test_every_state_is_effectively_disabled_without_provider(
    state: PostDraftUsageSettingsState,
    composed_spies: tuple[CountingRepository, CountingGenerator],
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository, generator = composed_spies
    supplied_settings = settings_result(state)
    sessions = CountingSessionFactory()

    with caplog.at_level(logging.DEBUG):
        composition = composition_module().compose_post_draft_services(
            settings=supplied_settings,
            session_factory=sessions,
        )
        with pytest.raises(PostDraftDisabledError) as error:
            await composition.service.generate(CANARIES, CANARIES)  # type: ignore[arg-type]

    assert composition.effective_enabled is False
    assert composition.settings is supplied_settings
    assert composition.settings.policy is supplied_settings.policy
    assert repository.calls == 0
    assert generator.calls == 0
    assert sessions.calls == 0
    assert error.value.code.value == "disabled"
    observed = " ".join((repr(composition), repr(error.value), str(error.value), caplog.text))
    assert all(canary not in observed for canary in CANARIES)
    assert caplog.text == ""


def test_real_repository_constructor_has_no_session_or_query_side_effect() -> None:
    sessions = CountingSessionFactory()
    repository = composition_module().PostgreSQLPostDraftUsageRepository(sessions)
    assert sessions.calls == 0
    assert repository is not None


@pytest.mark.asyncio
async def test_composition_starts_no_task_or_background_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = 0

    def reject_task(*_args: object, **_kwargs: object) -> None:
        nonlocal created
        created += 1
        raise AssertionError("composition must not create tasks")

    monkeypatch.setattr(asyncio, "create_task", reject_task)
    sessions = CountingSessionFactory()
    before = asyncio.all_tasks()
    composition_module().compose_post_draft_services(
        settings=settings_result(PostDraftUsageSettingsState.DISABLED),
        session_factory=sessions,
    )
    assert asyncio.all_tasks() == before
    assert created == 0
    assert sessions.calls == 0


def test_composition_retains_one_singleton_service_and_semaphore() -> None:
    composition = composition_module().compose_post_draft_services(
        settings=settings_result(PostDraftUsageSettingsState.DISABLED),
        session_factory=CountingSessionFactory(),
    )
    first = composition.service
    second = composition.service
    assert first is second
    assert first._semaphore is second._semaphore


def test_invalid_settings_remain_policyless_without_default_fallback() -> None:
    supplied = settings_result(PostDraftUsageSettingsState.INVALID)
    composition = composition_module().compose_post_draft_services(
        settings=supplied,
        session_factory=CountingSessionFactory(),
    )
    assert composition.settings.policy is None
    assert composition.effective_enabled is False


def test_composition_needs_no_provider_environment_or_core_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in tuple(__import__("os").environ):
        if "POST_DRAFT" in key or "OPENAI" in key:
            monkeypatch.delenv(key, raising=False)
    composition = composition_module().compose_post_draft_services(
        settings=settings_result(PostDraftUsageSettingsState.CONFIGURED),
        session_factory=CountingSessionFactory(),
    )
    assert composition.effective_enabled is False


def _imports(source: str) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(f"{'.' * node.level}{node.module or ''}")
    return imported


def test_production_composition_imports_use_exact_allowlist() -> None:
    source = composition_module().__file__
    assert source is not None
    with open(source, encoding="utf-8") as module_file:
        assert _imports(module_file.read()) == ALLOWED_IMPORTS


@pytest.mark.parametrize(
    "source",
    [
        "import discord",
        "import openai as provider",
        "import asyncio.subprocess",
        "from discord.ext import commands",
        "from discord_ai_reminder_bot.bot import ReminderBot",
        (
            "from discord_ai_reminder_bot.application.post_draft_usage_cleanup import "
            "CleanupPostDraftUsageService"
        ),
        "from .infrastructure.database import models",
        "from ..bot import client as bot_client",
        "import discord_ai_reminder_bot.infrastructure.database.session as sessions",
    ],
)
def test_import_guard_rejects_forbidden_import_canaries(source: str) -> None:
    assert not _imports(source) <= ALLOWED_IMPORTS


@pytest.mark.parametrize(
    "source",
    [
        "# import discord",
        '"""from openai import AsyncOpenAI"""',
        "text = 'from discord_ai_reminder_bot.bot import ReminderBot'",
    ],
)
def test_import_guard_ignores_comments_docstrings_and_strings(source: str) -> None:
    assert _imports(source) == set()


def test_composition_is_not_wired_to_bot_ui_or_cleanup() -> None:
    source_path = composition_module().__file__
    assert source_path is not None
    with open(source_path, encoding="utf-8") as module_file:
        source = module_file.read()
    assert "ReminderBot" not in source
    assert "CleanupPostDraftUsageService" not in source
    assert "discord" not in _imports(source)
