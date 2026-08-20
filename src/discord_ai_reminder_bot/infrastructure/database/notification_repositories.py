"""PostgreSQL notification outbox repositories without transaction ownership."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application.delivery import validate_message_id
from discord_ai_reminder_bot.config import MAX_POSTGRES_BIGINT
from discord_ai_reminder_bot.domain.enums import (
    NotificationAttemptStatus,
    NotificationErrorKind,
    NotificationStatus,
    NotificationType,
)
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.domain.safe_text import validate_safe_error_text
from discord_ai_reminder_bot.infrastructure.database.exceptions import (
    RepositoryNotFoundError,
    RepositoryOwnershipError,
    RepositoryStateConflictError,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    NotificationAttempt,
    NotificationLog,
)

MAX_NOTIFICATION_CLAIM_BATCH_SIZE = 20
_DRAFT_NOTIFICATION_VALUES = tuple(
    item.value
    for item in (
        NotificationType.DRAFT_24H,
        NotificationType.DRAFT_1H,
        NotificationType.DRAFT_IMMEDIATE,
    )
)


@dataclass(frozen=True)
class ClaimedNotification:
    notification: NotificationLog
    attempt: NotificationAttempt


def build_due_notification_claim_statement(
    *, now: datetime, batch_size: int
) -> Select[tuple[NotificationLog]]:
    now = require_utc(now)
    _validate_batch_size(batch_size)
    return (
        select(NotificationLog)
        .where(
            NotificationLog.status == NotificationStatus.PENDING.value,
            NotificationLog.next_attempt_at <= now,
            NotificationLog.scheduled_at <= now,
            NotificationLog.attempt_count < 3,
        )
        .order_by(
            NotificationLog.next_attempt_at.asc(),
            NotificationLog.scheduled_at.asc(),
            NotificationLog.id.asc(),
        )
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )


def build_expired_notification_statement(
    *, now: datetime, batch_size: int
) -> Select[tuple[NotificationLog]]:
    now = require_utc(now)
    _validate_batch_size(batch_size)
    return (
        select(NotificationLog)
        .where(
            NotificationLog.status == NotificationStatus.PROCESSING.value,
            NotificationLog.lease_expires_at <= now,
        )
        .order_by(NotificationLog.lease_expires_at.asc(), NotificationLog.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )


class NotificationLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_idempotent(
        self, notification: NotificationLog, *, allow_existing_event_time: bool = False
    ) -> NotificationLog:
        _validate_new_notification(notification)
        values = {
            "schedule_id": notification.schedule_id,
            "schedule_run_id": notification.schedule_run_id,
            "notification_type": notification.notification_type,
            "recipient_type": notification.recipient_type,
            "recipient_id": notification.recipient_id,
            "status": notification.status,
            "deduplication_key": notification.deduplication_key,
            "error_code": notification.error_code,
            "error_summary": notification.error_summary,
            "scheduled_at": notification.scheduled_at,
            "next_attempt_at": notification.next_attempt_at,
            "attempt_count": notification.attempt_count,
            "claimed_by": None,
            "claimed_at": None,
            "lease_expires_at": None,
            "started_at": None,
            "finished_at": None,
            "sent_at": None,
        }
        statement = (
            pg_insert(NotificationLog)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[NotificationLog.deduplication_key])
            .returning(NotificationLog.id)
        )
        inserted_id = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted_id is not None:
            notification.id = inserted_id
            stored = await self._session.get(NotificationLog, inserted_id)
            assert stored is not None
            return stored
        existing = (
            await self._session.execute(
                select(NotificationLog).where(
                    NotificationLog.deduplication_key == notification.deduplication_key
                )
            )
        ).scalar_one()
        if not _same_logical_route(
            existing,
            notification,
            compare_scheduled_at=not allow_existing_event_time,
        ):
            raise RepositoryStateConflictError(
                "deduplication key belongs to a different notification route"
            )
        return existing

    async def claim_due(
        self,
        *,
        now: datetime,
        worker_id: uuid.UUID,
        batch_size: int,
        lease_timeout: timedelta,
    ) -> list[ClaimedNotification]:
        now = require_utc(now)
        _validate_worker_id(worker_id)
        _validate_batch_size(batch_size)
        if not isinstance(lease_timeout, timedelta) or lease_timeout <= timedelta(0):
            raise ValueError("lease_timeout must be a positive timedelta")
        rows = list(
            (
                await self._session.execute(
                    build_due_notification_claim_statement(now=now, batch_size=batch_size)
                )
            ).scalars()
        )
        claimed: list[ClaimedNotification] = []
        for notification in rows:
            notification.status = NotificationStatus.PROCESSING.value
            notification.next_attempt_at = None
            notification.attempt_count += 1
            notification.claimed_by = worker_id
            notification.claimed_at = now
            notification.lease_expires_at = now + lease_timeout
            notification.started_at = notification.started_at or now
            notification.finished_at = None
            notification.sent_at = None
            attempt = NotificationAttempt(
                notification_log_id=notification.id,
                attempt_number=notification.attempt_count,
                status=NotificationAttemptStatus.CLAIMED.value,
                claimed_by=worker_id,
                claimed_at=now,
                send_started_at=None,
                finished_at=None,
                discord_message_id=None,
                error_kind=None,
                error_code=None,
                error_summary=None,
                created_at=now,
                updated_at=now,
            )
            self._session.add(attempt)
            claimed.append(ClaimedNotification(notification, attempt))
        await self._session.flush()
        return claimed

    async def lock_expired(self, *, now: datetime, batch_size: int) -> list[NotificationLog]:
        statement = build_expired_notification_statement(now=now, batch_size=batch_size)
        return list((await self._session.execute(statement)).scalars())

    async def list_draft_for_run(self, *, run_id: int) -> list[NotificationLog]:
        return list(
            (
                await self._session.execute(
                    select(NotificationLog)
                    .where(
                        NotificationLog.schedule_run_id == run_id,
                        NotificationLog.notification_type.in_(_DRAFT_NOTIFICATION_VALUES),
                    )
                    .order_by(NotificationLog.scheduled_at.asc(), NotificationLog.id.asc())
                )
            ).scalars()
        )

    async def cancel_unclaimed_overdue_draft(self, *, run_id: int, cutoff: datetime) -> int:
        cutoff = require_utc(cutoff)
        result = await self._session.execute(
            update(NotificationLog)
            .where(
                NotificationLog.schedule_run_id == run_id,
                NotificationLog.notification_type.in_(_DRAFT_NOTIFICATION_VALUES),
                NotificationLog.status == NotificationStatus.PENDING.value,
                NotificationLog.scheduled_at < cutoff,
            )
            .values(
                status=NotificationStatus.CANCELLED.value,
                next_attempt_at=None,
                finished_at=cutoff,
                error_code="draft_notification_elapsed",
                error_summary="Draft notification threshold elapsed while delivery was unavailable",
            )
        )
        await self._session.flush()
        return int(result.rowcount or 0)

    async def mark_sending_started(
        self, *, notification_id: int, worker_id: uuid.UUID, now: datetime
    ) -> NotificationLog:
        now = require_utc(now)
        _validate_worker_id(worker_id)
        return await self._owned_update(
            notification_id=notification_id,
            worker_id=worker_id,
            now=now,
            # A no-op assignment still makes ownership and the live lease an
            # atomic database precondition for the attempt transition.
            values={"started_at": NotificationLog.started_at},
            require_live_lease=True,
        )

    async def mark_succeeded(
        self, *, notification_id: int, worker_id: uuid.UUID, now: datetime
    ) -> NotificationLog:
        return await self._finish(
            notification_id=notification_id,
            worker_id=worker_id,
            now=now,
            status=NotificationStatus.SUCCEEDED,
            next_attempt_at=None,
            sent_at=now,
            error_code=None,
            error_summary=None,
        )

    async def mark_failed(
        self,
        *,
        notification_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        error_code: str,
        error_summary: str,
    ) -> NotificationLog:
        return await self._finish(
            notification_id=notification_id,
            worker_id=worker_id,
            now=now,
            status=NotificationStatus.FAILED,
            next_attempt_at=None,
            sent_at=None,
            error_code=validate_safe_error_text(error_code, field="error_code", maximum=64),
            error_summary=validate_safe_error_text(
                error_summary, field="error_summary", maximum=500
            ),
        )

    async def mark_unknown(
        self,
        *,
        notification_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        error_code: str,
        error_summary: str,
    ) -> NotificationLog:
        return await self._finish(
            notification_id=notification_id,
            worker_id=worker_id,
            now=now,
            status=NotificationStatus.UNKNOWN,
            next_attempt_at=None,
            sent_at=None,
            error_code=validate_safe_error_text(error_code, field="error_code", maximum=64),
            error_summary=validate_safe_error_text(
                error_summary, field="error_summary", maximum=500
            ),
        )

    async def return_to_pending(
        self,
        *,
        notification_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        retry_at: datetime,
        error_code: str,
        error_summary: str,
    ) -> NotificationLog:
        now = require_utc(now)
        retry_at = require_utc(retry_at)
        if retry_at <= now:
            raise ValueError("retry_at must be after now")
        return await self._finish(
            notification_id=notification_id,
            worker_id=worker_id,
            now=now,
            status=NotificationStatus.PENDING,
            next_attempt_at=retry_at,
            sent_at=None,
            error_code=validate_safe_error_text(error_code, field="error_code", maximum=64),
            error_summary=validate_safe_error_text(
                error_summary, field="error_summary", maximum=500
            ),
        )

    async def cancel(
        self,
        *,
        notification_id: int,
        now: datetime,
        error_code: str,
        error_summary: str,
        worker_id: uuid.UUID | None = None,
    ) -> NotificationLog:
        now = require_utc(now)
        if worker_id is not None:
            _validate_worker_id(worker_id)
        conditions = [NotificationLog.id == notification_id]
        if worker_id is None:
            conditions.append(NotificationLog.status == NotificationStatus.PENDING.value)
        else:
            conditions.extend(
                (
                    NotificationLog.status == NotificationStatus.PROCESSING.value,
                    NotificationLog.claimed_by == worker_id,
                )
            )
        statement = (
            update(NotificationLog)
            .where(*conditions)
            .values(
                status=NotificationStatus.CANCELLED.value,
                next_attempt_at=None,
                claimed_by=None,
                claimed_at=None,
                lease_expires_at=None,
                finished_at=now,
                sent_at=None,
                error_code=validate_safe_error_text(error_code, field="error_code", maximum=64),
                error_summary=validate_safe_error_text(
                    error_summary, field="error_summary", maximum=500
                ),
            )
            .returning(NotificationLog)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        result = (await self._session.execute(statement)).scalar_one_or_none()
        if result is None:
            await self._raise_transition_error(notification_id, worker_id)
        return result

    async def _finish(
        self,
        *,
        notification_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        status: NotificationStatus,
        next_attempt_at: datetime | None,
        sent_at: datetime | None,
        error_code: str | None,
        error_summary: str | None,
    ) -> NotificationLog:
        now = require_utc(now)
        _validate_worker_id(worker_id)
        return await self._owned_update(
            notification_id=notification_id,
            worker_id=worker_id,
            now=now,
            values={
                "status": status.value,
                "next_attempt_at": next_attempt_at,
                "claimed_by": None,
                "claimed_at": None,
                "lease_expires_at": None,
                "finished_at": None if status is NotificationStatus.PENDING else now,
                "sent_at": sent_at,
                "error_code": error_code,
                "error_summary": error_summary,
            },
            require_live_lease=False,
        )

    async def _owned_update(
        self,
        *,
        notification_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        values: dict[str, object],
        require_live_lease: bool,
    ) -> NotificationLog:
        conditions = [
            NotificationLog.id == notification_id,
            NotificationLog.status == NotificationStatus.PROCESSING.value,
            NotificationLog.claimed_by == worker_id,
        ]
        if require_live_lease:
            conditions.append(NotificationLog.lease_expires_at >= now)
        statement = (
            update(NotificationLog)
            .where(*conditions)
            .values(**values)
            .returning(NotificationLog)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        result = (await self._session.execute(statement)).scalar_one_or_none()
        if result is None:
            await self._raise_transition_error(notification_id, worker_id)
        return result

    async def _raise_transition_error(
        self, notification_id: int, worker_id: uuid.UUID | None
    ) -> None:
        notification = await self._session.get(
            NotificationLog, notification_id, populate_existing=True
        )
        if notification is None:
            raise RepositoryNotFoundError("notification log was not found")
        if worker_id is not None and notification.claimed_by != worker_id:
            raise RepositoryOwnershipError("notification log belongs to another worker")
        raise RepositoryStateConflictError("notification log state does not permit the update")


class NotificationAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_latest(self, *, notification_id: int) -> NotificationAttempt | None:
        return (
            await self._session.execute(
                select(NotificationAttempt)
                .where(NotificationAttempt.notification_log_id == notification_id)
                .order_by(NotificationAttempt.attempt_number.desc(), NotificationAttempt.id.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def mark_sending(
        self, *, attempt_id: int, worker_id: uuid.UUID, now: datetime
    ) -> NotificationAttempt:
        return await self._transition(
            attempt_id=attempt_id,
            worker_id=worker_id,
            expected=(NotificationAttemptStatus.CLAIMED,),
            target=NotificationAttemptStatus.SENDING,
            now=now,
            values={"send_started_at": now, "updated_at": now},
        )

    async def mark_succeeded(
        self,
        *,
        attempt_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        message_id: int,
    ) -> NotificationAttempt:
        message_id = validate_message_id(message_id)
        return await self._transition(
            attempt_id=attempt_id,
            worker_id=worker_id,
            expected=(NotificationAttemptStatus.SENDING,),
            target=NotificationAttemptStatus.SUCCEEDED,
            now=now,
            values={
                "finished_at": now,
                "discord_message_id": message_id,
                "updated_at": now,
            },
        )

    async def mark_failed(
        self,
        *,
        attempt_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        error_kind: NotificationErrorKind,
        error_code: str,
        error_summary: str,
    ) -> NotificationAttempt:
        error_kind = NotificationErrorKind(error_kind)
        if error_kind is NotificationErrorKind.UNKNOWN:
            raise ValueError("unknown attempts must use mark_unknown")
        return await self._transition(
            attempt_id=attempt_id,
            worker_id=worker_id,
            expected=(NotificationAttemptStatus.CLAIMED, NotificationAttemptStatus.SENDING),
            target=NotificationAttemptStatus.FAILED,
            now=now,
            values={
                "finished_at": now,
                "error_kind": error_kind.value,
                "error_code": validate_safe_error_text(error_code, field="error_code", maximum=64),
                "error_summary": validate_safe_error_text(
                    error_summary, field="error_summary", maximum=500
                ),
                "updated_at": now,
            },
        )

    async def mark_unknown(
        self,
        *,
        attempt_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        error_code: str,
        error_summary: str,
    ) -> NotificationAttempt:
        return await self._transition(
            attempt_id=attempt_id,
            worker_id=worker_id,
            expected=(NotificationAttemptStatus.SENDING,),
            target=NotificationAttemptStatus.UNKNOWN,
            now=now,
            values={
                "finished_at": now,
                "error_kind": NotificationErrorKind.UNKNOWN.value,
                "error_code": validate_safe_error_text(error_code, field="error_code", maximum=64),
                "error_summary": validate_safe_error_text(
                    error_summary, field="error_summary", maximum=500
                ),
                "updated_at": now,
            },
        )

    async def _transition(
        self,
        *,
        attempt_id: int,
        worker_id: uuid.UUID,
        expected: tuple[NotificationAttemptStatus, ...],
        target: NotificationAttemptStatus,
        now: datetime,
        values: dict[str, object],
    ) -> NotificationAttempt:
        now = require_utc(now)
        _validate_worker_id(worker_id)
        statement = (
            update(NotificationAttempt)
            .where(
                NotificationAttempt.id == attempt_id,
                NotificationAttempt.status.in_(tuple(item.value for item in expected)),
                NotificationAttempt.claimed_by == worker_id,
                NotificationAttempt.claimed_at <= now,
            )
            .values(status=target.value, **values)
            .returning(NotificationAttempt)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        result = (await self._session.execute(statement)).scalar_one_or_none()
        if result is None:
            attempt = await self._session.get(
                NotificationAttempt, attempt_id, populate_existing=True
            )
            if attempt is None:
                raise RepositoryNotFoundError("notification attempt was not found")
            if attempt.claimed_by != worker_id:
                raise RepositoryOwnershipError("notification attempt belongs to another worker")
            raise RepositoryStateConflictError(
                "notification attempt state does not permit the update"
            )
        return result


def _validate_new_notification(notification: NotificationLog) -> None:
    if (
        not isinstance(notification.deduplication_key, str)
        or not 1 <= len(notification.deduplication_key) <= 160
    ):
        raise ValueError("deduplication_key must contain 1-160 characters")
    notification.scheduled_at = require_utc(notification.scheduled_at)
    if notification.next_attempt_at is None:
        raise ValueError("new pending notification requires next_attempt_at")
    notification.next_attempt_at = require_utc(notification.next_attempt_at)
    if notification.next_attempt_at < notification.scheduled_at:
        raise ValueError("next_attempt_at must not precede scheduled_at")
    if notification.status != NotificationStatus.PENDING.value or notification.attempt_count != 0:
        raise ValueError("new notification must be an unclaimed pending route")
    if notification.recipient_id is not None:
        _validate_positive_bigint(notification.recipient_id, field="recipient_id")


def _same_logical_route(
    existing: NotificationLog,
    candidate: NotificationLog,
    *,
    compare_scheduled_at: bool,
) -> bool:
    fields = [
        "schedule_id",
        "schedule_run_id",
        "notification_type",
        "recipient_type",
        "recipient_id",
        "deduplication_key",
    ]
    if compare_scheduled_at:
        fields.append("scheduled_at")
    return all(getattr(existing, field) == getattr(candidate, field) for field in fields)


def _validate_positive_bigint(value: int, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_POSTGRES_BIGINT
    ):
        raise ValueError(f"{field} must be a positive PostgreSQL BIGINT")
    return value


def _validate_worker_id(worker_id: uuid.UUID) -> None:
    if not isinstance(worker_id, uuid.UUID):
        raise TypeError("worker_id must be a UUID")


def _validate_batch_size(batch_size: int) -> None:
    if isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_NOTIFICATION_CLAIM_BATCH_SIZE:
        raise ValueError("batch_size must be between 1 and 20")
