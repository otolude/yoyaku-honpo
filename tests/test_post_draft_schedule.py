import inspect
from datetime import UTC

import pytest

from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
    IdempotentScheduleCreationResult,
)
from discord_ai_reminder_bot.application.post_draft_schedule import (
    PostDraftSchedulePort,
    PostDraftScheduleScope,
)
from discord_ai_reminder_bot.application.schedule_creation import IdempotentScheduleCreationService
from discord_ai_reminder_bot.domain.enums import ScheduleType
from discord_ai_reminder_bot.domain.post_draft_generation import GeneratedPostDraft


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
    from datetime import date, datetime, time

    from discord_ai_reminder_bot.application.post_draft_schedule import (
        PostDraftDailyScheduleInput,
        PostDraftOnceScheduleInput,
        PostDraftScheduleSession,
        PostDraftScheduleState,
        PostDraftWeeklyScheduleInput,
    )

    session = PostDraftScheduleSession(scope=_scope(), accepted_draft=_draft())
    session.select_type(ScheduleType.ONCE)
    with pytest.raises(TypeError):
        session.set_validated_input(
            PostDraftDailyScheduleInput(local_time=time(9, 0), end_date=None)
        )
    session.set_validated_input(
        PostDraftOnceScheduleInput(scheduled_at=datetime(2030, 1, 1, tzinfo=UTC))
    )
    assert session.snapshot().state is PostDraftScheduleState.FINAL_CONFIRMATION
    session.edit_schedule_input()
    assert session.snapshot().state is PostDraftScheduleState.SCHEDULE_INPUT
    with pytest.raises(TypeError):
        session.set_validated_input(
            PostDraftWeeklyScheduleInput(
                local_time=time(9, 0), weekday=0, end_date=date(2030, 1, 31)
            )
        )


@pytest.mark.parametrize("schedule_type", list(ScheduleType))
def test_schedule_session_selects_every_schedule_type(schedule_type: ScheduleType) -> None:
    from discord_ai_reminder_bot.application.post_draft_schedule import PostDraftScheduleSession

    session = PostDraftScheduleSession(scope=_scope(), accepted_draft=_draft())
    session.select_type(schedule_type)
    assert session.snapshot().schedule_type is schedule_type


def test_schedule_session_transitions_results_and_rejects_terminal_mutation() -> None:
    from datetime import datetime

    from discord_ai_reminder_bot.application.post_draft_schedule import (
        PostDraftOnceScheduleInput,
        PostDraftScheduleSession,
        PostDraftScheduleState,
    )

    session = PostDraftScheduleSession(scope=_scope(), accepted_draft=_draft())
    session.select_type(ScheduleType.ONCE)
    session.set_validated_input(
        PostDraftOnceScheduleInput(scheduled_at=datetime(2030, 1, 1, tzinfo=UTC))
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
        from datetime import datetime

        from discord_ai_reminder_bot.application.post_draft_schedule import (
            PostDraftOnceScheduleInput,
        )

        session.set_validated_input(
            PostDraftOnceScheduleInput(scheduled_at=datetime(2030, 1, 1, tzinfo=UTC))
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


def _ready_session(schedule_type: ScheduleType = ScheduleType.ONCE):
    from datetime import datetime

    from discord_ai_reminder_bot.application.post_draft_schedule import (
        PostDraftOnceScheduleInput,
        PostDraftScheduleSession,
    )

    session = PostDraftScheduleSession(scope=_scope(), accepted_draft=_draft())
    session.select_type(schedule_type)
    if schedule_type is ScheduleType.ONCE:
        session.set_validated_input(PostDraftOnceScheduleInput(datetime(2030, 1, 1, tzinfo=UTC)))
    return session


def test_controller_maps_once_and_result() -> None:
    from datetime import datetime
    from uuid import uuid7

    from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
        IdempotentScheduleCreationCode,
        IdempotentScheduleCreationResult,
        ScheduleCreationPublicId,
    )
    from discord_ai_reminder_bot.application.post_draft_schedule import (
        PostDraftScheduleController,
        PostDraftScheduleState,
    )

    calls: list[dict[str, object]] = []

    class Port:
        async def create_once(self, **kwargs):
            calls.append(kwargs)
            return IdempotentScheduleCreationResult(IdempotentScheduleCreationCode.CREATED)

    public_id = ScheduleCreationPublicId.create(uuid7())
    controller = PostDraftScheduleController(
        session=_ready_session(), port=Port(), public_id_factory=lambda: public_id
    )
    result = __import__("asyncio").run(controller.confirm(now=datetime(2030, 1, 1, tzinfo=UTC)))
    assert result.code is IdempotentScheduleCreationCode.CREATED
    assert controller.snapshot().state is PostDraftScheduleState.COMPLETED
    assert calls == [
        {
            "public_id": public_id,
            "guild_id": 2,
            "channel_id": 3,
            "creator_user_id": 1,
            "scheduled_for": datetime(2030, 1, 1, tzinfo=UTC),
            "content": "本文",
            "allow_duplicate": False,
            "now": datetime(2030, 1, 1, tzinfo=UTC),
        }
    ]


@pytest.mark.parametrize(
    ("code", "state"),
    [
        ("ALREADY_CREATED", "COMPLETED"),
        ("CONFLICT", "CONFLICT"),
        ("UNKNOWN", "UNKNOWN"),
    ],
)
def test_controller_maps_all_results(code: str, state: str) -> None:
    from datetime import datetime
    from uuid import uuid7

    from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
        IdempotentScheduleCreationCode,
        IdempotentScheduleCreationResult,
        ScheduleCreationPublicId,
    )
    from discord_ai_reminder_bot.application.post_draft_schedule import (
        PostDraftScheduleController,
        PostDraftScheduleState,
    )

    result_code = IdempotentScheduleCreationCode[code]

    class Port:
        async def create_once(self, **kwargs):
            return IdempotentScheduleCreationResult(result_code)

    controller = PostDraftScheduleController(
        session=_ready_session(),
        port=Port(),
        public_id_factory=lambda: ScheduleCreationPublicId.create(uuid7()),
    )
    __import__("asyncio").run(controller.confirm(now=datetime(2030, 1, 1, tzinfo=UTC)))
    assert controller.snapshot().state is PostDraftScheduleState[state]


def test_controller_rejects_second_confirm_without_new_side_effect() -> None:
    from datetime import datetime
    from uuid import uuid7

    from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
        IdempotentScheduleCreationCode,
        IdempotentScheduleCreationResult,
        ScheduleCreationPublicId,
    )
    from discord_ai_reminder_bot.application.post_draft_schedule import PostDraftScheduleController

    factory_calls = 0
    service_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return ScheduleCreationPublicId.create(uuid7())

    class Port:
        async def create_once(self, **kwargs):
            nonlocal service_calls
            service_calls += 1
            return IdempotentScheduleCreationResult(IdempotentScheduleCreationCode.CREATED)

    import asyncio

    controller = PostDraftScheduleController(
        session=_ready_session(), port=Port(), public_id_factory=factory
    )
    asyncio.run(controller.confirm(now=datetime(2030, 1, 1, tzinfo=UTC)))
    with pytest.raises(ValueError):
        asyncio.run(controller.confirm(now=datetime(2030, 1, 1, tzinfo=UTC)))
    assert factory_calls == 1
    assert service_calls == 1


def test_composition_starts_independent_controller_sessions_without_side_effects() -> None:
    from uuid import uuid7

    from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
        ScheduleCreationPublicId,
    )
    from discord_ai_reminder_bot.application.post_draft_schedule import (
        PostDraftScheduleComposition,
        PostDraftScheduleState,
    )

    class Port:
        async def create_once(self, **kwargs):
            raise AssertionError("port must not be called")

        async def create_recurring(self, **kwargs):
            raise AssertionError("port must not be called")

    factory_calls = 0

    def factory() -> ScheduleCreationPublicId:
        nonlocal factory_calls
        factory_calls += 1
        return ScheduleCreationPublicId.create(uuid7())

    composition = PostDraftScheduleComposition(port=Port(), public_id_factory=factory)
    first = composition.start(scope=_scope(), accepted_draft=_draft())
    second = composition.start(scope=_scope(), accepted_draft=_draft())
    assert first is not second
    assert first.snapshot().state is PostDraftScheduleState.SCHEDULE_TYPE_SELECTION
    assert first.session is not second.session
    assert first.session.scope == _scope()
    assert first.session.accepted_draft == _draft()
    assert first._port is second._port  # type: ignore[attr-defined]
    assert first._public_id_factory is second._public_id_factory  # type: ignore[attr-defined]
    assert factory_calls == 0


@pytest.mark.parametrize("kwargs", [{"port": None}, {"public_id_factory": None}])
def test_composition_rejects_missing_dependencies(kwargs: dict[str, object]) -> None:
    from discord_ai_reminder_bot.application.post_draft_schedule import PostDraftScheduleComposition

    values: dict[str, object] = {
        "port": object(),
        "public_id_factory": lambda: None,
    }
    values.update(kwargs)
    with pytest.raises(TypeError):
        PostDraftScheduleComposition(**values)  # type: ignore[arg-type]


def test_composition_rejects_non_callable_factory() -> None:
    from discord_ai_reminder_bot.application.post_draft_schedule import PostDraftScheduleComposition

    with pytest.raises(TypeError):
        PostDraftScheduleComposition(port=object(), public_id_factory=object())  # type: ignore[arg-type]
