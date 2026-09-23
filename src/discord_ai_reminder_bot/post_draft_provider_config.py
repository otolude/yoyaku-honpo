"""Independent fail-closed settings for the optional post-draft provider."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from discord_ai_reminder_bot.infrastructure.ai.post_draft_request_guard import (
    LIVE_READINESS_CHECKS,
    MODEL_ALLOWLIST,
    LiveReadinessEvidence,
    ModelPricePolicy,
)

_INVALID_PROVIDER_SETTINGS = "invalid OpenAI post draft provider settings"
_PLACEHOLDERS = frozenset({"placeholder", "changeme", "your-api-key"})

# Governance remains closed even if an environment accidentally supplies every marker.
PRODUCTION_REAL_PROVIDER_GATE_OPEN = False
CLIENT_SHUTDOWN_STRATEGY_APPROVED = False


def _strict_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError(_INVALID_PROVIDER_SETTINGS)


def _strict_positive_integer(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError(_INVALID_PROVIDER_SETTINGS)  # noqa: TRY004
    if type(value) is int:
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        parsed = int(value)
    else:
        raise ValueError(_INVALID_PROVIDER_SETTINGS)
    if parsed <= 0:
        raise ValueError(_INVALID_PROVIDER_SETTINGS)
    return parsed


def _strict_positive_decimal(value: object) -> Decimal:
    if isinstance(value, (bool, float)):
        raise ValueError(_INVALID_PROVIDER_SETTINGS)  # noqa: TRY004
    if isinstance(value, Decimal):
        parsed = value
    elif type(value) is int:
        parsed = Decimal(value)
    elif isinstance(value, str) and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError(_INVALID_PROVIDER_SETTINGS) from error
    else:
        raise ValueError(_INVALID_PROVIDER_SETTINGS)
    if not parsed.is_finite() or parsed <= 0 or parsed.adjusted() > 30:
        raise ValueError(_INVALID_PROVIDER_SETTINGS)
    return parsed


class OpenAIPostDraftProviderSettings(BaseSettings):
    """Complete provider settings; completeness never implies live readiness."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        hide_input_in_errors=True,
    )

    enabled: bool = Field(
        default=False,
        validation_alias="AI_POST_DRAFT_PROVIDER_ENABLED",
        repr=False,
    )
    api_key: SecretStr | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_API_KEY",
        repr=False,
    )
    model: str | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_MODEL",
        repr=False,
    )
    reasoning_effort: str | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_REASONING_EFFORT",
        repr=False,
    )
    sdk_inner_timeout_seconds: float | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_INNER_TIMEOUT_SECONDS",
        repr=False,
    )
    application_outer_timeout_seconds: float | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_OUTER_TIMEOUT_SECONDS",
        repr=False,
    )
    max_output_tokens: int | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_MAX_OUTPUT_TOKENS",
        repr=False,
    )
    generation_attempt_cap: int | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_GENERATION_ATTEMPT_CAP",
        repr=False,
    )
    external_call_cap: int | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_EXTERNAL_CALL_CAP",
        repr=False,
    )
    generation_reserved_cost_cap_usd: Decimal | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_GENERATION_COST_CAP_USD",
        repr=False,
    )
    input_price_per_million_usd: Decimal | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_INPUT_PRICE_PER_MILLION_USD",
        repr=False,
    )
    output_price_per_million_usd: Decimal | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_OUTPUT_PRICE_PER_MILLION_USD",
        repr=False,
    )
    long_context_threshold_tokens: int | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_LONG_CONTEXT_THRESHOLD_TOKENS",
        repr=False,
    )
    long_context_input_multiplier: Decimal | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_LONG_CONTEXT_INPUT_MULTIPLIER",
        repr=False,
    )
    long_context_output_multiplier: Decimal | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_LONG_CONTEXT_OUTPUT_MULTIPLIER",
        repr=False,
    )
    price_source_url: str | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_PRICE_SOURCE_URL",
        repr=False,
    )
    price_verified_on: date | None = Field(
        default=None,
        validation_alias="AI_POST_DRAFT_OPENAI_PRICE_VERIFIED_ON",
        repr=False,
    )

    formal_model_selected: bool = Field(
        default=False,
        validation_alias="AI_POST_DRAFT_FORMAL_MODEL_SELECTED",
        repr=False,
    )
    exact_model_snapshot_policy_selected: bool = Field(
        default=False,
        validation_alias="AI_POST_DRAFT_EXACT_MODEL_SNAPSHOT_POLICY_SELECTED",
        repr=False,
    )
    model_price_snapshot_verified: bool = Field(
        default=False,
        validation_alias="AI_POST_DRAFT_MODEL_PRICE_SNAPSHOT_VERIFIED",
        repr=False,
    )
    long_context_pricing_verified: bool = Field(
        default=False,
        validation_alias="AI_POST_DRAFT_LONG_CONTEXT_PRICING_VERIFIED",
        repr=False,
    )
    input_tokens_pricing_contract_verified: bool = Field(
        default=False,
        validation_alias="AI_POST_DRAFT_INPUT_TOKENS_PRICING_VERIFIED",
        repr=False,
    )
    input_tokens_retention_contract_reviewed: bool = Field(
        default=False,
        validation_alias="AI_POST_DRAFT_INPUT_TOKENS_RETENTION_REVIEWED",
        repr=False,
    )
    dedicated_credential_configured: bool = Field(
        default=False,
        validation_alias="AI_POST_DRAFT_DEDICATED_CREDENTIAL_CONFIGURED",
        repr=False,
    )
    billing_budget_account_controls_reviewed: bool = Field(
        default=False,
        validation_alias="AI_POST_DRAFT_BILLING_BUDGET_CONTROLS_REVIEWED",
        repr=False,
    )
    explicit_live_authorization_present: bool = Field(
        default=False,
        validation_alias="AI_POST_DRAFT_EXPLICIT_LIVE_AUTHORIZATION_PRESENT",
        repr=False,
    )
    client_shutdown_strategy_approved: bool = Field(
        default=False,
        validation_alias="AI_POST_DRAFT_CLIENT_SHUTDOWN_STRATEGY_APPROVED",
        repr=False,
    )

    @field_validator("enabled", *LIVE_READINESS_CHECKS, mode="before")
    @classmethod
    def validate_boolean(cls, value: object) -> bool:
        return _strict_bool(value)

    @field_validator("client_shutdown_strategy_approved", mode="after")
    @classmethod
    def keep_shutdown_strategy_closed(cls, value: bool) -> bool:
        # This governance decision cannot be opened by an environment string alone.
        if value is not CLIENT_SHUTDOWN_STRATEGY_APPROVED:
            raise ValueError(_INVALID_PROVIDER_SETTINGS)
        return value

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(_INVALID_PROVIDER_SETTINGS)
        if value.casefold() in _PLACEHOLDERS:
            raise ValueError(_INVALID_PROVIDER_SETTINGS)
        return value

    @field_validator("model", mode="before")
    @classmethod
    def validate_model(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value not in MODEL_ALLOWLIST:
            raise ValueError(_INVALID_PROVIDER_SETTINGS)
        return value

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def validate_reasoning_effort(cls, value: object) -> str | None:
        if value is None:
            return None
        if value not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError(_INVALID_PROVIDER_SETTINGS)
        return str(value)

    @field_validator(
        "sdk_inner_timeout_seconds",
        "application_outer_timeout_seconds",
        mode="before",
    )
    @classmethod
    def validate_timeout(cls, value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(_INVALID_PROVIDER_SETTINGS)  # noqa: TRY004
        try:
            parsed = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise ValueError(_INVALID_PROVIDER_SETTINGS) from error
        if not math.isfinite(parsed) or parsed <= 0:
            raise ValueError(_INVALID_PROVIDER_SETTINGS)
        return parsed

    @field_validator(
        "max_output_tokens",
        "generation_attempt_cap",
        "external_call_cap",
        "long_context_threshold_tokens",
        mode="before",
    )
    @classmethod
    def validate_positive_integer(cls, value: object) -> int | None:
        return None if value is None else _strict_positive_integer(value)

    @field_validator(
        "generation_reserved_cost_cap_usd",
        "input_price_per_million_usd",
        "output_price_per_million_usd",
        "long_context_input_multiplier",
        "long_context_output_multiplier",
        mode="before",
    )
    @classmethod
    def validate_positive_decimal(cls, value: object) -> Decimal | None:
        return None if value is None else _strict_positive_decimal(value)

    def readiness(self) -> LiveReadinessEvidence:
        return LiveReadinessEvidence(
            **{name: getattr(self, name) for name in LIVE_READINESS_CHECKS}
        )

    def price_policy(self) -> ModelPricePolicy:
        values = (
            self.model,
            self.input_price_per_million_usd,
            self.output_price_per_million_usd,
            self.long_context_threshold_tokens,
            self.long_context_input_multiplier,
            self.long_context_output_multiplier,
            self.price_source_url,
            self.price_verified_on,
        )
        if any(value is None for value in values):
            raise ValueError(_INVALID_PROVIDER_SETTINGS)
        assert self.model is not None
        assert self.input_price_per_million_usd is not None
        assert self.output_price_per_million_usd is not None
        assert self.long_context_threshold_tokens is not None
        assert self.long_context_input_multiplier is not None
        assert self.long_context_output_multiplier is not None
        assert self.price_source_url is not None
        assert self.price_verified_on is not None
        return ModelPricePolicy(
            model=self.model,
            input_price_per_million=self.input_price_per_million_usd,
            output_price_per_million=self.output_price_per_million_usd,
            long_context_threshold_tokens=self.long_context_threshold_tokens,
            long_context_input_multiplier=self.long_context_input_multiplier,
            long_context_output_multiplier=self.long_context_output_multiplier,
            source_url=self.price_source_url,
            verified_on=self.price_verified_on,
            exact_snapshot_policy_selected=self.exact_model_snapshot_policy_selected,
            model_price_snapshot_verified=self.model_price_snapshot_verified,
            long_context_pricing_verified=self.long_context_pricing_verified,
        )


_REQUIRED_OPERATIONAL_FIELDS = (
    "api_key",
    "model",
    "reasoning_effort",
    "sdk_inner_timeout_seconds",
    "application_outer_timeout_seconds",
    "max_output_tokens",
    "generation_attempt_cap",
    "external_call_cap",
    "generation_reserved_cost_cap_usd",
    "input_price_per_million_usd",
    "output_price_per_million_usd",
    "long_context_threshold_tokens",
    "long_context_input_multiplier",
    "long_context_output_multiplier",
    "price_source_url",
    "price_verified_on",
)


class OpenAIPostDraftProviderSettingsState(StrEnum):
    UNCONFIGURED = "unconfigured"
    BLOCKED = "blocked"
    CONFIGURED = "configured"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class OpenAIPostDraftProviderSettingsResult:
    state: OpenAIPostDraftProviderSettingsState
    settings: OpenAIPostDraftProviderSettings | None = field(repr=False)
    requested_enabled: bool | None
    live_ready: bool
    blockers: tuple[str, ...]


def load_openai_post_draft_provider_settings(
    *, env_file: str | Path | None = ".env"
) -> OpenAIPostDraftProviderSettingsResult:
    """Load complete syntax without constructing an HTTP-capable SDK client."""
    try:
        settings = OpenAIPostDraftProviderSettings(_env_file=env_file)
        required = tuple(getattr(settings, name) for name in _REQUIRED_OPERATIONAL_FIELDS)
        present = tuple(value is not None for value in required)
        if any(present) and not all(present):
            raise ValueError(_INVALID_PROVIDER_SETTINGS)
        if all(present):
            assert settings.sdk_inner_timeout_seconds is not None
            assert settings.application_outer_timeout_seconds is not None
            if settings.sdk_inner_timeout_seconds >= settings.application_outer_timeout_seconds:
                raise ValueError(_INVALID_PROVIDER_SETTINGS)
            # Every admitted generation has one count and one create call.  Do
            # not allow a configured attempt budget that cannot complete its
            # own fixed two-call protocol.
            assert settings.generation_attempt_cap is not None
            assert settings.external_call_cap is not None
            if settings.external_call_cap < 2 * settings.generation_attempt_cap:
                raise ValueError(_INVALID_PROVIDER_SETTINGS)
            settings.price_policy()
    except TypeError, ValidationError, ValueError:
        return OpenAIPostDraftProviderSettingsResult(
            state=OpenAIPostDraftProviderSettingsState.INVALID,
            settings=None,
            requested_enabled=None,
            live_ready=False,
            blockers=("invalid_provider_settings",),
        )

    if not any(present):
        if settings.enabled:
            return OpenAIPostDraftProviderSettingsResult(
                state=OpenAIPostDraftProviderSettingsState.INVALID,
                settings=None,
                requested_enabled=True,
                live_ready=False,
                blockers=("incomplete_provider_settings",),
            )
        return OpenAIPostDraftProviderSettingsResult(
            state=OpenAIPostDraftProviderSettingsState.UNCONFIGURED,
            settings=None,
            requested_enabled=False,
            live_ready=False,
            blockers=LIVE_READINESS_CHECKS + ("production_effective_gate_closed",),
        )

    readiness = settings.readiness()
    blockers = readiness.blockers
    if not PRODUCTION_REAL_PROVIDER_GATE_OPEN:
        blockers += ("production_effective_gate_closed",)
    live_ready = settings.enabled and not blockers
    return OpenAIPostDraftProviderSettingsResult(
        state=(
            OpenAIPostDraftProviderSettingsState.CONFIGURED
            if live_ready
            else OpenAIPostDraftProviderSettingsState.BLOCKED
        ),
        settings=settings,
        requested_enabled=settings.enabled,
        live_ready=live_ready,
        blockers=blockers,
    )
