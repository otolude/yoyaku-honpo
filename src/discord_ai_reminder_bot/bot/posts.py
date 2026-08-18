"""Guild-only read commands for Phase 1 schedules."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time

import discord
from discord import app_commands
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.schedule_creation import (
    DuplicateScheduleWarning,
    OnceScheduleCreationService,
    RecurringScheduleCreationService,
)
from discord_ai_reminder_bot.application.schedule_deletion import (
    DeleteReasonRequired,
    ScheduleDeletionService,
    ScheduleDeletionUnavailable,
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
    WEEKDAY_LABELS,
    created_recurring_schedule_embed,
    created_schedule_embed,
    deleted_schedule_embed,
    schedule_deletion_preview_embed,
    schedule_detail_embed,
    schedule_list_embed,
)
from discord_ai_reminder_bot.domain.clock import Clock
from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.domain.schedule_creation import (
    InvalidScheduleContentError,
    parse_end_date,
    parse_local_time,
    parse_once_scheduled_at,
    validate_create_content,
)
from discord_ai_reminder_bot.domain.schedule_deletion import (
    InvalidDeleteReasonError,
    validate_delete_reason,
)

NOT_FOUND_MESSAGE = "指定された予約は見つからないか、表示する権限がありません。"
INVALID_INPUT_MESSAGE = "入力内容を確認してください。"
DUPLICATE_WARNING_MESSAGE = (
    "同一予約の可能性があります。意図的に作成する場合はallow_duplicate=trueで再実行してください。"
)
DELETE_UNAVAILABLE_MESSAGE = "指定された予約は見つからないか、削除できません。"
DELETE_REASON_REQUIRED_MESSAGE = (
    "他の利用者が作成した予約を削除する場合は、削除理由を入力してください。"
)
DELETE_CANCELLED_MESSAGE = "予約の削除をキャンセルしました。"
DELETE_EXPIRED_MESSAGE = (
    "確認の有効期限が切れました。必要な場合はもう一度 /post delete を実行してください。"
)


@dataclass(frozen=True)
class InteractionActor:
    user_id: int
    administrator: bool


class ScheduleDeletionConfirmView(discord.ui.View):
    """One-user, non-persistent confirmation state with no open DB resources."""

    def __init__(
        self,
        *,
        commands: PostCommands,
        interaction: discord.Interaction,
        public_id: str,
        reason: str,
        actor_user_id: int,
    ) -> None:
        super().__init__(timeout=120.0)
        self.commands = commands
        self.initial_interaction = interaction
        self.public_id = public_id
        self.reason = reason
        self.actor_user_id = actor_user_id
        self.action_lock = asyncio.Lock()
        self.finished = False

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        actor = authorized_actor(
            interaction,
            configured_guild_id=self.commands._configured_guild_id,
            allowed_role_ids=self.commands._allowed_role_ids,
        )
        if actor is not None and actor.user_id == self.actor_user_id:
            return True
        await respond_ephemeral(
            interaction,
            PERMISSION_DENIED_MESSAGE,
            logger=self.commands._logger,
        )
        return False

    @discord.ui.button(
        label="削除する",
        style=discord.ButtonStyle.danger,
        custom_id="post_delete_confirm",
    )
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        unused: discord.ui.Button[ScheduleDeletionConfirmView],
    ) -> None:
        await self.commands._confirm_deletion(self, interaction)

    @discord.ui.button(
        label="キャンセル",
        style=discord.ButtonStyle.secondary,
        custom_id="post_delete_cancel",
    )
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        unused: discord.ui.Button[ScheduleDeletionConfirmView],
    ) -> None:
        await self.commands._cancel_deletion(self, interaction)

    async def on_timeout(self) -> None:
        await self.commands._expire_deletion(self)


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
        self._delete_views: set[ScheduleDeletionConfirmView] = set()

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

    @app_commands.command(name="create-daily", description="毎日の予約投稿を作成します")
    @app_commands.describe(
        channel="投稿先のテキストチャンネルです",
        local_time="日本時間をHH:MMで指定します",
        end_date="終了日をYYYY-MM-DDで指定します",
        content="投稿本文です。未指定の場合は下書きになります",
        allow_duplicate="重複候補があっても意図的に作成します",
    )
    async def create_daily_command(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        local_time: app_commands.Range[str, 5, 5],
        end_date: app_commands.Range[str, 10, 10] | None = None,
        content: app_commands.Range[str, 1, 2_000] | None = None,
        allow_duplicate: bool = False,
    ) -> None:
        await self._create_recurring_command(
            interaction,
            channel=channel,
            schedule_type=ScheduleType.DAILY,
            local_time_value=local_time,
            weekday=None,
            end_date_value=end_date,
            content=content,
            allow_duplicate=allow_duplicate,
        )

    @app_commands.command(name="create-weekly", description="毎週の予約投稿を作成します")
    @app_commands.describe(
        channel="投稿先のテキストチャンネルです",
        weekday="投稿する曜日です",
        local_time="日本時間をHH:MMで指定します",
        end_date="終了日をYYYY-MM-DDで指定します",
        content="投稿本文です。未指定の場合は下書きになります",
        allow_duplicate="重複候補があっても意図的に作成します",
    )
    @app_commands.choices(
        weekday=[
            app_commands.Choice(name=label, value=value)
            for value, label in enumerate(WEEKDAY_LABELS)
        ]
    )
    async def create_weekly_command(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        weekday: app_commands.Choice[int],
        local_time: app_commands.Range[str, 5, 5],
        end_date: app_commands.Range[str, 10, 10] | None = None,
        content: app_commands.Range[str, 1, 2_000] | None = None,
        allow_duplicate: bool = False,
    ) -> None:
        await self._create_recurring_command(
            interaction,
            channel=channel,
            schedule_type=ScheduleType.WEEKLY,
            local_time_value=local_time,
            weekday=weekday.value,
            end_date_value=end_date,
            content=content,
            allow_duplicate=allow_duplicate,
        )

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

    @app_commands.command(name="delete", description="予約を確認して論理削除します")
    @app_commands.describe(
        public_id="予約IDを指定します",
        reason="削除理由です。他の利用者の予約を管理者が削除する場合は必須です",
    )
    async def delete_command(
        self,
        interaction: discord.Interaction,
        public_id: str,
        reason: app_commands.Range[str, 1, 500] | None = None,
    ) -> None:
        actor = await self._actor_or_respond(interaction)
        if actor is None:
            return
        try:
            reason = validate_delete_reason(reason)
        except InvalidDeleteReasonError:
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        try:
            async with self._session_factory() as session:
                preview = await ScheduleDeletionService(session).preview(
                    guild_id=self._configured_guild_id,
                    public_id=public_id,
                    actor_user_id=actor.user_id,
                    administrator=actor.administrator,
                    reason=reason,
                )
        except DeleteReasonRequired:
            await respond_ephemeral(
                interaction, DELETE_REASON_REQUIRED_MESSAGE, logger=self._logger
            )
            return
        except ScheduleDeletionUnavailable:
            await respond_ephemeral(interaction, DELETE_UNAVAILABLE_MESSAGE, logger=self._logger)
            return
        except Exception:  # noqa: BLE001 - database details must remain private
            self._logger.error("schedule_delete_preview_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        try:
            embed = schedule_deletion_preview_embed(preview)
        except Exception:  # noqa: BLE001 - presentation failures must remain sanitized
            self._logger.error("schedule_presentation_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        view = ScheduleDeletionConfirmView(
            commands=self,
            interaction=interaction,
            public_id=public_id,
            reason=preview.reason,
            actor_user_id=actor.user_id,
        )
        if await respond_ephemeral(interaction, embed=embed, view=view, logger=self._logger):
            self._delete_views.add(view)

    async def _confirm_deletion(
        self, view: ScheduleDeletionConfirmView, interaction: discord.Interaction
    ) -> None:
        async with view.action_lock:
            if view.finished:
                await respond_ephemeral(
                    interaction, DELETE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            actor = await self._actor_or_respond(interaction)
            if actor is None or actor.user_id != view.actor_user_id:
                return
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:  # noqa: BLE001 - Discord response details must remain private
                self._logger.error("schedule_delete_defer_failed")
                return
            try:
                async with self._session_factory() as session, session.begin():
                    deleted = await ScheduleDeletionService(session).delete(
                        guild_id=self._configured_guild_id,
                        public_id=view.public_id,
                        actor_user_id=actor.user_id,
                        administrator=actor.administrator,
                        reason=view.reason,
                        deleted_at=self._clock.now(),
                    )
            except DeleteReasonRequired:
                await self._finish_delete_view(
                    view, interaction, content=DELETE_REASON_REQUIRED_MESSAGE
                )
                return
            except ScheduleDeletionUnavailable:
                await self._finish_delete_view(
                    view, interaction, content=DELETE_UNAVAILABLE_MESSAGE
                )
                return
            except Exception:  # noqa: BLE001 - database details must remain private
                self._logger.error("schedule_delete_failed")
                await self._finish_delete_view(view, interaction, content=INTERNAL_ERROR_MESSAGE)
                return
            try:
                embed = deleted_schedule_embed(deleted)
            except Exception:  # noqa: BLE001 - presentation failures must remain sanitized
                self._logger.error("schedule_presentation_failed")
                await self._finish_delete_view(view, interaction, content=INTERNAL_ERROR_MESSAGE)
                return
            await self._finish_delete_view(view, interaction, embed=embed)

    async def _cancel_deletion(
        self, view: ScheduleDeletionConfirmView, interaction: discord.Interaction
    ) -> None:
        async with view.action_lock:
            if view.finished:
                await respond_ephemeral(
                    interaction, DELETE_UNAVAILABLE_MESSAGE, logger=self._logger
                )
                return
            actor = await self._actor_or_respond(interaction)
            if actor is None or actor.user_id != view.actor_user_id:
                return
            view.finished = True
            view.stop()
            self._delete_views.discard(view)
            try:
                await interaction.response.edit_message(
                    content=DELETE_CANCELLED_MESSAGE,
                    embed=None,
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:  # noqa: BLE001 - Discord failures can include private details
                self._logger.error("schedule_delete_cancel_response_failed")

    async def _expire_deletion(self, view: ScheduleDeletionConfirmView) -> None:
        async with view.action_lock:
            if view.finished:
                return
            view.finished = True
            view.stop()
            self._delete_views.discard(view)
            try:
                await view.initial_interaction.edit_original_response(
                    content=DELETE_EXPIRED_MESSAGE,
                    embed=None,
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:  # noqa: BLE001 - Discord failures can include private details
                self._logger.error("schedule_delete_timeout_response_failed")

    async def _finish_delete_view(
        self,
        view: ScheduleDeletionConfirmView,
        interaction: discord.Interaction,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
    ) -> None:
        view.finished = True
        view.stop()
        self._delete_views.discard(view)
        arguments: dict[str, object] = {
            "view": None,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if embed is not None:
            arguments["embed"] = embed
            arguments["content"] = None
        else:
            arguments["content"] = content
            arguments["embed"] = None
        try:
            await interaction.edit_original_response(**arguments)
        except Exception:  # noqa: BLE001 - Discord failures can include private details
            self._logger.error("schedule_delete_result_response_failed")

    async def close_delete_views(self) -> None:
        views = tuple(self._delete_views)
        self._delete_views.clear()
        for view in views:
            view.finished = True
            view.stop()
        if views:
            await asyncio.gather(*(view.wait() for view in views), return_exceptions=True)

    async def _actor_or_respond(self, interaction: discord.Interaction) -> InteractionActor | None:
        actor = authorized_actor(
            interaction,
            configured_guild_id=self._configured_guild_id,
            allowed_role_ids=self._allowed_role_ids,
        )
        if actor is None:
            await respond_ephemeral(interaction, PERMISSION_DENIED_MESSAGE, logger=self._logger)
        return actor

    async def _create_recurring_command(
        self,
        interaction: discord.Interaction,
        *,
        channel: discord.TextChannel,
        schedule_type: ScheduleType,
        local_time_value: str,
        weekday: int | None,
        end_date_value: str | None,
        content: str | None,
        allow_duplicate: bool,
    ) -> None:
        actor = await self._actor_or_respond(interaction)
        if actor is None:
            return
        try:
            channel_id = self._validated_channel(interaction, channel)
            parsed_time: time = parse_local_time(local_time_value)
            parsed_end_date: date | None = parse_end_date(end_date_value)
            content = validate_create_content(content)
            now = self._clock.now()
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
                created = await RecurringScheduleCreationService(session).create(
                    guild_id=interaction.guild_id,
                    channel_id=channel_id,
                    creator_user_id=actor.user_id,
                    schedule_type=schedule_type,
                    local_time=parsed_time,
                    weekday=weekday,
                    end_date=parsed_end_date,
                    content=content,
                    allow_duplicate=allow_duplicate,
                    now=now,
                )
        except DuplicateScheduleWarning:
            await respond_ephemeral(interaction, DUPLICATE_WARNING_MESSAGE, logger=self._logger)
            return
        except InvalidDateTimeError:
            await respond_ephemeral(interaction, INVALID_INPUT_MESSAGE, logger=self._logger)
            return
        except Exception:  # noqa: BLE001 - database details must not reach Discord or logs
            self._logger.error("schedule_create_failed")
            await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=self._logger)
            return
        await self._respond_embed(interaction, lambda: created_recurring_schedule_embed(created))

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
