from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application.schedule_naming import (
    ScheduleNameEditUnavailable,
    ScheduleNameNoChanges,
    ScheduleNameVersionConflict,
    ScheduleNamingService,
)
from discord_ai_reminder_bot.domain.enums import DisplayNameSource, ScheduleStatus
from discord_ai_reminder_bot.infrastructure.database.models import OperationLog, Schedule

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
GUILD_ID = 39_100
CREATOR_ID = 39_200
ADMIN_ID = 39_201


async def add_schedule(
    session: AsyncSession,
    *,
    status: ScheduleStatus = ScheduleStatus.ACTIVE,
    display_name: str | None = None,
    source: DisplayNameSource = DisplayNameSource.UNSET,
    guild_id: int = GUILD_ID,
) -> Schedule:
    terminal = status in {
        ScheduleStatus.COMPLETED,
        ScheduleStatus.ENDED,
        ScheduleStatus.DELETED,
    }
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=guild_id,
        channel_id=39_300,
        creator_user_id=CREATOR_ID,
        schedule_type="daily" if status is ScheduleStatus.ENDED else "once",
        status=status.value,
        content="body",
        display_name=display_name,
        display_name_source=source.value,
        next_run_at=NOW + timedelta(hours=1)
        if status in {ScheduleStatus.ACTIVE, ScheduleStatus.DRAFT}
        else None,
        local_time=datetime.min.time() if status is ScheduleStatus.ENDED else None,
        version=1,
        updated_at=NOW,
        terminal_at=NOW if terminal else None,
        deleted_at=NOW if status is ScheduleStatus.DELETED else None,
    )
    session.add(schedule)
    await session.flush()
    return schedule


async def edit(
    session: AsyncSession,
    schedule: Schedule,
    value: str,
    *,
    actor: int = CREATOR_ID,
    administrator: bool = False,
    expected_version: int = 1,
):
    return await ScheduleNamingService(session).edit_manual_name(
        guild_id=GUILD_ID,
        public_id=str(schedule.public_id),
        actor_user_id=actor,
        administrator=administrator,
        submitted_name=value,
        edited_at=NOW,
        expected_version=expected_version,
    )


async def test_manual_name_set_change_clear_and_audit_without_name_history(
    db_session: AsyncSession,
) -> None:
    schedule = await add_schedule(db_session)
    await edit(db_session, schedule, "  最初の名前  ")
    assert (schedule.display_name, schedule.display_name_source, schedule.version) == (
        "最初の名前",
        "manual",
        2,
    )
    await edit(db_session, schedule, "変更名", expected_version=2)
    await edit(db_session, schedule, "  ", expected_version=3)
    assert (schedule.display_name, schedule.display_name_source, schedule.version) == (
        None,
        "unset",
        4,
    )
    operations = list(
        await db_session.scalars(
            select(OperationLog)
            .where(OperationLog.schedule_id == schedule.id)
            .order_by(OperationLog.id)
        )
    )
    assert len(operations) == 3
    assert operations[0].changes == {
        "display_name_changed": True,
        "display_name_source": {"from": "unset", "to": "manual"},
    }
    assert "最初の名前" not in str([item.changes for item in operations])
    assert "変更名" not in str([item.changes for item in operations])


async def test_manual_name_noop_changes_nothing(db_session: AsyncSession) -> None:
    schedule = await add_schedule(
        db_session, display_name="同じ名前", source=DisplayNameSource.MANUAL
    )
    with pytest.raises(ScheduleNameNoChanges):
        await edit(db_session, schedule, "  同じ名前  ")
    assert schedule.version == 1
    assert await db_session.scalar(select(func.count(OperationLog.id))) == 0


async def test_ai_same_text_submission_changes_source_to_manual(db_session: AsyncSession) -> None:
    schedule = await add_schedule(db_session, display_name="同じ名前", source=DisplayNameSource.AI)
    await edit(db_session, schedule, "同じ名前")
    assert schedule.display_name_source == "manual" and schedule.version == 2


async def test_owner_admin_guild_and_version_boundaries(db_session: AsyncSession) -> None:
    owner = await add_schedule(db_session)
    await edit(db_session, owner, "管理者名", actor=ADMIN_ID, administrator=True)

    unauthorized = await add_schedule(db_session)
    with pytest.raises(ScheduleNameEditUnavailable):
        await edit(db_session, unauthorized, "不可", actor=ADMIN_ID, administrator=False)
    with pytest.raises(ScheduleNameEditUnavailable):
        await ScheduleNamingService(db_session).edit_manual_name(
            guild_id=GUILD_ID + 1,
            public_id=str(unauthorized.public_id),
            actor_user_id=CREATOR_ID,
            administrator=True,
            submitted_name="不可",
            edited_at=NOW,
            expected_version=1,
        )
    with pytest.raises(ScheduleNameVersionConflict):
        await edit(db_session, unauthorized, "不可", expected_version=2)
    assert unauthorized.version == 1 and unauthorized.display_name is None


async def test_admin_other_sets_changes_clears_name_with_safe_audit(
    db_session: AsyncSession,
) -> None:
    schedule = await add_schedule(db_session)
    await edit(
        db_session,
        schedule,
        "管理者による最初の名前",
        actor=ADMIN_ID,
        administrator=True,
    )
    await edit(
        db_session,
        schedule,
        "管理者による変更名",
        actor=ADMIN_ID,
        administrator=True,
        expected_version=2,
    )
    await edit(
        db_session,
        schedule,
        "  ",
        actor=ADMIN_ID,
        administrator=True,
        expected_version=3,
    )

    assert (schedule.display_name, schedule.display_name_source, schedule.version) == (
        None,
        DisplayNameSource.UNSET.value,
        4,
    )
    operations = list(
        await db_session.scalars(
            select(OperationLog)
            .where(OperationLog.schedule_id == schedule.id)
            .order_by(OperationLog.id)
        )
    )
    assert len(operations) == 3
    assert [item.actor_user_id for item in operations] == [ADMIN_ID] * 3
    assert [item.actor_type for item in operations] == ["user"] * 3
    assert [item.changes for item in operations] == [
        {
            "display_name_changed": True,
            "display_name_source": {"from": "unset", "to": "manual"},
        },
        {
            "display_name_changed": True,
            "display_name_source": {"from": "manual", "to": "manual"},
        },
        {
            "display_name_changed": True,
            "display_name_source": {"from": "manual", "to": "unset"},
        },
    ]
    audit_text = str([item.changes for item in operations])
    for forbidden in (
        "管理者による最初の名前",
        "管理者による変更名",
        schedule.content,
        str(schedule.guild_id),
        "version",
    ):
        assert forbidden not in audit_text


async def test_admin_name_edit_isolated_from_other_schedule_and_guild(
    db_session: AsyncSession,
) -> None:
    target = await add_schedule(db_session)
    same_guild_other = await add_schedule(
        db_session, display_name="同guild維持", source=DisplayNameSource.MANUAL
    )
    other_guild = await add_schedule(
        db_session,
        guild_id=GUILD_ID + 1,
        display_name="別guild維持",
        source=DisplayNameSource.MANUAL,
    )
    unchanged = {
        item.id: (item.display_name, item.display_name_source, item.version, item.updated_at)
        for item in (same_guild_other, other_guild)
    }

    await edit(
        db_session,
        target,
        "対象だけ変更",
        actor=ADMIN_ID,
        administrator=True,
    )

    assert (target.display_name, target.display_name_source, target.version) == (
        "対象だけ変更",
        DisplayNameSource.MANUAL.value,
        2,
    )
    for item in (same_guild_other, other_guild):
        assert (
            item.display_name,
            item.display_name_source,
            item.version,
            item.updated_at,
        ) == unchanged[item.id]
        assert (
            await db_session.scalar(
                select(func.count(OperationLog.id)).where(OperationLog.schedule_id == item.id)
            )
            == 0
        )
    assert (
        await db_session.scalar(
            select(func.count(OperationLog.id)).where(OperationLog.schedule_id == target.id)
        )
        == 1
    )


@pytest.mark.parametrize(
    "case",
    ["non-owner", "wrong-guild", "stale-version", "completed", "ended", "deleted", "failed"],
)
async def test_name_edit_rejections_change_no_database_state(
    db_session: AsyncSession, case: str
) -> None:
    status = {
        "completed": ScheduleStatus.COMPLETED,
        "ended": ScheduleStatus.ENDED,
        "deleted": ScheduleStatus.DELETED,
        "failed": ScheduleStatus.FAILED,
    }.get(case, ScheduleStatus.ACTIVE)
    rejected = await add_schedule(
        db_session,
        status=status,
        display_name="拒否前の名前",
        source=DisplayNameSource.MANUAL,
    )
    other = await add_schedule(
        db_session, display_name="別予約の名前", source=DisplayNameSource.MANUAL
    )
    rejected_before = (
        rejected.display_name,
        rejected.display_name_source,
        rejected.version,
        rejected.updated_at,
    )
    other_before = (
        other.display_name,
        other.display_name_source,
        other.version,
        other.updated_at,
    )

    with pytest.raises((ScheduleNameEditUnavailable, ScheduleNameVersionConflict)):
        if case == "wrong-guild":
            await ScheduleNamingService(db_session).edit_manual_name(
                guild_id=GUILD_ID + 1,
                public_id=str(rejected.public_id),
                actor_user_id=CREATOR_ID,
                administrator=True,
                submitted_name="拒否される変更",
                edited_at=NOW,
                expected_version=1,
            )
        else:
            await edit(
                db_session,
                rejected,
                "拒否される変更",
                actor=ADMIN_ID if case == "non-owner" else CREATOR_ID,
                administrator=False,
                expected_version=2 if case == "stale-version" else 1,
            )

    assert (
        rejected.display_name,
        rejected.display_name_source,
        rejected.version,
        rejected.updated_at,
    ) == rejected_before
    assert (other.display_name, other.display_name_source, other.version, other.updated_at) == (
        other_before
    )
    assert await db_session.scalar(select(func.count(OperationLog.id))) == 0


@pytest.mark.parametrize(
    "status",
    [
        ScheduleStatus.FAILED,
        ScheduleStatus.COMPLETED,
        ScheduleStatus.ENDED,
        ScheduleStatus.DELETED,
    ],
)
async def test_terminal_and_failed_names_are_not_editable(
    db_session: AsyncSession, status: ScheduleStatus
) -> None:
    schedule = await add_schedule(db_session, status=status)
    with pytest.raises(ScheduleNameEditUnavailable):
        await edit(db_session, schedule, "不可")
    assert schedule.version == 1 and schedule.display_name is None


@pytest.mark.parametrize(
    ("display_name", "source"),
    [
        ("name", "unset"),
        (None, "manual"),
        (" ", "manual"),
        ("line\nbreak", "manual"),
        ("format\u200bmark", "manual"),
        ("x" * 33, "ai"),
        ("name", "unknown"),
    ],
)
async def test_database_check_rejects_invalid_name_source_pairs(
    db_session: AsyncSession, display_name: str | None, source: str
) -> None:
    schedule = await add_schedule(db_session)
    nested = await db_session.begin_nested()
    try:
        with pytest.raises(SQLAlchemyError):
            await db_session.execute(
                update(Schedule)
                .where(Schedule.id == schedule.id)
                .values(display_name=display_name, display_name_source=source)
            )
            await db_session.flush()
    finally:
        await nested.rollback()
