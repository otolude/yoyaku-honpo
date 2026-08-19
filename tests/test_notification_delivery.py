import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from discord_ai_reminder_bot.application.gateway import SAFE_ALLOWED_MENTIONS
from discord_ai_reminder_bot.application.notification_gateway import (
    NotificationGateway,
    NotificationMessage,
    NotificationPermanentError,
)
from discord_ai_reminder_bot.application.notification_presenter import (
    NotificationPresentation,
    build_notification_message,
)
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.domain.enums import NotificationRecipientType, NotificationType
from discord_ai_reminder_bot.infrastructure.discord.notification_gateway import (
    DiscordNotificationGateway,
)

NOW = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)


def message(route=NotificationRecipientType.OPERATOR_CHANNEL, recipient_id=400):
    return NotificationMessage(
        notification_type=NotificationType.RECOVERY,
        recipient_type=route,
        recipient_id=recipient_id,
        content="通知処理の確認が必要です。",
        allowed_mentions=SAFE_ALLOWED_MENTIONS,
    )


def gateway():
    client = MagicMock(spec=discord.Client)
    guild = MagicMock(spec=discord.Guild)
    guild.id = 100
    guild.me = MagicMock(spec=discord.Member)
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild = guild
    permissions = MagicMock(spec=discord.Permissions)
    permissions.view_channel = True
    permissions.send_messages = True
    channel.permissions_for.return_value = permissions
    channel.send = AsyncMock(return_value=MagicMock(id=9001))
    guild.get_channel.return_value = channel
    client.get_guild.return_value = guild
    client.user = MagicMock(spec=discord.ClientUser, id=999, bot=True)
    adapter = DiscordNotificationGateway(
        client=client,
        configured_guild_id=100,
        operator_channel_id=400,
        operator_user_id=300,
        clock=FixedClock(NOW),
        logger=logging.getLogger("test.notification.gateway"),
    )
    return adapter, client, guild, channel


def test_notification_gateway_protocol_and_message_safety() -> None:
    adapter, *_ = gateway()
    assert isinstance(adapter, NotificationGateway)
    for unsafe in (" ", "@everyone ping", "token=secret", "https://example.test"):
        with pytest.raises(ValueError):
            NotificationMessage(
                notification_type=NotificationType.RECOVERY,
                recipient_type=NotificationRecipientType.LOG,
                recipient_id=None,
                content=unsafe,
                allowed_mentions=SAFE_ALLOWED_MENTIONS,
            )


def test_presenter_uses_fixed_content_without_post_body() -> None:
    private_body = "private post body must never appear"
    rendered = build_notification_message(
        NotificationPresentation(
            notification_type=NotificationType.DRAFT_1H,
            recipient_type=NotificationRecipientType.CREATOR_DM,
            recipient_id=200,
            schedule_public_id=uuid.uuid7(),
            scheduled_for=NOW,
            channel_id=500,
            current_status="draft@here",
        )
    )
    assert private_body not in rendered.content
    assert "@here" not in rendered.content
    assert len(rendered.content) <= 2000
    assert rendered.allowed_mentions == SAFE_ALLOWED_MENTIONS


@pytest.mark.asyncio
async def test_operator_channel_is_cached_validated_and_sent_once() -> None:
    adapter, client, guild, channel = gateway()
    assert await adapter.send(message()) == 9001
    client.get_guild.assert_called_once_with(100)
    guild.get_channel.assert_called_once_with(400)
    client.fetch_channel.assert_not_called()
    channel.send.assert_awaited_once()
    mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert mentions.everyone is False and mentions.roles is False and mentions.users is False


@pytest.mark.asyncio
async def test_operator_channel_rejects_wrong_fixed_id_before_send() -> None:
    adapter, _client, _guild, channel = gateway()
    with pytest.raises(NotificationPermanentError):
        await adapter.send(message(recipient_id=401))
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_log_route_never_uses_discord(caplog: pytest.LogCaptureFixture) -> None:
    adapter, client, _guild, channel = gateway()
    with caplog.at_level(logging.ERROR):
        assert await adapter.send(message(NotificationRecipientType.LOG, None)) is None
    client.get_guild.assert_not_called()
    client.fetch_user.assert_not_awaited()
    channel.send.assert_not_awaited()
    assert "notification_log_route_terminal" in caplog.text


@pytest.mark.asyncio
async def test_creator_dm_uses_cache_then_fetch_only_on_miss() -> None:
    adapter, client, _guild, _channel = gateway()
    user = MagicMock(spec=discord.User)
    user.bot = False
    user.send = AsyncMock(return_value=MagicMock(id=9002))
    client.get_user.return_value = user
    assert await adapter.send(message(NotificationRecipientType.CREATOR_DM, 200)) == 9002
    client.fetch_user.assert_not_awaited()
    user.send.assert_awaited_once()

    fetched = MagicMock(spec=discord.User)
    fetched.bot = False
    fetched.send = AsyncMock(return_value=MagicMock(id=9003))
    client.get_user.return_value = None
    client.fetch_user.return_value = fetched
    assert await adapter.send(message(NotificationRecipientType.OPERATOR_DM, 300)) == 9003
    client.fetch_user.assert_awaited_once_with(300)
    fetched.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_dm_rejects_bot_itself_and_wrong_operator() -> None:
    adapter, client, _guild, _channel = gateway()
    with pytest.raises(NotificationPermanentError):
        await adapter.send(message(NotificationRecipientType.CREATOR_DM, 999))
    with pytest.raises(NotificationPermanentError):
        await adapter.send(message(NotificationRecipientType.OPERATOR_DM, 301))
    client.fetch_user.assert_not_awaited()
