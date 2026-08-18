"""Guild-only read commands for Phase 1 schedules."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import discord
from discord import app_commands
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.schedule_creation import (
    DuplicateScheduleWarning,
    OnceScheduleCreationService,
)
from discord_ai_reminder_bot.application.schedule_queries import (
    MAX_PAGE_NUMBER,
    InvalidScheduleQueryError,
    ScheduleQueryService,
)
from discord_ai_reminder_bot.bot.interactions import (
    INTERNAL_ERROR_MESSAGE,
    PERMISSION_DENIED_MESSAGE,
    is_authorized_interaction,
    respond_ephemeral,
)
from discord_ai_reminder_bot.bot.post_presenter import (
    STATUS_LABELS,
    created_schedule_embed,
    schedule_detail_embed,
    schedule_list_embed,
)
from discord_ai_reminder_bot.domain.clock import Clock
from discord_ai_reminder_bot.domain.enums import ScheduleStatus
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.domain.schedule_creation import (
    InvalidScheduleContentError,
    parse_once_scheduled_at,
    validate_create_content,
)

NOT_FOUND_MESSAGE = "指定された予約は見つからないか、表示する権限がありません。"
INVALID_INPUT_MESSAGE = "入力内容を確認してください。"
DUPLICATE_WARNING_MESSAGE = (
    "同一予約の可能性があります。意図的に作成する場合はallow_duplicate=trueで再実行してください。"
)


@dataclass(frozen=True)
class InteractionActor:
    user_id: int
    administrator: bool


def authorized_actor(
    interaction: discord.Interaction,
    *,
    configured_guild_id: int,
    allowed_role_ids: tuple[int, ...],
) -> InteractionActor | None:
    """Repeat authorization at the operation boundary and extract safe identity data."""
    if not is_authorized_interaction(
        interaction,
        configured_guild_id=configured_guild_id,
        allowed_role_ids=allowed_role_ids,
    ):
        return None
    member = interaction.user
    if not isinstance(member, discord.Member):
        return None
    user_id = member.id
    permissions = member.guild_permissions
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
        or permissions is None
    ):
        return None
    return InteractionActor(user_id=user_id, administrator=permissions.administrator is True)


class PostCommands(app_commands.Group):
    """Phase 1 `/post` command group."""

    def __init__(
        self,
        *,
        queries: ScheduleQueryService,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        configured_guild_id: int,
        allowed_role_ids: tuple[int, ...],
        logger: logging.Logger,
    ) -> None:
        super().__init__(name="post", description="予約投稿を確認します")
        self._queries = queries
        self._session_factory = session_factory
        self._clock = clock
        self._configured_guild_id = configured_guild_id
        self._allowed_role_ids = allowed_role_ids
        self._logger = logger

    @app_commands.command(name="create", description="単発の予約投稿を作成します")
    @app_commands.describe(
        channel="投稿先のテキストチャンネルです",
        scheduled_at="日本時間をYYYY-MM-DD HH:MMで指定します",
        content="投稿本文です。未指定の場合は下書きになります",
        allow_duplicate="重複候補があっても意図的に作成します",
    )
    async def create_command(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        scheduled_at: app_commands.Range[str, 16, 16],
        content: app_commands.Range[str, 1, 2_000] | None = None,
        allow_duplicate: bool = False,
    ) -> None:
        actor = await self._actor_or_respond(interaction)
        if actor is None:
            return
        try:
            channel_id = self._validated_channel(interaction, channel)
            now = self._clock.now()
            scheduled_for = parse_once_scheduled_at(scheduled_at, now=now)
            content = validate_create_content(content)
        except InvalidDateTimeError, InvalidScheduleContentError, ValueError:
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:  # noqa: BLE001 - Discord response details must remain private
            self._logger.error("schedule_create_defer_failed")
            return
        try:
            async with self._session_factory() as session, session.begin():
                created = await OnceScheduleCreationService(session).create(
                    guild_id=interaction.guild_id,
                    channel_id=channel_id,
                    creator_user_id=actor.user_id,
                    scheduled_for=scheduled_for,
                    content=content,
                    allow_duplicate=allow_duplicate,
                    now=now,
                )
        except DuplicateScheduleWarning:
            await respond_ephemeral(interaction, DUPLICATE_WARNING_MESSAGE, logger=self._logger)
            return
        except Exception:  # noqa: BLE001 - database details must not reach Discord or logs
            self._logger.error("schedule_create_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        await self._respond_embed(interaction, lambda: created_schedule_embed(created))

    @app_commands.command(name="list", description="操作できる予約を一覧表示します")
    @app_commands.describe(status="状態で絞り込みます", page="1から始まるページ番号です")
    @app_commands.choices(
        status=[
            app_commands.Choice(name=label, value=status.value)
            for status, label in STATUS_LABELS.items()
        ]
    )
    async def list_command(
        self,
        interaction: discord.Interaction,
        status: app_commands.Choice[str] | None = None,
        page: app_commands.Range[int, 1, MAX_PAGE_NUMBER] = 1,
    ) -> None:
        actor = await self._actor_or_respond(interaction)
        if actor is None:
            return
        try:
            parsed_status = ScheduleStatus(status.value) if status is not None else None
            schedules = await self._queries.list_schedules(
                guild_id=self._configured_guild_id,
                requester_user_id=actor.user_id,
                administrator=actor.administrator,
                status=parsed_status,
                page=page,
            )
        except InvalidScheduleQueryError:
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        except Exception:  # noqa: BLE001 - database details must not reach Discord or logs
            self._logger.error("schedule_list_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        await self._respond_embed(
            interaction,
            lambda: schedule_list_embed(schedules, page=page, status_filter=parsed_status),
        )

    @app_commands.command(name="show", description="予約の詳細を表示します")
    @app_commands.describe(public_id="予約IDを指定します")
    async def show_command(self, interaction: discord.Interaction, public_id: str) -> None:
        actor = await self._actor_or_respond(interaction)
        if actor is None:
            return
        try:
            schedule = await self._queries.show_schedule(
                guild_id=self._configured_guild_id,
                requester_user_id=actor.user_id,
                administrator=actor.administrator,
                public_id=public_id,
            )
        except InvalidScheduleQueryError:
            schedule = None
        except Exception:  # noqa: BLE001 - database details must not reach Discord or logs
            self._logger.error("schedule_show_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        if schedule is None:
            await respond_ephemeral(interaction, NOT_FOUND_MESSAGE, logger=self._logger)
            return
        await self._respond_embed(interaction, lambda: schedule_detail_embed(schedule))

    async def _actor_or_respond(self, interaction: discord.Interaction) -> InteractionActor | None:
        actor = authorized_actor(
            interaction,
            configured_guild_id=self._configured_guild_id,
            allowed_role_ids=self._allowed_role_ids,
        )
        if actor is None:
            await respond_ephemeral(interaction, PERMISSION_DENIED_MESSAGE, logger=self._logger)
        return actor

    def _validated_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> int:
        guild = interaction.guild
        if (
            guild is None
            or guild.id != self._configured_guild_id
            or not isinstance(channel, discord.TextChannel)
            or channel.guild.id != guild.id
        ):
            raise ValueError("invalid channel")
        member = guild.me
        if member is None:
            raise ValueError("missing bot member")
        permissions = channel.permissions_for(member)
        if not permissions.view_channel or not permissions.send_messages:
            raise ValueError("missing channel permission")
        return channel.id

    async def _respond_embed(
        self, interaction: discord.Interaction, factory: Callable[[], discord.Embed]
    ) -> None:
        try:
            embed = factory()
        except Exception:  # noqa: BLE001 - presentation failures must remain sanitized
            self._logger.error("schedule_presentation_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        await respond_ephemeral(interaction, embed=embed, logger=self._logger)
