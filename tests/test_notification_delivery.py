import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from discord_ai_reminder_bot.application.gateway import SAFE_ALLOWED_MENTIONS
from discord_ai_reminder_bot.application.notification_gateway import (
    NotificationEmbed,
    NotificationEmbedField,
    NotificationGateway,
    NotificationMessage,
    NotificationPermanentError,
    NotificationRateLimitError,
    NotificationUnknownError,
)
from discord_ai_reminder_bot.application.notification_presenter import (
    NotificationPresentation,
    build_notification_message,
)
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.domain.enums import NotificationRecipientType, NotificationType
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.infrastructure.discord.notification_gateway import (
    DiscordNotificationGateway,
)

NOW = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)


class Response:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "safe reason"
        self.headers = {}


def message(route=NotificationRecipientType.OPERATOR_CHANNEL, recipient_id=400):
    return NotificationMessage(
        notification_type=NotificationType.RECOVERY,
        recipient_type=route,
        recipient_id=recipient_id,
        allowed_mentions=SAFE_ALLOWED_MENTIONS,
        embed=NotificationEmbed(
            title="⚠️ 予約状態の確認が必要です",
            description="安全のため確認が必要です。",
            color=0xE67E22,
            fields=(NotificationEmbedField("📍 投稿先", "<#500>"),),
        ),
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
    schedule_id = uuid.uuid7()
    rendered = build_notification_message(
        NotificationPresentation(
            notification_type=NotificationType.DRAFT_1H,
            recipient_type=NotificationRecipientType.CREATOR_DM,
            recipient_id=200,
            schedule_public_id=schedule_id,
            scheduled_for=NOW,
            channel_id=500,
            current_status="draft",
        )
    )
    assert rendered.content is None
    assert rendered.embed is not None
    displayed = rendered.embed.description + "".join(
        field.name + field.value for field in rendered.embed.fields
    )
    assert private_body not in displayed
    assert "draft" not in displayed
    assert {field.name: field.value for field in rendered.embed.fields} == {
        "📌 状態": "下書き",
        "📍 投稿先": "<#500>",
        "🗓️ 投稿予定": "2026-08-19 12:00 JST",
        "🆔 予約ID": f"`{schedule_id}`",
        "ℹ️ 対応": "投稿する場合は、予定時刻までに予約本文を設定してください。",
    }
    assert rendered.allowed_mentions == SAFE_ALLOWED_MENTIONS


def test_presenter_keeps_fixed_fallback_explanation() -> None:
    rendered = build_notification_message(
        NotificationPresentation(
            NotificationType.RECOVERY,
            NotificationRecipientType.OPERATOR_DM,
            300,
            None,
            None,
            None,
            "recovery_required",
            is_fallback=True,
        )
    )
    assert rendered.embed is not None
    assert "代替経路" in rendered.embed.description


@pytest.mark.parametrize(
    ("notification_type", "result_code", "recurring_missed", "expected"),
    [
        (NotificationType.RUN_SKIPPED, "draft_without_content", False, "下書きのまま"),
        (NotificationType.RUN_FAILED, "delivery_failed", False, "最終的に失敗"),
        (NotificationType.RUN_FAILED, "delivery_result_unknown", False, "確認できない"),
        (NotificationType.RUN_FAILED, "startup_overdue", False, "15分を超過"),
        (NotificationType.RUN_DELAYED, None, False, "15分以内の遅延投稿"),
        (NotificationType.RUN_SKIPPED, None, True, "定期投稿を送信せず"),
        (NotificationType.RECOVERY, "startup_inconsistent_pending", False, "運営者による確認"),
    ],
)
def test_presenter_uses_safe_fixed_business_reason(
    notification_type: NotificationType,
    result_code: str | None,
    recurring_missed: bool,
    expected: str,
) -> None:
    rendered = build_notification_message(
        NotificationPresentation(
            notification_type=notification_type,
            recipient_type=NotificationRecipientType.OPERATOR_CHANNEL,
            recipient_id=400,
            schedule_public_id=uuid.uuid7(),
            scheduled_for=None if recurring_missed else NOW,
            channel_id=500,
            current_status="failed",
            result_code=result_code,
            recurring_missed=recurring_missed,
        )
    )
    assert rendered.embed is not None
    assert expected in rendered.embed.description
    displayed = rendered.embed.description + "".join(
        field.name + field.value for field in rendered.embed.fields
    )
    assert "private post body" not in displayed


def test_recovery_presenter_excludes_internal_and_secret_diagnostics() -> None:
    public_id = uuid.uuid7()
    forbidden = (
        "private recovery body",
        "internal_id=987654321",
        "worker_id=018f0000-0000-7000-8000-000000000001",
        "RuntimeError: private failure",
        "Traceback (most recent call last)",
        "private-bot-token",
        "postgresql+psycopg://private",
        '{"message":"private Discord response"}',
    )
    rendered = build_notification_message(
        NotificationPresentation(
            notification_type=NotificationType.RECOVERY,
            recipient_type=NotificationRecipientType.OPERATOR_CHANNEL,
            recipient_id=400,
            schedule_public_id=public_id,
            scheduled_for=NOW,
            channel_id=500,
            current_status="failed",
            result_code="startup_inconsistent_pending",
        )
    )
    assert rendered.content is None and rendered.embed is not None
    displayed = rendered.embed.description + "".join(
        field.name + field.value for field in rendered.embed.fields
    )
    assert all(value not in displayed for value in forbidden)
    assert "987654321" not in displayed
    assert "postgresql" not in displayed


@pytest.mark.parametrize(
    ("kind", "result_code", "recurring", "title", "color"),
    [
        (NotificationType.DRAFT_24H, None, False, "📝 下書きの投稿予定が近づいています", 0xF1C40F),
        (NotificationType.DRAFT_1H, None, False, "⏰ 下書きの投稿予定まで1時間です", 0xF1C40F),
        (
            NotificationType.DRAFT_IMMEDIATE,
            None,
            False,
            "⚠️ 下書きの投稿予定が近づいています",
            0xE67E22,
        ),
        (
            NotificationType.RUN_SKIPPED,
            "draft_without_content",
            False,
            "⏭️ 下書き投稿を見送りました",
            0x95A5A6,
        ),
        (
            NotificationType.RUN_FAILED,
            "delivery_failed",
            False,
            "❌ Discordへの投稿に失敗しました",
            0xE74C3C,
        ),
        (
            NotificationType.RUN_FAILED,
            "delivery_result_unknown",
            False,
            "⚠️ 投稿結果を確認できません",
            0xE67E22,
        ),
        (NotificationType.RUN_DELAYED, None, False, "🕒 遅延した予約投稿を処理します", 0x3498DB),
        (NotificationType.RUN_SKIPPED, None, True, "⏭️ 停止中の定期投稿を見送りました", 0x95A5A6),
        (NotificationType.RECOVERY, None, False, "⚠️ 予約状態の確認が必要です", 0xE67E22),
    ],
)
def test_presenter_selects_title_and_color(kind, result_code, recurring, title, color) -> None:
    rendered = build_notification_message(
        NotificationPresentation(
            notification_type=kind,
            recipient_type=NotificationRecipientType.OPERATOR_CHANNEL,
            recipient_id=400,
            schedule_public_id=None,
            scheduled_for=None,
            channel_id=None,
            current_status="skipped" if kind is NotificationType.RUN_SKIPPED else "failed",
            result_code=result_code,
            recurring_missed=recurring,
        )
    )
    assert rendered.embed is not None
    assert (rendered.embed.title, rendered.embed.color) == (title, color)
    fields = {field.name: field.value for field in rendered.embed.fields}
    assert "📍 投稿先" not in fields
    if kind is NotificationType.RUN_SKIPPED:
        assert fields["📌 状態"] == "見送り済み"


@pytest.mark.parametrize(
    ("status", "label"),
    [
        ("draft", "下書き"),
        ("active", "有効"),
        ("paused", "一時停止中"),
        ("completed", "完了"),
        ("failed", "失敗"),
        ("ended", "終了"),
        ("deleted", "削除済み"),
        ("pending", "待機中"),
        ("processing", "処理中"),
        ("succeeded", "投稿済み"),
        ("unknown", "結果不明"),
    ],
)
def test_presenter_localizes_statuses(status: str, label: str) -> None:
    rendered = build_notification_message(
        NotificationPresentation(
            NotificationType.RECOVERY,
            NotificationRecipientType.LOG,
            None,
            None,
            None,
            None,
            status,
        )
    )
    assert rendered.embed is not None
    assert rendered.embed.fields[0].value == label


@pytest.mark.parametrize("channel_id", [0, -1, True, 2**63])
def test_presenter_rejects_invalid_channel_id(channel_id: object) -> None:
    with pytest.raises(ValueError):
        build_notification_message(
            NotificationPresentation(
                NotificationType.RECOVERY,
                NotificationRecipientType.LOG,
                None,
                None,
                None,
                channel_id,
                "unknown",  # type: ignore[arg-type]
            )
        )


def test_presenter_rejects_non_utc_datetime() -> None:
    with pytest.raises(InvalidDateTimeError):
        build_notification_message(
            NotificationPresentation(
                NotificationType.RECOVERY,
                NotificationRecipientType.LOG,
                None,
                None,
                NOW.astimezone(timezone(timedelta(hours=9))),
                None,
                "unknown",
            )
        )


def test_embed_limits_and_message_exclusivity() -> None:
    with pytest.raises(ValueError):
        NotificationEmbed("x" * 257, "description", 0)
    with pytest.raises(ValueError):
        NotificationEmbed("title", "x" * 4097, 0)
    with pytest.raises(ValueError):
        NotificationEmbedField("name", "x" * 1025)
    with pytest.raises(ValueError):
        NotificationEmbed(
            "title",
            "description",
            0,
            tuple(NotificationEmbedField(str(index), "value") for index in range(26)),
        )
    with pytest.raises(ValueError):
        NotificationEmbed(
            "x" * 256,
            "x" * 4096,
            0,
            (
                NotificationEmbedField("x" * 256, "x" * 1024),
                NotificationEmbedField("name", "x" * 365),
            ),
        )
    embed = NotificationEmbed("title", "description", 0)
    with pytest.raises(ValueError):
        NotificationMessage(
            NotificationType.RECOVERY,
            NotificationRecipientType.LOG,
            None,
            SAFE_ALLOWED_MENTIONS,
        )
    with pytest.raises(ValueError):
        NotificationMessage(
            NotificationType.RECOVERY,
            NotificationRecipientType.LOG,
            None,
            SAFE_ALLOWED_MENTIONS,
            content="content",
            embed=embed,
        )


@pytest.mark.asyncio
async def test_operator_channel_is_cached_validated_and_sent_once() -> None:
    adapter, client, guild, channel = gateway()
    assert await adapter.send(message()) == 9001
    client.get_guild.assert_called_once_with(100)
    guild.get_channel.assert_called_once_with(400)
    client.fetch_channel.assert_not_called()
    channel.send.assert_awaited_once()
    assert channel.send.await_args.args == ()
    sent_embed = channel.send.await_args.kwargs["embed"]
    assert isinstance(sent_embed, discord.Embed)
    assert sent_embed.fields[0].value == "<#500>"
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
    assert "予約状態" not in caplog.text
    assert "安全のため" not in caplog.text


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
    assert user.send.await_args.args == ()
    assert user.send.await_args.kwargs["embed"].fields[0].value == "<#500>"

    fetched = MagicMock(spec=discord.User)
    fetched.bot = False
    fetched.send = AsyncMock(return_value=MagicMock(id=9003))
    client.get_user.return_value = None
    client.fetch_user.return_value = fetched
    assert await adapter.send(message(NotificationRecipientType.OPERATOR_DM, 300)) == 9003
    client.fetch_user.assert_awaited_once_with(300)
    fetched.send.assert_awaited_once()
    assert fetched.send.await_args.args == ()
    assert isinstance(fetched.send.await_args.kwargs["embed"], discord.Embed)


@pytest.mark.asyncio
async def test_dm_rejects_bot_itself_and_wrong_operator() -> None:
    adapter, client, _guild, _channel = gateway()
    with pytest.raises(NotificationPermanentError):
        await adapter.send(message(NotificationRecipientType.CREATOR_DM, 999))
    with pytest.raises(NotificationPermanentError):
        await adapter.send(message(NotificationRecipientType.OPERATOR_DM, 301))
    client.fetch_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_exception_classification_and_no_internal_retry(monkeypatch) -> None:
    adapter, _client, _guild, channel = gateway()
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    channel.send.side_effect = discord.Forbidden(Response(403), {"code": 50013})
    with pytest.raises(NotificationPermanentError):
        await adapter.send(message())

    channel.send.side_effect = discord.RateLimited(2.5)
    with pytest.raises(NotificationRateLimitError) as captured:
        await adapter.send(message())
    assert captured.value.retry_at == NOW + timedelta(seconds=2.5)

    channel.send.side_effect = discord.HTTPException(Response(500), {"code": 0})
    with pytest.raises(NotificationUnknownError):
        await adapter.send(message())

    assert channel.send.await_count == 3
    sleep.assert_not_awaited()
