import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from discord_ai_reminder_bot.application.gateway import (
    MessageGateway,
    OutboundMessage,
    PermanentGatewayError,
    RateLimitGatewayError,
    UnknownGatewayError,
)
from discord_ai_reminder_bot.config import MAX_POSTGRES_BIGINT
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.infrastructure.discord.gateway import DiscordMessageGateway

NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
CONTENT = "private scheduled content"
TOKEN = "private-bot-token"
DATABASE_URL = "postgresql+psycopg://user:password@localhost/database"


class Response:
    def __init__(self, status: int, *, retry_after: str | None = None) -> None:
        self.status = status
        self.reason = "safe reason"
        self.headers = {} if retry_after is None else {"Retry-After": retry_after}


def outbound(*, guild_id: int = 100, channel_id: int = 200) -> OutboundMessage:
    return OutboundMessage(
        guild_id=guild_id,
        channel_id=channel_id,
        content=CONTENT,
        schedule_public_id=uuid.uuid7(),
        schedule_run_id=300,
    )


def setup_gateway(
    *,
    channel_type: type = discord.TextChannel,
    configured_guild_id: int = 100,
    guild_id: int = 100,
    channel_guild_id: int = 100,
    view_channel: bool = True,
    send_messages: bool = True,
):
    client = MagicMock(spec=discord.Client)
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    member = MagicMock(spec=discord.Member)
    guild.me = member
    channel = MagicMock(spec=channel_type)
    channel.guild = MagicMock(spec=discord.Guild)
    channel.guild.id = channel_guild_id
    permissions = MagicMock(spec=discord.Permissions)
    permissions.view_channel = view_channel
    permissions.send_messages = send_messages
    channel.permissions_for.return_value = permissions
    channel.send = AsyncMock(return_value=MagicMock(id=9001))
    guild.get_channel.return_value = channel
    client.get_guild.return_value = guild
    gateway = DiscordMessageGateway(
        client=client,
        configured_guild_id=configured_guild_id,
        clock=FixedClock(NOW),
    )
    return gateway, client, guild, channel


@pytest.mark.asyncio
async def test_sends_once_to_cached_text_channel_with_explicit_mentions() -> None:
    gateway, client, guild, channel = setup_gateway()
    assert isinstance(gateway, MessageGateway)

    assert await gateway.send(outbound()) == 9001

    channel.send.assert_awaited_once()
    args, kwargs = channel.send.await_args
    assert args == (CONTENT,)
    assert set(kwargs) == {"allowed_mentions"}
    mentions = kwargs["allowed_mentions"]
    assert mentions.everyone is False
    assert mentions.roles is False
    assert mentions.users is False
    assert mentions.replied_user is False
    client.get_guild.assert_called_once_with(100)
    guild.get_channel.assert_called_once_with(200)
    client.fetch_channel.assert_not_called()


@pytest.mark.parametrize(
    "case",
    ["configured_mismatch", "guild_missing", "guild_mismatch", "channel_missing"],
)
@pytest.mark.asyncio
async def test_missing_or_mismatched_cached_objects_are_permanent(case: str) -> None:
    gateway, client, guild, channel = setup_gateway()
    message = outbound()
    if case == "configured_mismatch":
        message = outbound(guild_id=101)
    elif case == "guild_missing":
        client.get_guild.return_value = None
    elif case == "guild_mismatch":
        guild.id = 101
    else:
        guild.get_channel.return_value = None

    with pytest.raises(PermanentGatewayError):
        await gateway.send(message)
    channel.send.assert_not_awaited()


@pytest.mark.parametrize(
    "channel_type",
    [discord.DMChannel, discord.CategoryChannel, discord.VoiceChannel, discord.Thread],
)
@pytest.mark.asyncio
async def test_non_text_channels_are_rejected(channel_type: type) -> None:
    gateway, _, _, channel = setup_gateway(channel_type=channel_type)
    with pytest.raises(PermanentGatewayError):
        await gateway.send(outbound())
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_from_another_guild_is_rejected() -> None:
    gateway, _, _, channel = setup_gateway(channel_guild_id=999)
    with pytest.raises(PermanentGatewayError):
        await gateway.send(outbound())
    channel.send.assert_not_awaited()


@pytest.mark.parametrize("missing", ["member", "view_channel", "send_messages"])
@pytest.mark.asyncio
async def test_missing_member_or_permissions_are_permanent(missing: str) -> None:
    gateway, _, guild, channel = setup_gateway(
        view_channel=missing != "view_channel",
        send_messages=missing != "send_messages",
    )
    if missing == "member":
        guild.me = None
    with pytest.raises(PermanentGatewayError):
        await gateway.send(outbound())
    channel.send.assert_not_awaited()


@pytest.mark.parametrize("exception_type", [discord.Forbidden, discord.NotFound])
@pytest.mark.asyncio
async def test_known_rejections_are_permanent(exception_type: type[discord.HTTPException]) -> None:
    gateway, _, _, channel = setup_gateway()
    channel.send.side_effect = exception_type(Response(403), {"code": 50013})
    with pytest.raises(PermanentGatewayError):
        await gateway.send(outbound())
    channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_4xx_is_permanent() -> None:
    gateway, _, _, channel = setup_gateway()
    channel.send.side_effect = discord.HTTPException(Response(400), {"code": 50035})
    with pytest.raises(PermanentGatewayError):
        await gateway.send(outbound())


@pytest.mark.asyncio
async def test_rate_limit_is_converted_without_sleep_or_retry(monkeypatch) -> None:
    gateway, _, _, channel = setup_gateway()
    channel.send.side_effect = discord.RateLimited(12.5)
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    with pytest.raises(RateLimitGatewayError) as captured:
        await gateway.send(outbound())
    assert captured.value.retry_at == NOW + timedelta(seconds=12.5)
    channel.send.assert_awaited_once()
    sleep.assert_not_awaited()


@pytest.mark.parametrize("retry_after", ["3.5", "0", "nan", "invalid", None])
@pytest.mark.asyncio
async def test_http_429_uses_only_valid_retry_after_header(retry_after: str | None) -> None:
    gateway, _, _, channel = setup_gateway()
    channel.send.side_effect = discord.HTTPException(
        Response(429, retry_after=retry_after), {"code": 0}
    )
    expected = RateLimitGatewayError if retry_after == "3.5" else UnknownGatewayError
    with pytest.raises(expected):
        await gateway.send(outbound())


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        ConnectionResetError(),
        discord.HTTPException(Response(500), {"code": 0}),
        ValueError("unexpected"),
    ],
)
@pytest.mark.asyncio
async def test_uncertain_send_failures_are_unknown(error: Exception) -> None:
    gateway, _, _, channel = setup_gateway()
    channel.send.side_effect = error
    with pytest.raises(UnknownGatewayError) as captured:
        await gateway.send(outbound())
    assert CONTENT not in str(captured.value)
    assert TOKEN not in str(captured.value)
    assert DATABASE_URL not in str(captured.value)
    channel.send.assert_awaited_once()


@pytest.mark.parametrize("message_id", [0, -1, MAX_POSTGRES_BIGINT + 1, True, "9001"])
@pytest.mark.asyncio
async def test_invalid_message_id_is_unknown(message_id: object) -> None:
    gateway, _, _, channel = setup_gateway()
    channel.send.return_value = MagicMock(id=message_id)
    with pytest.raises(UnknownGatewayError):
        await gateway.send(outbound())
    channel.send.assert_awaited_once()
