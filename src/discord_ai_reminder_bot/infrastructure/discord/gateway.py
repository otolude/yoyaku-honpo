"""discord.py adapter for the application message gateway."""

from __future__ import annotations

import math
from datetime import timedelta

import discord

from discord_ai_reminder_bot.application.gateway import (
    MessageGateway,
    OutboundMessage,
    PermanentGatewayError,
    RateLimitGatewayError,
    UnknownGatewayError,
)
from discord_ai_reminder_bot.config import MAX_POSTGRES_BIGINT
from discord_ai_reminder_bot.domain.clock import Clock
from discord_ai_reminder_bot.domain.recurrence import require_utc


class DiscordMessageGateway(MessageGateway):
    """Send through cached Phase 1 guild text channels without retrying."""

    def __init__(
        self,
        *,
        client: discord.Client,
        configured_guild_id: int,
        clock: Clock,
    ) -> None:
        if (
            isinstance(configured_guild_id, bool)
            or not 1 <= configured_guild_id <= MAX_POSTGRES_BIGINT
        ):
            raise ValueError("configured_guild_id must be a positive PostgreSQL BIGINT")
        self._client = client
        self._configured_guild_id = configured_guild_id
        self._clock = clock

    async def send(self, message: OutboundMessage) -> int:
        """Validate cached Discord objects, then make exactly one send call."""
        channel = self._resolve_channel(message)
        allowed_mentions = discord.AllowedMentions(
            everyone=message.allowed_mentions.allow_everyone,
            roles=message.allowed_mentions.allow_roles,
            users=message.allowed_mentions.allow_users,
            replied_user=message.allowed_mentions.replied_user,
        )

        try:
            sent = await channel.send(message.content, allowed_mentions=allowed_mentions)
        except (discord.Forbidden, discord.NotFound) as error:
            raise PermanentGatewayError() from error
        except discord.RateLimited as error:
            raise self._rate_limited(error.retry_after) from error
        except discord.HTTPException as error:
            raise self._classify_http(error) from error
        except Exception as error:
            raise UnknownGatewayError() from error

        message_id = getattr(sent, "id", None)
        if isinstance(message_id, bool) or not isinstance(message_id, int):
            raise UnknownGatewayError()
        if not 1 <= message_id <= MAX_POSTGRES_BIGINT:
            raise UnknownGatewayError()
        return message_id

    def _resolve_channel(self, message: OutboundMessage) -> discord.TextChannel:
        if message.guild_id != self._configured_guild_id:
            raise PermanentGatewayError()
        guild = self._client.get_guild(self._configured_guild_id)
        if guild is None or guild.id != message.guild_id:
            raise PermanentGatewayError()

        # Cache-only by design: avoid a second HTTP operation and reject stale IDs safely.
        channel = guild.get_channel(message.channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise PermanentGatewayError()
        if channel.guild.id != guild.id:
            raise PermanentGatewayError()

        member = guild.me
        if member is None:
            raise PermanentGatewayError()
        permissions = channel.permissions_for(member)
        if not permissions.view_channel or not permissions.send_messages:
            raise PermanentGatewayError()
        return channel

    def _classify_http(
        self, error: discord.HTTPException
    ) -> PermanentGatewayError | RateLimitGatewayError | UnknownGatewayError:
        status = error.status
        if status == 429:
            retry_after = _retry_after_header(error)
            if retry_after is not None:
                return self._rate_limited(retry_after)
            return UnknownGatewayError()
        if 400 <= status < 500:
            return PermanentGatewayError()
        return UnknownGatewayError()

    def _rate_limited(self, retry_after: float) -> RateLimitGatewayError:
        if isinstance(retry_after, bool) or not isinstance(retry_after, (int, float)):
            raise UnknownGatewayError()
        if not math.isfinite(retry_after) or retry_after <= 0:
            raise UnknownGatewayError()
        now = require_utc(self._clock.now())
        retry_at = now + timedelta(seconds=retry_after)
        if retry_at <= now:
            raise UnknownGatewayError()
        return RateLimitGatewayError(retry_at)


def _retry_after_header(error: discord.HTTPException) -> float | None:
    headers = getattr(error.response, "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After")
    try:
        parsed = float(value)
    except TypeError, ValueError:
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None
