import ast
import inspect
from dataclasses import fields
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import get_type_hints
from uuid import UUID

import pytest
from discord_ai_reminder_bot.application.post_draft_usage import (
    PostDraftUsageRepository,
    PostDraftUsageReservation,
)

from discord_ai_reminder_bot.domain.post_draft_usage import (
    MAX_POSTGRES_BIGINT,
    PostDraftGuildId,
    PostDraftOperationKey,
    PostDraftUsagePolicy,
    PostDraftUsageReservationCode,
    PostDraftUsageReservationResult,
    PostDraftUserId,
)

OPERATION_CANARY = UUID("23fd10ce-795d-4907-9556-af2868ba3b23")
USER_CANARY = 8_765_432_109_876_543
GUILD_CANARY = 7_654_321_098_765_432
COST_CANARY = 6_543_210_987_654_321


def reservation(**overrides: object) -> PostDraftUsageReservation:
    values: dict[str, object] = {
        "operation_key": PostDraftOperationKey(OPERATION_CANARY),
        "user_id": PostDraftUserId(USER_CANARY),
        "guild_id": PostDraftGuildId(GUILD_CANARY),
        "maximum_cost_microunits": 10_000,
        "now": datetime(2026, 9, 3, 0, 19, 30, tzinfo=UTC),
        "policy": PostDraftUsagePolicy(),
    }
    values.update(overrides)
    return PostDraftUsageReservation.create(**values)  # type: ignore[arg-type]


class FakeUsageRepository:
    def __init__(self, code: PostDraftUsageReservationCode) -> None:
        self.code = code
        self.calls: list[PostDraftUsageReservation] = []

    async def reserve(self, value: PostDraftUsageReservation) -> PostDraftUsageReservationResult:
        self.calls.append(value)
        return PostDraftUsageReservationResult(self.code)


@pytest.mark.asyncio
async def test_async_fake_satisfies_repository_protocol_and_returns_fixed_result() -> None:
    repository: PostDraftUsageRepository = FakeUsageRepository(
        PostDraftUsageReservationCode.RESERVED
    )
    value = reservation()
    result = await repository.reserve(value)
    assert result == PostDraftUsageReservationResult(PostDraftUsageReservationCode.RESERVED)


def test_reservation_factory_creates_validated_content_free_dto() -> None:
    value = reservation()
    assert value.operation_key == PostDraftOperationKey(OPERATION_CANARY)
    assert value.user_id == PostDraftUserId(USER_CANARY)
    assert value.guild_id == PostDraftGuildId(GUILD_CANARY)
    assert value.maximum_cost_microunits == 10_000
    assert value.policy == PostDraftUsagePolicy()


def test_factory_normalizes_now_and_derives_all_windows() -> None:
    jst = timezone(timedelta(hours=9))
    value = reservation(now=datetime(2026, 9, 3, 9, 19, 30, tzinfo=jst))
    assert value.now == datetime(2026, 9, 3, 0, 19, 30, tzinfo=UTC)
    assert value.user_window_start == datetime(2026, 9, 3, 0, 10, tzinfo=UTC)
    assert value.daily_window_start == datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
    assert value.monthly_window_start == datetime(2026, 8, 31, 15, 0, tzinfo=UTC)


def test_factory_rejects_naive_now() -> None:
    naive = datetime(2026, 9, 3, 0, 19, 30, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(ValueError, match="^invalid post draft usage reservation$"):
        reservation(now=naive)


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    [
        ("operation_key", OPERATION_CANARY),
        ("operation_key", str(OPERATION_CANARY)),
        ("user_id", USER_CANARY),
        ("guild_id", GUILD_CANARY),
        ("policy", object()),
    ],
)
def test_factory_rejects_raw_or_invalid_domain_types(field_name: str, invalid: object) -> None:
    with pytest.raises(ValueError, match="^invalid post draft usage reservation$"):
        reservation(**{field_name: invalid})


@pytest.mark.parametrize("invalid", [0, -1, True, 1.0, "1", MAX_POSTGRES_BIGINT + 1])
def test_factory_rejects_invalid_maximum_cost(invalid: object) -> None:
    with pytest.raises(ValueError, match="^invalid post draft usage reservation$"):
        reservation(maximum_cost_microunits=invalid)


def test_factory_does_not_accept_injected_window_starts() -> None:
    parameters = inspect.signature(PostDraftUsageReservation.create).parameters
    assert tuple(parameters) == (
        "operation_key",
        "user_id",
        "guild_id",
        "maximum_cost_microunits",
        "now",
        "policy",
    )
    assert all(not name.endswith("window_start") for name in parameters)


def test_sensitive_values_are_absent_from_repr_and_exceptions() -> None:
    value = reservation(maximum_cost_microunits=COST_CANARY)
    with pytest.raises(ValueError) as operation_error:
        reservation(operation_key=OPERATION_CANARY)
    with pytest.raises(ValueError) as user_error:
        reservation(user_id=USER_CANARY)
    with pytest.raises(ValueError) as guild_error:
        reservation(guild_id=GUILD_CANARY)
    with pytest.raises(ValueError) as cost_error:
        reservation(maximum_cost_microunits=COST_CANARY + MAX_POSTGRES_BIGINT)

    observed = " ".join(
        (
            repr(value),
            str(operation_error.value),
            str(user_error.value),
            str(guild_error.value),
            str(cost_error.value),
        )
    )
    for canary in (OPERATION_CANARY, USER_CANARY, GUILD_CANARY, COST_CANARY):
        assert str(canary) not in observed


def test_reservation_fields_cannot_hold_payload_or_external_identifiers() -> None:
    field_names = {field.name for field in fields(PostDraftUsageReservation)}
    assert field_names == {
        "operation_key",
        "user_id",
        "guild_id",
        "user_window_start",
        "daily_window_start",
        "monthly_window_start",
        "maximum_cost_microunits",
        "now",
        "policy",
    }
    assert {
        "prompt",
        "purpose",
        "key_points",
        "body",
        "content",
        "draft",
        "schedule_id",
        "channel_id",
        "interaction_id",
        "provider_id",
    }.isdisjoint(field_names)


def test_repository_port_has_one_provider_independent_async_method() -> None:
    public_methods = {
        name: member
        for name, member in inspect.getmembers(PostDraftUsageRepository, inspect.isfunction)
        if not name.startswith("_")
    }
    assert set(public_methods) == {"reserve"}
    assert inspect.iscoroutinefunction(public_methods["reserve"])
    hints = get_type_hints(public_methods["reserve"])
    assert hints == {
        "reservation": PostDraftUsageReservation,
        "return": PostDraftUsageReservationResult,
    }


def test_application_module_imports_only_standard_library_and_usage_domain() -> None:
    module_path = (
        Path(__file__).parents[1] / "src/discord_ai_reminder_bot/application/post_draft_usage.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert imported_modules <= {
        "__future__",
        "dataclasses",
        "datetime",
        "typing",
        "discord_ai_reminder_bot.domain.post_draft_usage",
    }


@pytest.mark.asyncio
async def test_already_reserved_is_not_a_new_successful_reservation() -> None:
    repository: PostDraftUsageRepository = FakeUsageRepository(
        PostDraftUsageReservationCode.ALREADY_RESERVED
    )
    result = await repository.reserve(reservation())
    assert result.code is PostDraftUsageReservationCode.ALREADY_RESERVED
    assert result.code is not PostDraftUsageReservationCode.RESERVED
