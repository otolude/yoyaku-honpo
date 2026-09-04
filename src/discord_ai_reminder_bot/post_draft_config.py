"""Independent, fail-closed settings for post-draft usage controls."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from discord_ai_reminder_bot.domain.post_draft_usage import (
    MAX_POSTGRES_BIGINT,
    PostDraftOperatorBudgetPolicy,
    PostDraftRateLimitPolicy,
    PostDraftUsagePolicy,
)

_INVALID_SETTINGS = "invalid post draft usage settings"
_SETTINGS_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    case_sensitive=True,
)


def _strict_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError(_INVALID_SETTINGS)


def _strict_positive_bigint(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError(_INVALID_SETTINGS)  # noqa: TRY004
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        parsed = int(value)
    else:
        raise ValueError(_INVALID_SETTINGS)
    if parsed > MAX_POSTGRES_BIGINT:
        raise ValueError(_INVALID_SETTINGS)
    return parsed


class PostDraftUsageSettings(BaseSettings):
    """Strict environment representation, independent from core bot settings."""

    model_config = _SETTINGS_CONFIG

    enabled: bool = Field(default=False, validation_alias="AI_POST_DRAFT_ENABLED", repr=False)
    user_request_limit: int = Field(
        default=3, validation_alias="AI_POST_DRAFT_USER_REQUEST_LIMIT", repr=False
    )
    user_window_minutes: int = Field(
        default=10, validation_alias="AI_POST_DRAFT_USER_WINDOW_MINUTES", repr=False
    )
    guild_daily_request_limit: int = Field(
        default=30,
        validation_alias="AI_POST_DRAFT_GUILD_DAILY_REQUEST_LIMIT",
        repr=False,
    )
    global_daily_request_limit: int = Field(
        default=50,
        validation_alias="AI_POST_DRAFT_GLOBAL_DAILY_REQUEST_LIMIT",
        repr=False,
    )
    global_monthly_request_limit: int = Field(
        default=500,
        validation_alias="AI_POST_DRAFT_GLOBAL_MONTHLY_REQUEST_LIMIT",
        repr=False,
    )
    global_monthly_cost_limit_microunits: int = Field(
        default=500_000_000,
        validation_alias="AI_POST_DRAFT_GLOBAL_MONTHLY_COST_LIMIT_MICROUNITS",
        repr=False,
    )
    cost_currency: str = Field(
        default="JPY", validation_alias="AI_POST_DRAFT_COST_CURRENCY", repr=False
    )
    max_concurrency: int = Field(
        default=1, validation_alias="AI_POST_DRAFT_MAX_CONCURRENCY", repr=False
    )
    user_retention_days: int = Field(
        default=7, validation_alias="AI_POST_DRAFT_USER_RETENTION_DAYS", repr=False
    )
    guild_retention_days: int = Field(
        default=30, validation_alias="AI_POST_DRAFT_GUILD_RETENTION_DAYS", repr=False
    )
    operator_retention_days: int = Field(
        default=90, validation_alias="AI_POST_DRAFT_OPERATOR_RETENTION_DAYS", repr=False
    )
    receipt_retention_days: int = Field(
        default=7, validation_alias="AI_POST_DRAFT_RECEIPT_RETENTION_DAYS", repr=False
    )

    @field_validator("enabled", mode="before")
    @classmethod
    def validate_enabled(cls, value: object) -> bool:
        return _strict_bool(value)

    @field_validator(
        "user_request_limit",
        "user_window_minutes",
        "guild_daily_request_limit",
        "global_daily_request_limit",
        "global_monthly_request_limit",
        "global_monthly_cost_limit_microunits",
        "max_concurrency",
        "user_retention_days",
        "guild_retention_days",
        "operator_retention_days",
        "receipt_retention_days",
        mode="before",
    )
    @classmethod
    def validate_positive_bigint(cls, value: object) -> int:
        return _strict_positive_bigint(value)

    @field_validator("cost_currency", mode="before")
    @classmethod
    def validate_currency(cls, value: object) -> str:
        if value != "JPY":
            raise ValueError(_INVALID_SETTINGS)
        return "JPY"


class _PostDraftUsageEnabledSettings(BaseSettings):
    model_config = _SETTINGS_CONFIG

    enabled: bool = Field(default=False, validation_alias="AI_POST_DRAFT_ENABLED", repr=False)

    @field_validator("enabled", mode="before")
    @classmethod
    def validate_enabled(cls, value: object) -> bool:
        return _strict_bool(value)


class PostDraftUsageSettingsState(StrEnum):
    """Safe classification of requested post-draft usage configuration."""

    DISABLED = "disabled"
    CONFIGURED = "configured"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class PostDraftUsageSettingsResult:
    """Detail-free settings result safe to pass through normal bot startup."""

    state: PostDraftUsageSettingsState
    policy: PostDraftUsagePolicy | None = field(repr=False)
    requested_enabled: bool | None


def build_post_draft_usage_policy(settings: PostDraftUsageSettings) -> PostDraftUsagePolicy:
    """Build the authoritative Domain policy from strictly parsed settings."""
    if not isinstance(settings, PostDraftUsageSettings):
        raise ValueError(_INVALID_SETTINGS)  # noqa: TRY004
    try:
        return PostDraftUsagePolicy(
            operator_budget=PostDraftOperatorBudgetPolicy(
                daily_request_limit=settings.global_daily_request_limit,
                monthly_request_limit=settings.global_monthly_request_limit,
                monthly_cost_limit_microunits=(settings.global_monthly_cost_limit_microunits),
                cost_currency=settings.cost_currency,
                retention_days=settings.operator_retention_days,
            ),
            rate_limit=PostDraftRateLimitPolicy(
                user_request_limit=settings.user_request_limit,
                user_window_minutes=settings.user_window_minutes,
                guild_daily_request_limit=settings.guild_daily_request_limit,
                global_daily_request_limit=settings.global_daily_request_limit,
                user_retention_days=settings.user_retention_days,
                guild_retention_days=settings.guild_retention_days,
            ),
            maximum_concurrency=settings.max_concurrency,
            receipt_retention_days=settings.receipt_retention_days,
        )
    except TypeError, ValueError:
        raise ValueError(_INVALID_SETTINGS) from None


def load_post_draft_usage_settings(
    *, env_file: str | Path | None = ".env"
) -> PostDraftUsageSettingsResult:
    """Load independent settings without propagating validation details to startup."""
    try:
        requested_enabled = _PostDraftUsageEnabledSettings(_env_file=env_file).enabled
    except TypeError, ValidationError, ValueError:
        return PostDraftUsageSettingsResult(
            state=PostDraftUsageSettingsState.INVALID,
            policy=None,
            requested_enabled=None,
        )

    try:
        settings = PostDraftUsageSettings(_env_file=env_file)
        policy = build_post_draft_usage_policy(settings)
    except TypeError, ValidationError, ValueError:
        return PostDraftUsageSettingsResult(
            state=PostDraftUsageSettingsState.INVALID,
            policy=None,
            requested_enabled=requested_enabled,
        )

    state = (
        PostDraftUsageSettingsState.CONFIGURED
        if requested_enabled
        else PostDraftUsageSettingsState.DISABLED
    )
    return PostDraftUsageSettingsResult(
        state=state,
        policy=policy,
        requested_enabled=requested_enabled,
    )
