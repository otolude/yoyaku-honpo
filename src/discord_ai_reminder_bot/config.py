"""Application settings loaded from environment variables and a local .env file."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

MAX_POSTGRES_BIGINT = 9_223_372_036_854_775_807
DiscordId = Annotated[int, Field(gt=0, le=MAX_POSTGRES_BIGINT)]
AllowedRoleIds = Annotated[tuple[DiscordId, ...], NoDecode]


class Settings(BaseSettings):
    """Validated runtime settings.

    Secret values use ``SecretStr`` so their values are masked in ``repr`` and
    validation output. Call ``get_secret_value()`` only at the integration boundary.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    app_env: Literal["development", "test", "production"] = Field(validation_alias="APP_ENV")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", validation_alias="LOG_LEVEL"
    )
    timezone: Literal["Asia/Tokyo"] = Field(validation_alias="TIMEZONE")

    discord_bot_token: SecretStr = Field(validation_alias="DISCORD_BOT_TOKEN")
    discord_guild_id: DiscordId = Field(validation_alias="DISCORD_GUILD_ID")
    discord_allowed_role_ids: AllowedRoleIds = Field(validation_alias="DISCORD_ALLOWED_ROLE_IDS")
    discord_operator_user_id: DiscordId = Field(validation_alias="DISCORD_OPERATOR_USER_ID")
    discord_operator_channel_id: DiscordId = Field(validation_alias="DISCORD_OPERATOR_CHANNEL_ID")

    database_url: SecretStr = Field(validation_alias="DATABASE_URL")

    scheduler_poll_interval_seconds: int = Field(
        default=10, gt=0, validation_alias="SCHEDULER_POLL_INTERVAL_SECONDS"
    )
    scheduler_batch_size: int = Field(default=20, gt=0, validation_alias="SCHEDULER_BATCH_SIZE")
    scheduler_max_concurrency: int = Field(
        default=5, gt=0, validation_alias="SCHEDULER_MAX_CONCURRENCY"
    )
    scheduler_processing_timeout_seconds: int = Field(
        default=120, gt=0, validation_alias="SCHEDULER_PROCESSING_TIMEOUT_SECONDS"
    )

    @field_validator("discord_allowed_role_ids", mode="before")
    @classmethod
    def parse_allowed_role_ids(cls, value: object) -> tuple[int, ...]:
        """Parse a comma-separated role list and reject empty or duplicate IDs."""
        if isinstance(value, str):
            raw_ids = [item.strip() for item in value.split(",")]
            if not raw_ids or any(not item for item in raw_ids):
                raise ValueError("許可ロールIDを1件以上指定してください")
            if any(not item.isdecimal() for item in raw_ids):
                raise ValueError("許可ロールIDは正の整数をカンマ区切りで指定してください")
            parsed = tuple(int(item) for item in raw_ids)
        elif isinstance(value, (list, tuple)):
            parsed = tuple(value)
        else:
            raise TypeError("許可ロールIDはカンマ区切りで指定してください")

        if not parsed:
            raise ValueError("許可ロールIDを1件以上指定してください")
        if len(parsed) != len(set(parsed)):
            raise ValueError("許可ロールIDに重複があります")
        return parsed

    @field_validator("discord_bot_token")
    @classmethod
    def validate_bot_token(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Discord Botトークンを設定してください")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        url = value.get_secret_value()
        if not url.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URLにはPsycopg用のPostgreSQL URLを指定してください")
        return value

    @model_validator(mode="after")
    def validate_scheduler_limits(self) -> Self:
        if self.scheduler_max_concurrency > self.scheduler_batch_size:
            raise ValueError("最大並行数は1回の取得件数以下にしてください")
        return self


def load_settings() -> Settings:
    """Load and validate settings before starting the application."""
    return Settings()
