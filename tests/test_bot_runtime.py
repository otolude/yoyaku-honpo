from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands
from discord.ext import commands

from discord_ai_reminder_bot.application.cleanup import CleanupResult
from discord_ai_reminder_bot.application.draft_notification_bootstrap import (
    DraftNotificationBootstrapSummary,
)
from discord_ai_reminder_bot.application.gateway import MessageGateway
from discord_ai_reminder_bot.application.name_generation_worker import NameGenerationPollResult
from discord_ai_reminder_bot.application.notification_recovery import NotificationRecoverySummary
from discord_ai_reminder_bot.application.pending_recovery import PendingRecoverySummary
from discord_ai_reminder_bot.application.worker import PollResult
from discord_ai_reminder_bot.bot.client import (
    MAINTENANCE_TIME,
    MAX_RATELIMIT_TIMEOUT_SECONDS,
    MAX_STARTUP_RECOVERY_BATCHES,
    ReminderBot,
    StartupRecoveryIncompleteError,
)
from discord_ai_reminder_bot.bot.post_draft_runtime import PostDraftRuntime
from discord_ai_reminder_bot.config import Settings
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.log_config import UtcEventFormatter

NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
TOKEN = "test-token-never-connect"
DATABASE_URL = "postgresql+psycopg://user:test-password@localhost/database_test"


def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        TIMEZONE="Asia/Tokyo",
        DISCORD_BOT_TOKEN=TOKEN,
        DISCORD_GUILD_ID=100,
        DISCORD_ALLOWED_ROLE_IDS="200",
        DISCORD_OPERATOR_USER_ID=300,
        DISCORD_OPERATOR_CHANNEL_ID=400,
        DATABASE_URL=DATABASE_URL,
        SCHEDULER_POLL_INTERVAL_SECONDS=7,
        SCHEDULER_BATCH_SIZE=2,
        SCHEDULER_MAX_CONCURRENCY=1,
        SCHEDULER_PROCESSING_TIMEOUT_SECONDS=120,
    )


def make_bot() -> ReminderBot:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    sessions = MagicMock()
    bot = ReminderBot(
        settings=settings(),
        engine=engine,
        session_factory=sessions,
        clock=FixedClock(NOW),
        worker_id=uuid.uuid7(),
        logger=logging.getLogger("test.bot"),
        post_draft_runtime=MagicMock(spec=PostDraftRuntime),
    )
    bot.recover_expired_notifications = AsyncMock(  # type: ignore[method-assign]
        return_value=NotificationRecoverySummary()
    )
    bot.recover_name_generation = AsyncMock(return_value=0)  # type: ignore[method-assign]
    return bot


def test_bot_configuration_is_minimal_and_does_not_connect() -> None:
    bot = make_bot()
    assert isinstance(bot, commands.Bot)
    assert bot.intents.guilds
    assert not bot.intents.message_content
    assert not bot.intents.members
    assert not bot.intents.presences
    assert bot.intents.value == discord.Intents(guilds=True).value
    assert bot.help_command is None
    assert bot.allowed_mentions is not None
    assert bot.allowed_mentions.everyone is False
    assert bot.allowed_mentions.roles is False
    assert bot.allowed_mentions.users is False
    assert bot.allowed_mentions.replied_user is False
    assert bot.http.max_ratelimit_timeout == MAX_RATELIMIT_TIMEOUT_SECONDS == 30.0
    assert bot.polling_loop.seconds == 7.0
    assert bot.name_generation_polling_loop.seconds == 5.0
    assert bot.name_generation_worker.available is False
    assert isinstance(bot.gateway, MessageGateway)
    assert not bot.is_ready()


@pytest.mark.asyncio
async def test_name_generation_recovery_precedes_existing_recovery_and_disabled_starts_no_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    order: list[str] = []
    bot.recover_expired_processing = AsyncMock(
        side_effect=lambda **unused: order.append("run") or 0
    )  # type: ignore[method-assign]
    bot.recover_name_generation = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda unused: order.append("name") or 0
    )
    bot.recover_overdue_pending = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda **unused: order.append("pending") or PendingRecoverySummary()
    )
    bot.recover_expired_notifications = AsyncMock(  # type: ignore[method-assign]
        return_value=NotificationRecoverySummary()
    )
    bot.bootstrap_draft_notifications = AsyncMock(  # type: ignore[method-assign]
        return_value=DraftNotificationBootstrapSummary()
    )
    for loop in (bot.polling_loop, bot.notification_polling_loop, bot.maintenance_loop):
        monkeypatch.setattr(loop, "start", MagicMock())
        monkeypatch.setattr(loop, "is_running", lambda: False)
    name_start = MagicMock()
    monkeypatch.setattr(bot.name_generation_polling_loop, "start", name_start)
    monkeypatch.setattr(bot.name_generation_polling_loop, "is_running", lambda: False)
    await bot.on_ready()
    assert order[:3] == ["run", "name", "pending"]
    name_start.assert_not_called()


@pytest.mark.asyncio
async def test_available_name_generator_starts_poll_only_after_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    bot.name_generation_worker._enabled = True
    bot.name_generation_worker._generator = MagicMock(available=True)
    bot.recover_expired_processing = AsyncMock(return_value=0)  # type: ignore[method-assign]
    bot.recover_overdue_pending = AsyncMock(return_value=PendingRecoverySummary())  # type: ignore[method-assign]
    bot.bootstrap_draft_notifications = AsyncMock(  # type: ignore[method-assign]
        return_value=DraftNotificationBootstrapSummary()
    )
    for loop in (bot.polling_loop, bot.notification_polling_loop, bot.maintenance_loop):
        monkeypatch.setattr(loop, "start", MagicMock())
        monkeypatch.setattr(loop, "is_running", lambda: False)
    name_start = MagicMock()
    monkeypatch.setattr(bot.name_generation_polling_loop, "start", name_start)
    monkeypatch.setattr(bot.name_generation_polling_loop, "is_running", lambda: False)
    await bot.on_ready()
    assert bot._recovery_complete.is_set()
    name_start.assert_called_once()


@pytest.mark.asyncio
async def test_name_generation_cycle_logs_fixed_summary_and_survives_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bot = make_bot()
    bot.name_generation_worker.poll_once = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            RuntimeError("private-exception-canary"),
            NameGenerationPollResult(selected=1, failed=1, result_code="timeout"),
        ]
    )
    with caplog.at_level(logging.INFO):
        await bot.name_generation_polling_loop()
        await bot.name_generation_polling_loop()
    assert "name_generation_poll_cycle_failed" in caplog.messages
    assert "name_generation_poll_cycle_complete" in caplog.messages
    assert "private-exception-canary" not in caplog.text


@pytest.mark.asyncio
async def test_close_shuts_name_generation_once_before_engine_dispose() -> None:
    bot = make_bot()
    order: list[str] = []
    bot.name_generation_worker.shutdown = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda: order.append("name_shutdown")
    )
    bot.engine.dispose = AsyncMock(side_effect=lambda: order.append("dispose"))
    await bot.close()
    await bot.close()
    bot.name_generation_worker.shutdown.assert_awaited_once()  # type: ignore[attr-defined]
    assert order == ["name_shutdown", "dispose"]


@pytest.mark.asyncio
async def test_setup_hook_verifies_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = make_bot()
    verify = AsyncMock(return_value="bf82b90bcd5e")
    sync = AsyncMock(return_value=[])
    monkeypatch.setattr("discord_ai_reminder_bot.bot.client.verify_schema_revision", verify)
    monkeypatch.setattr(bot.tree, "sync", sync)
    await bot.setup_hook()
    verify.assert_awaited_once_with(bot.engine)
    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_setup_hook_syncs_configured_guild_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    verify = AsyncMock(return_value="bf82b90bcd5e")
    sync = AsyncMock(return_value=[object(), object()])
    monkeypatch.setattr("discord_ai_reminder_bot.bot.client.verify_schema_revision", verify)
    monkeypatch.setattr(bot.tree, "sync", sync)

    await bot.setup_hook()
    await bot.setup_hook()

    assert sync.await_count == 1
    guild = sync.await_args.kwargs["guild"]
    assert guild.id == bot.settings.discord_guild_id


@pytest.mark.asyncio
async def test_setup_hook_does_not_restore_dynamic_schedule_views(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    verify = AsyncMock(return_value="bf82b90bcd5e")
    sync = AsyncMock(return_value=[])
    add_view = MagicMock()
    monkeypatch.setattr("discord_ai_reminder_bot.bot.client.verify_schema_revision", verify)
    monkeypatch.setattr(bot.tree, "sync", sync)
    monkeypatch.setattr(bot, "add_view", add_view)

    await bot.setup_hook()

    add_view.assert_not_called()


@pytest.mark.asyncio
async def test_setup_hook_does_not_sync_when_schema_verification_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    verify = AsyncMock(side_effect=RuntimeError("schema mismatch"))
    sync = AsyncMock()
    monkeypatch.setattr("discord_ai_reminder_bot.bot.client.verify_schema_revision", verify)
    monkeypatch.setattr(bot.tree, "sync", sync)
    with pytest.raises(RuntimeError, match="schema mismatch"):
        await bot.setup_hook()
    sync.assert_not_awaited()


def test_add_guild_command_never_registers_globally() -> None:
    bot = make_bot()

    async def callback(interaction: discord.Interaction) -> None:
        pass

    command = app_commands.Command(name="probe", description="test command", callback=callback)
    bot.add_guild_command(command)
    guild = discord.Object(id=bot.settings.discord_guild_id)
    assert bot.tree.get_command("probe", guild=guild) is command
    assert bot.tree.get_command("probe") is None


def test_post_group_is_registered_only_for_configured_guild() -> None:
    bot = make_bot()
    guild = discord.Object(id=bot.settings.discord_guild_id)
    assert bot.tree.get_command("post", guild=guild) is bot.post_commands
    assert bot.tree.get_command("post") is None


def test_post_compose_is_registered_once_and_uses_runtime_instance() -> None:
    bot = make_bot()
    guild = discord.Object(id=bot.settings.discord_guild_id)
    post = bot.tree.get_command("post", guild=guild)
    assert post is bot.post_commands
    compose = post.get_command("compose")
    assert compose is not None
    assert compose.description == "投稿する文章を作成します"
    assert sum(command.name == "compose" for command in post.commands) == 1
    assert bot.post_commands._post_draft_runtime is bot.post_draft_runtime


def test_bot_constructs_post_draft_runtime_once(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = MagicMock(spec=PostDraftRuntime)
    create_runtime = MagicMock(return_value=runtime)
    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.client.create_post_draft_runtime", create_runtime
    )
    engine = MagicMock()
    ReminderBot(
        settings=settings(),
        engine=engine,
        session_factory=MagicMock(),
        clock=FixedClock(NOW),
        worker_id=uuid.uuid7(),
        logger=logging.getLogger("test.bot.post-draft"),
    )
    create_runtime.assert_called_once()
    engine.assert_not_called()


@pytest.mark.asyncio
async def test_on_ready_recovers_once_and_starts_one_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = make_bot()
    bot.recover_expired_processing = AsyncMock(return_value=3)  # type: ignore[method-assign]
    bot.recover_overdue_pending = AsyncMock(  # type: ignore[method-assign]
        return_value=PendingRecoverySummary()
    )
    bot.recover_expired_notifications = AsyncMock(  # type: ignore[method-assign]
        return_value=NotificationRecoverySummary()
    )
    bot.bootstrap_draft_notifications = AsyncMock(  # type: ignore[method-assign]
        return_value=DraftNotificationBootstrapSummary()
    )
    start = MagicMock()
    monkeypatch.setattr(bot.polling_loop, "start", start)
    monkeypatch.setattr(bot.polling_loop, "is_running", lambda: False)
    notification_start = MagicMock()
    monkeypatch.setattr(bot.notification_polling_loop, "start", notification_start)
    monkeypatch.setattr(bot.notification_polling_loop, "is_running", lambda: False)
    maintenance_start = MagicMock()
    monkeypatch.setattr(bot.maintenance_loop, "start", maintenance_start)
    monkeypatch.setattr(bot.maintenance_loop, "is_running", lambda: False)

    await bot.on_ready()
    await bot.on_ready()

    bot.recover_expired_processing.assert_awaited_once()  # type: ignore[attr-defined]
    bot.recover_overdue_pending.assert_awaited_once()  # type: ignore[attr-defined]
    start.assert_called_once()
    notification_start.assert_called_once()
    maintenance_start.assert_called_once()
    assert bot._recovery_complete.is_set()


@pytest.mark.asyncio
async def test_concurrent_and_repeated_on_ready_share_one_startup_and_three_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def processing(**kwargs):
        entered.set()
        await release.wait()
        return 0

    bot.recover_expired_processing = AsyncMock(side_effect=processing)  # type: ignore[method-assign]
    bot.recover_overdue_pending = AsyncMock(  # type: ignore[method-assign]
        return_value=PendingRecoverySummary()
    )
    bot.recover_expired_notifications = AsyncMock(  # type: ignore[method-assign]
        return_value=NotificationRecoverySummary()
    )
    bot.bootstrap_draft_notifications = AsyncMock(  # type: ignore[method-assign]
        return_value=DraftNotificationBootstrapSummary()
    )
    starts = [MagicMock(), MagicMock(), MagicMock()]
    for loop, start in zip(
        (bot.polling_loop, bot.notification_polling_loop, bot.maintenance_loop),
        starts,
        strict=True,
    ):
        monkeypatch.setattr(loop, "start", start)
        monkeypatch.setattr(loop, "is_running", lambda: False)

    ready_tasks = [asyncio.create_task(bot.on_ready()) for _ in range(3)]
    await entered.wait()
    startup_task = bot._startup_task
    assert startup_task is not None and not startup_task.done()
    assert sum(task is startup_task for task in asyncio.all_tasks()) == 1
    release.set()
    await asyncio.gather(*ready_tasks)
    await bot.on_ready()

    bot.recover_expired_processing.assert_awaited_once()  # type: ignore[attr-defined]
    bot.recover_overdue_pending.assert_awaited_once()  # type: ignore[attr-defined]
    bot.recover_expired_notifications.assert_awaited_once()  # type: ignore[attr-defined]
    bot.bootstrap_draft_notifications.assert_awaited_once()  # type: ignore[attr-defined]
    assert bot._startup_task is startup_task and startup_task.done()
    assert all(start.call_count == 1 for start in starts)


@pytest.mark.asyncio
async def test_startup_recoveries_share_one_fixed_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    processing = AsyncMock(return_value=0)
    pending = AsyncMock(return_value=PendingRecoverySummary())
    bot.recover_expired_processing = processing  # type: ignore[method-assign]
    bot.recover_overdue_pending = pending  # type: ignore[method-assign]
    notification = AsyncMock(return_value=NotificationRecoverySummary())
    bootstrap = AsyncMock(return_value=DraftNotificationBootstrapSummary())
    bot.recover_expired_notifications = notification  # type: ignore[method-assign]
    bot.bootstrap_draft_notifications = bootstrap  # type: ignore[method-assign]
    monkeypatch.setattr(bot.polling_loop, "start", MagicMock())
    monkeypatch.setattr(bot.polling_loop, "is_running", lambda: False)
    monkeypatch.setattr(bot.notification_polling_loop, "start", MagicMock())
    monkeypatch.setattr(bot.notification_polling_loop, "is_running", lambda: False)
    monkeypatch.setattr(bot.maintenance_loop, "start", MagicMock())
    monkeypatch.setattr(bot.maintenance_loop, "is_running", lambda: False)

    await bot.on_ready()

    assert processing.await_args.kwargs["recovery_cutoff"] == NOW
    assert pending.await_args.kwargs["recovery_cutoff"] == NOW
    assert notification.await_args.kwargs["recovery_cutoff"] == NOW
    assert bootstrap.await_args.kwargs["recovery_cutoff"] == NOW


@pytest.mark.asyncio
async def test_startup_recovery_order_and_both_loops_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    order: list[str] = []

    async def processing(**kwargs):
        order.append("processing")
        return 0

    async def pending(**kwargs):
        order.append("pending")
        return PendingRecoverySummary()

    async def notification(**kwargs):
        order.append("notification")
        return NotificationRecoverySummary()

    async def bootstrap(**kwargs):
        order.append("bootstrap")
        return DraftNotificationBootstrapSummary()

    bot.recover_expired_processing = processing  # type: ignore[method-assign]
    bot.recover_overdue_pending = pending  # type: ignore[method-assign]
    bot.recover_expired_notifications = notification  # type: ignore[method-assign]
    bot.bootstrap_draft_notifications = bootstrap  # type: ignore[method-assign]
    poll_start = MagicMock(side_effect=lambda: order.append("schedule_loop"))
    notification_start = MagicMock(side_effect=lambda: order.append("notification_loop"))
    maintenance_start = MagicMock(side_effect=lambda: order.append("maintenance_loop"))
    monkeypatch.setattr(bot.polling_loop, "start", poll_start)
    monkeypatch.setattr(bot.polling_loop, "is_running", lambda: False)
    monkeypatch.setattr(bot.notification_polling_loop, "start", notification_start)
    monkeypatch.setattr(bot.notification_polling_loop, "is_running", lambda: False)
    monkeypatch.setattr(bot.maintenance_loop, "start", maintenance_start)
    monkeypatch.setattr(bot.maintenance_loop, "is_running", lambda: False)

    await bot.on_ready()

    assert order == [
        "processing",
        "pending",
        "notification",
        "bootstrap",
        "schedule_loop",
        "notification_loop",
        "maintenance_loop",
    ]
    assert bot._recovery_complete.is_set()


@pytest.mark.asyncio
async def test_notification_recovery_failure_starts_neither_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    bot.recover_expired_processing = AsyncMock(return_value=0)  # type: ignore[method-assign]
    bot.recover_overdue_pending = AsyncMock(  # type: ignore[method-assign]
        return_value=PendingRecoverySummary()
    )
    bot.recover_expired_notifications = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("safe notification recovery failure")
    )
    schedule_start = MagicMock()
    notification_start = MagicMock()
    maintenance_start = MagicMock()
    monkeypatch.setattr(bot.polling_loop, "start", schedule_start)
    monkeypatch.setattr(bot.notification_polling_loop, "start", notification_start)
    monkeypatch.setattr(bot.maintenance_loop, "start", maintenance_start)

    await bot.on_ready()

    schedule_start.assert_not_called()
    notification_start.assert_not_called()
    maintenance_start.assert_not_called()
    assert not bot._recovery_complete.is_set()


@pytest.mark.asyncio
async def test_pending_recovery_failure_never_starts_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    bot.recover_expired_processing = AsyncMock(return_value=0)  # type: ignore[method-assign]
    bot.recover_overdue_pending = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("safe pending failure")
    )
    start = MagicMock()
    monkeypatch.setattr(bot.polling_loop, "start", start)

    await bot.on_ready()

    start.assert_not_called()
    assert not bot._recovery_complete.is_set()


@pytest.mark.asyncio
async def test_recovery_failure_never_starts_polling(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bot = make_bot()
    bot.recover_expired_processing = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError(DATABASE_URL)
    )
    bot.recover_overdue_pending = AsyncMock()  # type: ignore[method-assign]
    start = MagicMock()
    monkeypatch.setattr(bot.polling_loop, "start", start)
    with caplog.at_level(logging.ERROR):
        await bot.on_ready()
    start.assert_not_called()
    bot.recover_overdue_pending.assert_not_awaited()  # type: ignore[attr-defined]
    assert not bot._recovery_complete.is_set()
    assert DATABASE_URL not in caplog.text
    assert "test-password" not in caplog.text


@pytest.mark.asyncio
async def test_poll_cycle_logs_counts_and_survives_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bot = make_bot()
    bot.polling_worker.poll_once = AsyncMock(  # type: ignore[method-assign]
        return_value=PollResult(claimed=2, succeeded=1, failed=1)
    )
    with caplog.at_level(logging.INFO):
        await bot.polling_loop()
    assert "poll_cycle_complete" in caplog.text
    bot.polling_worker.poll_once = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError(DATABASE_URL)
    )
    with caplog.at_level(logging.ERROR):
        await bot.polling_loop()
    assert "poll_cycle_failed" in caplog.text
    assert DATABASE_URL not in caplog.text


@pytest.mark.asyncio
async def test_poll_cycle_propagates_cancellation() -> None:
    bot = make_bot()
    bot.polling_worker.poll_once = AsyncMock(  # type: ignore[method-assign]
        side_effect=asyncio.CancelledError
    )
    with pytest.raises(asyncio.CancelledError):
        await bot.polling_loop()


def test_maintenance_loop_runs_daily_at_timezone_aware_tokyo_0400() -> None:
    bot = make_bot()
    assert MAINTENANCE_TIME.hour == 4
    assert MAINTENANCE_TIME.minute == 0
    assert str(MAINTENANCE_TIME.tzinfo) == "Asia/Tokyo"
    assert bot.maintenance_loop.time == [MAINTENANCE_TIME]


@pytest.mark.asyncio
async def test_maintenance_cycle_logs_only_safe_summary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bot = make_bot()
    bot.cleanup_service.run_cycle = AsyncMock(  # type: ignore[method-assign]
        return_value=CleanupResult(
            cleanup_cutoff=NOW,
            schedules_deleted=1,
            global_notifications_deleted=2,
            incomplete=True,
        )
    )
    with caplog.at_level(logging.INFO):
        await bot.maintenance_loop()
    assert "maintenance_cleanup_cycle_complete" in caplog.text
    assert "test-password" not in caplog.text


@pytest.mark.asyncio
async def test_maintenance_failure_does_not_stop_other_loops(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bot = make_bot()
    bot.cleanup_service.run_cycle = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError(DATABASE_URL)
    )
    with caplog.at_level(logging.ERROR):
        await bot.maintenance_loop()
    assert "maintenance_cleanup_cycle_failed" in caplog.text
    assert DATABASE_URL not in caplog.text
    assert not bot.polling_loop.is_being_cancelled()
    assert not bot.notification_polling_loop.is_being_cancelled()


@pytest.mark.asyncio
async def test_maintenance_cycle_propagates_cancellation() -> None:
    bot = make_bot()
    bot.cleanup_service.run_cycle = AsyncMock(  # type: ignore[method-assign]
        side_effect=asyncio.CancelledError
    )
    with pytest.raises(asyncio.CancelledError):
        await bot.maintenance_loop()


@pytest.mark.asyncio
async def test_cleanup_failure_keeps_all_loops_available_for_later_cycles(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bot = make_bot()
    unsafe = (
        DATABASE_URL,
        "complete exception details",
        "Traceback (most recent call last)",
    )
    bot.cleanup_service.run_cycle = AsyncMock(  # type: ignore[method-assign]
        side_effect=[RuntimeError(" | ".join(unsafe)), CleanupResult(cleanup_cutoff=NOW)]
    )
    bot.polling_worker.poll_once = AsyncMock(return_value=PollResult(succeeded=1))  # type: ignore[method-assign]
    bot.notification_worker.poll_once = AsyncMock()  # type: ignore[method-assign]
    stops = [MagicMock(), MagicMock(), MagicMock()]
    cancels = [MagicMock(), MagicMock(), MagicMock()]
    for loop, stop, cancel in zip(
        (bot.polling_loop, bot.notification_polling_loop, bot.maintenance_loop),
        stops,
        cancels,
        strict=True,
    ):
        monkeypatch.setattr(loop, "stop", stop)
        monkeypatch.setattr(loop, "cancel", cancel)

    with caplog.at_level(logging.ERROR):
        await bot.maintenance_loop()
        await bot.polling_loop()
        await bot.notification_polling_loop()
        await bot.maintenance_loop()

    assert bot.cleanup_service.run_cycle.await_count == 2  # type: ignore[attr-defined]
    bot.polling_worker.poll_once.assert_awaited_once()  # type: ignore[attr-defined]
    bot.notification_worker.poll_once.assert_awaited_once()  # type: ignore[attr-defined]
    assert all(stop.call_count == 0 for stop in stops)
    assert all(cancel.call_count == 0 for cancel in cancels)
    assert caplog.messages.count("maintenance_cleanup_cycle_failed") == 1
    assert all(value not in caplog.text for value in unsafe)


@pytest.mark.asyncio
async def test_ready_starts_scheduled_maintenance_without_running_cleanup_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    bot.recover_expired_processing = AsyncMock(return_value=0)  # type: ignore[method-assign]
    bot.recover_overdue_pending = AsyncMock(  # type: ignore[method-assign]
        return_value=PendingRecoverySummary()
    )
    bot.bootstrap_draft_notifications = AsyncMock(  # type: ignore[method-assign]
        return_value=DraftNotificationBootstrapSummary()
    )
    monkeypatch.setattr(bot.polling_loop, "start", MagicMock())
    monkeypatch.setattr(bot.polling_loop, "is_running", lambda: False)
    monkeypatch.setattr(bot.notification_polling_loop, "start", MagicMock())
    monkeypatch.setattr(bot.notification_polling_loop, "is_running", lambda: False)
    maintenance_start = MagicMock()
    monkeypatch.setattr(bot.maintenance_loop, "start", maintenance_start)
    monkeypatch.setattr(bot.maintenance_loop, "is_running", lambda: False)
    bot.cleanup_service.run_cycle = AsyncMock()  # type: ignore[method-assign]

    await bot.on_ready()

    maintenance_start.assert_called_once()
    bot.cleanup_service.run_cycle.assert_not_awaited()  # type: ignore[attr-defined]


class Transaction(AbstractAsyncContextManager):
    def __init__(self, owner: Session) -> None:
        self.owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.owner.exits.append(exc_type)


class Session(AbstractAsyncContextManager):
    def __init__(self) -> None:
        self.exits: list[type[BaseException] | None] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    def begin(self) -> Transaction:
        return Transaction(self)


@pytest.mark.asyncio
async def test_recovery_uses_multiple_short_committed_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    sessions: list[Session] = []
    bot.session_factory = lambda: sessions.append(Session()) or sessions[-1]  # type: ignore[assignment]
    batches = [[object(), object()], [object()]]
    calls: list[dict[str, object]] = []

    class Recovery:
        def __init__(self, session, **kwargs):
            self.session = session

        async def recover_expired(self, **kwargs):
            calls.append(kwargs)
            return batches.pop(0)

    monkeypatch.setattr("discord_ai_reminder_bot.bot.client.ProcessingRecoveryService", Recovery)
    assert await bot.recover_expired_processing() == 3
    assert len(sessions) == 2
    assert all(session.exits == [None] for session in sessions)
    assert [call["recovered_at"] for call in calls] == [NOW, NOW]
    assert all(call["batch_size"] == bot.settings.scheduler_batch_size for call in calls)


@pytest.mark.parametrize("batches", [[], [[object()]]])
@pytest.mark.asyncio
async def test_recovery_succeeds_for_empty_or_partial_first_batch(
    batches: list[list[object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = make_bot()
    session = Session()
    bot.session_factory = lambda: session  # type: ignore[assignment]
    responses = list(batches) or [[]]

    class Recovery:
        def __init__(self, unused_session, **kwargs):
            pass

        async def recover_expired(self, **kwargs):
            return responses.pop(0)

    monkeypatch.setattr("discord_ai_reminder_bot.bot.client.ProcessingRecoveryService", Recovery)
    expected = len(batches[0]) if batches else 0
    assert await bot.recover_expired_processing() == expected
    assert session.exits == [None]


@pytest.mark.asyncio
async def test_full_twenty_fifth_batch_is_incomplete_and_does_not_start_polling(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bot = make_bot()
    sessions: list[Session] = []
    bot.session_factory = lambda: sessions.append(Session()) or sessions[-1]  # type: ignore[assignment]

    class Recovery:
        def __init__(self, unused_session, **kwargs):
            pass

        async def recover_expired(self, **kwargs):
            return [object()] * bot.settings.scheduler_batch_size

    monkeypatch.setattr("discord_ai_reminder_bot.bot.client.ProcessingRecoveryService", Recovery)
    start = MagicMock()
    monkeypatch.setattr(bot.polling_loop, "start", start)
    bot.polling_worker.poll_once = AsyncMock()  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR):
        await bot.on_ready()
        await bot.on_ready()

    assert len(sessions) == MAX_STARTUP_RECOVERY_BATCHES
    assert all(session.exits == [None] for session in sessions)
    assert not bot._recovery_complete.is_set()
    start.assert_not_called()
    bot.polling_worker.poll_once.assert_not_awaited()  # type: ignore[attr-defined]
    assert "startup_recovery_incomplete" in caplog.text
    incomplete = next(
        record for record in caplog.records if record.message == "startup_recovery_incomplete"
    )
    assert incomplete.recovered == 50  # type: ignore[attr-defined]
    assert DATABASE_URL not in caplog.text
    assert "test-password" not in caplog.text


@pytest.mark.asyncio
async def test_recovery_failure_rolls_back_current_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = make_bot()
    session = Session()
    bot.session_factory = lambda: session  # type: ignore[assignment]

    class Recovery:
        def __init__(self, unused_session, **kwargs):
            pass

        async def recover_expired(self, **kwargs):
            raise RuntimeError("safe recovery failure")

    monkeypatch.setattr("discord_ai_reminder_bot.bot.client.ProcessingRecoveryService", Recovery)
    with pytest.raises(RuntimeError, match="safe recovery failure"):
        await bot.recover_expired_processing()
    assert session.exits == [RuntimeError]


@pytest.mark.asyncio
async def test_recovery_limit_exception_carries_only_safe_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    bot.session_factory = lambda: Session()  # type: ignore[assignment]

    class Recovery:
        def __init__(self, unused_session, **kwargs):
            pass

        async def recover_expired(self, **kwargs):
            return [object()] * bot.settings.scheduler_batch_size

    monkeypatch.setattr("discord_ai_reminder_bot.bot.client.ProcessingRecoveryService", Recovery)
    with pytest.raises(StartupRecoveryIncompleteError) as captured:
        await bot.recover_expired_processing()
    assert captured.value.recovered_count == 50
    assert DATABASE_URL not in str(captured.value)


@pytest.mark.asyncio
async def test_full_twenty_fifth_pending_batch_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    sessions: list[Session] = []
    bot.session_factory = lambda: sessions.append(Session()) or sessions[-1]  # type: ignore[assignment]

    class PendingRecovery:
        def __init__(self, unused_session):
            pass

        async def recover_pending(self, **kwargs):
            return PendingRecoverySummary(selected=bot.settings.scheduler_batch_size)

    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.client.PendingStartupRecoveryService", PendingRecovery
    )
    with pytest.raises(StartupRecoveryIncompleteError) as captured:
        await bot.recover_overdue_pending(recovery_cutoff=NOW)
    assert captured.value.recovered_count == 50
    assert len(sessions) == MAX_STARTUP_RECOVERY_BATCHES
    assert all(session.exits == [None] for session in sessions)


@pytest.mark.asyncio
async def test_full_twenty_fifth_notification_batch_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    sessions: list[Session] = []
    bot.session_factory = lambda: sessions.append(Session()) or sessions[-1]  # type: ignore[assignment]

    class NotificationRecovery:
        def __init__(self, unused_session, **kwargs):
            pass

        async def recover_expired(self, **kwargs):
            return NotificationRecoverySummary(selected=bot.settings.notification_batch_size)

    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.client.NotificationRecoveryService",
        NotificationRecovery,
    )
    with pytest.raises(StartupRecoveryIncompleteError) as captured:
        await ReminderBot.recover_expired_notifications(bot, recovery_cutoff=NOW)
    assert captured.value.recovered_count == 500
    assert len(sessions) == MAX_STARTUP_RECOVERY_BATCHES
    assert all(session.exits == [None] for session in sessions)


@pytest.mark.asyncio
async def test_full_twenty_fifth_draft_bootstrap_batch_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    sessions: list[Session] = []
    bot.session_factory = lambda: sessions.append(Session()) or sessions[-1]  # type: ignore[assignment]

    class Bootstrap:
        def __init__(self, unused_session, **kwargs):
            pass

        async def bootstrap(self, **kwargs):
            return DraftNotificationBootstrapSummary(selected=bot.settings.notification_batch_size)

    monkeypatch.setattr(
        "discord_ai_reminder_bot.bot.client.DraftNotificationBootstrapService", Bootstrap
    )
    with pytest.raises(StartupRecoveryIncompleteError) as captured:
        await bot.bootstrap_draft_notifications(recovery_cutoff=NOW)
    assert captured.value.recovered_count == 500
    assert len(sessions) == MAX_STARTUP_RECOVERY_BATCHES
    assert all(session.exits == [None] for session in sessions)


@pytest.mark.asyncio
async def test_close_cancels_tasks_closes_client_disposes_engine_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    cancel = MagicMock()
    monkeypatch.setattr(bot.polling_loop, "cancel", cancel)
    monkeypatch.setattr(bot.polling_loop, "get_task", lambda: None)
    maintenance_cancel = MagicMock()
    monkeypatch.setattr(bot.maintenance_loop, "cancel", maintenance_cancel)
    monkeypatch.setattr(bot.maintenance_loop, "get_task", lambda: None)
    client_close = AsyncMock()
    monkeypatch.setattr(commands.Bot, "close", client_close)
    close_views = AsyncMock()
    bot.post_commands.close_delete_views = close_views

    await bot.close()
    await bot.close()

    cancel.assert_called_once()
    maintenance_cancel.assert_called_once()
    client_close.assert_awaited_once_with()
    close_views.assert_awaited_once_with()
    bot.engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_collects_waiting_startup_recovery_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    monkeypatch.setattr(bot.polling_loop, "cancel", MagicMock())
    monkeypatch.setattr(bot.polling_loop, "get_task", lambda: None)
    monkeypatch.setattr(bot.maintenance_loop, "cancel", MagicMock())
    monkeypatch.setattr(bot.maintenance_loop, "get_task", lambda: None)
    monkeypatch.setattr(commands.Bot, "close", AsyncMock())
    never_set = asyncio.Event()
    startup_task = asyncio.create_task(never_set.wait())
    bot._startup_task = startup_task

    await bot.close()

    assert startup_task.done()
    assert startup_task.cancelled()


@pytest.mark.asyncio
async def test_close_collects_all_three_loop_tasks_and_startup_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    loops = (bot.polling_loop, bot.notification_polling_loop, bot.maintenance_loop)
    loop_tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in loops]
    startup_task = asyncio.create_task(asyncio.Event().wait())
    bot._startup_task = startup_task
    stops = [MagicMock() for _ in loops]
    cancels = []
    for loop, task, stop in zip(loops, loop_tasks, stops, strict=True):
        cancel = MagicMock(side_effect=task.cancel)
        cancels.append(cancel)
        monkeypatch.setattr(loop, "stop", stop)
        monkeypatch.setattr(loop, "cancel", cancel)
        monkeypatch.setattr(loop, "get_task", lambda task=task: task)
    monkeypatch.setattr(commands.Bot, "close", AsyncMock())
    bot.post_commands.close_confirmation_views = AsyncMock()  # type: ignore[method-assign]

    await bot.close()
    await bot.close()

    assert all(task.done() and task.cancelled() for task in loop_tasks)
    assert startup_task.done() and startup_task.cancelled()
    assert all(stop.call_count == 1 for stop in stops)
    assert all(cancel.call_count == 1 for cancel in cancels)


def test_formatter_suppresses_exception_details() -> None:
    formatter = UtcEventFormatter("%(message)s %(worker_id)s")
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "safe_event", (), None)
    assert formatter.format(record) == "safe_event -"
    assert formatter.formatException((RuntimeError, RuntimeError(TOKEN), None)) == (
        "exception details suppressed"
    )
