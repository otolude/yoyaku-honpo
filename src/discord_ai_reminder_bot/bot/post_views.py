"""Non-persistent detail UI state without database resources."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from discord_ai_reminder_bot.application.schedule_queries import ScheduleActionAvailability
from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType

if TYPE_CHECKING:
    from discord_ai_reminder_bot.bot.posts import PostCommands

DETAIL_BACK_CUSTOM_ID = "post_detail_back"


@dataclass(frozen=True)
class ScheduleListOrigin:
    status: ScheduleStatus | None
    schedule_type: ScheduleType | None
    page: int

    def __post_init__(self) -> None:
        if isinstance(self.page, bool) or not isinstance(self.page, int) or self.page <= 0:
            raise ValueError("list origin page must be positive")


@dataclass(frozen=True)
class ScheduleDetailContext:
    public_id: uuid.UUID
    expected_version: int
    actor_user_id: int
    actions: ScheduleActionAvailability
    list_origin: ScheduleListOrigin | None = None

    def __post_init__(self) -> None:
        if self.public_id.version != 7 or str(self.public_id) != str(self.public_id).lower():
            raise ValueError("detail context requires canonical UUIDv7")
        for value in (self.expected_version, self.actor_user_id):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("detail context identifiers must be positive")
        if self.actions.observed_version != self.expected_version:
            raise ValueError("detail context versions must match")


class ScheduleDetailView(discord.ui.View):
    """One-user detail navigation retaining no Session or transaction."""

    def __init__(
        self,
        *,
        commands: PostCommands,
        interaction: discord.Interaction,
        context: ScheduleDetailContext,
        embed: discord.Embed,
    ) -> None:
        super().__init__(timeout=900.0)
        self.commands = commands
        self.initial_interaction = interaction
        self.context = context
        self.current_embed = embed
        self.action_lock = asyncio.Lock()
        self.finished = False
        self.closed = False
        self.timed_out = False
        if context.list_origin is not None:
            button = discord.ui.Button(
                label="一覧へ戻る",
                style=discord.ButtonStyle.secondary,
                custom_id=DETAIL_BACK_CUSTOM_ID,
            )
            button.callback = self._back
            self.add_item(button)

    @property
    def has_components(self) -> bool:
        return bool(self.children)

    async def _back(self, interaction: discord.Interaction) -> None:
        await self.commands._return_to_list(self, interaction)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return await self.commands._detail_interaction_allowed(self, interaction)

    async def on_timeout(self) -> None:
        await self.commands._expire_detail(self)

    def disable_components(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button | discord.ui.Select):
                item.disabled = True
