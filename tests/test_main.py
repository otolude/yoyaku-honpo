import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from discord_ai_reminder_bot.config import Settings
from discord_ai_reminder_bot.post_draft_provider_config import (
    OpenAIPostDraftProviderSettingsResult,
    OpenAIPostDraftProviderSettingsState,
)


def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        TIMEZONE="Asia/Tokyo",
        DISCORD_BOT_TOKEN="main-boundary-token",
        DISCORD_APPLICATION_ID=900000000000000001,
        DISCORD_GUILD_ID=100,
        DISCORD_ALLOWED_ROLE_IDS="200",
        DISCORD_OPERATOR_USER_ID=300,
        DISCORD_OPERATOR_CHANNEL_ID=400,
        DATABASE_URL="postgresql+psycopg://user:password@localhost/database_test",
    )


def test_import_does_not_run_bot(monkeypatch) -> None:
    run = MagicMock()
    monkeypatch.setattr("discord.ext.commands.Bot.run", run)
    module = importlib.import_module("discord_ai_reminder_bot.__main__")
    importlib.reload(module)
    run.assert_not_called()


def test_main_unwraps_token_only_for_run(monkeypatch) -> None:
    module = importlib.import_module("discord_ai_reminder_bot.__main__")
    configured = settings()
    bot = MagicMock()
    bot.worker_id = "worker"
    engine = MagicMock()
    create_provider_owner = MagicMock()
    monkeypatch.setattr(module, "load_settings", lambda: configured)
    monkeypatch.setattr(module, "configure_logging", MagicMock())
    monkeypatch.setattr(module, "create_database_engine", lambda value: engine)
    monkeypatch.setattr(module, "create_session_factory", lambda value: "sessions")
    monkeypatch.setattr(module, "ReminderBot", lambda **kwargs: bot)
    monkeypatch.setattr(
        module.ProductionOpenAIPostDraftRuntimeOwner,
        "create",
        create_provider_owner,
    )
    monkeypatch.setattr(
        module,
        "load_openai_post_draft_provider_settings",
        lambda: OpenAIPostDraftProviderSettingsResult(
            state=OpenAIPostDraftProviderSettingsState.UNCONFIGURED,
            settings=None,
            requested_enabled=False,
            live_ready=False,
            blockers=("production_effective_gate_closed",),
        ),
    )

    assert module.main() == 0
    create_provider_owner.assert_not_called()
    bot.run.assert_called_once_with("main-boundary-token", reconnect=True, log_handler=None)


def test_main_rejects_requested_provider_before_database_or_bot(monkeypatch) -> None:
    module = importlib.import_module("discord_ai_reminder_bot.__main__")
    configured = settings()
    create_engine = MagicMock()
    bot = MagicMock()
    monkeypatch.setattr(module, "load_settings", lambda: configured)
    monkeypatch.setattr(module, "configure_logging", MagicMock())
    monkeypatch.setattr(module, "create_database_engine", create_engine)
    monkeypatch.setattr(module, "ReminderBot", bot)
    monkeypatch.setattr(
        module,
        "load_openai_post_draft_provider_settings",
        lambda: OpenAIPostDraftProviderSettingsResult(
            state=OpenAIPostDraftProviderSettingsState.BLOCKED,
            settings=None,
            requested_enabled=True,
            live_ready=False,
            blockers=("formal_model_selected",),
        ),
    )
    assert module.main() == 1
    create_engine.assert_not_called()
    bot.assert_not_called()


def _live_result() -> OpenAIPostDraftProviderSettingsResult:
    return OpenAIPostDraftProviderSettingsResult(
        state=OpenAIPostDraftProviderSettingsState.CONFIGURED,
        settings=object(),  # type: ignore[arg-type] - the factory is replaced before use
        requested_enabled=True,
        live_ready=True,
        blockers=(),
    )


def _configure_live_owner(monkeypatch, module):
    owner = MagicMock()
    owner.close = AsyncMock(return_value=True)
    monkeypatch.setattr(module, "load_settings", settings)
    monkeypatch.setattr(module, "configure_logging", MagicMock())
    monkeypatch.setattr(module, "load_openai_post_draft_provider_settings", _live_result)
    monkeypatch.setattr(
        module.ProductionOpenAIPostDraftRuntimeOwner, "create", lambda _settings: owner
    )
    return owner


def _failing_logger(monkeypatch, module, error: BaseException) -> MagicMock:
    logger = MagicMock()
    logger.error.side_effect = error
    monkeypatch.setattr(module, "logging", SimpleNamespace(getLogger=lambda _name: logger))
    return logger


def test_engine_startup_failure_closes_created_provider_once(monkeypatch) -> None:
    module = importlib.import_module("discord_ai_reminder_bot.__main__")
    owner = _configure_live_owner(monkeypatch, module)
    create_engine = MagicMock(side_effect=RuntimeError("private-engine-canary"))
    monkeypatch.setattr(module, "create_database_engine", create_engine)
    monkeypatch.setattr(module, "ReminderBot", MagicMock())

    assert module.main() == 1
    create_engine.assert_called_once()
    owner.close.assert_awaited_once()


def test_owner_creation_failure_starts_no_engine_or_bot_cleanup(monkeypatch) -> None:
    module = importlib.import_module("discord_ai_reminder_bot.__main__")
    configured = settings()
    create_engine = MagicMock()
    bot = MagicMock()
    monkeypatch.setattr(module, "load_settings", lambda: configured)
    monkeypatch.setattr(module, "configure_logging", MagicMock())
    monkeypatch.setattr(module, "load_openai_post_draft_provider_settings", _live_result)
    monkeypatch.setattr(
        module.ProductionOpenAIPostDraftRuntimeOwner,
        "create",
        MagicMock(side_effect=RuntimeError("private-owner-canary")),
    )
    monkeypatch.setattr(module, "create_database_engine", create_engine)
    monkeypatch.setattr(module, "ReminderBot", bot)

    assert module.main() == 1
    create_engine.assert_not_called()
    bot.assert_not_called()


def test_bot_construction_failure_closes_provider_then_engine(monkeypatch) -> None:
    module = importlib.import_module("discord_ai_reminder_bot.__main__")
    owner = _configure_live_owner(monkeypatch, module)
    engine = MagicMock()
    monkeypatch.setattr(module, "create_database_engine", lambda _value: engine)
    monkeypatch.setattr(module, "create_session_factory", MagicMock())
    monkeypatch.setattr(
        module, "ReminderBot", MagicMock(side_effect=RuntimeError("private-bot-canary"))
    )

    assert module.main() == 1
    owner.close.assert_awaited_once()
    engine.dispose.assert_called_once()


def test_bot_run_failure_closes_resources_through_bot_ownership_once(monkeypatch) -> None:
    module = importlib.import_module("discord_ai_reminder_bot.__main__")
    owner = _configure_live_owner(monkeypatch, module)
    engine = MagicMock()
    bot = MagicMock()
    bot.run.side_effect = RuntimeError("private-run-canary")
    closed = False

    async def close() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        await owner.close()
        engine.dispose()

    bot.close = AsyncMock(side_effect=close)
    monkeypatch.setattr(module, "create_database_engine", lambda _value: engine)
    monkeypatch.setattr(module, "create_session_factory", MagicMock(return_value="sessions"))
    monkeypatch.setattr(module, "ReminderBot", lambda **_kwargs: bot)

    assert module.main() == 1
    bot.close.assert_awaited_once()
    owner.close.assert_awaited_once()
    engine.dispose.assert_called_once()


def test_bot_run_failure_after_internal_close_never_repeats_underlying_cleanup(monkeypatch) -> None:
    module = importlib.import_module("discord_ai_reminder_bot.__main__")
    owner = _configure_live_owner(monkeypatch, module)
    engine = MagicMock()
    bot = MagicMock()
    closed = False

    async def close() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        await owner.close()
        engine.dispose()

    bot.close = AsyncMock(side_effect=close)

    def run(*_args: object, **_kwargs: object) -> None:
        asyncio.run(bot.close())
        raise RuntimeError("private-run-after-close-canary")

    bot.run.side_effect = run
    monkeypatch.setattr(module, "create_database_engine", lambda _value: engine)
    monkeypatch.setattr(module, "create_session_factory", MagicMock(return_value="sessions"))
    monkeypatch.setattr(module, "ReminderBot", lambda **_kwargs: bot)

    assert module.main() == 1
    assert bot.close.await_count == 2
    owner.close.assert_awaited_once()
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("error_type", (asyncio.CancelledError, KeyboardInterrupt, SystemExit))
def test_bot_run_special_exception_preserves_identity_after_owned_cleanup(
    monkeypatch, error_type
) -> None:
    module = importlib.import_module("discord_ai_reminder_bot.__main__")
    owner = _configure_live_owner(monkeypatch, module)
    engine = MagicMock()
    bot = MagicMock()
    primary = error_type("private-run-special-canary")
    bot.run.side_effect = primary
    closed = False

    async def close() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        await owner.close()
        engine.dispose()

    bot.close = AsyncMock(side_effect=close)
    monkeypatch.setattr(module, "create_database_engine", lambda _value: engine)
    monkeypatch.setattr(module, "create_session_factory", MagicMock(return_value="sessions"))
    monkeypatch.setattr(module, "ReminderBot", lambda **_kwargs: bot)

    try:
        module.main()
    except error_type as error:
        assert error is primary
    else:
        raise AssertionError("special bot.run exception must remain authoritative")
    bot.close.assert_awaited_once()
    owner.close.assert_awaited_once()
    engine.dispose.assert_called_once()


def test_startup_primary_cleanup_and_logger_failures_do_not_change_control_flow(
    monkeypatch,
) -> None:
    module = importlib.import_module("discord_ai_reminder_bot.__main__")
    owner = _configure_live_owner(monkeypatch, module)
    owner.close.side_effect = RuntimeError("private-cleanup-canary")
    logger = _failing_logger(monkeypatch, module, RuntimeError("private-logging-canary"))
    monkeypatch.setattr(
        module,
        "create_database_engine",
        MagicMock(side_effect=RuntimeError("private-primary-canary")),
    )

    assert module.main() == 1
    owner.close.assert_awaited_once()
    assert logger.error.call_count == 2


def test_owned_bot_cleanup_continues_after_owner_and_logger_failures(monkeypatch) -> None:
    module = importlib.import_module("discord_ai_reminder_bot.__main__")
    owner = _configure_live_owner(monkeypatch, module)
    owner.close.side_effect = RuntimeError("private-owner-cleanup-canary")
    engine = MagicMock()
    engine.dispose.side_effect = RuntimeError("private-engine-cleanup-canary")
    bot = MagicMock()
    bot.run.side_effect = RuntimeError("private-run-primary-canary")

    async def close() -> None:
        owner_failed = engine_failed = False
        try:
            await owner.close()
        except BaseException:  # noqa: BLE001 - test models cleanup continuation
            owner_failed = True
        try:
            engine.dispose()
        except BaseException:  # noqa: BLE001 - test models cleanup continuation
            engine_failed = True
        if owner_failed or engine_failed:
            raise RuntimeError("private-bot-cleanup-canary")

    bot.close = AsyncMock(side_effect=close)
    logger = _failing_logger(monkeypatch, module, RuntimeError("private-logging-canary"))
    monkeypatch.setattr(module, "create_database_engine", lambda _value: engine)
    monkeypatch.setattr(module, "create_session_factory", MagicMock(return_value="sessions"))
    monkeypatch.setattr(module, "ReminderBot", lambda **_kwargs: bot)

    assert module.main() == 1
    bot.close.assert_awaited_once()
    owner.close.assert_awaited_once()
    engine.dispose.assert_called_once()
    assert logger.error.call_count == 2


@pytest.mark.parametrize(
    "log_error", (Exception, asyncio.CancelledError, KeyboardInterrupt, SystemExit)
)
@pytest.mark.parametrize("primary_type", (asyncio.CancelledError, KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("failure_phase", ("startup", "run"))
def test_special_primary_identity_survives_every_logger_failure(
    monkeypatch, log_error, primary_type, failure_phase
) -> None:
    module = importlib.import_module("discord_ai_reminder_bot.__main__")
    owner = _configure_live_owner(monkeypatch, module)
    owner.close.side_effect = RuntimeError("private-cleanup-canary")
    engine = MagicMock()
    primary = primary_type("private-primary-canary")
    logger_failure = log_error("private-logging-canary")
    logger = _failing_logger(monkeypatch, module, logger_failure)
    logged_failures: list[BaseException] = []

    def fail_logging(*_args: object, **_kwargs: object) -> None:
        logged_failures.append(logger_failure)
        raise logger_failure

    logger.error.side_effect = fail_logging
    create_engine = MagicMock(return_value=engine)
    bot = MagicMock()

    async def close_after_run_failure() -> None:
        owner_failed = False
        try:
            await owner.close()
        except BaseException:  # noqa: BLE001 - test models required cleanup continuation
            owner_failed = True
        engine.dispose()
        if owner_failed:
            raise RuntimeError("private-bot-cleanup-canary")

    if failure_phase == "startup":
        bot_factory = MagicMock(side_effect=primary)
    else:
        bot.run.side_effect = primary
        bot.close = AsyncMock(side_effect=close_after_run_failure)
        bot_factory = MagicMock(return_value=bot)
    monkeypatch.setattr(module, "create_database_engine", create_engine)
    monkeypatch.setattr(module, "create_session_factory", MagicMock(return_value="sessions"))
    monkeypatch.setattr(module, "ReminderBot", bot_factory)

    try:
        module.main()
    except primary_type as error:
        assert error is primary
        assert "private-cleanup-canary" not in repr(error)
        assert "private-logging-canary" not in repr(error)
    else:
        raise AssertionError("logger failure must not replace the special primary")
    owner.close.assert_awaited_once()
    engine.dispose.assert_called_once()
    create_engine.assert_called_once()
    bot_factory.assert_called_once()
    if failure_phase == "run":
        bot.run.assert_called_once()
        bot.close.assert_awaited_once()
    logger.error.assert_called_once_with("post_draft_provider_startup_cleanup_failed", extra=None)
    assert logged_failures == [logger_failure]


@pytest.mark.parametrize("error_type", (asyncio.CancelledError, KeyboardInterrupt, SystemExit))
def test_startup_special_exception_identity_survives_cleanup(monkeypatch, error_type) -> None:
    module = importlib.import_module("discord_ai_reminder_bot.__main__")
    owner = _configure_live_owner(monkeypatch, module)
    primary = error_type("private-primary-canary")
    monkeypatch.setattr(module, "create_database_engine", MagicMock(side_effect=primary))

    try:
        module.main()
    except error_type as error:
        assert error is primary
    else:
        raise AssertionError("special startup exception must remain authoritative")
    owner.close.assert_awaited_once()
