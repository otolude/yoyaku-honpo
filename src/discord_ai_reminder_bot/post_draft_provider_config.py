"""Independent fail-closed settings for the optional post-draft provider."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INVALID_PROVIDER_SETTINGS = "invalid OpenAI post draft provider settings"
_PLACEHOLDERS = frozenset({"placeholder", "changeme", "your-api-key"})


class OpenAIPostDraftProviderSettings(BaseSettings):
    """Syntactic provider settings that do not imply runtime availability."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    api_key: SecretStr | None = Field(
        default=None, validation_alias="AI_POST_DRAFT_OPENAI_API_KEY", repr=False
    )
    model: str | None = Field(
        default=None, validation_alias="AI_POST_DRAFT_OPENAI_MODEL", repr=False
    )
    timeout_seconds: float | None = Field(
        default=None, validation_alias="AI_POST_DRAFT_OPENAI_TIMEOUT_SECONDS", repr=False
    )

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
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(_INVALID_PROVIDER_SETTINGS)
        return value

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def validate_timeout(cls, value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(_INVALID_PROVIDER_SETTINGS)  # noqa: TRY004
        if type(value) in {int, float} or (
            isinstance(value, str) and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value)
        ):
            parsed = float(value)
        else:
            raise ValueError(_INVALID_PROVIDER_SETTINGS)
        if not math.isfinite(parsed) or parsed <= 0:
            raise ValueError(_INVALID_PROVIDER_SETTINGS)
        return parsed


class OpenAIPostDraftProviderSettingsState(StrEnum):
    UNCONFIGURED = "unconfigured"
    CONFIGURED = "configured"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class OpenAIPostDraftProviderSettingsResult:
    state: OpenAIPostDraftProviderSettingsState
    settings: OpenAIPostDraftProviderSettings | None = field(repr=False)


def load_openai_post_draft_provider_settings(
    *, env_file: str | Path | None = ".env"
) -> OpenAIPostDraftProviderSettingsResult:
    """Load provider syntax without propagating raw validation details."""
    try:
        settings = OpenAIPostDraftProviderSettings(_env_file=env_file)
    except TypeError, ValidationError, ValueError:
        return OpenAIPostDraftProviderSettingsResult(
            state=OpenAIPostDraftProviderSettingsState.INVALID,
            settings=None,
        )
    values = (settings.api_key, settings.model, settings.timeout_seconds)
    if all(value is None for value in values):
        return OpenAIPostDraftProviderSettingsResult(
            state=OpenAIPostDraftProviderSettingsState.UNCONFIGURED,
            settings=None,
        )
    if any(value is None for value in values):
        return OpenAIPostDraftProviderSettingsResult(
            state=OpenAIPostDraftProviderSettingsState.INVALID,
            settings=None,
        )
    return OpenAIPostDraftProviderSettingsResult(
        state=OpenAIPostDraftProviderSettingsState.CONFIGURED,
        settings=settings,
    )
