"""Fail-closed composition boundary for AI post-draft services."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.post_draft_generation import (
    DisabledPostDraftGenerator,
    GeneratePostDraftService,
)
from discord_ai_reminder_bot.application.post_draft_usage_generation import (
    GeneratePostDraftWithUsageService,
)
from discord_ai_reminder_bot.infrastructure.ai.openai_post_draft_generator import (
    ProductionOpenAIPostDraftRuntimeOwner,
)
from discord_ai_reminder_bot.infrastructure.database.post_draft_usage_repository import (
    PostgreSQLPostDraftUsageRepository,
)
from discord_ai_reminder_bot.post_draft_config import (
    PostDraftUsageSettingsResult,
    PostDraftUsageSettingsState,
)
from discord_ai_reminder_bot.post_draft_provider_config import (
    CLIENT_SHUTDOWN_STRATEGY_APPROVED,
    PRODUCTION_REAL_PROVIDER_GATE_OPEN,
)

_DISABLED_GENERATOR_TIMEOUT_SECONDS = 1.0
_POST_DRAFT_PROVIDER_CONFIGURED = False


@dataclass(frozen=True, slots=True)
class PostDraftServiceComposition:
    """One runtime-owned graph; provider admission is source-gated."""

    settings: PostDraftUsageSettingsResult = field(repr=False)
    service: GeneratePostDraftWithUsageService = field(repr=False)
    effective_enabled: bool

    def __repr__(self) -> str:
        return f"PostDraftServiceComposition(effective_enabled={self.effective_enabled})"


def compose_post_draft_services(
    *,
    settings: PostDraftUsageSettingsResult,
    session_factory: async_sessionmaker[AsyncSession],
    provider_owner: ProductionOpenAIPostDraftRuntimeOwner | None = None,
) -> PostDraftServiceComposition:
    """Build a singleton-ready graph without ever deriving a gate from environment alone."""
    if not isinstance(settings, PostDraftUsageSettingsResult):
        raise TypeError("invalid post draft settings result")

    settings_gate = (
        settings.state is PostDraftUsageSettingsState.CONFIGURED and settings.policy is not None
    )
    if (
        provider_owner is not None
        and type(provider_owner) is not ProductionOpenAIPostDraftRuntimeOwner
    ):
        raise TypeError("invalid production post draft provider")
    provider_gate = (
        _POST_DRAFT_PROVIDER_CONFIGURED
        and PRODUCTION_REAL_PROVIDER_GATE_OPEN
        and CLIENT_SHUTDOWN_STRATEGY_APPROVED
        and provider_owner is not None
        and not provider_owner.closing
        and not provider_owner.closed
    )
    effective_enabled = settings_gate and provider_gate

    usage_repository = PostgreSQLPostDraftUsageRepository(session_factory)
    generator = (
        provider_owner.create_generator()
        if effective_enabled and provider_owner is not None
        else DisabledPostDraftGenerator()
    )
    generation_service = GeneratePostDraftService(
        generator=generator,
        timeout_seconds=_DISABLED_GENERATOR_TIMEOUT_SECONDS,
    )
    service = GeneratePostDraftWithUsageService(
        usage_repository=usage_repository,
        generation_service=generation_service,
        enabled=effective_enabled,
    )
    return PostDraftServiceComposition(
        settings=settings,
        service=service,
        effective_enabled=effective_enabled,
    )
