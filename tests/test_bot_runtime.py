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

from discord_ai_reminder_bot.application.gateway import MessageGateway
from discord_ai_reminder_bot.application.worker import PollResult
from discord_ai_reminder_bot.bot.client import (
    MAX_RATELIMIT_TIMEOUT_SECONDS,
    MAX_STARTUP_RECOVERY_BATCHES,
    ReminderBot,
    StartupRecoveryIncompleteError,
)
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
    return ReminderBot(
        settings=settings(),
        engine=engine,
        session_factory=sessions,
        clock=FixedClock(NOW),
        worker_id=uuid.uuid7(),
        logger=logging.getLogger("test.bot"),
    )


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
    assert isinstance(bot.gateway, MessageGateway)
    assert not bot.is_ready()


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


@pytest.mark.asyncio
async def test_on_ready_recovers_once_and_starts_one_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = make_bot()
    bot.recover_expired_processing = AsyncMock(return_value=3)  # type: ignore[method-assign]
    start = MagicMock()
    monkeypatch.setattr(bot.polling_loop, "start", start)
    monkeypatch.setattr(bot.polling_loop, "is_running", lambda: False)

    await bot.on_ready()
    await bot.on_ready()

    bot.recover_expired_processing.assert_awaited_once()  # type: ignore[attr-defined]
    start.assert_called_once()
    assert bot._recovery_complete.is_set()


@pytest.mark.asyncio
async def test_recovery_failure_never_starts_polling(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bot = make_bot()
    bot.recover_expired_processing = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError(DATABASE_URL)
    )
    start = MagicMock()
    monkeypatch.setattr(bot.polling_loop, "start", start)
    with caplog.at_level(logging.ERROR):
        await bot.on_ready()
    start.assert_not_called()
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

    class Recovery:
        def __init__(self, session):
            self.session = session

        async def recover_expired(self, **kwargs):
            return batches.pop(0)

    monkeypatch.setattr("discord_ai_reminder_bot.bot.client.ProcessingRecoveryService", Recovery)
    assert await bot.recover_expired_processing() == 3
    assert len(sessions) == 2
    assert all(session.exits == [None] for session in sessions)


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
        def __init__(self, unused_session):
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
        def __init__(self, unused_session):
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
        def __init__(self, unused_session):
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
        def __init__(self, unused_session):
            pass

        async def recover_expired(self, **kwargs):
            return [object()] * bot.settings.scheduler_batch_size

    monkeypatch.setattr("discord_ai_reminder_bot.bot.client.ProcessingRecoveryService", Recovery)
    with pytest.raises(StartupRecoveryIncompleteError) as captured:
        await bot.recover_expired_processing()
    assert captured.value.recovered_count == 50
    assert DATABASE_URL not in str(captured.value)


@pytest.mark.asyncio
async def test_close_cancels_tasks_closes_client_disposes_engine_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    cancel = MagicMock()
    monkeypatch.setattr(bot.polling_loop, "cancel", cancel)
    monkeypatch.setattr(bot.polling_loop, "get_task", lambda: None)
    client_close = AsyncMock()
    monkeypatch.setattr(commands.Bot, "close", client_close)

    await bot.close()
    await bot.close()

    cancel.assert_called_once()
    client_close.assert_awaited_once_with()
    bot.engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_collects_waiting_startup_recovery_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot()
    monkeypatch.setattr(bot.polling_loop, "cancel", MagicMock())
    monkeypatch.setattr(bot.polling_loop, "get_task", lambda: None)
    monkeypatch.setattr(commands.Bot, "close", AsyncMock())
    never_set = asyncio.Event()
    startup_task = asyncio.create_task(never_set.wait())
    bot._startup_task = startup_task

    await bot.close()

    assert startup_task.done()
    assert startup_task.cancelled()


def test_formatter_suppresses_exception_details() -> None:
    formatter = UtcEventFormatter("%(message)s %(worker_id)s")
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "safe_event", (), None)
    assert formatter.format(record) == "safe_event -"
    assert formatter.formatException((RuntimeError, RuntimeError(TOKEN), None)) == (
        "exception details suppressed"
    )
