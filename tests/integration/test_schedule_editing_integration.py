from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application.schedule_editing import (
    EditValues,
    InvalidScheduleEditOptions,
    ScheduleEditingService,
    ScheduleEditNoChanges,
    ScheduleEditUnavailable,
    ScheduleEditVersionConflict,
)
from discord_ai_reminder_bot.domain.enums import DisplayNameSource
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    OperationLog,
    Schedule,
    ScheduleRun,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 19, 0, 0, tzinfo=UTC)  # 09:00 JST
GUILD_ID = 29_100
CREATOR_ID = 29_200


async def add_schedule(
    session: AsyncSession,
    *,
    schedule_type: str = "once",
    status: str = "active",
    content: str | None = "body",
    next_at: datetime | None = None,
    local_time: time | None = None,
    weekday: int | None = None,
    end_date: date | None = None,
    display_name: str | None = None,
    display_name_source: str = "unset",
) -> tuple[Schedule, ScheduleRun | None]:
    next_at = next_at or NOW + timedelta(hours=1)
    recurring = schedule_type != "once"
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=GUILD_ID,
        channel_id=29_300,
        creator_user_id=CREATOR_ID,
        schedule_type=schedule_type,
        status=status,
        content=content,
        display_name=display_name,
        display_name_source=display_name_source,
        next_run_at=next_at if status in {"active", "draft"} else None,
        local_time=(local_time or time(10)) if recurring else None,
        weekday=(weekday if weekday is not None else 2) if schedule_type == "weekly" else None,
        end_date=end_date,
        version=1,
        updated_at=NOW,
    )
    session.add(schedule)
    await session.flush()
    run = None
    if status in {"active", "draft"}:
        run = ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=next_at,
            status="pending",
            attempt_count=0,
            next_attempt_at=next_at,
            updated_at=NOW,
        )
        session.add(run)
        await session.flush()
    return schedule, run


async def edit(session: AsyncSession, schedule: Schedule, values: EditValues, *, now=NOW):
    return await ScheduleEditingService(session).edit(
        guild_id=GUILD_ID,
        public_id=str(schedule.public_id),
        actor_user_id=CREATOR_ID,
        administrator=False,
        values=values,
        edited_at=now,
    )


async def test_edit_expected_version_conflict_changes_nothing(
    db_session: AsyncSession,
) -> None:
    schedule, run = await add_schedule(db_session)
    with pytest.raises(ScheduleEditVersionConflict):
        await ScheduleEditingService(db_session).edit(
            guild_id=GUILD_ID,
            public_id=str(schedule.public_id),
            actor_user_id=CREATOR_ID,
            administrator=False,
            values=EditValues(content="new"),
            edited_at=NOW,
            expected_version=schedule.version + 1,
        )
    assert schedule.version == 1 and schedule.content == "body"
    assert run is not None and run.status == "pending"
    assert await db_session.scalar(select(func.count(OperationLog.id))) == 0


async def test_consecutive_edits_use_latest_version_and_noop_does_not_increment(
    db_session: AsyncSession,
) -> None:
    schedule, _ = await add_schedule(
        db_session,
        schedule_type="daily",
        local_time=time(10),
        end_date=date(2026, 8, 30),
    )
    service = ScheduleEditingService(db_session)

    await service.edit(
        guild_id=GUILD_ID,
        public_id=str(schedule.public_id),
        actor_user_id=CREATOR_ID,
        administrator=False,
        values=EditValues(content="updated"),
        edited_at=NOW,
        expected_version=1,
    )
    assert schedule.version == 2

    with pytest.raises(ScheduleEditNoChanges):
        await service.edit(
            guild_id=GUILD_ID,
            public_id=str(schedule.public_id),
            actor_user_id=CREATOR_ID,
            administrator=False,
            values=EditValues(content="updated"),
            edited_at=NOW,
            expected_version=2,
        )
    assert schedule.version == 2

    await service.edit(
        guild_id=GUILD_ID,
        public_id=str(schedule.public_id),
        actor_user_id=CREATOR_ID,
        administrator=False,
        values=EditValues(clear_content=True, clear_end_date=True),
        edited_at=NOW,
        expected_version=2,
    )
    assert schedule.version == 3
    assert schedule.content is None and schedule.end_date is None


@pytest.mark.parametrize("clear_content", [False, True])
async def test_manual_name_survives_content_change_and_clear(
    db_session: AsyncSession, clear_content: bool
) -> None:
    schedule, _ = await add_schedule(
        db_session,
        display_name="手動名",
        display_name_source=DisplayNameSource.MANUAL.value,
    )
    values = EditValues(clear_content=True) if clear_content else EditValues(content="changed")
    await edit(db_session, schedule, values)
    assert schedule.display_name == "手動名"
    assert schedule.display_name_source == DisplayNameSource.MANUAL.value


async def test_ai_name_is_cleared_on_content_change_without_generation_job(
    db_session: AsyncSession,
) -> None:
    schedule, _ = await add_schedule(
        db_session,
        display_name="古いAI名",
        display_name_source=DisplayNameSource.AI.value,
    )
    await edit(db_session, schedule, EditValues(content="changed"))
    assert schedule.display_name is None
    assert schedule.display_name_source == DisplayNameSource.UNSET.value
    operation = (
        await db_session.scalars(
            select(OperationLog).where(OperationLog.schedule_id == schedule.id)
        )
    ).one()
    assert operation.changes["display_name_changed"] is True
    assert operation.changes["display_name_source"] == {"from": "ai", "to": "unset"}
    assert "古いAI名" not in str(operation.changes)


async def test_once_content_channel_and_time_edit_replaces_run_and_audits(
    db_session: AsyncSession,
) -> None:
    schedule, old = await add_schedule(db_session)
    new_at = NOW + timedelta(minutes=5)
    result = await edit(
        db_session,
        schedule,
        EditValues(channel_id=29_301, content="new body", scheduled_at=new_at),
    )
    assert result.channel_id == 29_301 and result.content == "new body"
    assert schedule.next_run_at == new_at and schedule.version == 2
    assert old.status == "skipped" and old.result_code == "schedule_edited"
    assert old.next_attempt_at is None and old.finished_at == old.updated_at == NOW
    runs = list(
        await db_session.scalars(
            select(ScheduleRun)
            .where(ScheduleRun.schedule_id == schedule.id)
            .order_by(ScheduleRun.id)
        )
    )
    assert len(runs) == 2 and runs[1].scheduled_for == runs[1].next_attempt_at == new_at
    assert runs[1].attempt_count == 0
    operation = (
        await db_session.scalars(
            select(OperationLog).where(OperationLog.schedule_id == schedule.id)
        )
    ).one()
    assert operation.action == "edited" and operation.actor_user_id == CREATOR_ID
    assert operation.changes["content_changed"] is True
    assert "body" not in str(operation.changes)


async def test_content_and_channel_only_preserve_retry_pending_and_attempt_history(
    db_session: AsyncSession,
) -> None:
    schedule, run = await add_schedule(db_session)
    run.attempt_count = 1
    run.next_attempt_at = NOW + timedelta(minutes=10)
    run.result_code = "retry_pending"
    worker = uuid.uuid4()
    attempt = DeliveryAttempt(
        schedule_run_id=run.id,
        attempt_number=1,
        status="failed",
        claimed_by=worker,
        claimed_at=NOW - timedelta(minutes=2),
        finished_at=NOW - timedelta(minutes=1),
        error_kind="transient",
        error_code="network",
        error_summary="safe",
    )
    db_session.add(attempt)
    await db_session.flush()
    result = await edit(db_session, schedule, EditValues(channel_id=29_302, content="retry body"))
    assert result.retry_pending_preserved is True and result.pending_runs_skipped == 0
    assert run.status == "pending" and run.attempt_count == 1
    assert run.next_attempt_at == NOW + timedelta(minutes=10)
    assert attempt.status == "failed"
    assert await db_session.scalar(select(func.count()).select_from(DeliveryAttempt)) == 1


async def test_clear_content_transitions_active_to_draft_without_replacing_run(
    db_session: AsyncSession,
) -> None:
    schedule, run = await add_schedule(db_session, schedule_type="daily")
    result = await edit(db_session, schedule, EditValues(clear_content=True))
    assert result.status.value == schedule.status == "draft"
    assert schedule.content is None and run.status == "pending"
    assert schedule.next_run_at == run.scheduled_for


async def test_draft_content_transitions_to_active_and_paused_edit_stays_paused(
    db_session: AsyncSession,
) -> None:
    draft, draft_run = await add_schedule(
        db_session, schedule_type="weekly", status="draft", content=None
    )
    result = await edit(db_session, draft, EditValues(content="ready"))
    assert result.status.value == "active" and draft_run.status == "pending"
    paused, no_run = await add_schedule(db_session, schedule_type="daily", status="paused")
    paused_result = await edit(
        db_session,
        paused,
        EditValues(
            local_time=time(8),
            end_date=date(2020, 1, 1),
            end_date_supplied=True,
            clear_content=True,
        ),
    )
    assert no_run is None and paused_result.status.value == paused.status == "paused"
    assert paused.next_run_at is None and paused.local_time == time(8)
    assert paused.end_date == date(2020, 1, 1) and paused.content is None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(ScheduleRun)
            .where(ScheduleRun.schedule_id == paused.id)
        )
        == 0
    )


async def test_daily_same_candidate_keeps_run_and_clear_end_date(
    db_session: AsyncSession,
) -> None:
    current = NOW + timedelta(hours=1)
    schedule, run = await add_schedule(
        db_session,
        schedule_type="daily",
        next_at=current,
        local_time=time(10),
        end_date=date(2026, 8, 25),
    )
    result = await edit(db_session, schedule, EditValues(clear_end_date=True))
    assert result.run_replaced is False and run.status == "pending"
    assert schedule.end_date is None and schedule.next_run_at == current


async def test_daily_time_change_replaces_pending_at_inclusive_five_minute_candidate(
    db_session: AsyncSession,
) -> None:
    schedule, old = await add_schedule(
        db_session,
        schedule_type="daily",
        next_at=NOW + timedelta(hours=1),
        local_time=time(10),
    )
    result = await edit(db_session, schedule, EditValues(local_time=time(9, 5)))
    expected = NOW + timedelta(minutes=5)
    assert old.status == "skipped" and result.run_replaced is True
    assert schedule.local_time == time(9, 5) and schedule.next_run_at == expected
    new_run = (
        await db_session.scalars(
            select(ScheduleRun).where(
                ScheduleRun.schedule_id == schedule.id,
                ScheduleRun.status == "pending",
            )
        )
    ).one()
    assert new_run.scheduled_for == new_run.next_attempt_at == expected


async def test_no_recurring_candidate_ends_active_but_rejects_draft_atomically(
    db_session: AsyncSession,
) -> None:
    active, active_run = await add_schedule(
        db_session,
        schedule_type="daily",
        next_at=NOW + timedelta(hours=1),
        local_time=time(10),
    )
    result = await edit(
        db_session,
        active,
        EditValues(end_date=date(2026, 8, 18), end_date_supplied=True),
    )
    assert result.status.value == active.status == "ended"
    assert active.next_run_at is None and active.terminal_at == NOW
    assert active_run.status == "skipped"

    draft, draft_run = await add_schedule(
        db_session,
        schedule_type="daily",
        status="draft",
        content=None,
        next_at=NOW + timedelta(hours=1),
        local_time=time(10),
    )
    with pytest.raises(ScheduleEditUnavailable):
        await edit(
            db_session,
            draft,
            EditValues(end_date=date(2026, 8, 18), end_date_supplied=True),
        )
    assert draft.status == "draft" and draft.end_date is None and draft.version == 1
    assert draft_run.status == "pending"


async def test_once_rejects_timestamp_already_used_by_terminal_history(
    db_session: AsyncSession,
) -> None:
    schedule, current = await add_schedule(db_session)
    used_at = NOW + timedelta(hours=2)
    db_session.add(
        ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=used_at,
            status="skipped",
            attempt_count=0,
            next_attempt_at=None,
            result_code="old",
            finished_at=NOW,
            updated_at=NOW,
        )
    )
    await db_session.flush()
    with pytest.raises(ScheduleEditUnavailable):
        await edit(db_session, schedule, EditValues(scheduled_at=used_at))
    assert current.status == "pending" and schedule.version == 1


async def test_weekly_used_candidate_advances_to_next_unused_occurrence(
    db_session: AsyncSession,
) -> None:
    current = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)
    schedule, old = await add_schedule(
        db_session,
        schedule_type="weekly",
        next_at=current,
        local_time=time(9),
        weekday=3,
    )
    used = ScheduleRun(
        schedule_id=schedule.id,
        scheduled_for=datetime(2026, 8, 21, 0, 0, tzinfo=UTC),
        status="skipped",
        attempt_count=0,
        next_attempt_at=None,
        result_code="old",
        finished_at=NOW,
        updated_at=NOW,
    )
    db_session.add(used)
    await db_session.flush()
    result = await edit(
        db_session,
        schedule,
        EditValues(weekday=4, weekday_supplied=True),
    )
    assert old.status == "skipped" and used.status == "skipped"
    assert result.next_run_at == datetime(2026, 8, 28, 0, 0, tzinfo=UTC)


async def test_noop_invalid_options_boundary_and_authorization_change_nothing(
    db_session: AsyncSession,
) -> None:
    schedule, run = await add_schedule(db_session)
    with pytest.raises(ScheduleEditNoChanges):
        await edit(db_session, schedule, EditValues(channel_id=schedule.channel_id))
    with pytest.raises(InvalidScheduleEditOptions):
        await edit(db_session, schedule, EditValues(local_time=time(12)))
    with pytest.raises(ScheduleEditUnavailable):
        await ScheduleEditingService(db_session).edit(
            guild_id=GUILD_ID,
            public_id=str(schedule.public_id),
            actor_user_id=CREATOR_ID + 1,
            administrator=False,
            values=EditValues(content="forbidden"),
            edited_at=NOW,
        )
    with pytest.raises(ScheduleEditUnavailable):
        await edit(
            db_session,
            schedule,
            EditValues(content="too late"),
            now=schedule.next_run_at - timedelta(minutes=4),
        )
    assert schedule.version == 1 and run.status == "pending"
    assert await db_session.scalar(select(func.count()).select_from(OperationLog)) == 0


@pytest.mark.parametrize("attempt_status", ["claimed", "sending"])
async def test_processing_claimed_and_sending_are_rejected(
    db_session: AsyncSession, attempt_status: str
) -> None:
    schedule, run = await add_schedule(db_session)
    worker = uuid.uuid4()
    run.status = "processing"
    run.attempt_count = 1
    run.next_attempt_at = None
    run.claimed_by = worker
    run.claimed_at = NOW
    run.lease_expires_at = NOW + timedelta(minutes=5)
    run.started_at = NOW
    db_session.add(
        DeliveryAttempt(
            schedule_run_id=run.id,
            attempt_number=1,
            status=attempt_status,
            claimed_by=worker,
            claimed_at=NOW,
            send_started_at=NOW if attempt_status == "sending" else None,
        )
    )
    await db_session.flush()
    with pytest.raises(ScheduleEditUnavailable):
        await edit(db_session, schedule, EditValues(content="blocked"))


async def test_success_waiting_for_schedule_finalization_is_rejected(
    db_session: AsyncSession,
) -> None:
    schedule, run = await add_schedule(db_session)
    run.status = "succeeded"
    run.attempt_count = 1
    run.next_attempt_at = None
    run.discord_message_id = 123
    run.result_code = "delivered"
    run.finished_at = NOW
    await db_session.flush()
    with pytest.raises(ScheduleEditUnavailable):
        await edit(db_session, schedule, EditValues(content="blocked"))
