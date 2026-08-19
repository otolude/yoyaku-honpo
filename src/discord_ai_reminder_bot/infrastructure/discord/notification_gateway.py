"""discord.py adapter for notification routes."""

from __future__ import annotations

import logging
import math
from datetime import timedelta

import discord

from discord_ai_reminder_bot.application.notification_gateway import (
    NotificationGateway,
    NotificationMessage,
    NotificationPermanentError,
    NotificationRateLimitError,
    NotificationUnknownError,
)
from discord_ai_reminder_bot.config import MAX_POSTGRES_BIGINT
from discord_ai_reminder_bot.domain.clock import Clock
from discord_ai_reminder_bot.domain.enums import NotificationRecipientType
from discord_ai_reminder_bot.domain.recurrence import require_utc


class DiscordNotificationGateway(NotificationGateway):
    def __init__(
        self,
        *,
        client: discord.Client,
        configured_guild_id: int,
        operator_channel_id: int,
        operator_user_id: int,
        clock: Clock,
        logger: logging.Logger,
    ) -> None:
        for value in (configured_guild_id, operator_channel_id, operator_user_id):
            if isinstance(value, bool) or not 1 <= value <= MAX_POSTGRES_BIGINT:
                raise ValueError("configured Discord IDs must be positive BIGINT values")
        self._client = client
        self._guild_id = configured_guild_id
        self._operator_channel_id = operator_channel_id
        self._operator_user_id = operator_user_id
        self._clock = clock
        self._logger = logger

    async def send(self, message: NotificationMessage) -> int | None:
        route = message.recipient_type
        if route is NotificationRecipientType.LOG:
            self._logger.error("notification_log_route_terminal")
            return None
        allowed_mentions = discord.AllowedMentions.none()
        target = (
            self._operator_channel(message)
            if route is NotificationRecipientType.OPERATOR_CHANNEL
            else await self._dm_user(message)
        )
        try:
            sent = await target.send(message.content, allowed_mentions=allowed_mentions)
        except (discord.Forbidden, discord.NotFound) as error:
            raise NotificationPermanentError() from error
        except discord.RateLimited as error:
            raise self._rate_limited(error.retry_after) from error
        except discord.HTTPException as error:
            raise self._classify_http(error) from error
        except Exception as error:
            # Once send() has been invoked, the adapter cannot prove non-delivery.
            raise NotificationUnknownError() from error
        message_id = getattr(sent, "id", None)
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or not 1 <= message_id <= MAX_POSTGRES_BIGINT
        ):
            raise NotificationUnknownError()
        return message_id

    def _operator_channel(self, message: NotificationMessage) -> discord.TextChannel:
        if message.recipient_id != self._operator_channel_id:
            raise NotificationPermanentError()
        guild = self._client.get_guild(self._guild_id)
        if guild is None or guild.id != self._guild_id:
            raise NotificationPermanentError()
        channel = guild.get_channel(self._operator_channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != self._guild_id:
            raise NotificationPermanentError()
        member = guild.me
        if member is None:
            raise NotificationPermanentError()
        permissions = channel.permissions_for(member)
        if not permissions.view_channel or not permissions.send_messages:
            raise NotificationPermanentError()
        return channel

    async def _dm_user(self, message: NotificationMessage) -> discord.User | discord.Member:
        if (
            message.recipient_type is NotificationRecipientType.OPERATOR_DM
            and message.recipient_id != self._operator_user_id
        ):
            raise NotificationPermanentError()
        recipient_id = message.recipient_id
        assert recipient_id is not None
        bot_user = self._client.user
        if bot_user is not None and recipient_id == bot_user.id:
            raise NotificationPermanentError()
        user = self._client.get_user(recipient_id)
        if user is None:
            try:
                user = await self._client.fetch_user(recipient_id)
            except (discord.Forbidden, discord.NotFound) as error:
                raise NotificationPermanentError() from error
            except discord.HTTPException as error:
                raise self._classify_http(error) from error
            except Exception as error:
                # Fetch happens before message send, but unclassified failures are
                # deliberately not assumed transient.
                raise NotificationUnknownError() from error
        if not isinstance(user, (discord.User, discord.Member)) or user.bot:
            raise NotificationPermanentError()
        return user

    def _classify_http(
        self, error: discord.HTTPException
    ) -> NotificationPermanentError | NotificationRateLimitError | NotificationUnknownError:
        if error.status == 429:
            retry_after = _retry_after_header(error)
            return (
                self._rate_limited(retry_after)
                if retry_after is not None
                else NotificationUnknownError()
            )
        if 400 <= error.status < 500:
            return NotificationPermanentError()
        return NotificationUnknownError()

    def _rate_limited(self, retry_after: float) -> NotificationRateLimitError:
        if (
            isinstance(retry_after, bool)
            or not isinstance(retry_after, (int, float))
            or not math.isfinite(retry_after)
            or retry_after <= 0
        ):
            raise NotificationUnknownError()
        now = require_utc(self._clock.now())
        return NotificationRateLimitError(now + timedelta(seconds=retry_after))


def _retry_after_header(error: discord.HTTPException) -> float | None:
    headers = getattr(error.response, "headers", None)
    if headers is None:
        return None
    try:
        value = float(headers.get("Retry-After"))
    except TypeError, ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None
