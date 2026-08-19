"""Safe fixed notification templates without post content."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from discord_ai_reminder_bot.application.gateway import SAFE_ALLOWED_MENTIONS
from discord_ai_reminder_bot.application.notification_gateway import NotificationMessage
from discord_ai_reminder_bot.domain.enums import NotificationRecipientType, NotificationType
from discord_ai_reminder_bot.domain.recurrence import require_utc

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class NotificationPresentation:
    notification_type: NotificationType
    recipient_type: NotificationRecipientType
    recipient_id: int | None
    schedule_public_id: uuid.UUID | None
    scheduled_for: datetime | None
    channel_id: int | None
    current_status: str
    is_fallback: bool = False


_PURPOSES = {
    NotificationType.DRAFT_24H: "予約が下書きのままです（予定時刻の24時間前）。",
    NotificationType.DRAFT_1H: "予約が下書きのままです（予定時刻の1時間前）。",
    NotificationType.DRAFT_IMMEDIATE: "予定時刻が近い下書き予約があります。",
    NotificationType.RUN_FAILED: "予約投稿が最終的に失敗しました。",
    NotificationType.RUN_DELAYED: "予約投稿の処理が遅延しています。",
    NotificationType.RUN_SKIPPED: "予約投稿は安全のため送信されませんでした。",
    NotificationType.RECOVERY: "通知処理で運営者の確認が必要です。",
}


def build_notification_message(value: NotificationPresentation) -> NotificationMessage:
    notification_type = NotificationType(value.notification_type)
    parts = [_PURPOSES[notification_type]]
    if value.is_fallback:
        parts.append("元の通知経路へ送信できなかったため、代替経路へ通知しています。")
    if value.schedule_public_id is not None:
        if value.schedule_public_id.version != 7:
            raise ValueError("schedule_public_id must be UUIDv7")
        parts.append(f"予約ID: {value.schedule_public_id}")
    if value.scheduled_for is not None:
        instant = require_utc(value.scheduled_for).astimezone(JST)
        parts.append(f"予定日時: {instant:%Y-%m-%d %H:%M:%S} JST")
    if value.channel_id is not None:
        parts.append(f"投稿先: #channel-{value.channel_id}")
    parts.append(f"現在状態: {_neutralize(value.current_status)}")
    parts.append("必要に応じて予約またはBotの状態を確認してください。")
    content = "\n".join(parts)
    if len(content) > 2000:
        raise ValueError("notification template exceeds Discord limit")
    return NotificationMessage(
        notification_type=notification_type,
        recipient_type=NotificationRecipientType(value.recipient_type),
        recipient_id=value.recipient_id,
        content=_neutralize(content),
        allowed_mentions=SAFE_ALLOWED_MENTIONS,
    )


def _neutralize(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("notification status text must not be empty")
    return value.replace("@", "@\u200b")
