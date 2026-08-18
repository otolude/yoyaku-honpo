import uuid
from datetime import UTC, date, datetime

import pytest

from discord_ai_reminder_bot.application.schedule_creation import CreatedOnceSchedule
from discord_ai_reminder_bot.application.schedule_queries import ScheduleView
from discord_ai_reminder_bot.bot.post_presenter import (
    EMBED_FIELD_LIMIT,
    EMBED_FIELD_NAME_LIMIT,
    EMBED_FIELD_VALUE_LIMIT,
    EMBED_TOTAL_LIMIT,
    STATUS_COLOURS,
    STATUS_LABELS,
    created_schedule_embed,
    schedule_detail_embed,
    schedule_list_embed,
)
from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType

NOW = datetime(2026, 8, 20, 10, 30, tzinfo=UTC)


def view(
    *,
    status: ScheduleStatus = ScheduleStatus.ACTIVE,
    content: str | None = "本文",
    schedule_type: ScheduleType = ScheduleType.ONCE,
) -> ScheduleView:
    return ScheduleView(
        public_id=uuid.uuid7(),
        channel_id=400,
        creator_user_id=300,
        schedule_type=schedule_type,
        status=status,
        content=content,
        next_run_at=NOW,
        local_time=None,
        weekday=None,
        end_date=date(2026, 8, 31) if schedule_type is not ScheduleType.ONCE else None,
    )


def all_text(embed) -> str:
    return "\n".join(
        [embed.title or "", embed.description or ""]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
    )


@pytest.mark.parametrize("status", list(ScheduleStatus))
def test_all_statuses_have_distinct_text_and_expected_colour(status: ScheduleStatus) -> None:
    embed = schedule_detail_embed(view(status=status))
    assert embed.colour.value == STATUS_COLOURS[status]
    assert STATUS_LABELS[status] in all_text(embed)


def test_create_embed_structure_draft_and_full_public_id() -> None:
    public_id = uuid.uuid7()
    embed = created_schedule_embed(
        CreatedOnceSchedule(
            public_id=public_id,
            channel_id=400,
            status=ScheduleStatus.DRAFT,
            content=None,
            scheduled_for=NOW,
        )
    )
    text = all_text(embed)
    assert embed.title == "単発予約を作成しました"
    assert "本文なし" in text
    assert f"`{public_id}`" in text
    assert "2026-08-20 19:30 JST" in text


def test_list_ten_items_stays_within_all_embed_limits_and_order() -> None:
    schedules = [view(content=f"本文{i}") for i in range(10)]
    embed = schedule_list_embed(schedules, page=3, status_filter=ScheduleStatus.ACTIVE)
    assert embed.title == "予約一覧"
    assert "ページ: 3" in embed.description
    assert "状態フィルター: 有効" in embed.description
    assert len(embed.fields) == 10 <= EMBED_FIELD_LIMIT
    assert len(embed) <= EMBED_TOTAL_LIMIT
    assert all(len(field.name) <= EMBED_FIELD_NAME_LIMIT for field in embed.fields)
    assert all(len(field.value) <= EMBED_FIELD_VALUE_LIMIT for field in embed.fields)
    positions = [all_text(embed).index(str(item.public_id)) for item in schedules]
    assert positions == sorted(positions)


def test_list_preview_collapses_lines_escapes_markup_mentions_and_truncates() -> None:
    secret_tail = "do-not-show-full-body"
    content = "@everyone **first**\n`second` <@12345678901234567> " + secret_tail
    embed = schedule_list_embed([view(content=content)], page=1, status_filter=None)
    text = all_text(embed)
    assert "\n`second`" not in text
    assert "\\*\\*first\\*\\*" in text
    assert "@\u200beveryone" in text
    assert secret_tail not in text


def test_show_two_thousand_markup_characters_stays_within_limits() -> None:
    item = view(content="*" * 2_000, status=ScheduleStatus.DELETED)
    embed = schedule_detail_embed(item)
    assert embed.title == "予約詳細"
    assert len(embed) <= EMBED_TOTAL_LIMIT
    assert len(embed.fields) <= EMBED_FIELD_LIMIT
    assert all(len(field.value) <= EMBED_FIELD_VALUE_LIMIT for field in embed.fields)
    assert f"`{item.public_id}`" in all_text(embed)


def test_show_preserves_line_breaks_but_escapes_user_markup_and_mentions() -> None:
    embed = schedule_detail_embed(
        view(content="line 1\n**bold** `code` <@&12345678901234567> @here")
    )
    text = all_text(embed)
    assert "line 1\n" in text
    assert "\\*\\*bold\\*\\*" in text
    assert "\\`code\\`" in text
    assert "@\u200b&12345678901234567" in text
    assert "@\u200bhere" in text


def test_empty_list_is_an_embed() -> None:
    embed = schedule_list_embed([], page=99, status_filter=ScheduleStatus.DELETED)
    assert len(embed.fields) == 1
    assert "表示できる予約はありません" in embed.fields[0].value
