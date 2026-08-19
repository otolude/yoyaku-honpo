"""Discord Embed presentation for schedule command DTOs."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import discord

from discord_ai_reminder_bot.application.schedule_creation import (
    CreatedOnceSchedule,
    CreatedRecurringSchedule,
)
from discord_ai_reminder_bot.application.schedule_deletion import (
    DeletedSchedule,
    ScheduleDeletionView,
)
from discord_ai_reminder_bot.application.schedule_editing import EditedSchedule
from discord_ai_reminder_bot.application.schedule_pause import PausedSchedule, ResumedSchedule
from discord_ai_reminder_bot.application.schedule_queries import ScheduleView
from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.domain.schedule_deletion import MISSING_DELETE_REASON

EMBED_TOTAL_LIMIT = 6_000
EMBED_FIELD_LIMIT = 25
EMBED_FIELD_NAME_LIMIT = 256
EMBED_FIELD_VALUE_LIMIT = 1_024
DETAIL_CONTENT_FIELDS = 4
CONTENT_PREVIEW_LIMIT = 40
_TOKYO = ZoneInfo("Asia/Tokyo")

TYPE_LABELS = {
    ScheduleType.ONCE: "単発",
    ScheduleType.DAILY: "毎日",
    ScheduleType.WEEKLY: "毎週",
}
STATUS_LABELS = {
    ScheduleStatus.DRAFT: "下書き",
    ScheduleStatus.ACTIVE: "有効",
    ScheduleStatus.PAUSED: "一時停止",
    ScheduleStatus.FAILED: "失敗",
    ScheduleStatus.COMPLETED: "完了",
    ScheduleStatus.ENDED: "終了済み",
    ScheduleStatus.DELETED: "削除済み",
}
STATUS_ICONS = {
    ScheduleStatus.DRAFT: "🟡",
    ScheduleStatus.ACTIVE: "🟢",
    ScheduleStatus.PAUSED: "🟠",
    ScheduleStatus.FAILED: "🔴",
    ScheduleStatus.COMPLETED: "🔵",
    ScheduleStatus.ENDED: "🟣",
    ScheduleStatus.DELETED: "⚪",
}
STATUS_COLOURS = {
    ScheduleStatus.DRAFT: 0xF1C40F,
    ScheduleStatus.ACTIVE: 0x2ECC71,
    ScheduleStatus.PAUSED: 0xE67E22,
    ScheduleStatus.FAILED: 0xE74C3C,
    ScheduleStatus.COMPLETED: 0x3498DB,
    ScheduleStatus.ENDED: 0x6C3483,
    ScheduleStatus.DELETED: 0x7F8C8D,
}
WEEKDAY_LABELS = ("月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日")


def created_schedule_embed(created: CreatedOnceSchedule) -> discord.Embed:
    embed = _embed(title="単発予約を作成しました", status=created.status)
    _field(embed, "状態", status_text(created.status), inline=True)
    _field(embed, "📍 投稿先", channel_text(created.channel_id), inline=True)
    _field(embed, "🗓️ 投稿予定", datetime_text(created.scheduled_for), inline=False)
    _field(embed, "📝 本文", content_preview(created.content), inline=False)
    _field(embed, "🆔 予約ID", public_id_text(created.public_id), inline=False)
    return _validated(embed)


def created_recurring_schedule_embed(created: CreatedRecurringSchedule) -> discord.Embed:
    type_label = TYPE_LABELS[created.schedule_type]
    embed = _embed(title=f"{type_label}予約を作成しました", status=created.status)
    _field(embed, "状態", status_text(created.status), inline=True)
    _field(embed, "種別", type_label, inline=True)
    _field(embed, "📍 投稿先", channel_text(created.channel_id), inline=False)
    if created.schedule_type is ScheduleType.WEEKLY:
        _field(embed, "曜日", weekday_text(created.weekday), inline=True)
    _field(embed, "投稿時刻", created.local_time.strftime("%H:%M JST"), inline=True)
    _field(
        embed, "終了日", created.end_date.isoformat() if created.end_date else "なし", inline=True
    )
    _field(embed, "🗓️ 次回投稿", datetime_text(created.next_run_at), inline=False)
    _field(embed, "📝 本文", content_preview(created.content), inline=False)
    _field(embed, "🆔 予約ID", public_id_text(created.public_id), inline=False)
    return _validated(embed)


def edited_schedule_embed(edited: EditedSchedule) -> discord.Embed:
    embed = _embed(title="予約を編集しました", status=edited.status)
    _field(embed, "状態", status_text(edited.status), inline=True)
    _field(embed, "種別", TYPE_LABELS[edited.schedule_type], inline=True)
    _field(embed, "📍 投稿先", channel_text(edited.channel_id), inline=False)
    if edited.schedule_type is ScheduleType.WEEKLY:
        _field(embed, "曜日", weekday_text(edited.weekday), inline=True)
    if edited.schedule_type is not ScheduleType.ONCE:
        _field(embed, "投稿時刻", local_time_text(edited.local_time), inline=True)
        _field(
            embed,
            "終了日",
            edited.end_date.isoformat() if edited.end_date else "なし",
            inline=True,
        )
    _field(
        embed,
        f"🗓️ {datetime_label(edited.schedule_type)}",
        datetime_text(edited.next_run_at),
        inline=False,
    )
    _field(embed, "📝 本文", content_preview(edited.content), inline=False)
    _field(embed, "🆔 予約ID", public_id_text(edited.public_id), inline=False)
    labels = {
        "channel_id": "投稿先",
        "content": "本文",
        "scheduled_at": "投稿予定",
        "local_time": "投稿時刻",
        "weekday": "曜日",
        "end_date": "終了日",
    }
    _field(
        embed,
        "変更した項目",
        "、".join(labels[item] for item in edited.changed_fields),
        inline=False,
    )
    notes: list[str] = []
    if edited.status is ScheduleStatus.PAUSED:
        notes.append("一時停止を維持しています。再開するまで投稿されません。")
    if edited.previous_status is ScheduleStatus.ACTIVE and edited.status is ScheduleStatus.DRAFT:
        notes.append("本文削除により下書きになりました。")
    if edited.previous_status is ScheduleStatus.DRAFT and edited.status is ScheduleStatus.ACTIVE:
        notes.append("本文設定により有効になりました。")
    if edited.status is ScheduleStatus.ENDED:
        notes.append("終了日内に次回投稿がないため終了済みになりました。")
    if edited.run_replaced:
        notes.append("変更前の実行予定を見送り、新しい次回投稿を作成しました。")
    if edited.retry_pending_preserved:
        notes.append("次回試行は変更後の内容を使用します。")
    if notes:
        _field(embed, "補足", "\n".join(notes), inline=False)
    return _validated(embed)


def schedule_deletion_preview_embed(schedule: ScheduleDeletionView) -> discord.Embed:
    embed = _embed(title="予約削除の確認", status=schedule.previous_status)
    _field(embed, "削除前の状態", status_text(schedule.previous_status), inline=True)
    _field(embed, "種別", TYPE_LABELS[schedule.schedule_type], inline=True)
    _field(embed, "📍 投稿先", channel_text(schedule.channel_id), inline=False)
    if schedule.next_run_at is not None:
        _field(
            embed,
            f"🗓️ {datetime_label(schedule.schedule_type)}",
            datetime_text(schedule.next_run_at),
            inline=False,
        )
    _field(embed, "📝 本文", content_preview(schedule.content), inline=False)
    _field(embed, "🆔 予約ID", public_id_text(schedule.public_id), inline=False)
    _field(embed, "削除理由", _delete_reason_text(schedule.reason), inline=False)
    _field(
        embed,
        "確認方法",
        "下のボタンから削除またはキャンセルを選択してください。",
        inline=False,
    )
    return _validated(embed)


def deleted_schedule_embed(schedule: DeletedSchedule) -> discord.Embed:
    embed = _embed(title="予約を削除しました", status=ScheduleStatus.DELETED)
    _field(embed, "🆔 予約ID", public_id_text(schedule.public_id), inline=False)
    _field(embed, "種別", TYPE_LABELS[schedule.schedule_type], inline=True)
    _field(embed, "削除前の状態", status_text(schedule.previous_status), inline=True)
    _field(embed, "📍 投稿先", channel_text(schedule.channel_id), inline=False)
    _field(embed, "削除日時", datetime_text(schedule.deleted_at), inline=False)
    _field(embed, "削除理由", _delete_reason_text(schedule.reason), inline=False)
    _field(
        embed,
        "削除結果",
        "削除済みとして記録しました。\nすでにDiscordへ投稿されたメッセージは削除されません。",
        inline=False,
    )
    return _validated(embed)


def paused_schedule_embed(schedule: PausedSchedule) -> discord.Embed:
    embed = _embed(title="予約を一時停止しました", status=ScheduleStatus.PAUSED)
    _field(embed, "🆔 予約ID", public_id_text(schedule.public_id), inline=False)
    _field(embed, "種別", TYPE_LABELS[schedule.schedule_type], inline=True)
    _field(embed, "停止前の状態", status_text(schedule.previous_status), inline=True)
    _field(embed, "📍 投稿先", channel_text(schedule.channel_id), inline=False)
    _field(
        embed,
        "一時停止について",
        "一時停止中は投稿されません。\n"
        "本文と繰り返し設定は保持されています。\n"
        "停止時に見送った投稿回は、再開しても送信されません。",
        inline=False,
    )
    return _validated(embed)


def resumed_schedule_embed(schedule: ResumedSchedule) -> discord.Embed:
    if schedule.status is ScheduleStatus.ENDED:
        embed = _embed(title="予約の終了を確定しました", status=schedule.status)
    else:
        embed = _embed(title="予約を再開しました", status=schedule.status)
    _field(embed, "🆔 予約ID", public_id_text(schedule.public_id), inline=False)
    _field(embed, "種別", TYPE_LABELS[schedule.schedule_type], inline=True)
    _field(embed, "再開後の状態", status_text(schedule.status), inline=True)
    _field(embed, "📍 投稿先", channel_text(schedule.channel_id), inline=False)
    if schedule.next_run_at is not None:
        _field(embed, "🗓️ 次回投稿", datetime_text(schedule.next_run_at), inline=False)
    if schedule.schedule_type is ScheduleType.WEEKLY:
        _field(embed, "曜日", weekday_text(schedule.weekday), inline=True)
    _field(embed, "投稿時刻", local_time_text(schedule.local_time), inline=True)
    _field(
        embed,
        "終了日",
        schedule.end_date.isoformat() if schedule.end_date else "なし",
        inline=True,
    )
    if schedule.status is ScheduleStatus.DRAFT:
        _field(
            embed,
            "下書きについて",
            "本文がないため下書きとして再開しました。本文を設定するまで投稿されません。",
            inline=False,
        )
    elif schedule.status is ScheduleStatus.ENDED:
        _field(
            embed,
            "終了結果",
            "終了日内に次の投稿予定がないため、予約は終了済みになりました。",
            inline=False,
        )
    return _validated(embed)


def _delete_reason_text(reason: str) -> str:
    if reason == MISSING_DELETE_REASON:
        return "未入力"
    return escape_user_text(reason)


def schedule_list_embed(
    schedules: list[ScheduleView], *, page: int, status_filter: ScheduleStatus | None
) -> discord.Embed:
    filter_label = STATUS_LABELS[status_filter] if status_filter is not None else "すべて"
    embed = discord.Embed(
        title="予約一覧",
        description=f"ページ {page}｜表示：{filter_label}｜日本時間（JST）",
        colour=0x5865F2,
    )
    if not schedules:
        _field(embed, "表示結果", "このページに表示できる予約はありません。", inline=False)
        return _validated(embed)
    for schedule in schedules:
        name = f"{status_text(schedule.status)}・{TYPE_LABELS[schedule.schedule_type]}"
        lines = [f"📍 投稿先：{channel_text(schedule.channel_id)}"]
        if schedule.next_run_at is not None:
            lines.append(
                f"🗓️ {datetime_label(schedule.schedule_type)}：{datetime_text(schedule.next_run_at)}"
            )
        lines.extend(
            (
                f"📝 本文：{content_preview(schedule.content)}",
                f"🆔 予約ID：{public_id_text(schedule.public_id)}",
            )
        )
        value = "\n".join(lines)
        _field(embed, name, value, inline=False)
    return _validated(embed)


def schedule_detail_embed(schedule: ScheduleView) -> discord.Embed:
    embed = _embed(title="予約詳細", status=schedule.status)
    _field(embed, "状態", status_text(schedule.status), inline=True)
    _field(embed, "種別", TYPE_LABELS[schedule.schedule_type], inline=True)
    _field(embed, "📍 投稿先", channel_text(schedule.channel_id), inline=False)
    if schedule.schedule_type is ScheduleType.WEEKLY:
        _field(embed, "曜日", weekday_text(schedule.weekday), inline=True)
    if schedule.schedule_type is not ScheduleType.ONCE:
        _field(embed, "投稿時刻", local_time_text(schedule.local_time), inline=True)
    if schedule.next_run_at is not None:
        _field(
            embed,
            f"🗓️ {datetime_label(schedule.schedule_type)}",
            datetime_text(schedule.next_run_at),
            inline=True,
        )
    _field(
        embed, "終了日", schedule.end_date.isoformat() if schedule.end_date else "なし", inline=True
    )
    _add_detail_content(embed, schedule.content)
    _field(embed, "🆔 予約ID", public_id_text(schedule.public_id), inline=False)
    return _validated(embed)


def status_text(status: ScheduleStatus) -> str:
    return f"{STATUS_ICONS[status]} {STATUS_LABELS[status]}"


def channel_text(channel_id: int) -> str:
    return f"<#{channel_id}>"


def public_id_text(public_id) -> str:
    return f"`{public_id}`"


def datetime_text(value: datetime | None) -> str:
    if value is None:
        return "なし"
    if value.tzinfo is None or value.utcoffset() is None:
        return "日時不明"
    return value.astimezone(UTC).astimezone(_TOKYO).strftime("%Y-%m-%d %H:%M JST")


def datetime_label(schedule_type: ScheduleType) -> str:
    return "投稿予定" if schedule_type is ScheduleType.ONCE else "次回投稿"


def weekday_text(weekday: int | None) -> str:
    if weekday is None or isinstance(weekday, bool) or not 0 <= weekday < len(WEEKDAY_LABELS):
        return "曜日不明"
    return WEEKDAY_LABELS[weekday]


def local_time_text(value) -> str:
    if value is None or value.tzinfo is not None:
        return "時刻不明"
    return value.strftime("%H:%M JST")


def content_preview(content: str | None) -> str:
    if content is None:
        return "本文なし"
    compact = " ".join(content.splitlines())
    shortened = compact if len(compact) <= CONTENT_PREVIEW_LIMIT else compact[:39] + "…"
    return escape_user_text(shortened)


def escape_user_text(value: str) -> str:
    escaped = re.sub(
        r"@(everyone|here|[!&]?[0-9]{17,20})",
        "@\u200b\\1",
        value,
        flags=re.IGNORECASE,
    )
    escaped = re.sub(r"<(?=[@#])", "<\u200b", escaped)
    for marker in ("\\", "`", "*", "_", "~", "|", ">", "#", "-"):
        escaped = escaped.replace(marker, f"\\{marker}")
    return escaped


def _add_detail_content(embed: discord.Embed, content: str | None) -> None:
    if content is None:
        _field(embed, "📝 本文", "本文なし", inline=False)
        return
    escaped = escape_user_text(content)
    maximum = EMBED_FIELD_VALUE_LIMIT * DETAIL_CONTENT_FIELDS
    omitted = len(escaped) > maximum
    if omitted:
        suffix = "\n…（表示上省略）"
        escaped = escaped[: maximum - len(suffix)] + suffix
    chunks = [
        escaped[index : index + EMBED_FIELD_VALUE_LIMIT]
        for index in range(0, len(escaped), EMBED_FIELD_VALUE_LIMIT)
    ]
    for index, chunk in enumerate(chunks, start=1):
        name = "📝 本文" if index == 1 else "📝 本文（続き）"
        _field(embed, name, chunk, inline=False)


def _embed(*, title: str, status: ScheduleStatus) -> discord.Embed:
    return discord.Embed(title=title, colour=STATUS_COLOURS[status])


def _field(embed: discord.Embed, name: str, value: str, *, inline: bool) -> None:
    if not name or len(name) > EMBED_FIELD_NAME_LIMIT:
        raise ValueError("invalid embed field name")
    if not value or len(value) > EMBED_FIELD_VALUE_LIMIT:
        raise ValueError("invalid embed field value")
    embed.add_field(name=name, value=value, inline=inline)


def _validated(embed: discord.Embed) -> discord.Embed:
    if len(embed.fields) > EMBED_FIELD_LIMIT or len(embed) > EMBED_TOTAL_LIMIT:
        raise ValueError("embed exceeds Discord limits")
    return embed
