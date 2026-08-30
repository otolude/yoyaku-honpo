"""Application settings loaded from environment variables and a local .env file."""

from __future__ import annotations

import math
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from discord_ai_reminder_bot.domain.name_generation import BudgetPolicy

MAX_POSTGRES_BIGINT = 9_223_372_036_854_775_807
DiscordId = Annotated[int, Field(gt=0, le=MAX_POSTGRES_BIGINT)]
AllowedRoleIds = Annotated[tuple[DiscordId, ...], NoDecode]


class DatabaseSettings(BaseSettings):
    """Settings required by database-only commands such as Alembic."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    database_url: SecretStr = Field(validation_alias="DATABASE_URL")

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        url = value.get_secret_value()
        if not url.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URLにはPsycopg用のPostgreSQL URLを指定してください")
        return value


class Settings(DatabaseSettings):
    """Validated settings required by the complete application.

    Secret values use ``SecretStr`` so their values are masked in ``repr`` and
    validation output. Call ``get_secret_value()`` only at the integration boundary.
    """

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
    notification_poll_interval_seconds: float = Field(
        default=10, validation_alias="NOTIFICATION_POLL_INTERVAL_SECONDS"
    )
    notification_batch_size: int = Field(default=20, validation_alias="NOTIFICATION_BATCH_SIZE")
    notification_max_concurrency: int = Field(
        default=5, validation_alias="NOTIFICATION_MAX_CONCURRENCY"
    )
    notification_processing_timeout_seconds: int = Field(
        default=120, validation_alias="NOTIFICATION_PROCESSING_TIMEOUT_SECONDS"
    )
    ai_name_generation_enabled: bool = Field(
        default=False, validation_alias="AI_NAME_GENERATION_ENABLED"
    )
    ai_name_generation_poll_interval_seconds: int = Field(
        default=5, validation_alias="AI_NAME_GENERATION_POLL_INTERVAL_SECONDS"
    )
    ai_name_generation_timeout_seconds: int = Field(
        default=5, validation_alias="AI_NAME_GENERATION_TIMEOUT_SECONDS"
    )
    ai_name_generation_max_concurrency: int = Field(
        default=1, validation_alias="AI_NAME_GENERATION_MAX_CONCURRENCY"
    )
    ai_name_generation_daily_request_limit: int = Field(
        default=50, validation_alias="AI_NAME_GENERATION_DAILY_REQUEST_LIMIT"
    )
    ai_name_generation_monthly_request_limit: int = Field(
        default=500, validation_alias="AI_NAME_GENERATION_MONTHLY_REQUEST_LIMIT"
    )
    ai_name_generation_monthly_cost_limit_microunits: int = Field(
        default=100_000_000,
        validation_alias="AI_NAME_GENERATION_MONTHLY_COST_LIMIT_MICROUNITS",
    )
    ai_name_generation_cost_currency: str = Field(
        default="JPY", validation_alias="AI_NAME_GENERATION_COST_CURRENCY"
    )
    ai_name_generation_processing_lease_seconds: int = Field(
        default=30, validation_alias="AI_NAME_GENERATION_PROCESSING_LEASE_SECONDS"
    )
    ai_name_generation_job_retention_days: int = Field(
        default=30, validation_alias="AI_NAME_GENERATION_JOB_RETENTION_DAYS"
    )
    ai_name_generation_budget_retention_days: int = Field(
        default=90, validation_alias="AI_NAME_GENERATION_BUDGET_RETENTION_DAYS"
    )

    @field_validator("ai_name_generation_enabled", mode="before")
    @classmethod
    def validate_ai_enabled(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise ValueError("AI予約名生成の有効値はtrueまたはfalseにしてください")

    @field_validator(
        "ai_name_generation_poll_interval_seconds",
        "ai_name_generation_timeout_seconds",
        "ai_name_generation_max_concurrency",
        "ai_name_generation_daily_request_limit",
        "ai_name_generation_monthly_request_limit",
        "ai_name_generation_monthly_cost_limit_microunits",
        "ai_name_generation_processing_lease_seconds",
        "ai_name_generation_job_retention_days",
        "ai_name_generation_budget_retention_days",
        mode="before",
    )
    @classmethod
    def validate_ai_positive_integer(cls, value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("AI予約名生成設定は正の整数にしてください")  # noqa: TRY004
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise ValueError("AI予約名生成設定は正の整数にしてください") from error
        if str(value).strip() != str(parsed) or not 1 <= parsed <= MAX_POSTGRES_BIGINT:
            raise ValueError("AI予約名生成設定は正の整数にしてください")
        return parsed

    @field_validator("notification_poll_interval_seconds", mode="before")
    @classmethod
    def validate_notification_poll_interval(cls, value: object) -> float:
        if isinstance(value, bool):
            raise ValueError("通知確認間隔は正の有限値にしてください")  # noqa: TRY004
        try:
            parsed = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise ValueError("通知確認間隔は正の有限値にしてください") from error
        if not math.isfinite(parsed) or parsed <= 0:
            raise ValueError("通知確認間隔は正の有限値にしてください")
        return parsed

    @field_validator("notification_batch_size", "notification_max_concurrency", mode="before")
    @classmethod
    def validate_notification_bounded_integer(cls, value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("通知件数は1から20にしてください")  # noqa: TRY004
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise ValueError("通知件数は1から20にしてください") from error
        if str(value).strip() != str(parsed) or not 1 <= parsed <= 20:
            raise ValueError("通知件数は1から20にしてください")
        return parsed

    @field_validator("notification_processing_timeout_seconds", mode="before")
    @classmethod
    def validate_notification_timeout(cls, value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("通知処理期限は正の整数にしてください")  # noqa: TRY004
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise ValueError("通知処理期限は正の整数にしてください") from error
        if str(value).strip() != str(parsed) or parsed <= 0:
            raise ValueError("通知処理期限は正の整数にしてください")
        return parsed

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

    @model_validator(mode="after")
    def validate_scheduler_limits(self) -> Self:
        if self.scheduler_max_concurrency > self.scheduler_batch_size:
            raise ValueError("最大並行数は1回の取得件数以下にしてください")
        if self.notification_max_concurrency > self.notification_batch_size:
            raise ValueError("通知最大並行数は1回の通知取得件数以下にしてください")
        if self.ai_name_generation_max_concurrency != 1:
            raise ValueError("2BではAI予約名生成の最大並行数は1だけです")
        if (
            self.ai_name_generation_processing_lease_seconds
            < self.ai_name_generation_timeout_seconds * 2
        ):
            raise ValueError("AI予約名生成leaseはtimeoutの2倍以上にしてください")
        BudgetPolicy(
            daily_request_limit=self.ai_name_generation_daily_request_limit,
            monthly_request_limit=self.ai_name_generation_monthly_request_limit,
            monthly_cost_limit_microunits=(self.ai_name_generation_monthly_cost_limit_microunits),
            cost_currency=self.ai_name_generation_cost_currency,
        )
        return self

    def name_generation_budget_policy(self) -> BudgetPolicy:
        return BudgetPolicy(
            daily_request_limit=self.ai_name_generation_daily_request_limit,
            monthly_request_limit=self.ai_name_generation_monthly_request_limit,
            monthly_cost_limit_microunits=self.ai_name_generation_monthly_cost_limit_microunits,
            cost_currency=self.ai_name_generation_cost_currency,
        )


def load_settings() -> Settings:
    """Load and validate settings before starting the application."""
    return Settings()


def load_database_settings() -> DatabaseSettings:
    """Load only the settings required to connect to PostgreSQL."""
    return DatabaseSettings()
