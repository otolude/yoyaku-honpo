import uuid
from datetime import UTC, date, datetime, time

import pytest

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
from discord_ai_reminder_bot.bot.post_presenter import (
    EMBED_FIELD_LIMIT,
    EMBED_FIELD_NAME_LIMIT,
    EMBED_FIELD_VALUE_LIMIT,
    EMBED_TOTAL_LIMIT,
    LIST_OPERATION_GUIDANCE,
    STATUS_COLOURS,
    STATUS_LABELS,
    created_recurring_schedule_embed,
    created_schedule_embed,
    deleted_schedule_embed,
    edited_schedule_embed,
    paused_schedule_embed,
    resumed_schedule_embed,
    schedule_deletion_preview_embed,
    schedule_detail_embed,
    schedule_list_embed,
    schedule_select_option,
)
from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType

NOW = datetime(2026, 8, 20, 10, 30, tzinfo=UTC)


def view(
    *,
    status: ScheduleStatus = ScheduleStatus.ACTIVE,
    content: str | None = "本文",
    schedule_type: ScheduleType = ScheduleType.ONCE,
    next_run_at: datetime | None = NOW,
) -> ScheduleView:
    return ScheduleView(
        public_id=uuid.uuid7(),
        channel_id=400,
        creator_user_id=300,
        schedule_type=schedule_type,
        status=status,
        content=content,
        next_run_at=next_run_at,
        local_time=time(9, 15) if schedule_type is not ScheduleType.ONCE else None,
        weekday=0 if schedule_type is ScheduleType.WEEKLY else None,
        end_date=date(2026, 8, 31) if schedule_type is not ScheduleType.ONCE else None,
    )


def all_text(embed) -> str:
    return "\n".join(
        [embed.title or "", embed.description or ""]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
    )


def test_pause_embed_explains_preservation_and_skipped_occurrence() -> None:
    embed = paused_schedule_embed(
        PausedSchedule(
            public_id=uuid.uuid7(),
            channel_id=400,
            schedule_type=ScheduleType.DAILY,
            previous_status=ScheduleStatus.ACTIVE,
            pending_runs_skipped=1,
        )
    )
    text = all_text(embed)
    assert embed.title == "予約を一時停止しました"
    assert "一時停止中は投稿されません" in text
    assert "本文と繰り返し設定は保持" in text
    assert "現在の状態" in text
    assert "⏸️ 一時停止中" in text


def test_edit_embed_shows_changes_and_safe_state_notes() -> None:
    public_id = uuid.uuid7()
    embed = edited_schedule_embed(
        EditedSchedule(
            public_id=public_id,
            channel_id=400,
            schedule_type=ScheduleType.WEEKLY,
            status=ScheduleStatus.PAUSED,
            content="<@123456789012345678> **本文**",
            next_run_at=None,
            local_time=time(9, 15),
            weekday=2,
            end_date=None,
            changed_fields=("channel_id", "content", "weekday"),
            pending_runs_skipped=0,
            run_replaced=False,
            retry_pending_preserved=True,
            previous_status=ScheduleStatus.PAUSED,
        )
    )
    text = all_text(embed)
    assert embed.title == "予約を編集しました"
    assert "投稿先、本文、曜日" in text
    assert "再開するまで投稿されません" in text
    assert "次回試行は変更後の内容を使用します" in text
    assert "<@123456789012345678>" not in text
    assert f"`{public_id}`" in text
    assert len(embed.fields) <= EMBED_FIELD_LIMIT and len(embed) <= EMBED_TOTAL_LIMIT


@pytest.mark.parametrize(
    ("status", "title", "message"),
    [
        (ScheduleStatus.DRAFT, "予約を再開しました", "本文を設定するまで投稿されません"),
        (ScheduleStatus.ENDED, "予約の終了を確定しました", "終了日内に次の投稿予定がない"),
    ],
)
def test_resume_embed_draft_and_ended_messages(
    status: ScheduleStatus, title: str, message: str
) -> None:
    embed = resumed_schedule_embed(
        ResumedSchedule(
            public_id=uuid.uuid7(),
            channel_id=400,
            schedule_type=ScheduleType.WEEKLY,
            status=status,
            next_run_at=NOW if status is ScheduleStatus.DRAFT else None,
            local_time=time(9),
            weekday=2,
            end_date=date(2026, 8, 31),
            content=None if status is ScheduleStatus.DRAFT else "body",
        )
    )
    assert embed.title == title
    assert message in all_text(embed)


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
    assert [field.name for field in embed.fields[1:]] == [
        "📍 投稿先",
        "🗓️ 投稿予定",
        "📝 本文",
        "🆔 予約ID",
    ]


@pytest.mark.parametrize(
    ("schedule_type", "weekday", "title"),
    [
        (ScheduleType.DAILY, None, "毎日予約を作成しました"),
        (ScheduleType.WEEKLY, 0, "毎週予約を作成しました"),
    ],
)
def test_recurring_create_embed_contains_definition_and_safe_limits(
    schedule_type: ScheduleType, weekday: int | None, title: str
) -> None:
    created = CreatedRecurringSchedule(
        public_id=uuid.uuid7(),
        channel_id=400,
        schedule_type=schedule_type,
        status=ScheduleStatus.DRAFT,
        content=None,
        local_time=time(9, 15),
        weekday=weekday,
        end_date=date(2026, 8, 31),
        next_run_at=NOW,
    )
    embed = created_recurring_schedule_embed(created)
    text = all_text(embed)
    assert embed.title == title
    assert "09:15 JST" in text
    assert "2026-08-31" in text
    assert "🗓️ 次回投稿" in text
    assert "本文なし" in text
    assert f"`{created.public_id}`" in text
    if schedule_type is ScheduleType.WEEKLY:
        assert "月曜日" in text
    assert len(embed.fields) <= EMBED_FIELD_LIMIT
    assert len(embed) <= EMBED_TOTAL_LIMIT


def test_list_ten_items_stays_within_all_embed_limits_and_order() -> None:
    schedules = [view(content=f"本文{i}") for i in range(10)]
    embed = schedule_list_embed(schedules, page=3, status_filter=ScheduleStatus.ACTIVE)
    assert embed.title == "予約一覧"
    assert embed.description == (
        f"ページ 3｜状態：有効｜種類：すべて｜日本時間（JST）\n{LIST_OPERATION_GUIDANCE}"
    )
    assert len(embed.fields) == 10 <= EMBED_FIELD_LIMIT
    assert len(embed) <= EMBED_TOTAL_LIMIT
    assert all(len(field.name) <= EMBED_FIELD_NAME_LIMIT for field in embed.fields)
    assert all(len(field.value) <= EMBED_FIELD_VALUE_LIMIT for field in embed.fields)
    positions = [all_text(embed).index(str(item.public_id)) for item in schedules]
    assert positions == sorted(positions)
    assert all("📍 投稿先：" in field.value for field in embed.fields)
    assert all("🗓️ 投稿予定：" in field.value for field in embed.fields)
    assert all("📝 本文：" in field.value for field in embed.fields)
    assert all("🆔 予約ID：" in field.value for field in embed.fields)


def test_list_header_contains_total_count_and_total_pages() -> None:
    embed = schedule_list_embed(
        [view()],
        page=1,
        status_filter=ScheduleStatus.PAUSED,
        total_count=12,
        total_pages=2,
    )
    assert embed.description == (
        f"1 / 2ページ｜全12件\n状態：一時停止中｜種類：すべて｜日本時間（JST）\n"
        f"{LIST_OPERATION_GUIDANCE}"
    )


def test_list_header_contains_schedule_type_filter() -> None:
    embed = schedule_list_embed(
        [],
        page=1,
        status_filter=ScheduleStatus.PAUSED,
        schedule_type_filter=ScheduleType.DAILY,
        total_count=0,
        total_pages=1,
    )
    assert embed.description == (
        f"1 / 1ページ｜全0件\n状態：一時停止中｜種類：毎日｜日本時間（JST）\n"
        f"{LIST_OPERATION_GUIDANCE}"
    )


def test_select_option_is_bounded_and_contains_only_public_summary() -> None:
    item = view(content="絶対に選択肢へ出さない本文")
    option = schedule_select_option(item, channel_name="x" * 200)
    assert len(option.label) <= 100
    assert option.value == str(item.public_id)
    assert len(option.value) == 36
    assert "本文" not in option.label


@pytest.mark.parametrize("schedule_type", [ScheduleType.DAILY, ScheduleType.WEEKLY])
def test_recurring_list_and_show_use_next_post_label(schedule_type: ScheduleType) -> None:
    item = view(schedule_type=schedule_type)
    listed = schedule_list_embed([item], page=1, status_filter=None)
    detailed = schedule_detail_embed(item)
    assert "🗓️ 次回投稿：" in listed.fields[0].value
    assert any(field.name == "🗓️ 次回投稿" for field in detailed.fields)
    assert "次回:" not in all_text(listed)
    assert "次回：" not in all_text(listed)
    detail_text = all_text(detailed)
    assert "09:15 JST" in detail_text
    if schedule_type is ScheduleType.WEEKLY:
        assert "月曜日" in detail_text


@pytest.mark.parametrize(
    "status",
    [
        ScheduleStatus.COMPLETED,
        ScheduleStatus.FAILED,
        ScheduleStatus.ENDED,
        ScheduleStatus.DELETED,
    ],
)
def test_terminal_without_next_run_omits_datetime_from_list_and_show(
    status: ScheduleStatus,
) -> None:
    item = view(status=status, next_run_at=None)
    listed = schedule_list_embed([item], page=1, status_filter=status)
    detailed = schedule_detail_embed(item)
    assert "🗓️" not in listed.fields[0].value
    assert all(not field.name.startswith("🗓️") for field in detailed.fields)
    assert "次回:" not in all_text(listed) + all_text(detailed)
    assert "次回：" not in all_text(listed) + all_text(detailed)


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
    content_fields = [field for field in embed.fields if field.name.startswith("📝 本文")]
    assert content_fields[0].name == "📝 本文"
    assert all(field.name == "📝 本文（続き）" for field in content_fields[1:])


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
    assert embed.description == (
        f"ページ 99｜状態：削除済み｜種類：すべて｜日本時間（JST）\n{LIST_OPERATION_GUIDANCE}"
    )


def test_delete_preview_escapes_reason_and_shows_confirmation_without_mutation_claims() -> None:
    public_id = uuid.uuid7()
    embed = schedule_deletion_preview_embed(
        ScheduleDeletionView(
            public_id=public_id,
            channel_id=400,
            schedule_type=ScheduleType.ONCE,
            previous_status=ScheduleStatus.ACTIVE,
            content="line 1\nline 2",
            next_run_at=NOW,
            reason="**reason** @everyone <@12345678901234567>",
        )
    )
    text = all_text(embed)
    assert embed.title == "予約削除の確認"
    assert f"`{public_id}`" in text
    assert "🗓️ 投稿予定" in text
    assert "line 1 line 2" in text
    assert "\\*\\*reason\\*\\*" in text
    assert "@\u200beveryone" in text
    assert "下のボタン" in text
    assert len(embed.fields) <= EMBED_FIELD_LIMIT
    assert len(embed) <= EMBED_TOTAL_LIMIT


def test_delete_embeds_show_missing_reason_as_unentered() -> None:
    public_id = uuid.uuid7()
    preview = ScheduleDeletionView(
        public_id=public_id,
        channel_id=400,
        schedule_type=ScheduleType.ONCE,
        previous_status=ScheduleStatus.ACTIVE,
        content="body",
        next_run_at=NOW,
        reason="理由未入力",
    )
    assert "未入力" in all_text(schedule_deletion_preview_embed(preview))
    deleted = DeletedSchedule(**vars(preview), deleted_at=NOW, pending_runs_skipped=1)
    assert "未入力" in all_text(deleted_schedule_embed(deleted))


def test_deleted_embed_has_safe_fixed_result_without_thirty_day_promise() -> None:
    public_id = uuid.uuid7()
    embed = deleted_schedule_embed(
        DeletedSchedule(
            public_id=public_id,
            channel_id=400,
            schedule_type=ScheduleType.WEEKLY,
            previous_status=ScheduleStatus.FAILED,
            content="body",
            next_run_at=None,
            reason="operator _resolved_",
            deleted_at=NOW,
            pending_runs_skipped=0,
        )
    )
    text = all_text(embed)
    assert embed.title == "予約を削除しました"
    assert f"`{public_id}`" in text
    assert "削除済みとして記録しました" in text
    assert "すでにDiscordへ投稿されたメッセージは削除されません" in text
    assert "30日後" not in text
    assert "\\_resolved\\_" in text
    assert len(embed.fields) <= EMBED_FIELD_LIMIT
    assert len(embed) <= EMBED_TOTAL_LIMIT
