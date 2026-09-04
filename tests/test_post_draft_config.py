from __future__ import annotations

import importlib
import inspect
import logging
from pathlib import Path

import pytest

from discord_ai_reminder_bot.config import Settings, load_settings
from discord_ai_reminder_bot.domain.post_draft_usage import (
    MAX_POSTGRES_BIGINT,
    PostDraftUsagePolicy,
)

MODULE_NAME = "discord_ai_reminder_bot.post_draft_config"
ENV_EXAMPLE = Path(".env.example")
CANARY = "post-draft-config-private-canary"
POST_DRAFT_ENV_KEYS = (
    "AI_POST_DRAFT_ENABLED",
    "AI_POST_DRAFT_USER_REQUEST_LIMIT",
    "AI_POST_DRAFT_USER_WINDOW_MINUTES",
    "AI_POST_DRAFT_GUILD_DAILY_REQUEST_LIMIT",
    "AI_POST_DRAFT_GLOBAL_DAILY_REQUEST_LIMIT",
    "AI_POST_DRAFT_GLOBAL_MONTHLY_REQUEST_LIMIT",
    "AI_POST_DRAFT_GLOBAL_MONTHLY_COST_LIMIT_MICROUNITS",
    "AI_POST_DRAFT_COST_CURRENCY",
    "AI_POST_DRAFT_MAX_CONCURRENCY",
    "AI_POST_DRAFT_USER_RETENTION_DAYS",
    "AI_POST_DRAFT_GUILD_RETENTION_DAYS",
    "AI_POST_DRAFT_OPERATOR_RETENTION_DAYS",
    "AI_POST_DRAFT_RECEIPT_RETENTION_DAYS",
)
INTEGER_ENV_KEYS = POST_DRAFT_ENV_KEYS[1:7] + POST_DRAFT_ENV_KEYS[8:]
POSITIVE_INTEGER_FIELDS = tuple(
    key.removeprefix("AI_POST_DRAFT_").lower() for key in INTEGER_ENV_KEYS
)


def config_module():
    return importlib.import_module(MODULE_NAME)


def clear_post_draft_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in POST_DRAFT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def load(monkeypatch: pytest.MonkeyPatch, **values: object):
    clear_post_draft_environment(monkeypatch)
    for key, value in values.items():
        monkeypatch.setenv(key, str(value))
    return config_module().load_post_draft_usage_settings(env_file=None)


def core_settings() -> Settings:
    return Settings(
        _env_file=None,
        APP_ENV="test",
        TIMEZONE="Asia/Tokyo",
        DISCORD_BOT_TOKEN="fixed-test-token",
        DISCORD_GUILD_ID=100,
        DISCORD_ALLOWED_ROLE_IDS="200",
        DISCORD_OPERATOR_USER_ID=300,
        DISCORD_OPERATOR_CHANNEL_ID=400,
        DATABASE_URL="postgresql+psycopg://user:password@localhost/database_test",
    )


def test_env_unset_is_disabled_with_domain_default_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    result = load(monkeypatch)
    assert result.state is config_module().PostDraftUsageSettingsState.DISABLED
    assert result.requested_enabled is False
    assert result.policy == PostDraftUsagePolicy()


@pytest.mark.parametrize("value", ["true", "TRUE", "True", "false", "FALSE", "False"])
def test_enabled_accepts_only_case_variants_of_true_false(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    result = load(monkeypatch, AI_POST_DRAFT_ENABLED=value)
    expected = (
        config_module().PostDraftUsageSettingsState.CONFIGURED
        if value.lower() == "true"
        else config_module().PostDraftUsageSettingsState.DISABLED
    )
    assert result.state is expected
    assert result.requested_enabled is (value.lower() == "true")
    assert result.policy == PostDraftUsagePolicy()


@pytest.mark.parametrize(("value", "expected"), [(True, True), (False, False)])
def test_settings_model_accepts_python_bool(value: bool, expected: bool) -> None:
    settings = config_module().PostDraftUsageSettings(_env_file=None, AI_POST_DRAFT_ENABLED=value)
    assert settings.enabled is expected


@pytest.mark.parametrize("value", ["1", "0", "yes", "no", "on", "off", "", " true ", "2"])
def test_invalid_enabled_is_fixed_invalid(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    result = load(monkeypatch, AI_POST_DRAFT_ENABLED=value)
    assert result.state is config_module().PostDraftUsageSettingsState.INVALID
    assert result.policy is None
    assert result.requested_enabled is None


@pytest.mark.parametrize("key", INTEGER_ENV_KEYS)
def test_canonical_positive_integer_strings_are_accepted(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    clear_post_draft_environment(monkeypatch)
    settings = config_module().PostDraftUsageSettings(_env_file=None, **{key: "1"})
    field_name = key.removeprefix("AI_POST_DRAFT_").lower()
    assert getattr(settings, field_name) == 1


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "+1", "1.0", "1e2", " 1", "1 ", "", "abc", str(MAX_POSTGRES_BIGINT + 1)],
)
def test_invalid_integer_strings_are_fixed_invalid(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    result = load(monkeypatch, AI_POST_DRAFT_USER_REQUEST_LIMIT=value)
    assert result.state is config_module().PostDraftUsageSettingsState.INVALID
    assert result.policy is None
    assert result.requested_enabled is False


@pytest.mark.parametrize("value", [True, False, 0.5, 1.0, None, object()])
def test_settings_model_rejects_non_integer_values(value: object) -> None:
    settings_type = config_module().PostDraftUsageSettings
    with pytest.raises(ValueError):
        settings_type(_env_file=None, AI_POST_DRAFT_USER_REQUEST_LIMIT=value)


@pytest.mark.parametrize("field_name", POSITIVE_INTEGER_FIELDS)
@pytest.mark.parametrize("value", [0, -1])
def test_settings_model_rejects_nonpositive_python_integers(field_name: str, value: int) -> None:
    settings_type = config_module().PostDraftUsageSettings
    alias = settings_type.model_fields[field_name].validation_alias
    assert isinstance(alias, str)
    with pytest.raises(ValueError, match="^invalid post draft usage settings$"):
        settings_type(_env_file=None, **{alias: value})


def test_settings_validation_error_does_not_expose_input_canary() -> None:
    settings_type = config_module().PostDraftUsageSettings
    with pytest.raises(ValueError) as error:
        settings_type(_env_file=None, AI_POST_DRAFT_USER_REQUEST_LIMIT=CANARY)
    observed = " ".join((str(error.value), repr(error.value)))
    assert CANARY not in observed


@pytest.mark.parametrize(
    ("values", "requested"),
    [
        (
            {
                "AI_POST_DRAFT_GLOBAL_DAILY_REQUEST_LIMIT": "50",
                "AI_POST_DRAFT_GLOBAL_MONTHLY_REQUEST_LIMIT": "49",
            },
            False,
        ),
        ({"AI_POST_DRAFT_GUILD_DAILY_REQUEST_LIMIT": "51"}, False),
        ({"AI_POST_DRAFT_COST_CURRENCY": "USD"}, False),
        ({"AI_POST_DRAFT_COST_CURRENCY": "jpy"}, False),
        ({"AI_POST_DRAFT_COST_CURRENCY": " JPY"}, False),
        ({"AI_POST_DRAFT_USER_WINDOW_MINUTES": "11"}, False),
        ({"AI_POST_DRAFT_MAX_CONCURRENCY": "2"}, False),
        ({"AI_POST_DRAFT_USER_RETENTION_DAYS": "0"}, False),
        ({"AI_POST_DRAFT_ENABLED": "true", "AI_POST_DRAFT_GUILD_DAILY_REQUEST_LIMIT": "51"}, True),
    ],
)
def test_domain_policy_failures_become_fixed_invalid(
    monkeypatch: pytest.MonkeyPatch, values: dict[str, str], requested: bool
) -> None:
    result = load(monkeypatch, **values)
    assert result.state is config_module().PostDraftUsageSettingsState.INVALID
    assert result.policy is None
    assert result.requested_enabled is requested


def test_configured_does_not_claim_provider_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    result = load(monkeypatch, AI_POST_DRAFT_ENABLED="true")
    assert result.state is config_module().PostDraftUsageSettingsState.CONFIGURED
    assert result.requested_enabled is True
    assert result.policy == PostDraftUsagePolicy()
    assert not hasattr(result, "provider_available")
    assert not hasattr(result, "effective_enabled")


def test_factory_keeps_operator_and_rate_global_daily_limits_equal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = load(monkeypatch, AI_POST_DRAFT_GLOBAL_DAILY_REQUEST_LIMIT="60")
    assert result.policy is not None
    assert result.policy.operator_budget.daily_request_limit == 60
    assert result.policy.rate_limit.global_daily_request_limit == 60


def test_invalid_post_draft_environment_does_not_affect_core_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_post_draft_environment(monkeypatch)
    monkeypatch.setenv("AI_POST_DRAFT_ENABLED", "not-valid")
    configured = core_settings()
    monkeypatch.setattr("discord_ai_reminder_bot.config.Settings", lambda: configured)
    assert load_settings() is configured


def test_invalid_canary_is_absent_from_result_repr_error_and_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        result = load(monkeypatch, AI_POST_DRAFT_USER_REQUEST_LIMIT=CANARY)
    observed = " ".join((repr(result), str(result), caplog.text))
    assert result.state is config_module().PostDraftUsageSettingsState.INVALID
    assert CANARY not in observed
    assert caplog.text == ""


def test_public_api_and_import_are_side_effect_free(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_post_draft_environment(monkeypatch)
    module = importlib.reload(config_module())
    assert inspect.isclass(module.PostDraftUsageSettings)
    assert inspect.isclass(module.PostDraftUsageSettingsResult)
    assert inspect.isclass(module.PostDraftUsageSettingsState)
    assert callable(module.load_post_draft_usage_settings)
    assert callable(module.build_post_draft_usage_policy)


def test_env_example_has_exactly_one_entry_for_every_usage_field() -> None:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    keys = [line.split("=", 1)[0] for line in lines if line.startswith("AI_POST_DRAFT_")]
    assert tuple(keys) == POST_DRAFT_ENV_KEYS
    assert len(keys) == len(set(keys))


def test_env_example_marks_defaults_as_temporary_non_plan_and_disabled() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    section = text[text.index("AI_POST_DRAFT_ENABLED") - 500 :]
    assert "実装・安全検証用" in section
    assert "暫定値" in section
    assert "正式な商品仕様ではありません" in section
    assert "プラン別利用枠ではありません" in section
    assert "価格や恒久上限ではありません" in section
    assert "実Provider有効化前に再監査" in section
    assert "Plan／Entitlementは未実装" in section
    assert "AI_POST_DRAFT_ENABLED=false" in section


def test_post_draft_settings_have_no_provider_or_secret_fields() -> None:
    fields = set(config_module().PostDraftUsageSettings.model_fields)
    assert fields == {key.removeprefix("AI_POST_DRAFT_").lower() for key in POST_DRAFT_ENV_KEYS}
    forbidden = ("api_key", "model", "provider", "price", "exchange", "timeout", "token")
    assert not any(word in field for field in fields for word in forbidden)


def test_post_draft_config_does_not_reference_name_generation() -> None:
    source = Path("src/discord_ai_reminder_bot/post_draft_config.py").read_text(encoding="utf-8")
    assert "AI_NAME_GENERATION" not in source
    assert "name_generation" not in source
    assert "logging" not in source
