"""Non-persistent detail UI state without database resources."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import TYPE_CHECKING

import discord

from discord_ai_reminder_bot.application.schedule_queries import ScheduleActionAvailability
from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType

if TYPE_CHECKING:
    from discord_ai_reminder_bot.bot.posts import PostCommands

DETAIL_BACK_CUSTOM_ID = "post_detail_back"
DETAIL_EDIT_CUSTOM_ID = "post_detail_edit"
DETAIL_PAUSE_CUSTOM_ID = "post_detail_pause"
DETAIL_RESUME_CUSTOM_ID = "post_detail_resume"
DETAIL_DELETE_CUSTOM_ID = "post_detail_delete"


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
    creator_user_id: int
    actions: ScheduleActionAvailability
    schedule_type: ScheduleType
    channel_id: int
    content: str | None
    next_run_at: datetime | None
    local_time: time | None
    weekday: int | None
    end_date: date | None
    list_origin: ScheduleListOrigin | None = None

    def __post_init__(self) -> None:
        if self.public_id.version != 7 or str(self.public_id) != str(self.public_id).lower():
            raise ValueError("detail context requires canonical UUIDv7")
        for value in (self.expected_version, self.actor_user_id, self.creator_user_id):
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
        self.edit_modal_active = False
        edit = discord.ui.Button(
            label="編集",
            emoji="✏️",
            style=discord.ButtonStyle.primary,
            custom_id=DETAIL_EDIT_CUSTOM_ID,
            disabled=not context.actions.can_edit,
        )
        edit.callback = self._edit
        self.add_item(edit)
        if context.actions.can_pause:
            button = discord.ui.Button(
                label="一時停止",
                emoji="⏸️",
                style=discord.ButtonStyle.secondary,
                custom_id=DETAIL_PAUSE_CUSTOM_ID,
            )
            button.callback = self._pause
            self.add_item(button)
        if context.actions.can_resume:
            button = discord.ui.Button(
                label="再開",
                emoji="▶️",
                style=discord.ButtonStyle.success,
                custom_id=DETAIL_RESUME_CUSTOM_ID,
            )
            button.callback = self._resume
            self.add_item(button)
        delete = discord.ui.Button(
            label="削除",
            emoji="🗑️",
            style=discord.ButtonStyle.danger,
            custom_id=DETAIL_DELETE_CUSTOM_ID,
            disabled=not context.actions.can_delete,
        )
        self.add_item(delete)
        delete.callback = self._delete
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

    async def _pause(self, interaction: discord.Interaction) -> None:
        await self.commands._pause_from_detail(self, interaction)

    async def _edit(self, interaction: discord.Interaction) -> None:
        await self.commands._edit_from_detail(self, interaction)

    async def _resume(self, interaction: discord.Interaction) -> None:
        await self.commands._resume_from_detail(self, interaction)

    async def _delete(self, interaction: discord.Interaction) -> None:
        await self.commands._delete_from_detail(self, interaction)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return await self.commands._detail_interaction_allowed(self, interaction)

    async def on_timeout(self) -> None:
        await self.commands._expire_detail(self)

    def disable_components(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button | discord.ui.Select):
                item.disabled = True
