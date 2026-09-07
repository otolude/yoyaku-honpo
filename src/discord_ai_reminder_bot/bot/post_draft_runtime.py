"""Runtime-owned disabled post-draft composition and command boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import discord
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
    ScheduleCreationPublicId,
    ScheduleCreationWaitLimit,
)
from discord_ai_reminder_bot.application.post_draft_schedule import (
    PostDraftScheduleComposition,
    PostDraftScheduleScope,
)
from discord_ai_reminder_bot.application.post_draft_ui_session import (
    PostDraftUISession,
    PostDraftUISessionController,
)
from discord_ai_reminder_bot.application.post_draft_usage import PostDraftUsageReservation
from discord_ai_reminder_bot.application.schedule_creation import IdempotentScheduleCreationService
from discord_ai_reminder_bot.bot.post_draft_ui import (
    PostDraftDiscordUI,
    send_disabled_post_draft_mode,
)
from discord_ai_reminder_bot.domain.clock import Clock
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.infrastructure.database.idempotent_schedule_creation_repository import (
    PostgreSQLIdempotentScheduleCreationRepository,
)
from discord_ai_reminder_bot.post_draft_composition import (
    PostDraftServiceComposition,
    compose_post_draft_services,
)
from discord_ai_reminder_bot.post_draft_config import PostDraftUsageSettingsResult

# Temporary UI safety lifetime; it is not a subscription or usage allowance.
POST_DRAFT_UI_TIMEOUT_SECONDS = 15.0 * 60.0
_INVALID_INTERACTION_MESSAGE = "この操作はサーバー内で最初からやり直してください。"


def _unused_reservation_factory(_instant: object) -> PostDraftUsageReservation:
    raise RuntimeError("AI post draft generation is disabled")


@dataclass(frozen=True, slots=True)
class PostDraftRuntime:
    """One Bot-owned service graph that retains no UI session registry."""

    composition: PostDraftServiceComposition = field(repr=False)
    schedule_composition: PostDraftScheduleComposition = field(repr=False)
    clock: Clock = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.schedule_composition, PostDraftScheduleComposition):
            raise TypeError("invalid post draft schedule composition")

    def __repr__(self) -> str:
        return "PostDraftRuntime(effective_enabled=False)"

    async def start(self, interaction: discord.Interaction) -> None:
        user = getattr(interaction, "user", None)
        owner_user_id = getattr(user, "id", None)
        guild_id = getattr(interaction, "guild_id", None)
        channel = getattr(interaction, "channel", None)
        channel_id = getattr(interaction, "channel_id", None)
        channel_object_id = getattr(channel, "id", None)
        channel_guild = getattr(channel, "guild", None)
        channel_guild_id = getattr(channel_guild, "id", None)
        channel_type = getattr(channel, "type", None)
        if (
            isinstance(owner_user_id, bool)
            or type(owner_user_id) is not int
            or owner_user_id <= 0
            or isinstance(guild_id, bool)
            or type(guild_id) is not int
            or guild_id <= 0
            or isinstance(channel_id, bool)
            or type(channel_id) is not int
            or channel_id <= 0
            or channel_object_id != channel_id
            or channel_guild_id != guild_id
            or channel_type is not discord.ChannelType.text
        ):
            await _safe_initial_error(interaction)
            return
        instant = require_utc(self.clock.now())
        session = PostDraftUISession.create(
            owner_user_id=owner_user_id,
            guild_id=guild_id,
            created_at=instant,
            expires_at=instant + timedelta(seconds=POST_DRAFT_UI_TIMEOUT_SECONDS),
        )
        scope = PostDraftScheduleScope(
            owner_user_id=owner_user_id,
            guild_id=guild_id,
            channel_id=channel_id,
        )
        controller = PostDraftUISessionController(
            session=session,
            generation_service=self.composition.service,
        )
        ui = PostDraftDiscordUI(
            controller=controller,
            now=self.clock.now,
            reservation_factory=_unused_reservation_factory,
            timeout_seconds=POST_DRAFT_UI_TIMEOUT_SECONDS,
            schedule_scope=scope,
            schedule_composition=self.schedule_composition,
        )
        try:
            await send_disabled_post_draft_mode(interaction, ui=ui)
        except Exception:  # noqa: BLE001 - Discord details must not cross this boundary
            return


def create_post_draft_runtime(
    *,
    settings: PostDraftUsageSettingsResult,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
) -> PostDraftRuntime:
    """Compose the disabled service graph exactly once for one Bot runtime."""
    composition = compose_post_draft_services(settings=settings, session_factory=session_factory)
    repository = PostgreSQLIdempotentScheduleCreationRepository(
        session_factory, wait_limit=ScheduleCreationWaitLimit.create(5.0)
    )
    schedule_service = IdempotentScheduleCreationService(repository)
    schedule_composition = PostDraftScheduleComposition(
        port=schedule_service,
        public_id_factory=ScheduleCreationPublicId.generate,
    )
    return PostDraftRuntime(
        composition=composition,
        schedule_composition=schedule_composition,
        clock=clock,
    )


async def _safe_initial_error(interaction: discord.Interaction) -> None:
    try:
        await interaction.response.send_message(
            _INVALID_INTERACTION_MESSAGE,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
            view=None,
        )
    except Exception:  # noqa: BLE001, S110 - do not retry Discord transport
        pass
