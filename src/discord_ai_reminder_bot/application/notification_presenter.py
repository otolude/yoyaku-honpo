"""Safe fixed notification templates without post content."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from discord_ai_reminder_bot.application.gateway import SAFE_ALLOWED_MENTIONS
from discord_ai_reminder_bot.application.notification_gateway import (
    NotificationEmbed,
    NotificationEmbedField,
    NotificationMessage,
)
from discord_ai_reminder_bot.config import MAX_POSTGRES_BIGINT
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
    result_code: str | None = None
    recurring_missed: bool = False
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

_STATUS_LABELS = {
    "draft": "下書き",
    "active": "有効",
    "paused": "一時停止中",
    "completed": "完了",
    "failed": "失敗",
    "ended": "終了",
    "deleted": "削除済み",
    "skipped": "見送り済み",
    "pending": "待機中",
    "processing": "処理中",
    "succeeded": "投稿済み",
    "unknown": "結果不明",
    "recovery_required": "確認が必要",
}

YELLOW = 0xF1C40F
ORANGE = 0xE67E22
GREY = 0x95A5A6
RED = 0xE74C3C
BLUE = 0x3498DB


def build_notification_message(value: NotificationPresentation) -> NotificationMessage:
    notification_type = NotificationType(value.notification_type)
    description = _purpose(value)
    if value.is_fallback:
        description += "\n元の通知経路へ送信できなかったため、代替経路へ通知しています。"
    fields = [NotificationEmbedField("📌 状態", _status_label(value.current_status))]
    if value.channel_id is not None:
        if (
            isinstance(value.channel_id, bool)
            or not isinstance(value.channel_id, int)
            or not 1 <= value.channel_id <= MAX_POSTGRES_BIGINT
        ):
            raise ValueError("notification channel_id must be a positive BIGINT")
        fields.append(NotificationEmbedField("📍 投稿先", f"<#{value.channel_id}>"))
    if value.scheduled_for is not None:
        instant = require_utc(value.scheduled_for).astimezone(JST)
        fields.append(NotificationEmbedField("🗓️ 投稿予定", f"{instant:%Y-%m-%d %H:%M} JST"))
    if value.schedule_public_id is not None:
        if value.schedule_public_id.version != 7:
            raise ValueError("schedule_public_id must be UUIDv7")
        fields.append(NotificationEmbedField("🆔 予約ID", f"`{value.schedule_public_id}`"))
    fields.append(NotificationEmbedField("ℹ️ 対応", _action(value)))
    return NotificationMessage(
        notification_type=notification_type,
        recipient_type=NotificationRecipientType(value.recipient_type),
        recipient_id=value.recipient_id,
        allowed_mentions=SAFE_ALLOWED_MENTIONS,
        embed=NotificationEmbed(
            title=_title(value),
            description=_neutralize(description),
            color=_color(value),
            fields=tuple(fields),
        ),
    )


def _title(value: NotificationPresentation) -> str:
    kind = NotificationType(value.notification_type)
    if value.recurring_missed:
        return "⏭️ 停止中の定期投稿を見送りました"
    if kind is NotificationType.DRAFT_24H:
        return "📝 下書きの投稿予定が近づいています"
    if kind is NotificationType.DRAFT_1H:
        return "⏰ 下書きの投稿予定まで1時間です"
    if kind is NotificationType.DRAFT_IMMEDIATE:
        return "⚠️ 下書きの投稿予定が近づいています"
    if kind is NotificationType.RUN_SKIPPED or value.result_code == "draft_without_content":
        return "⏭️ 下書き投稿を見送りました"
    if kind is NotificationType.RUN_FAILED and value.result_code == "delivery_result_unknown":
        return "⚠️ 投稿結果を確認できません"
    if kind is NotificationType.RUN_FAILED:
        return "❌ Discordへの投稿に失敗しました"
    if kind is NotificationType.RUN_DELAYED:
        return "🕒 遅延した予約投稿を処理します"
    return "⚠️ 予約状態の確認が必要です"


def _color(value: NotificationPresentation) -> int:
    kind = NotificationType(value.notification_type)
    if kind in {NotificationType.DRAFT_24H, NotificationType.DRAFT_1H}:
        return YELLOW
    if kind is NotificationType.RUN_FAILED and value.result_code != "delivery_result_unknown":
        return RED
    if kind is NotificationType.RUN_DELAYED:
        return BLUE
    if kind is NotificationType.RUN_SKIPPED or value.recurring_missed:
        return GREY
    return ORANGE


def _status_label(value: str) -> str:
    neutral = _neutralize(value)
    try:
        return _STATUS_LABELS[neutral]
    except KeyError as error:
        raise ValueError("unsupported notification status") from error


def _action(value: NotificationPresentation) -> str:
    kind = NotificationType(value.notification_type)
    if kind is NotificationType.RUN_SKIPPED and value.result_code == "draft_without_content":
        return "必要に応じて予約内容を確認し、本文や投稿日時を編集してください。"
    if kind in {
        NotificationType.DRAFT_24H,
        NotificationType.DRAFT_1H,
        NotificationType.DRAFT_IMMEDIATE,
    }:
        return "投稿する場合は、予定時刻までに予約本文を設定してください。"
    if kind is NotificationType.RUN_FAILED:
        return "投稿先とBotの権限を確認し、必要に応じて予約を再設定してください。"
    if kind is NotificationType.RUN_DELAYED:
        return "投稿結果を確認してください。"
    return "必要に応じて予約またはBotの状態を確認してください。"


def _purpose(value: NotificationPresentation) -> str:
    kind = NotificationType(value.notification_type)
    if value.recurring_missed:
        return (
            "Bot停止中に期限を過ぎた定期投稿を送信せず見送りました。"
            "予約内容と次回予定を確認してください。"
        )
    if kind is NotificationType.RUN_SKIPPED and value.result_code == "draft_without_content":
        return "下書きのまま投稿時刻を迎えたため、Discordへの投稿を行いませんでした。"
    if kind is NotificationType.RUN_FAILED:
        if value.result_code == "delivery_result_unknown":
            return "投稿されたか確認できないため、自動再送していません。"
        if value.result_code == "startup_overdue":
            return "Bot停止中に15分を超過したため単発投稿を見送りました。"
        return "Discordへの投稿が最終的に失敗しました。"
    if kind is NotificationType.RUN_DELAYED:
        return "Bot起動後、15分以内の遅延投稿として処理します。"
    if kind is NotificationType.RECOVERY:
        return "安全のため自動送信せず、運営者による確認が必要です。"
    return _PURPOSES[kind]


def _neutralize(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("notification status text must not be empty")
    return value.replace("@", "@\u200b")
