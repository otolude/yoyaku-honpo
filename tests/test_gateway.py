from datetime import UTC, datetime

import pytest

from discord_ai_reminder_bot.application.gateway import (
    SAFE_ALLOWED_MENTIONS,
    RateLimitGatewayError,
)
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError


def test_safe_allowed_mentions_denies_all_implicit_mentions() -> None:
    assert not SAFE_ALLOWED_MENTIONS.allow_everyone
    assert not SAFE_ALLOWED_MENTIONS.allow_roles
    assert not SAFE_ALLOWED_MENTIONS.allow_users
    assert not SAFE_ALLOWED_MENTIONS.replied_user


def test_rate_limit_requires_utc_retry_at() -> None:
    retry_at = datetime(2026, 8, 18, 1, 0, tzinfo=UTC)
    assert RateLimitGatewayError(retry_at).retry_at == retry_at
    with pytest.raises(InvalidDateTimeError):
        RateLimitGatewayError(datetime(2026, 8, 18, 1, 0))  # noqa: DTZ001
