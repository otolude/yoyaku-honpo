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
class NotificationEmbedField:
    name: str
    value: str
    inline: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "name", _safe_embed_text(self.name, field="embed field name", maximum=256)
        )
        object.__setattr__(
            self, "value", _safe_embed_text(self.value, field="embed field value", maximum=1024)
        )


@dataclass(frozen=True)
class NotificationEmbed:
    title: str
    description: str
    color: int
    fields: tuple[NotificationEmbedField, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "title", _safe_embed_text(self.title, field="embed title", maximum=256)
        )
        object.__setattr__(
            self,
            "description",
            _safe_embed_text(self.description, field="embed description", maximum=4096),
        )
        if (
            isinstance(self.color, bool)
            or not isinstance(self.color, int)
            or not 0 <= self.color <= 0xFFFFFF
        ):
            raise ValueError("embed color must be an RGB integer")
        fields = tuple(self.fields)
        if len(fields) > 25 or not all(isinstance(item, NotificationEmbedField) for item in fields):
            raise ValueError("embed fields must contain at most 25 NotificationEmbedField values")
        object.__setattr__(self, "fields", fields)
        total = (
            len(self.title)
            + len(self.description)
            + sum(len(item.name) + len(item.value) for item in fields)
        )
        if total > 6000:
            raise ValueError("embed text exceeds Discord limit")


@dataclass(frozen=True)
class NotificationMessage:
    notification_type: NotificationType
    recipient_type: NotificationRecipientType
    recipient_id: int | None
    allowed_mentions: AllowedMentionsPolicy
    content: str | None = None
    embed: NotificationEmbed | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "notification_type", NotificationType(self.notification_type))
        route = NotificationRecipientType(self.recipient_type)
        object.__setattr__(self, "recipient_type", route)
        if (self.content is None) == (self.embed is None):
            raise ValueError("notification message requires exactly one of content or embed")
        if self.content is not None:
            content = _safe_embed_text(self.content, field="notification content", maximum=2000)
            object.__setattr__(self, "content", content)
        if self.embed is not None and not isinstance(self.embed, NotificationEmbed):
            raise TypeError("notification embed must be a NotificationEmbed")
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


def _safe_embed_text(value: str, *, field: str, maximum: int) -> str:
    text = validate_safe_error_text(value, field=field, maximum=maximum)
    if any(marker in text.casefold() for marker in ("@everyone", "@here", "<@", "<@&")):
        raise ValueError(f"{field} contains an active mention form")
    return text


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
