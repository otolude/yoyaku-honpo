import inspect

import pytest

from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
    IdempotentScheduleCreationResult,
)
from discord_ai_reminder_bot.application.post_draft_schedule import (
    PostDraftSchedulePort,
    PostDraftScheduleScope,
)
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
