from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from discord_ai_reminder_bot.application.name_generation import DisabledNameGenerator
from discord_ai_reminder_bot.config import Settings
from discord_ai_reminder_bot.infrastructure.ai.factory import build_name_generator


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "APP_ENV": "test",
        "TIMEZONE": "Asia/Tokyo",
        "DISCORD_BOT_TOKEN": "test-token",
        "DISCORD_GUILD_ID": 100,
        "DISCORD_ALLOWED_ROLE_IDS": "200",
        "DISCORD_OPERATOR_USER_ID": 300,
        "DISCORD_OPERATOR_CHANNEL_ID": 400,
        "DATABASE_URL": "postgresql+psycopg://user:password@localhost/database_test",
    }
    values.update(overrides)
    return Settings(**values)


def test_disabled_or_incomplete_settings_never_construct_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = MagicMock()
    monkeypatch.setattr(
        "discord_ai_reminder_bot.infrastructure.ai.factory.create_openai_generator", create
    )
    assert isinstance(build_name_generator(settings()), DisabledNameGenerator)
    assert isinstance(
        build_name_generator(
            settings(AI_NAME_GENERATION_ENABLED=True, AI_NAME_GENERATION_PROVIDER="openai")
        ),
        DisabledNameGenerator,
    )
    create.assert_not_called()


def test_complete_settings_construct_one_configured_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    create = MagicMock(return_value=sentinel)
    monkeypatch.setattr(
        "discord_ai_reminder_bot.infrastructure.ai.factory.create_openai_generator", create
    )
    configured = settings(
        AI_NAME_GENERATION_ENABLED=True,
        AI_NAME_GENERATION_PROVIDER="openai",
        AI_NAME_GENERATION_OPENAI_API_KEY="secret-canary",
        AI_NAME_GENERATION_OPENAI_MODEL="gpt-5.6-luna",
        AI_NAME_GENERATION_OPENAI_REASONING_EFFORT="none",
        AI_NAME_GENERATION_OPENAI_INPUT_PRICE_MICRO_USD_PER_MILLION_TOKENS=200_000,
        AI_NAME_GENERATION_OPENAI_OUTPUT_PRICE_MICRO_USD_PER_MILLION_TOKENS=1_200_000,
        AI_NAME_GENERATION_USD_JPY_RATE_MICROUNITS=150_000_000,
    )
    assert build_name_generator(configured) is sentinel
    called = create.call_args
    assert called.kwargs["api_key"] == "secret-canary"
    assert called.kwargs["connect_timeout_seconds"] == 1.0
    assert called.kwargs["request_timeout_seconds"] == 4.0
    assert called.kwargs["config"].model == "gpt-5.6-luna"
    assert "secret-canary" not in repr(configured)
