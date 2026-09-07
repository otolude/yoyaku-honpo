import inspect

import pytest

from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
    IdempotentScheduleCreationResult,
)
from discord_ai_reminder_bot.application.post_draft_schedule import (
    PostDraftSchedulePort,
    PostDraftScheduleScope,
)
from discord_ai_reminder_bot.domain.post_draft_generation import GeneratedPostDraft
from discord_ai_reminder_bot.domain.enums import ScheduleType
from discord_ai_reminder_bot.application.schedule_creation import IdempotentScheduleCreationService


def test_port_signatures_match_service() -> None:
    for name in ("create_once", "create_recurring"):
        port = inspect.signature(getattr(PostDraftSchedulePort, name))
        service = inspect.signature(getattr(IdempotentScheduleCreationService, name))
        assert list(port.parameters)[1:] == list(service.parameters)[1:]
        assert port.return_annotation == service.return_annotation
        assert port.return_annotation in (
            IdempotentScheduleCreationResult,
            "IdempotentScheduleCreationResult",
        )


def test_scope_is_validated_immutable_hashable_and_redacts_ids() -> None:
    scope = PostDraftScheduleScope(owner_user_id=1, guild_id=2, channel_id=3)
    assert scope == PostDraftScheduleScope(1, 2, 3)
    assert hash(scope) == hash(PostDraftScheduleScope(1, 2, 3))
    assert "1" not in repr(scope) and "2" not in repr(scope) and "3" not in repr(scope)
    with pytest.raises((AttributeError, TypeError)):
        scope.guild_id = 4  # type: ignore[misc]
    assert hasattr(scope, "__slots__")


@pytest.mark.parametrize("value", [True, False, 0, -1, "1", None, object()])
def test_scope_rejects_invalid_ids(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        PostDraftScheduleScope(value, 2, 3)  # type: ignore[arg-type]


def _scope() -> PostDraftScheduleScope:
    return PostDraftScheduleScope(1, 2, 3)


def _draft() -> GeneratedPostDraft:
    return GeneratedPostDraft("本文")


def test_schedule_session_initial_state_and_fixed_values() -> None:
    from discord_ai_reminder_bot.application.post_draft_schedule import (
        PostDraftScheduleSession,
        PostDraftScheduleState,
    )

    draft = _draft()
    session = PostDraftScheduleSession(scope=_scope(), accepted_draft=draft)
    snapshot = session.snapshot()
    assert snapshot.state is PostDraftScheduleState.SCHEDULE_TYPE_SELECTION
    assert snapshot.schedule_type is None
    assert snapshot.timezone == "Asia/Tokyo"
    assert snapshot.allow_duplicate is False
    assert session.accepted_draft == draft


def test_schedule_session_selects_types_and_rejects_mismatched_input() -> None:
    from datetime import date, datetime, time, timezone

    from discord_ai_reminder_bot.application.post_draft_schedule import (
        PostDraftOnceScheduleInput,
        PostDraftDailyScheduleInput,
        PostDraftScheduleSession,
        PostDraftScheduleState,
        PostDraftWeeklyScheduleInput,
    )

    session = PostDraftScheduleSession(scope=_scope(), accepted_draft=_draft())
    session.select_type(ScheduleType.ONCE)
    with pytest.raises(ValueError):
        session.set_validated_input(PostDraftDailyScheduleInput(local_time=time(9, 0), end_date=None))
    session.set_validated_input(
        PostDraftOnceScheduleInput(scheduled_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    )
    assert session.snapshot().state is PostDraftScheduleState.FINAL_CONFIRMATION
    session.edit_schedule_input()
    assert session.snapshot().state is PostDraftScheduleState.SCHEDULE_INPUT
    with pytest.raises(ValueError):
        session.set_validated_input(
            PostDraftWeeklyScheduleInput(local_time=time(9, 0), weekday=0, end_date=date(2030, 1, 31))
        )


@pytest.mark.parametrize("schedule_type", list(ScheduleType))
def test_schedule_session_selects_every_schedule_type(schedule_type: ScheduleType) -> None:
    from discord_ai_reminder_bot.application.post_draft_schedule import PostDraftScheduleSession

    session = PostDraftScheduleSession(scope=_scope(), accepted_draft=_draft())
    session.select_type(schedule_type)
    assert session.snapshot().schedule_type is schedule_type


def test_schedule_session_transitions_results_and_rejects_terminal_mutation() -> None:
    from datetime import datetime, timezone

    from discord_ai_reminder_bot.application.post_draft_schedule import (
        PostDraftOnceScheduleInput,
        PostDraftScheduleSession,
        PostDraftScheduleState,
    )

    session = PostDraftScheduleSession(scope=_scope(), accepted_draft=_draft())
    session.select_type(ScheduleType.ONCE)
    session.set_validated_input(
        PostDraftOnceScheduleInput(scheduled_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    )
    session.begin_saving()
    assert session.snapshot().state is PostDraftScheduleState.SAVING
    with pytest.raises(ValueError):
        session.cancel()
    session.mark_completed()
    assert session.snapshot().state is PostDraftScheduleState.COMPLETED
    with pytest.raises(ValueError):
        session.mark_completed()


@pytest.mark.parametrize("method", ["cancel", "mark_conflict", "mark_unknown"])
def test_schedule_session_terminal_paths(method: str) -> None:
    from discord_ai_reminder_bot.application.post_draft_schedule import PostDraftScheduleSession

    session = PostDraftScheduleSession(scope=_scope(), accepted_draft=_draft())
    if method == "cancel":
        session.cancel()
    else:
        session.select_type(ScheduleType.ONCE)
        from datetime import datetime, timezone

        from discord_ai_reminder_bot.application.post_draft_schedule import PostDraftOnceScheduleInput

        session.set_validated_input(
            PostDraftOnceScheduleInput(scheduled_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
        )
        session.begin_saving()
        getattr(session, method)()
    with pytest.raises(ValueError):
        session.cancel()


def test_schedule_session_has_no_public_id_or_infrastructure_dependency() -> None:
    import inspect

    from discord_ai_reminder_bot.application.post_draft_schedule import PostDraftScheduleSession

    assert "public_id" not in PostDraftScheduleSession.__annotations__
    source = inspect.getsource(PostDraftScheduleSession)
    assert "sqlalchemy" not in source
    assert "discord" not in source
