"""Discord Bot lifecycle and scheduler wiring."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import datetime, time, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.draft_notification_bootstrap import (
    DraftNotificationBootstrapService,
    DraftNotificationBootstrapSummary,
)
from discord_ai_reminder_bot.application.gateway import MessageGateway
from discord_ai_reminder_bot.application.name_generation import (
    DisabledNameGenerator,
    NameGenerationRegistrationPolicy,
    NameGenerator,
)
from discord_ai_reminder_bot.application.name_generation_maintenance import (
    NameGenerationRecoveryService,
)
from discord_ai_reminder_bot.application.name_generation_worker import NameGenerationWorker
from discord_ai_reminder_bot.application.notification_gateway import NotificationGateway
from discord_ai_reminder_bot.application.notification_recovery import (
    NotificationRecoveryService,
    NotificationRecoverySummary,
)
from discord_ai_reminder_bot.application.notification_worker import NotificationWorker
from discord_ai_reminder_bot.application.pending_recovery import (
    PendingRecoverySummary,
    PendingStartupRecoveryService,
)
from discord_ai_reminder_bot.application.recovery import ProcessingRecoveryService
from discord_ai_reminder_bot.application.schedule_queries import ScheduleQueryService
from discord_ai_reminder_bot.application.worker import PollingWorker
from discord_ai_reminder_bot.bot.interactions import Phase1CommandTree
from discord_ai_reminder_bot.bot.post_draft_runtime import (
    PostDraftRuntime,
    create_post_draft_runtime,
)
from discord_ai_reminder_bot.bot.posts import PostCommands
from discord_ai_reminder_bot.config import Settings
from discord_ai_reminder_bot.domain.clock import Clock
from discord_ai_reminder_bot.domain.recurrence import TOKYO, require_utc
from discord_ai_reminder_bot.infrastructure.database.schema import verify_schema_revision
from discord_ai_reminder_bot.infrastructure.discord.gateway import DiscordMessageGateway
from discord_ai_reminder_bot.infrastructure.discord.notification_gateway import (
    DiscordNotificationGateway,
)
from discord_ai_reminder_bot.post_draft_config import (
    PostDraftUsageSettingsResult,
    load_post_draft_usage_settings,
)

MAX_RATELIMIT_TIMEOUT_SECONDS = 30.0
MAX_STARTUP_RECOVERY_BATCHES = 25
SLASH_ONLY_PREFIX = "__slash_commands_only__"
MAINTENANCE_TIME = time(hour=4, tzinfo=TOKYO)


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
        name_generator: NameGenerator | None = None,
        post_draft_usage_settings: PostDraftUsageSettingsResult | None = None,
        post_draft_runtime: PostDraftRuntime | None = None,
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
        self.name_generator = name_generator or DisabledNameGenerator()
        self.post_draft_runtime = post_draft_runtime or create_post_draft_runtime(
            settings=post_draft_usage_settings or load_post_draft_usage_settings(env_file=None),
            session_factory=session_factory,
            clock=clock,
        )
        self.gateway: MessageGateway = DiscordMessageGateway(
            client=self,
            configured_guild_id=settings.discord_guild_id,
            clock=clock,
        )
        self.notification_gateway: NotificationGateway = DiscordNotificationGateway(
            client=self,
            configured_guild_id=settings.discord_guild_id,
            operator_channel_id=settings.discord_operator_channel_id,
            operator_user_id=settings.discord_operator_user_id,
            clock=clock,
            logger=logger,
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
            configured_guild_id=settings.discord_guild_id,
            operator_channel_id=settings.discord_operator_channel_id,
        )
        self.notification_worker = NotificationWorker(
            session_factory=session_factory,
            gateway=self.notification_gateway,
            clock=clock,
            worker_id=worker_id,
            configured_guild_id=settings.discord_guild_id,
            operator_channel_id=settings.discord_operator_channel_id,
            operator_user_id=settings.discord_operator_user_id,
            batch_size=settings.notification_batch_size,
            max_concurrency=settings.notification_max_concurrency,
            lease_timeout=timedelta(seconds=settings.notification_processing_timeout_seconds),
            logger=logger,
        )
        self.cleanup_service = CleanupService(
            session_factory=session_factory,
            clock=clock,
            name_generation_job_retention_days=settings.ai_name_generation_job_retention_days,
            name_generation_budget_retention_days=(
                settings.ai_name_generation_budget_retention_days
            ),
        )
        self.name_generation_worker = NameGenerationWorker(
            session_factory=session_factory,
            generator=self.name_generator,
            clock=clock,
            enabled=settings.ai_name_generation_enabled,
            budget_policy=settings.name_generation_budget_policy(),
            timeout_seconds=settings.ai_name_generation_timeout_seconds,
            processing_lease_seconds=settings.ai_name_generation_processing_lease_seconds,
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
        self._maintenance_lock = asyncio.Lock()
        self._commands_synced = False
        self.post_commands = PostCommands(
            queries=ScheduleQueryService(session_factory),
            session_factory=session_factory,
            clock=clock,
            configured_guild_id=settings.discord_guild_id,
            allowed_role_ids=settings.discord_allowed_role_ids,
            logger=logger,
            name_generation_policy=NameGenerationRegistrationPolicy(
                enabled=settings.ai_name_generation_enabled,
                generator_available=self.name_generator.available,
            ),
            post_draft_runtime=self.post_draft_runtime,
        )
        self.add_guild_command(self.post_commands)
        self.polling_loop.change_interval(seconds=settings.scheduler_poll_interval_seconds)
        self.notification_polling_loop.change_interval(
            seconds=settings.notification_poll_interval_seconds
        )
        self.name_generation_polling_loop.change_interval(
            seconds=settings.ai_name_generation_poll_interval_seconds
        )

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
        recovery_cutoff = require_utc(self._clock.now())
        recovered = await self.recover_expired_processing(recovery_cutoff=recovery_cutoff)
        name_generation_recovered = await self.recover_name_generation(recovery_cutoff)
        pending = await self.recover_overdue_pending(recovery_cutoff=recovery_cutoff)
        notification = await self.recover_expired_notifications(recovery_cutoff=recovery_cutoff)
        draft_bootstrap = await self.bootstrap_draft_notifications(recovery_cutoff=recovery_cutoff)
        if self._closing:
            return
        self._recovery_complete.set()
        self.logger.info(
            "startup_recovery_complete",
            extra={
                "worker_id": str(self.worker_id),
                "processing_recovered": recovered,
                "name_generation_abandoned": name_generation_recovered,
                "initial_pending_preserved": pending.initial_pending_preserved,
                "retry_pending_preserved": pending.retry_pending_preserved,
                "runs_skipped": pending.runs_skipped,
                "once_schedules_failed": pending.once_schedules_failed,
                "future_runs_created": pending.future_runs_created,
                "schedules_ended": pending.schedules_ended,
                "inconsistencies_detected": pending.inconsistencies_detected,
                "notifications_recovered": notification.selected,
                "draft_notifications_cancelled": draft_bootstrap.notifications_cancelled,
                "draft_notifications_planned": draft_bootstrap.notifications_planned,
            },
        )
        if not self.polling_loop.is_running():
            self.polling_loop.start()
        if not self.notification_polling_loop.is_running():
            self.notification_polling_loop.start()
        if not self.maintenance_loop.is_running():
            self.maintenance_loop.start()
        if (
            self.name_generation_worker.available
            and not self.name_generation_polling_loop.is_running()
        ):
            self.name_generation_polling_loop.start()

    async def recover_name_generation(self, recovery_cutoff: datetime) -> int:
        async with self.session_factory() as session, session.begin():
            recovered = await NameGenerationRecoveryService(session).abandon_expired(
                now=require_utc(recovery_cutoff)
            )
        self.logger.info(
            "startup_name_generation_recovery_complete",
            extra={"abandoned": recovered},
        )
        return recovered

    async def recover_expired_processing(self, *, recovery_cutoff: datetime | None = None) -> int:
        recovery_cutoff = require_utc(recovery_cutoff or self._clock.now())
        total = 0
        for _ in range(MAX_STARTUP_RECOVERY_BATCHES):
            async with self.session_factory() as session, session.begin():
                recovered = await ProcessingRecoveryService(
                    session,
                    configured_guild_id=self.settings.discord_guild_id,
                    operator_channel_id=self.settings.discord_operator_channel_id,
                ).recover_expired(
                    recovered_at=recovery_cutoff,
                    batch_size=self.settings.scheduler_batch_size,
                )
            total += len(recovered)
            if len(recovered) < self.settings.scheduler_batch_size:
                return total
        raise StartupRecoveryIncompleteError(total)

    async def recover_overdue_pending(self, *, recovery_cutoff: datetime) -> PendingRecoverySummary:
        recovery_cutoff = require_utc(recovery_cutoff)
        total = PendingRecoverySummary()
        batches = 0
        for _ in range(MAX_STARTUP_RECOVERY_BATCHES):
            async with self.session_factory() as session, session.begin():
                recovered = await PendingStartupRecoveryService(session).recover_pending(
                    recovery_cutoff=recovery_cutoff,
                    batch_size=self.settings.scheduler_batch_size,
                    configured_guild_id=self.settings.discord_guild_id,
                    operator_channel_id=self.settings.discord_operator_channel_id,
                )
            batches += 1
            total.add(recovered)
            if recovered.selected < self.settings.scheduler_batch_size:
                self.logger.info(
                    "startup_pending_recovery_complete",
                    extra={
                        "worker_id": str(self.worker_id),
                        "batches_completed": batches,
                        "initial_pending_preserved": total.initial_pending_preserved,
                        "retry_pending_preserved": total.retry_pending_preserved,
                        "runs_skipped": total.runs_skipped,
                        "once_schedules_failed": total.once_schedules_failed,
                        "future_runs_created": total.future_runs_created,
                        "schedules_ended": total.schedules_ended,
                        "inconsistencies_detected": total.inconsistencies_detected,
                    },
                )
                return total
        self.logger.error(
            "startup_pending_recovery_incomplete",
            extra={"worker_id": str(self.worker_id), "batches_completed": batches},
        )
        raise StartupRecoveryIncompleteError(total.selected)

    async def bootstrap_draft_notifications(
        self, *, recovery_cutoff: datetime
    ) -> DraftNotificationBootstrapSummary:
        recovery_cutoff = require_utc(recovery_cutoff)
        selected = cancelled = planned = 0
        for batch_index in range(MAX_STARTUP_RECOVERY_BATCHES):
            async with self.session_factory() as session, session.begin():
                result = await DraftNotificationBootstrapService(
                    session, configured_guild_id=self.settings.discord_guild_id
                ).bootstrap(
                    recovery_cutoff=recovery_cutoff,
                    batch_size=self.settings.notification_batch_size,
                )
            selected += result.selected
            cancelled += result.notifications_cancelled
            planned += result.notifications_planned
            if result.selected < self.settings.notification_batch_size:
                summary = DraftNotificationBootstrapSummary(selected, cancelled, planned)
                self.logger.info(
                    "startup_draft_notification_bootstrap_complete",
                    extra={
                        "batches_completed": batch_index + 1,
                        "notifications_cancelled": cancelled,
                        "notifications_planned": planned,
                    },
                )
                return summary
        self.logger.error(
            "startup_draft_notification_bootstrap_incomplete",
            extra={"batches_completed": MAX_STARTUP_RECOVERY_BATCHES},
        )
        raise StartupRecoveryIncompleteError(selected)

    async def recover_expired_notifications(
        self, *, recovery_cutoff: datetime
    ) -> NotificationRecoverySummary:
        recovery_cutoff = require_utc(recovery_cutoff)
        total = NotificationRecoverySummary()
        for _ in range(MAX_STARTUP_RECOVERY_BATCHES):
            async with self.session_factory() as session, session.begin():
                recovered = await NotificationRecoveryService(
                    session,
                    operator_channel_id=self.settings.discord_operator_channel_id,
                    operator_user_id=self.settings.discord_operator_user_id,
                ).recover_expired(
                    recovered_at=recovery_cutoff,
                    batch_size=self.settings.notification_batch_size,
                )
            total = total.add(recovered)
            if recovered.selected < self.settings.notification_batch_size:
                self.logger.info(
                    "startup_notification_recovery_complete",
                    extra={"batches_completed": _ + 1, "recovered": total.selected},
                )
                return total
        self.logger.error(
            "startup_notification_recovery_incomplete",
            extra={"batches_completed": MAX_STARTUP_RECOVERY_BATCHES},
        )
        raise StartupRecoveryIncompleteError(total.selected)

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

    @tasks.loop(seconds=10.0, reconnect=False, name="notification-polling-loop")
    async def notification_polling_loop(self) -> None:
        if self._closing:
            return
        try:
            result = await self.notification_worker.poll_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            self.logger.error("notification_poll_cycle_failed")
            return
        self.logger.info("notification_poll_cycle_complete", extra=vars(result))

    @notification_polling_loop.before_loop
    async def before_notification_polling_loop(self) -> None:
        await self.wait_until_ready()
        await self._recovery_complete.wait()
        if self._closing:
            self.notification_polling_loop.stop()

    @tasks.loop(time=MAINTENANCE_TIME, reconnect=False, name="maintenance-cleanup-loop")
    async def maintenance_loop(self) -> None:
        if self._closing:
            return
        async with self._maintenance_lock:
            try:
                result = await self.cleanup_service.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never expose database or target details
                self.logger.error("maintenance_cleanup_cycle_failed")
                return
        self.logger.info(
            "maintenance_cleanup_cycle_complete",
            extra={
                "cleanup_cutoff": result.cleanup_cutoff.isoformat(),
                "schedules_deleted": result.schedules_deleted,
                "global_notifications_deleted": result.global_notifications_deleted,
                "notification_attempts_deleted": result.notification_attempts_deleted,
                "notification_logs_deleted": result.notification_logs_deleted,
                "delivery_attempts_deleted": result.delivery_attempts_deleted,
                "operation_logs_deleted": result.operation_logs_deleted,
                "schedule_runs_deleted": result.schedule_runs_deleted,
                "name_generation_jobs_deleted": result.name_generation_jobs_deleted,
                "name_generation_budget_buckets_deleted": (
                    result.name_generation_budget_buckets_deleted
                ),
                "internal_errors": result.internal_errors,
                "schedules_remaining_due": result.schedules_remaining_due,
                "global_notifications_remaining_due": result.global_notifications_remaining_due,
                "incomplete": result.incomplete,
            },
        )

    @maintenance_loop.before_loop
    async def before_maintenance_loop(self) -> None:
        await self.wait_until_ready()
        await self._recovery_complete.wait()
        if self._closing:
            self.maintenance_loop.stop()

    @tasks.loop(seconds=5.0, reconnect=False, name="name-generation-polling-loop")
    async def name_generation_polling_loop(self) -> None:
        if self._closing:
            return
        try:
            result = await self.name_generation_worker.poll_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one cycle must not terminate the loop
            self.logger.error("name_generation_poll_cycle_failed")
            return
        self.logger.info(
            "name_generation_poll_cycle_complete",
            extra={
                "selected": result.selected,
                "generated": result.generated,
                "failed": result.failed,
                "internal_errors": result.internal_errors,
                "result_code": result.result_code,
            },
        )

    @name_generation_polling_loop.before_loop
    async def before_name_generation_polling_loop(self) -> None:
        await self.wait_until_ready()
        await self._recovery_complete.wait()
        if self._closing or not self.name_generation_worker.available:
            self.name_generation_polling_loop.stop()

    async def close(self) -> None:
        if self._closed_once:
            return
        self._closed_once = True
        self._closing = True

        self.name_generation_polling_loop.stop()
        self.name_generation_polling_loop.cancel()
        name_polling_task = self.name_generation_polling_loop.get_task()
        if name_polling_task is not None and name_polling_task is not asyncio.current_task():
            with contextlib.suppress(asyncio.CancelledError):
                await name_polling_task
        await self.name_generation_worker.shutdown()

        self.polling_loop.stop()
        self.notification_polling_loop.stop()
        self.maintenance_loop.stop()
        for loop in (self.polling_loop, self.notification_polling_loop, self.maintenance_loop):
            loop.cancel()
            polling_task = loop.get_task()
            if polling_task is not None and polling_task is not asyncio.current_task():
                with contextlib.suppress(asyncio.CancelledError):
                    await polling_task

        startup_task = self._startup_task
        if startup_task is not None and startup_task is not asyncio.current_task():
            if not startup_task.done():
                startup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await startup_task

        await self.post_commands.close_confirmation_views()

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


from discord_ai_reminder_bot.application.cleanup import CleanupService
