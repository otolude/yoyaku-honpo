from dataclasses import fields
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from discord_ai_reminder_bot.domain.post_draft_usage import (
    MAX_POSTGRES_BIGINT,
    PostDraftGuildId,
    PostDraftOperationKey,
    PostDraftOperatorBudgetPolicy,
    PostDraftRateLimitPolicy,
    PostDraftUsagePolicy,
    PostDraftUsageReservationCode,
    PostDraftUsageReservationResult,
    PostDraftUserId,
    jst_daily_window_start,
    jst_monthly_window_start,
    retention_cutoff,
    user_fixed_window_start,
    validate_maximum_cost_microunits,
)

ID_CANARY = 8_765_432_109_876_543
COST_CANARY = 7_654_321_098_765_432
UUID_CANARY = UUID("f84c1dc1-cce8-4827-96e6-b7166f28bca7")


def test_operator_budget_policy_has_mvp_defaults() -> None:
    policy = PostDraftOperatorBudgetPolicy()
    assert policy.daily_request_limit == 50
    assert policy.monthly_request_limit == 500
    assert policy.monthly_cost_limit_microunits == 500_000_000
    assert policy.cost_currency == "JPY"
    assert policy.retention_days == 90


def test_rate_limit_policy_has_mvp_defaults() -> None:
    policy = PostDraftRateLimitPolicy()
    assert policy.user_request_limit == 3
    assert policy.user_window_minutes == 10
    assert policy.guild_daily_request_limit == 30
    assert policy.user_retention_days == 7
    assert policy.guild_retention_days == 30


def test_usage_policy_has_mvp_defaults() -> None:
    policy = PostDraftUsagePolicy()
    assert policy.operator_budget == PostDraftOperatorBudgetPolicy()
    assert policy.rate_limit == PostDraftRateLimitPolicy()
    assert policy.maximum_concurrency == 1
    assert policy.receipt_retention_days == 7


def test_mvp_fixed_limits_accept_only_supported_values() -> None:
    assert PostDraftRateLimitPolicy(user_window_minutes=10).user_window_minutes == 10
    assert PostDraftUsagePolicy(maximum_concurrency=1).maximum_concurrency == 1


@pytest.mark.parametrize("invalid", [0, -1, 1, 9, 11, 60, True, 10.0, "10", MAX_POSTGRES_BIGINT])
def test_user_window_rejects_values_other_than_fixed_ten_minutes(invalid: object) -> None:
    with pytest.raises(ValueError, match="^invalid post draft usage policy$"):
        PostDraftRateLimitPolicy(user_window_minutes=invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", [0, -1, True, 1.0, "1", 2, 3, 60, MAX_POSTGRES_BIGINT])
def test_maximum_concurrency_rejects_values_other_than_one(invalid: object) -> None:
    with pytest.raises(ValueError, match="^invalid post draft usage policy$"):
        PostDraftUsagePolicy(maximum_concurrency=invalid)  # type: ignore[arg-type]


def test_fixed_limit_errors_and_repr_do_not_expose_input_canaries() -> None:
    window_canary = 60_123
    concurrency_canary = 70_123
    with pytest.raises(ValueError) as window_error:
        PostDraftRateLimitPolicy(user_window_minutes=window_canary)
    with pytest.raises(ValueError) as concurrency_error:
        PostDraftUsagePolicy(maximum_concurrency=concurrency_canary)

    observed = " ".join(
        (
            repr(PostDraftRateLimitPolicy()),
            repr(PostDraftUsagePolicy()),
            str(window_error.value),
            str(concurrency_error.value),
        )
    )
    assert str(window_canary) not in observed
    assert str(concurrency_canary) not in observed


def test_fixed_ten_minute_calculation_matches_rate_limit_policy() -> None:
    policy = PostDraftRateLimitPolicy()
    start = user_fixed_window_start(datetime(2026, 9, 3, 0, 9, 59, tzinfo=UTC))
    next_start = user_fixed_window_start(datetime(2026, 9, 3, 0, 10, tzinfo=UTC))
    assert next_start - start == timedelta(minutes=policy.user_window_minutes)


@pytest.mark.parametrize(
    ("policy_type", "field_name"),
    [
        (PostDraftOperatorBudgetPolicy, "daily_request_limit"),
        (PostDraftOperatorBudgetPolicy, "monthly_request_limit"),
        (PostDraftOperatorBudgetPolicy, "monthly_cost_limit_microunits"),
        (PostDraftOperatorBudgetPolicy, "retention_days"),
        (PostDraftRateLimitPolicy, "user_request_limit"),
        (PostDraftRateLimitPolicy, "guild_daily_request_limit"),
        (PostDraftRateLimitPolicy, "user_retention_days"),
        (PostDraftRateLimitPolicy, "guild_retention_days"),
        (PostDraftUsagePolicy, "receipt_retention_days"),
    ],
)
@pytest.mark.parametrize("invalid", [0, -1, True, 1.0, "1"])
def test_policy_integer_fields_reject_invalid_values(
    policy_type: type, field_name: str, invalid: object
) -> None:
    with pytest.raises(ValueError):
        policy_type(**{field_name: invalid})


@pytest.mark.parametrize(
    ("policy_type", "field_name"),
    [
        (PostDraftOperatorBudgetPolicy, "daily_request_limit"),
        (PostDraftOperatorBudgetPolicy, "monthly_request_limit"),
        (PostDraftOperatorBudgetPolicy, "monthly_cost_limit_microunits"),
        (PostDraftOperatorBudgetPolicy, "retention_days"),
        (PostDraftRateLimitPolicy, "user_request_limit"),
        (PostDraftRateLimitPolicy, "guild_daily_request_limit"),
        (PostDraftRateLimitPolicy, "user_retention_days"),
        (PostDraftRateLimitPolicy, "guild_retention_days"),
        (PostDraftUsagePolicy, "receipt_retention_days"),
    ],
)
def test_policy_integer_fields_accept_bigint_boundary_and_reject_overflow(
    policy_type: type, field_name: str
) -> None:
    overrides: dict[str, object] = {field_name: MAX_POSTGRES_BIGINT}
    if policy_type is PostDraftOperatorBudgetPolicy and field_name == "daily_request_limit":
        overrides["monthly_request_limit"] = MAX_POSTGRES_BIGINT
    if policy_type is PostDraftRateLimitPolicy and field_name == "guild_daily_request_limit":
        overrides["global_daily_request_limit"] = MAX_POSTGRES_BIGINT
    assert getattr(policy_type(**overrides), field_name) == MAX_POSTGRES_BIGINT
    overrides[field_name] = MAX_POSTGRES_BIGINT + 1
    with pytest.raises(ValueError):
        policy_type(**overrides)


def test_operator_policy_rejects_monthly_request_limit_below_daily() -> None:
    with pytest.raises(ValueError, match="^invalid post draft usage policy$"):
        PostDraftOperatorBudgetPolicy(daily_request_limit=51, monthly_request_limit=50)


def test_rate_policy_rejects_guild_daily_limit_above_global_daily() -> None:
    with pytest.raises(ValueError, match="^invalid post draft usage policy$"):
        PostDraftRateLimitPolicy(guild_daily_request_limit=51, global_daily_request_limit=50)


def test_usage_policy_directly_rejects_mismatched_global_daily_limits() -> None:
    matching = PostDraftUsagePolicy(
        operator_budget=PostDraftOperatorBudgetPolicy(daily_request_limit=60),
        rate_limit=PostDraftRateLimitPolicy(global_daily_request_limit=60),
    )
    assert matching.operator_budget.daily_request_limit == 60
    assert matching.rate_limit.global_daily_request_limit == 60

    with pytest.raises(ValueError, match="^invalid post draft usage policy$") as error:
        PostDraftUsagePolicy(
            operator_budget=PostDraftOperatorBudgetPolicy(daily_request_limit=61),
            rate_limit=PostDraftRateLimitPolicy(global_daily_request_limit=60),
        )
    assert "61" not in str(error.value)
    assert "60" not in str(error.value)


@pytest.mark.parametrize("currency", ["USD", "jpy", "", 1, None])
def test_operator_policy_rejects_currency_other_than_jpy(currency: object) -> None:
    with pytest.raises(ValueError, match="^invalid post draft usage policy$"):
        PostDraftOperatorBudgetPolicy(cost_currency=currency)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "function", [user_fixed_window_start, jst_daily_window_start, jst_monthly_window_start]
)
def test_window_calculations_reject_naive_datetime(function) -> None:
    with pytest.raises(ValueError, match="^timestamp must be timezone-aware$"):
        function(datetime(2026, 9, 3, 12, 0, tzinfo=UTC).replace(tzinfo=None))


def test_user_fixed_window_handles_before_boundary_boundary_and_after() -> None:
    assert user_fixed_window_start(datetime(2026, 9, 3, 0, 9, 59, 999999, tzinfo=UTC)) == datetime(
        2026, 9, 3, 0, 0, tzinfo=UTC
    )
    assert user_fixed_window_start(datetime(2026, 9, 3, 0, 10, tzinfo=UTC)) == datetime(
        2026, 9, 3, 0, 10, tzinfo=UTC
    )
    assert user_fixed_window_start(datetime(2026, 9, 3, 0, 10, 0, 1, tzinfo=UTC)) == datetime(
        2026, 9, 3, 0, 10, tzinfo=UTC
    )


def test_user_fixed_window_normalizes_offset_to_utc() -> None:
    jst = timezone(timedelta(hours=9))
    assert user_fixed_window_start(datetime(2026, 9, 3, 9, 19, tzinfo=jst)) == datetime(
        2026, 9, 3, 0, 10, tzinfo=UTC
    )


def test_jst_daily_start_handles_date_change_boundary() -> None:
    assert jst_daily_window_start(datetime(2026, 9, 2, 14, 59, 59, tzinfo=UTC)) == datetime(
        2026, 9, 1, 15, 0, tzinfo=UTC
    )
    assert jst_daily_window_start(datetime(2026, 9, 2, 15, 0, tzinfo=UTC)) == datetime(
        2026, 9, 2, 15, 0, tzinfo=UTC
    )


def test_jst_monthly_start_handles_month_boundary() -> None:
    assert jst_monthly_window_start(datetime(2026, 8, 31, 14, 59, 59, tzinfo=UTC)) == datetime(
        2026, 7, 31, 15, 0, tzinfo=UTC
    )
    assert jst_monthly_window_start(datetime(2026, 8, 31, 15, 0, tzinfo=UTC)) == datetime(
        2026, 8, 31, 15, 0, tzinfo=UTC
    )


def test_retention_cutoff_normalizes_to_utc_without_reading_a_clock() -> None:
    jst = timezone(timedelta(hours=9))
    assert retention_cutoff(datetime(2026, 9, 3, 9, 0, tzinfo=jst), retention_days=7) == datetime(
        2026, 8, 27, 0, 0, tzinfo=UTC
    )


@pytest.mark.parametrize("invalid", [0, -1, True, 1.0, "1", MAX_POSTGRES_BIGINT + 1])
def test_retention_cutoff_rejects_invalid_days(invalid: object) -> None:
    with pytest.raises(ValueError, match="^invalid post draft usage policy$"):
        retention_cutoff(datetime(2026, 9, 3, tzinfo=UTC), retention_days=invalid)  # type: ignore[arg-type]


def test_operation_key_requires_uuid_without_implicit_string_conversion() -> None:
    value = uuid4()
    assert PostDraftOperationKey(value).value == value
    with pytest.raises(ValueError, match="^operation key must be a UUID$"):
        PostDraftOperationKey(str(value))  # type: ignore[arg-type]


@pytest.mark.parametrize("identifier_type", [PostDraftUserId, PostDraftGuildId])
def test_subject_ids_accept_positive_bigint_boundary(identifier_type: type) -> None:
    assert identifier_type(MAX_POSTGRES_BIGINT).value == MAX_POSTGRES_BIGINT


@pytest.mark.parametrize("identifier_type", [PostDraftUserId, PostDraftGuildId])
@pytest.mark.parametrize("invalid", [0, -1, True, 1.0, "1", MAX_POSTGRES_BIGINT + 1])
def test_subject_ids_reject_invalid_values(identifier_type: type, invalid: object) -> None:
    with pytest.raises(ValueError, match="^invalid post draft subject identifier$"):
        identifier_type(invalid)


def test_usage_reservation_result_codes_are_fixed() -> None:
    assert {code.value for code in PostDraftUsageReservationCode} == {
        "reserved",
        "already_reserved",
        "user_rate_limited",
        "guild_rate_limited",
        "global_daily_exhausted",
        "global_monthly_exhausted",
        "global_cost_exhausted",
        "price_unknown",
        "invalid_policy",
        "usage_unavailable",
    }
    for code in PostDraftUsageReservationCode:
        assert PostDraftUsageReservationResult(code).code is code
    with pytest.raises(ValueError):
        PostDraftUsageReservationResult("reserved")  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", [0, -1, True, 1.0, "1", MAX_POSTGRES_BIGINT + 1])
def test_maximum_cost_validation_rejects_invalid_values(invalid: object) -> None:
    with pytest.raises(ValueError, match="^invalid maximum cost$"):
        validate_maximum_cost_microunits(invalid)


def test_maximum_cost_validation_accepts_bigint_boundary() -> None:
    assert validate_maximum_cost_microunits(MAX_POSTGRES_BIGINT) == MAX_POSTGRES_BIGINT


def test_sensitive_identifiers_and_cost_are_absent_from_repr_and_errors() -> None:
    operation = PostDraftOperationKey(UUID_CANARY)
    with pytest.raises(ValueError) as id_error:
        PostDraftUserId(ID_CANARY + MAX_POSTGRES_BIGINT)
    with pytest.raises(ValueError) as cost_error:
        validate_maximum_cost_microunits(COST_CANARY + MAX_POSTGRES_BIGINT)
    observed = " ".join((repr(operation), str(id_error.value), str(cost_error.value)))
    assert str(ID_CANARY) not in observed
    assert str(UUID_CANARY) not in observed
    assert str(COST_CANARY) not in observed


def test_domain_types_cannot_hold_post_body_payloads_or_identifiers() -> None:
    domain_types = (
        PostDraftOperatorBudgetPolicy,
        PostDraftRateLimitPolicy,
        PostDraftUsagePolicy,
        PostDraftUsageReservationResult,
        PostDraftOperationKey,
    )
    prohibited = {
        "purpose",
        "key_points",
        "body",
        "content",
        "draft",
        "schedule_id",
        "interaction_id",
        "user_id",
        "guild_id",
    }
    for domain_type in domain_types:
        assert prohibited.isdisjoint(field.name for field in fields(domain_type))


def test_identifier_types_only_hold_their_validated_opaque_value() -> None:
    assert {field.name for field in fields(PostDraftUserId)} == {"value"}
    assert {field.name for field in fields(PostDraftGuildId)} == {"value"}
