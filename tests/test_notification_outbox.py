import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from discord_ai_reminder_bot.domain.enums import NotificationRecipientType, NotificationType
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.domain.notification import (
    NotificationDecisionAction,
    NotificationOutcome,
    decide_notification_result,
    global_notification_deduplication_key,
    notification_deduplication_key,
)
from discord_ai_reminder_bot.domain.safe_text import validate_safe_error_text
from discord_ai_reminder_bot.infrastructure.database.notification_repositories import (
    build_due_notification_claim_statement,
    build_expired_notification_statement,
)

NOW = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)


def key(*, recipient: NotificationRecipientType = NotificationRecipientType.CREATOR_DM) -> str:
    return notification_deduplication_key(
        event_kind="draft_due",
        schedule_public_id=uuid.uuid7(),
        scheduled_for=NOW,
        notification_type=NotificationType.DRAFT_24H,
        recipient_type=recipient,
    )


def test_deduplication_key_is_canonical_bounded_and_route_specific() -> None:
    public_id = uuid.uuid7()
    creator = notification_deduplication_key(
        event_kind="draft_due",
        schedule_public_id=public_id,
        scheduled_for=NOW,
        notification_type=NotificationType.DRAFT_24H,
        recipient_type=NotificationRecipientType.CREATOR_DM,
    )
    same = notification_deduplication_key(
        event_kind="draft_due",
        schedule_public_id=public_id,
        scheduled_for=NOW,
        notification_type=NotificationType.DRAFT_24H,
        recipient_type=NotificationRecipientType.CREATOR_DM,
    )
    fallback = notification_deduplication_key(
        event_kind="draft_due",
        schedule_public_id=public_id,
        scheduled_for=NOW,
        notification_type=NotificationType.DRAFT_24H,
        recipient_type=NotificationRecipientType.OPERATOR_CHANNEL,
    )
    assert creator == same and creator != fallback
    assert len(creator) <= 160
    assert str(public_id) in creator and "20260819T030000.000000Z" in creator


def test_global_key_uses_public_event_uuid() -> None:
    value = global_notification_deduplication_key(
        event_kind="database_recovery",
        event_public_id=uuid.uuid7(),
        occurred_at=NOW,
        notification_type=NotificationType.RECOVERY,
        recipient_type=NotificationRecipientType.LOG,
    )
    assert value.startswith("v1g|database_recovery|")
    assert len(value) <= 160


@pytest.mark.parametrize(
    "kwargs",
    [
        {"event_kind": "bad|event", "schedule_public_id": uuid.uuid7(), "scheduled_for": NOW},
        {"event_kind": "draft", "schedule_public_id": uuid.uuid4(), "scheduled_for": NOW},
        {
            "event_kind": "draft",
            "schedule_public_id": uuid.uuid7(),
            "scheduled_for": datetime(2026, 8, 19, 3, 0),  # noqa: DTZ001
        },
        {
            "event_kind": "draft",
            "schedule_public_id": uuid.uuid7(),
            "scheduled_for": NOW.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Tokyo")),
        },
    ],
)
def test_deduplication_key_rejects_unsafe_identity(kwargs: dict[str, object]) -> None:
    with pytest.raises((ValueError, InvalidDateTimeError)):
        notification_deduplication_key(
            **kwargs,
            notification_type=NotificationType.DRAFT_1H,
            recipient_type=NotificationRecipientType.CREATOR_DM,
        )


@pytest.mark.parametrize(
    ("attempt", "outcome", "delay", "action"),
    [
        (1, NotificationOutcome.TRANSIENT, timedelta(minutes=1), NotificationDecisionAction.RETRY),
        (2, NotificationOutcome.TRANSIENT, timedelta(minutes=5), NotificationDecisionAction.RETRY),
        (
            3,
            NotificationOutcome.TRANSIENT,
            None,
            NotificationDecisionAction.FINAL_FAILED_WITH_FALLBACK_ALLOWED,
        ),
        (
            1,
            NotificationOutcome.PERMANENT,
            None,
            NotificationDecisionAction.FINAL_FAILED_WITH_FALLBACK_ALLOWED,
        ),
        (
            1,
            NotificationOutcome.UNKNOWN,
            None,
            NotificationDecisionAction.UNKNOWN_WITHOUT_FALLBACK,
        ),
        (1, NotificationOutcome.SUCCEEDED, None, NotificationDecisionAction.SUCCEEDED),
    ],
)
def test_notification_retry_decisions(attempt, outcome, delay, action) -> None:
    decision = decide_notification_result(attempt_number=attempt, outcome=outcome, decided_at=NOW)
    assert decision.action is action
    assert decision.next_attempt_at == (NOW + delay if delay else None)


def test_rate_limit_requires_future_utc_retry_after() -> None:
    retry_at = NOW + timedelta(seconds=30)
    decision = decide_notification_result(
        attempt_number=1,
        outcome=NotificationOutcome.RATE_LIMITED,
        decided_at=NOW,
        retry_at=retry_at,
    )
    assert decision.action is NotificationDecisionAction.RETRY
    assert decision.next_attempt_at == retry_at
    for invalid in (None, NOW, NOW - timedelta(microseconds=1)):
        with pytest.raises(ValueError):
            decide_notification_result(
                attempt_number=1,
                outcome=NotificationOutcome.RATE_LIMITED,
                decided_at=NOW,
                retry_at=invalid,
            )


def test_third_rate_limit_is_final_even_with_future_retry_after() -> None:
    decision = decide_notification_result(
        attempt_number=3,
        outcome=NotificationOutcome.RATE_LIMITED,
        decided_at=NOW,
        retry_at=NOW + timedelta(minutes=10),
    )
    assert decision.action is NotificationDecisionAction.FINAL_FAILED_WITH_FALLBACK_ALLOWED
    assert decision.next_attempt_at is None


@pytest.mark.parametrize("attempt", [0, 4, True])
def test_retry_rejects_invalid_attempt(attempt: int) -> None:
    with pytest.raises(ValueError):
        decide_notification_result(
            attempt_number=attempt,
            outcome=NotificationOutcome.TRANSIENT,
            decided_at=NOW,
        )


@pytest.mark.parametrize(
    "value",
    [
        "https://example.test/failure",
        "postgresql+psycopg://user:password@localhost/db",
        "token was rejected",
        "Traceback (most recent call last)",
    ],
)
def test_safe_error_text_rejects_secrets_urls_and_tracebacks(value: str) -> None:
    with pytest.raises(ValueError):
        validate_safe_error_text(value, field="error_summary", maximum=500)


def test_notification_claim_statements_are_stable_skip_locked_queries() -> None:
    due = build_due_notification_claim_statement(now=NOW, batch_size=20).compile(
        dialect=postgresql.dialect()
    )
    sql = str(due).upper()
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "NEXT_ATTEMPT_AT" in sql and "SCHEDULED_AT" in sql
    assert "ORDER BY" in sql and "LIMIT" in sql
    expired = build_expired_notification_statement(now=NOW, batch_size=20).compile(
        dialect=postgresql.dialect()
    )
    assert "FOR UPDATE SKIP LOCKED" in str(expired).upper()


@pytest.mark.parametrize("batch_size", [0, 21, True])
def test_notification_claim_rejects_invalid_batch(batch_size: int) -> None:
    with pytest.raises(ValueError):
        build_due_notification_claim_statement(now=NOW, batch_size=batch_size)
