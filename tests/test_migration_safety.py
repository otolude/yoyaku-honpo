import pytest
from pydantic import SecretStr

from discord_ai_reminder_bot.infrastructure.database.migration_safety import (
    MigrationOperation,
    MigrationSafetyError,
    MigrationTarget,
    invocation_from_environment,
    select_database_url,
    validate_invocation,
    validate_url_database,
)


@pytest.mark.parametrize("value", [None, "", " test", "test ", "TEST", True, "staging"])
def test_target_is_explicit_closed_and_case_sensitive(value) -> None:
    with pytest.raises(MigrationSafetyError):
        validate_invocation(target=value, expected_database="discord_bot_test", operation="current")


@pytest.mark.parametrize("value", [None, "", " discord_bot_test", "DISCORD_BOT_TEST", True])
def test_expected_database_rejects_missing_ambiguous_values(value) -> None:
    with pytest.raises(MigrationSafetyError):
        validate_invocation(target="test", expected_database=value, operation="current")


def test_target_database_pairs_are_fixed_except_explicit_production() -> None:
    test = validate_invocation(
        target="test", expected_database="discord_bot_test", operation="current"
    )
    development = validate_invocation(
        target="development", expected_database="discord_bot_dev", operation="check"
    )
    production = validate_invocation(
        target="production", expected_database="bot_release", operation="current"
    )
    assert (test.target, development.target, production.expected_database) == (
        MigrationTarget.TEST,
        MigrationTarget.DEVELOPMENT,
        "bot_release",
    )
    with pytest.raises(MigrationSafetyError):
        validate_invocation(target="test", expected_database="discord_bot_dev", operation="current")
    with pytest.raises(MigrationSafetyError):
        validate_invocation(
            target="development", expected_database="discord_bot_test", operation="current"
        )
    with pytest.raises(MigrationSafetyError):
        validate_invocation(target="production", expected_database=None, operation="current")


@pytest.mark.parametrize("operation", ["upgrade", "downgrade", "stamp", "autogenerate"])
def test_write_operations_require_exact_bound_confirmation(operation: str) -> None:
    expected = f"test:discord_bot_test:{operation}"
    invocation = validate_invocation(
        target="test",
        expected_database="discord_bot_test",
        operation=operation,
        confirmation=expected,
    )
    assert invocation.required_confirmation == expected
    for bad in (None, True, "true", expected.upper(), f"prefix-{expected}", expected[:-1]):
        with pytest.raises(MigrationSafetyError):
            validate_invocation(
                target="test",
                expected_database="discord_bot_test",
                operation=operation,
                confirmation=bad,
            )


@pytest.mark.parametrize("operation", ["current", "check"])
def test_read_operations_need_no_confirmation_and_reject_one(operation: str) -> None:
    assert (
        validate_invocation(
            target="test", expected_database="discord_bot_test", operation=operation
        ).operation.value
        == operation
    )
    with pytest.raises(MigrationSafetyError):
        validate_invocation(
            target="test",
            expected_database="discord_bot_test",
            operation=operation,
            confirmation="test:discord_bot_test:upgrade",
        )


def test_direct_cli_environment_requires_all_safety_values() -> None:
    with pytest.raises(MigrationSafetyError):
        invocation_from_environment(
            MigrationOperation.UPGRADE,
            {
                "TEST_DATABASE_URL": "postgresql+psycopg://ignored:ignored@localhost/discord_bot_test"
            },
        )
    invocation = invocation_from_environment(
        MigrationOperation.UPGRADE,
        {
            "MIGRATION_TARGET_ENV": "test",
            "MIGRATION_EXPECTED_DATABASE": "discord_bot_test",
            "MIGRATION_APPLY_CONFIRMATION": "test:discord_bot_test:upgrade",
        },
    )
    assert invocation.operation is MigrationOperation.UPGRADE


def test_test_and_production_urls_never_fall_back() -> None:
    test = validate_invocation(
        target="test", expected_database="discord_bot_test", operation="current"
    )
    production = validate_invocation(
        target="production", expected_database="release_db", operation="current"
    )
    with pytest.raises(MigrationSafetyError):
        select_database_url(
            test,
            {"DATABASE_URL": "postgresql+psycopg://dev:secret@devhost/discord_bot_dev"},
        )
    with pytest.raises(MigrationSafetyError):
        select_database_url(
            production,
            {"TEST_DATABASE_URL": "postgresql+psycopg://test:secret@testhost/discord_bot_test"},
        )


def test_url_database_requires_exact_match_without_exposing_credentials() -> None:
    secret = "postgresql+psycopg://private-user:private-password@private-host/discord_bot_dev"
    with pytest.raises(MigrationSafetyError) as captured:
        validate_url_database(SecretStr(secret), "discord_bot_test")
    rendered = str(captured.value)
    assert all(
        value not in rendered
        for value in (secret, "private-user", "private-password", "private-host")
    )
