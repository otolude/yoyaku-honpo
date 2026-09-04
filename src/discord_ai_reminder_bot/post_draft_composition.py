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
from discord_ai_reminder_bot.infrastructure.database.post_draft_usage_repository import (
    PostgreSQLPostDraftUsageRepository,
)
from discord_ai_reminder_bot.post_draft_config import (
    PostDraftUsageSettingsResult,
    PostDraftUsageSettingsState,
)

_DISABLED_GENERATOR_TIMEOUT_SECONDS = 1.0
_POST_DRAFT_PROVIDER_CONFIGURED = False


@dataclass(frozen=True, slots=True)
class PostDraftServiceComposition:
    """One runtime-owned post-draft service graph with no active provider."""

    settings: PostDraftUsageSettingsResult = field(repr=False)
    service: GeneratePostDraftWithUsageService = field(repr=False)
    effective_enabled: bool = field(default=False, init=False)

    def __repr__(self) -> str:
        return "PostDraftServiceComposition(effective_enabled=False)"


def compose_post_draft_services(
    *,
    settings: PostDraftUsageSettingsResult,
    session_factory: async_sessionmaker[AsyncSession],
) -> PostDraftServiceComposition:
    """Build a singleton-ready service graph while the provider gate is closed."""
    if not isinstance(settings, PostDraftUsageSettingsResult):
        raise TypeError("invalid post draft settings result")

    settings_gate = (
        settings.state is PostDraftUsageSettingsState.CONFIGURED and settings.policy is not None
    )
    provider_gate = _POST_DRAFT_PROVIDER_CONFIGURED
    effective_enabled = settings_gate and provider_gate

    usage_repository = PostgreSQLPostDraftUsageRepository(session_factory)
    generation_service = GeneratePostDraftService(
        generator=DisabledPostDraftGenerator(),
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
    )
