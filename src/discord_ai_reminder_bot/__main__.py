"""Process entry point for ``python -m discord_ai_reminder_bot``."""

from __future__ import annotations

import logging
import uuid

from discord_ai_reminder_bot.bot.client import ReminderBot
from discord_ai_reminder_bot.config import load_settings
from discord_ai_reminder_bot.domain.clock import SystemClock
from discord_ai_reminder_bot.infrastructure.ai.factory import build_name_generator
from discord_ai_reminder_bot.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from discord_ai_reminder_bot.log_config import configure_logging


def main() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger("discord_ai_reminder_bot")
    engine = create_database_engine(settings.database_url)
    bot = ReminderBot(
        settings=settings,
        engine=engine,
        session_factory=create_session_factory(engine),
        clock=SystemClock(),
        worker_id=uuid.uuid7(),
        logger=logger,
        name_generator=build_name_generator(settings),
    )
    try:
        bot.run(
            settings.discord_bot_token.get_secret_value(),
            reconnect=True,
            log_handler=None,
        )
    except Exception:  # noqa: BLE001 - startup errors can contain credentials or response bodies
        logger.error("bot_run_failed", extra={"worker_id": str(bot.worker_id)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
