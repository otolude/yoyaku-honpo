"""Discord Bot lifecycle and scheduler wiring."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.gateway import MessageGateway
from discord_ai_reminder_bot.application.recovery import ProcessingRecoveryService
from discord_ai_reminder_bot.application.schedule_queries import ScheduleQueryService
from discord_ai_reminder_bot.application.worker import PollingWorker
from discord_ai_reminder_bot.bot.interactions import Phase1CommandTree
from discord_ai_reminder_bot.bot.posts import PostCommands
from discord_ai_reminder_bot.config import Settings
from discord_ai_reminder_bot.domain.clock import Clock
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.infrastructure.database.schema import verify_schema_revision
from discord_ai_reminder_bot.infrastructure.discord.gateway import DiscordMessageGateway

MAX_RATELIMIT_TIMEOUT_SECONDS = 30.0
MAX_STARTUP_RECOVERY_BATCHES = 25
SLASH_ONLY_PREFIX = "__slash_commands_only__"


class StartupRecoveryIncompleteError(RuntimeError):
    """Recovery reached its safety limit and may have left expired work."""

    def __init__(self, recovered_count: int) -> None:
        super().__init__("Startup recovery reached its safe batch limit")
        self.recovered_count = recovered_count


def minimal_intents() -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.message_content = False
    intents.members = False
    intents.presences = False
    return intents


class ReminderBot(commands.Bot):
    def __init__(
        self,
        *,
        settings: Settings,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        worker_id: uuid.UUID,
        logger: logging.Logger,
    ) -> None:
        super().__init__(
            command_prefix=SLASH_ONLY_PREFIX,
            help_command=None,
            tree_cls=Phase1CommandTree,
            intents=minimal_intents(),
            member_cache_flags=discord.MemberCacheFlags.none(),
            allowed_mentions=discord.AllowedMentions.none(),
            max_ratelimit_timeout=MAX_RATELIMIT_TIMEOUT_SECONDS,
        )
        self.settings = settings
        self.engine = engine
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.logger = logger
        self.gateway: MessageGateway = DiscordMessageGateway(
            client=self,
            configured_guild_id=settings.discord_guild_id,
            clock=clock,
        )
        self.polling_worker = PollingWorker(
            session_factory=session_factory,
            gateway=self.gateway,
            clock=clock,
            worker_id=worker_id,
            batch_size=settings.scheduler_batch_size,
            max_concurrency=settings.scheduler_max_concurrency,
            lease_timeout=timedelta(seconds=settings.scheduler_processing_timeout_seconds),
            logger=logger,
        )
        self._clock = clock
        self._recovery_complete = asyncio.Event()
        self._startup_lock = asyncio.Lock()
        self._startup_attempted = False
        self._startup_task: asyncio.Task[None] | None = None
        self._closing = False
        self._closed_once = False
        self._command_sync_lock = asyncio.Lock()
        self._commands_synced = False
        self.post_commands = PostCommands(
            queries=ScheduleQueryService(session_factory),
            configured_guild_id=settings.discord_guild_id,
            allowed_role_ids=settings.discord_allowed_role_ids,
            logger=logger,
        )
        self.add_guild_command(self.post_commands)
        self.polling_loop.change_interval(seconds=settings.scheduler_poll_interval_seconds)

    async def setup_hook(self) -> None:
        revision = await verify_schema_revision(self.engine)
        self.logger.info(
            "database_schema_verified",
            extra={"worker_id": str(self.worker_id), "revision": revision},
        )
        await self.sync_guild_commands()

    def add_guild_command(
        self,
        command: app_commands.Command | app_commands.Group | app_commands.ContextMenu,
        *,
        override: bool = False,
    ) -> None:
        """Register a command only for the configured Phase 1 guild."""
        self.tree.add_command(
            command,
            guild=discord.Object(id=self.settings.discord_guild_id),
            override=override,
        )

    async def sync_guild_commands(self) -> int:
        """Synchronize the configured guild at most once per process."""
        async with self._command_sync_lock:
            if self._commands_synced:
                return 0
            synced = await self.tree.sync(guild=discord.Object(id=self.settings.discord_guild_id))
            self._commands_synced = True
            self.logger.info(
                "application_commands_synced",
                extra={"worker_id": str(self.worker_id), "command_count": len(synced)},
            )
            return len(synced)

    async def on_ready(self) -> None:
        async with self._startup_lock:
            if self._startup_attempted or self._closing:
                return
            self._startup_attempted = True
            self._startup_task = asyncio.create_task(
                self._recover_and_start(), name="discord-reminder-startup"
            )
        try:
            await self._startup_task
        except asyncio.CancelledError:
            raise
        except StartupRecoveryIncompleteError as error:
            self.logger.error(
                "startup_recovery_incomplete",
                extra={
                    "worker_id": str(self.worker_id),
                    "recovered": error.recovered_count,
                },
            )
        except Exception:  # noqa: BLE001 - Discord event dispatch must not expose DB details
            self.logger.error("startup_recovery_failed", extra={"worker_id": str(self.worker_id)})

    async def _recover_and_start(self) -> None:
        recovered = await self.recover_expired_processing()
        if self._closing:
            return
        self._recovery_complete.set()
        self.logger.info(
            "startup_recovery_complete",
            extra={"worker_id": str(self.worker_id), "recovered": recovered},
        )
        if not self.polling_loop.is_running():
            self.polling_loop.start()

    async def recover_expired_processing(self) -> int:
        total = 0
        for _ in range(MAX_STARTUP_RECOVERY_BATCHES):
            async with self.session_factory() as session, session.begin():
                recovered = await ProcessingRecoveryService(session).recover_expired(
                    recovered_at=require_utc(self._clock.now()),
                    batch_size=self.settings.scheduler_batch_size,
                )
            total += len(recovered)
            if len(recovered) < self.settings.scheduler_batch_size:
                return total
        raise StartupRecoveryIncompleteError(total)

    @tasks.loop(seconds=10.0, reconnect=False, name="database-polling-loop")
    async def polling_loop(self) -> None:
        if self._closing:
            return
        try:
            result = await self.polling_worker.poll_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one cycle must not terminate the loop
            self.logger.error("poll_cycle_failed", extra={"worker_id": str(self.worker_id)})
            return
        self.logger.info(
            "poll_cycle_complete",
            extra={"worker_id": str(self.worker_id), **vars(result)},
        )

    @polling_loop.before_loop
    async def before_polling_loop(self) -> None:
        await self.wait_until_ready()
        await self._recovery_complete.wait()
        if self._closing:
            self.polling_loop.stop()

    async def close(self) -> None:
        if self._closed_once:
            return
        self._closed_once = True
        self._closing = True

        self.polling_loop.cancel()
        polling_task = self.polling_loop.get_task()
        if polling_task is not None and polling_task is not asyncio.current_task():
            with contextlib.suppress(asyncio.CancelledError):
                await polling_task

        startup_task = self._startup_task
        if startup_task is not None and startup_task is not asyncio.current_task():
            if not startup_task.done():
                startup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await startup_task

        try:
            await super().close()
        finally:
            try:
                await self.engine.dispose()
            except Exception:  # noqa: BLE001 - never expose engine connection details
                self.logger.error(
                    "database_engine_dispose_failed",
                    extra={"worker_id": str(self.worker_id)},
                )
