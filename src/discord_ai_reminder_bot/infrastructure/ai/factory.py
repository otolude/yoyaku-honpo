"""Fail-closed composition of the optional OpenAI name generator."""

from __future__ import annotations

from discord_ai_reminder_bot.application.name_generation import (
    DisabledNameGenerator,
    NameGenerator,
)
from discord_ai_reminder_bot.config import Settings
from discord_ai_reminder_bot.infrastructure.ai.openai_name_generator import (
    OPENAI_PROVIDER,
    OpenAINameGeneratorConfig,
    create_openai_generator,
)


def build_name_generator(settings: Settings) -> NameGenerator:
    """Return Disabled unless every explicit external-I/O gate is satisfied."""
    if not settings.ai_name_generation_enabled:
        return DisabledNameGenerator()
    if settings.ai_name_generation_provider != OPENAI_PROVIDER:
        return DisabledNameGenerator()
    secret = settings.ai_name_generation_openai_api_key
    required = (
        settings.ai_name_generation_openai_model,
        settings.ai_name_generation_openai_reasoning_effort,
        settings.ai_name_generation_openai_input_price_micro_usd_per_million_tokens,
        settings.ai_name_generation_openai_output_price_micro_usd_per_million_tokens,
        settings.ai_name_generation_usd_jpy_rate_microunits,
    )
    if secret is None or not secret.get_secret_value().strip() or any(v is None for v in required):
        return DisabledNameGenerator()
    config = OpenAINameGeneratorConfig(
        model=settings.ai_name_generation_openai_model,  # type: ignore[arg-type]
        reasoning_effort=settings.ai_name_generation_openai_reasoning_effort,  # type: ignore[arg-type]
        input_price_micro_usd_per_million_tokens=(
            settings.ai_name_generation_openai_input_price_micro_usd_per_million_tokens
        ),  # type: ignore[arg-type]
        output_price_micro_usd_per_million_tokens=(
            settings.ai_name_generation_openai_output_price_micro_usd_per_million_tokens
        ),  # type: ignore[arg-type]
        usd_jpy_rate_microunits=settings.ai_name_generation_usd_jpy_rate_microunits,  # type: ignore[arg-type]
        cost_safety_basis_points=settings.ai_name_generation_cost_safety_basis_points,
        max_input_characters=settings.ai_name_generation_max_input_characters,
        max_input_bytes=settings.ai_name_generation_max_input_bytes,
        max_input_tokens=settings.ai_name_generation_max_input_tokens,
        max_output_tokens=settings.ai_name_generation_max_output_tokens,
    )
    return create_openai_generator(
        api_key=secret.get_secret_value(),
        config=config,
        connect_timeout_seconds=(
            settings.ai_name_generation_openai_connect_timeout_milliseconds / 1_000
        ),
        request_timeout_seconds=(
            settings.ai_name_generation_openai_request_timeout_milliseconds / 1_000
        ),
    )
