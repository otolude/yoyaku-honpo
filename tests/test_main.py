import importlib
from unittest.mock import MagicMock

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
    monkeypatch.setattr(module, "load_settings", lambda: configured)
    monkeypatch.setattr(module, "configure_logging", MagicMock())
    monkeypatch.setattr(module, "create_database_engine", lambda value: engine)
    monkeypatch.setattr(module, "create_session_factory", lambda value: "sessions")
    monkeypatch.setattr(module, "ReminderBot", lambda **kwargs: bot)
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
