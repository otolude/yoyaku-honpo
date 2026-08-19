import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application.draft_notification_bootstrap import (
    DraftNotificationBootstrapService,
)
from discord_ai_reminder_bot.application.schedule_creation import OnceScheduleCreationService
from discord_ai_reminder_bot.infrastructure.database.models import (
    NotificationLog,
    Schedule,
    ScheduleRun,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)
GUILD_ID = 98_100


async def test_draft_creation_atomically_preplans_future_routes(
    db_session: AsyncSession,
) -> None:
    await OnceScheduleCreationService(db_session).create(
        guild_id=GUILD_ID,
        channel_id=98_200,
        creator_user_id=98_300,
        scheduled_for=NOW + timedelta(hours=25),
        content=None,
        allow_duplicate=False,
        now=NOW,
        configured_guild_id=GUILD_ID,
    )
    rows = list(
        (
            await db_session.execute(
                select(NotificationLog).order_by(NotificationLog.scheduled_at.asc())
            )
        ).scalars()
    )
    assert [row.notification_type for row in rows] == ["draft_24h", "draft_1h"]
    assert all(row.recipient_type == "creator_dm" and row.attempt_count == 0 for row in rows)
    assert all(row.schedule_id is not None and row.schedule_run_id is not None for row in rows)


async def test_bootstrap_creates_one_immediate_without_replaying_elapsed_thresholds(
    db_session: AsyncSession,
) -> None:
    run_at = NOW + timedelta(minutes=30)
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=GUILD_ID,
        channel_id=98_201,
        creator_user_id=98_301,
        schedule_type="once",
        status="draft",
        content=None,
        next_run_at=run_at,
        version=1,
    )
    db_session.add(schedule)
    await db_session.flush()
    run = ScheduleRun(
        schedule_id=schedule.id,
        scheduled_for=run_at,
        status="pending",
        attempt_count=0,
        next_attempt_at=run_at,
    )
    db_session.add(run)
    await db_session.flush()

    service = DraftNotificationBootstrapService(db_session, configured_guild_id=GUILD_ID)
    first = await service.bootstrap(recovery_cutoff=NOW, batch_size=20)
    second = await service.bootstrap(recovery_cutoff=NOW, batch_size=20)
    rows = list((await db_session.execute(select(NotificationLog))).scalars())

    assert first.selected == 1
    assert second.selected == 0
    assert len(rows) == 1
    assert rows[0].notification_type == "draft_immediate"
    assert rows[0].scheduled_at == NOW
