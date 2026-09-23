"""Process entry point for ``python -m discord_ai_reminder_bot``."""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid

from discord_ai_reminder_bot.bot.client import ReminderBot
from discord_ai_reminder_bot.config import load_settings
from discord_ai_reminder_bot.domain.clock import SystemClock
from discord_ai_reminder_bot.infrastructure.ai.factory import build_name_generator
from discord_ai_reminder_bot.infrastructure.ai.openai_post_draft_generator import (
    ProductionOpenAIPostDraftRuntimeOwner,
)
from discord_ai_reminder_bot.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from discord_ai_reminder_bot.log_config import configure_logging
from discord_ai_reminder_bot.post_draft_config import load_post_draft_usage_settings
from discord_ai_reminder_bot.post_draft_provider_config import (
    OpenAIPostDraftProviderSettingsState,
    load_openai_post_draft_provider_settings,
)


async def _cleanup_startup_resources(
    *, provider_owner: ProductionOpenAIPostDraftRuntimeOwner | None, engine: object | None
) -> bool:
    """Best-effort cleanup which never exposes a startup or SDK failure detail."""
    completed = True
    if provider_owner is not None:
        try:
            completed = (await provider_owner.close()) and completed
        except BaseException:  # noqa: BLE001 - cleanup never replaces the primary failure
            completed = False
    dispose = getattr(engine, "dispose", None) if engine is not None else None
    if callable(dispose):
        try:
            pending = dispose()
            if inspect.isawaitable(pending):
                await pending
        except BaseException:  # noqa: BLE001 - cleanup never replaces the primary failure
            completed = False
    return completed


def _safe_log_fixed_error(
    logger: logging.Logger, marker: str, *, extra: dict[str, object] | None = None
) -> None:
    """A logger failure must never replace startup/run cleanup outcomes."""
    try:
        logger.error(marker, extra=extra)
    except BaseException:  # noqa: BLE001, S110 - reporting is never authoritative
        pass


def _cleanup_after_startup_failure(
    *,
    provider_owner: ProductionOpenAIPostDraftRuntimeOwner | None,
    engine: object | None,
    logger: logging.Logger,
) -> None:
    try:
        completed = asyncio.run(
            _cleanup_startup_resources(provider_owner=provider_owner, engine=engine)
        )
    except BaseException:  # noqa: BLE001 - cleanup failure is a fixed classification only
        completed = False
    if not completed:
        _safe_log_fixed_error(logger, "post_draft_provider_startup_cleanup_failed")


def _close_owned_bot_after_run_failure(*, bot: ReminderBot, logger: logging.Logger) -> None:
    """The Bot owns provider/engine cleanup after successful construction."""

    async def close_once() -> bool:
        try:
            pending = bot.close()
            if not inspect.isawaitable(pending):
                return False
            await pending
        except BaseException:  # noqa: BLE001 - primary run failure remains authoritative
            return False
        return True

    try:
        completed = asyncio.run(close_once())
    except BaseException:  # noqa: BLE001 - no cleanup detail may escape startup
        completed = False
    if not completed:
        _safe_log_fixed_error(logger, "post_draft_provider_startup_cleanup_failed")


def main() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger("discord_ai_reminder_bot")
    provider_settings = load_openai_post_draft_provider_settings()
    if provider_settings.state is OpenAIPostDraftProviderSettingsState.INVALID or (
        provider_settings.requested_enabled and not provider_settings.live_ready
    ):
        _safe_log_fixed_error(logger, "post_draft_provider_startup_blocked")
        return 1
    provider_owner: ProductionOpenAIPostDraftRuntimeOwner | None = None
    if provider_settings.live_ready:
        if provider_settings.settings is None:
            _safe_log_fixed_error(logger, "post_draft_provider_startup_blocked")
            return 1
        try:
            provider_owner = ProductionOpenAIPostDraftRuntimeOwner.create(
                provider_settings.settings
            )
        except Exception:  # noqa: BLE001 - client construction details are never logged
            _safe_log_fixed_error(logger, "post_draft_provider_startup_blocked")
            return 1
    engine: object | None = None
    bot: ReminderBot | None = None
    worker_id = uuid.uuid7()
    try:
        engine = create_database_engine(settings.database_url)
        bot = ReminderBot(
            settings=settings,
            engine=engine,
            session_factory=create_session_factory(engine),
            clock=SystemClock(),
            worker_id=worker_id,
            logger=logger,
            name_generator=build_name_generator(settings),
            post_draft_usage_settings=load_post_draft_usage_settings(),
            post_draft_provider_resource=provider_owner,
        )
        bot.run(
            settings.discord_bot_token.get_secret_value(),
            reconnect=True,
            log_handler=None,
        )
    except BaseException as error:
        if bot is None:
            _cleanup_after_startup_failure(
                provider_owner=provider_owner, engine=engine, logger=logger
            )
        else:
            _close_owned_bot_after_run_failure(bot=bot, logger=logger)
        if isinstance(error, asyncio.CancelledError | KeyboardInterrupt | SystemExit):
            raise
        _safe_log_fixed_error(logger, "bot_run_failed", extra={"worker_id": str(worker_id)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
