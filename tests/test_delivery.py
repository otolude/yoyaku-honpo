import uuid
from datetime import UTC, datetime, timedelta

import pytest

from discord_ai_reminder_bot.application.delivery import (
    RESULT_RETRY_PENDING,
    DeliveryService,
    validate_message_id,
    validate_safe_error_text,
)
from discord_ai_reminder_bot.config import MAX_POSTGRES_BIGINT
from discord_ai_reminder_bot.domain.enums import DeliveryErrorKind
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError

NOW = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize("message_id", [0, -1, MAX_POSTGRES_BIGINT + 1, True])
def test_message_id_validation(message_id: int) -> None:
    with pytest.raises(ValueError):
        validate_message_id(message_id)
    assert validate_message_id(MAX_POSTGRES_BIGINT) == MAX_POSTGRES_BIGINT


@pytest.mark.parametrize(
    ("value", "maximum"),
    [
        ("", 64),
        ("x" * 65, 64),
        ("postgresql+psycopg://user:secret@localhost/db", 500),
        ("Discord token=secret-value", 500),
        ("Traceback (most recent call last)", 500),
    ],
)
def test_unsafe_error_text_is_rejected(value: str, maximum: int) -> None:
    with pytest.raises(ValueError):
        validate_safe_error_text(value, field="error", maximum=maximum)


def test_safe_error_text_is_trimmed() -> None:
    assert validate_safe_error_text("  timeout  ", field="error", maximum=64) == "timeout"


@pytest.mark.asyncio
async def test_delivery_service_uses_domain_retry_decision() -> None:
    class Attempts:
        async def mark_failed(self, **kwargs):
            return type("Attempt", (), {"attempt_number": 1, "schedule_run_id": 10})()

    class Runs:
        async def mark_failed_or_pending(self, **kwargs):
            assert kwargs["retry_at"] == NOW + timedelta(minutes=1)
            assert kwargs["result_code"] == RESULT_RETRY_PENDING
            return type("Run", (), {})()

    service = DeliveryService.__new__(DeliveryService)
    service._attempts = Attempts()  # type: ignore[attr-defined]
    service._runs = Runs()  # type: ignore[attr-defined]
    await service.complete_failure(
        attempt_id=1,
        worker_id=uuid.uuid7(),
        now=NOW,
        error_kind=DeliveryErrorKind.TRANSIENT,
        error_code="timeout",
        error_summary="temporary timeout",
    )


@pytest.mark.asyncio
async def test_delivery_service_rejects_naive_time_before_database_access() -> None:
    service = DeliveryService.__new__(DeliveryService)
    with pytest.raises(InvalidDateTimeError):
        await service.start_sending(
            attempt_id=1,
            worker_id=uuid.uuid7(),
            now=datetime(2026, 8, 17, 3, 0),  # noqa: DTZ001
        )
