"""Discord-independent outbound message boundary for the polling worker."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from discord_ai_reminder_bot.domain.recurrence import require_utc


@dataclass(frozen=True)
class AllowedMentionsPolicy:
    """Safe Phase 1 defaults; the eventual Discord adapter must enforce them."""

    allow_everyone: bool = False
    allow_roles: bool = False
    allow_users: bool = False
    replied_user: bool = False


SAFE_ALLOWED_MENTIONS = AllowedMentionsPolicy()


@dataclass(frozen=True)
class OutboundMessage:
    guild_id: int
    channel_id: int
    content: str
    schedule_public_id: UUID
    schedule_run_id: int
    allowed_mentions: AllowedMentionsPolicy = SAFE_ALLOWED_MENTIONS


@runtime_checkable
class MessageGateway(Protocol):
    async def send(self, message: OutboundMessage) -> int:
        """Send one message and return its positive Discord message ID."""
        ...


class GatewayError(Exception):
    """Base class for deliberately classified, safe outbound failures."""

    error_code = "gateway_error"
    safe_summary = "Discord delivery failed"


class TransientGatewayError(GatewayError):
    error_code = "transient_gateway_error"
    safe_summary = "Discord delivery failed temporarily before a result was returned"


class PermanentGatewayError(GatewayError):
    error_code = "permanent_gateway_error"
    safe_summary = "Discord delivery was permanently rejected"


class UnknownGatewayError(GatewayError):
    """The adapter cannot prove that Discord did not accept the message."""

    error_code = "delivery_result_unknown"
    safe_summary = "Discord delivery result is unknown"


class RateLimitGatewayError(GatewayError):
    error_code = "discord_rate_limited"
    safe_summary = "Discord rate limit requires a later retry"

    def __init__(self, retry_at: datetime) -> None:
        super().__init__(self.safe_summary)
        self.retry_at = require_utc(retry_at)
