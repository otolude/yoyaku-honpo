"""Discord-independent notification delivery boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from discord_ai_reminder_bot.application.gateway import AllowedMentionsPolicy
from discord_ai_reminder_bot.config import MAX_POSTGRES_BIGINT
from discord_ai_reminder_bot.domain.enums import NotificationRecipientType, NotificationType
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.domain.safe_text import validate_safe_error_text


@dataclass(frozen=True)
class NotificationMessage:
    notification_type: NotificationType
    recipient_type: NotificationRecipientType
    recipient_id: int | None
    content: str
    allowed_mentions: AllowedMentionsPolicy

    def __post_init__(self) -> None:
        object.__setattr__(self, "notification_type", NotificationType(self.notification_type))
        route = NotificationRecipientType(self.recipient_type)
        object.__setattr__(self, "recipient_type", route)
        content = validate_safe_error_text(self.content, field="notification content", maximum=2000)
        if any(marker in content.casefold() for marker in ("@everyone", "@here", "<@", "<@&")):
            raise ValueError("notification content contains an active mention form")
        object.__setattr__(self, "content", content)
        if route is NotificationRecipientType.LOG:
            if self.recipient_id is not None:
                raise ValueError("log notifications cannot have a recipient ID")
        elif (
            isinstance(self.recipient_id, bool)
            or not isinstance(self.recipient_id, int)
            or not 1 <= self.recipient_id <= MAX_POSTGRES_BIGINT
        ):
            raise ValueError("Discord notification recipient must be a positive BIGINT")
        if any(
            (
                self.allowed_mentions.allow_everyone,
                self.allowed_mentions.allow_roles,
                self.allowed_mentions.allow_users,
                self.allowed_mentions.replied_user,
            )
        ):
            raise ValueError("notification mentions must all be disabled")


@runtime_checkable
class NotificationGateway(Protocol):
    async def send(self, message: NotificationMessage) -> int | None:
        """Send once, or return None for the log-only route."""
        ...


class NotificationGatewayError(Exception):
    error_code = "notification_gateway_error"
    safe_summary = "Notification delivery failed"


class NotificationTransientError(NotificationGatewayError):
    error_code = "notification_transient"
    safe_summary = "Notification delivery failed before sending"


class NotificationPermanentError(NotificationGatewayError):
    error_code = "notification_permanent"
    safe_summary = "Notification delivery was permanently rejected"


class NotificationUnknownError(NotificationGatewayError):
    error_code = "notification_result_unknown"
    safe_summary = "Notification delivery result is unknown"


class NotificationRateLimitError(NotificationGatewayError):
    error_code = "notification_rate_limited"
    safe_summary = "Notification rate limit requires a later retry"

    def __init__(self, retry_at: datetime) -> None:
        super().__init__(self.safe_summary)
        self.retry_at = require_utc(retry_at)
