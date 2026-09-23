from __future__ import annotations

import ast
import dataclasses
import re
import socket
import subprocess
import traceback
from pathlib import Path

import discord
import httpx
import openai
import pytest
import sqlalchemy

from discord_ai_reminder_bot.application import systemd_deployment
from discord_ai_reminder_bot.application.systemd_deployment import (
    INSTALLABLE_SYSTEMD_ARTIFACT_GENERATION_APPROVED,
    REQUIRED_SYSTEMD_CANDIDATE_FIELDS,
    SystemdDeploymentFailure,
    SystemdDeploymentValidationError,
    build_systemd_deployment_candidate,
    require_installable_systemd_artifact_approval,
    snapshot_systemd_deployment_candidate,
    validate_systemd_deployment_candidate,
    verify_systemd_candidate_snapshot,
)
from discord_ai_reminder_bot.post_draft_provider_config import (
    CLIENT_SHUTDOWN_STRATEGY_APPROVED,
    PRODUCTION_REAL_PROVIDER_GATE_OPEN,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "src/discord_ai_reminder_bot/application/systemd_deployment.py"
TEMPLATE_PATH = REPOSITORY_ROOT / "deployment/systemd/discord-ai-reminder-bot.service.in"
DOCUMENT_PATHS = (
    REPOSITORY_ROOT / "docs/manual-acceptance-ai-post-drafting.md",
    REPOSITORY_ROOT / "docs/operations.md",
    REPOSITORY_ROOT / "docs/development-roadmap.md",
    REPOSITORY_ROOT / "docs/requirements-beta.md",
    REPOSITORY_ROOT / "docs/technical-design-beta.md",
)


def _candidate_values() -> dict[str, object]:
    return {
        "service_user": "discord_bot",
        "service_group": "discord_bot",
        "working_directory": "/srv/discord-bot/releases/aaaaaaaa",
        "exec_start": (
            "/srv/discord-bot/releases/aaaaaaaa/.venv/bin/python",
            "-m",
            "discord_ai_reminder_bot",
        ),
        "environment_file": "/etc/discord-bot/runtime.env",
        "kill_signal": "SIGINT",
        "kill_mode": "control-group",
        "timeout_stop_sec": "120",
        "send_sigkill": True,
        "restart": "on-failure",
        "restart_sec": "5",
        "start_limit_interval_sec": "300",
        "start_limit_burst": "3",
        "readiness_journal_marker": "startup_recovery_complete",
        "release_identifier": "a" * 40,
        "rollback_target": "b" * 40,
        "no_new_privileges": True,
        "private_tmp": True,
        "protect_system": "strict",
        "protect_home": "yes",
        "capability_bounding_set": ("CAP_NET_BIND_SERVICE",),
        "restrict_address_families": ("AF_UNIX", "AF_INET6", "AF_INET"),
        "lock_personality": True,
        "restrict_suid_sgid": True,
        "umask": "0077",
    }


def _candidate_pairs(**changes: object) -> tuple[tuple[str, object], ...]:
    values = _candidate_values() | changes
    return tuple(values.items())


def _candidate(**changes: object) -> object:
    return build_systemd_deployment_candidate(_candidate_pairs(**changes))


def test_complete_candidate_contract_is_valid_and_normalized() -> None:
    result = validate_systemd_deployment_candidate(_candidate_pairs())

    assert result.valid is True
    assert result.failure is None
    assert type(result.candidate) is systemd_deployment._SystemdDeploymentCandidate
    assert result.candidate is not None
    assert result.candidate.timeout_stop_sec == 120
    assert result.candidate.restrict_address_families == (
        "AF_INET",
        "AF_INET6",
        "AF_UNIX",
    )
    assert len(REQUIRED_SYSTEMD_CANDIDATE_FIELDS) == 25


@pytest.mark.parametrize("missing_field", REQUIRED_SYSTEMD_CANDIDATE_FIELDS)
def test_each_required_candidate_field_is_fail_closed(missing_field: str) -> None:
    pairs = tuple(pair for pair in _candidate_pairs() if pair[0] != missing_field)

    result = validate_systemd_deployment_candidate(pairs)

    assert result.valid is False
    assert result.candidate is None
    assert result.failure is SystemdDeploymentFailure.MISSING_FIELD


def test_unknown_duplicate_and_non_tuple_containers_are_rejected() -> None:
    unknown = _candidate_pairs() + (("unknown", "value"),)
    duplicate = _candidate_pairs() + (("service_user", "another"),)

    assert validate_systemd_deployment_candidate(unknown).failure is (
        SystemdDeploymentFailure.UNKNOWN_FIELD
    )
    assert validate_systemd_deployment_candidate(duplicate).failure is (
        SystemdDeploymentFailure.DUPLICATE_FIELD
    )
    assert validate_systemd_deployment_candidate(dict(_candidate_pairs())).failure is (
        SystemdDeploymentFailure.INVALID_CONTAINER
    )


@pytest.mark.parametrize(
    "field",
    (
        "timeout_stop_sec",
        "restart_sec",
        "start_limit_interval_sec",
        "start_limit_burst",
    ),
)
@pytest.mark.parametrize(
    "invalid",
    (
        True,
        False,
        0,
        -1,
        1.0,
        "",
        " 1",
        "1 ",
        "+1",
        "01",
        "1e3",
        "NaN",
        "Infinity",
        "1,000",
        2_147_483_648,
    ),
)
def test_numeric_fields_reject_noncanonical_values(field: str, invalid: object) -> None:
    result = validate_systemd_deployment_candidate(_candidate_pairs(**{field: invalid}))

    assert result.valid is False
    assert result.failure is SystemdDeploymentFailure.INVALID_VALUE


def test_huge_numeric_input_is_rejected_before_regex_or_integer_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ForbiddenRegex:
        calls = 0

        def fullmatch(self, value: str) -> None:
            del value
            self.calls += 1
            raise AssertionError("regex must not receive a huge numeric input")

    parse_calls = 0

    def forbidden_parse(value: str) -> int:
        nonlocal parse_calls
        del value
        parse_calls += 1
        raise AssertionError("integer conversion must not receive a huge numeric input")

    forbidden_regex = ForbiddenRegex()
    monkeypatch.setattr(systemd_deployment, "_CANONICAL_POSITIVE_INTEGER", forbidden_regex)
    monkeypatch.setattr(systemd_deployment, "_parse_canonical_positive_integer", forbidden_parse)

    result = validate_systemd_deployment_candidate(_candidate_pairs(timeout_stop_sec="9" * 100_000))

    assert result.valid is False
    assert result.failure is SystemdDeploymentFailure.INVALID_VALUE
    assert forbidden_regex.calls == 0
    assert parse_calls == 0


def test_exact_ints_use_the_same_bound_as_canonical_strings() -> None:
    maximum = systemd_deployment.MAX_SYSTEMD_INTEGER

    assert (
        validate_systemd_deployment_candidate(_candidate_pairs(timeout_stop_sec=maximum)).valid
        is True
    )
    assert (
        validate_systemd_deployment_candidate(_candidate_pairs(timeout_stop_sec=str(maximum))).valid
        is True
    )
    assert (
        validate_systemd_deployment_candidate(
            _candidate_pairs(timeout_stop_sec=maximum + 1)
        ).failure
        is SystemdDeploymentFailure.INVALID_VALUE
    )
    assert (
        validate_systemd_deployment_candidate(
            _candidate_pairs(timeout_stop_sec=str(maximum + 1))
        ).failure
        is SystemdDeploymentFailure.INVALID_VALUE
    )


def test_huge_exact_ints_are_rejected_before_range_or_factory_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory_calls = 0
    fingerprint_calls = 0

    def forbidden_factory(normalized: dict[str, object]) -> object:
        nonlocal factory_calls
        del normalized
        factory_calls += 1
        raise AssertionError("factory must not receive a huge integer")

    def forbidden_fingerprint(*args: object, **kwargs: object) -> object:
        nonlocal fingerprint_calls
        del args, kwargs
        fingerprint_calls += 1
        raise AssertionError("fingerprint must not receive a huge integer")

    monkeypatch.setattr(systemd_deployment, "_make_candidate", forbidden_factory)
    monkeypatch.setattr(systemd_deployment.hashlib, "sha256", forbidden_fingerprint)

    for huge_value in (1 << 100_000, -(1 << 100_000)):
        result = validate_systemd_deployment_candidate(
            _candidate_pairs(timeout_stop_sec=huge_value)
        )
        assert result.valid is False
        assert result.failure is SystemdDeploymentFailure.INVALID_VALUE

    assert factory_calls == 0
    assert fingerprint_calls == 0


def test_exact_int_size_guard_precedes_range_comparison_without_string_conversion() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_positive_integer"
    )
    exact_guard_lines = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_exact_int_within_systemd_bound"
    ]
    range_comparison_lines = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Compare)
        and any(
            isinstance(operand, ast.Name) and operand.id == "parsed"
            for operand in (node.left, *node.comparators)
        )
    ]
    string_conversion_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str"
    ]

    assert exact_guard_lines and range_comparison_lines
    assert max(exact_guard_lines) < min(range_comparison_lines)
    assert string_conversion_calls == []


def test_bool_and_int_subclass_remain_rejected_without_reflecting_values() -> None:
    class IntSubclass(int):
        pass

    for invalid in (True, False, IntSubclass(1)):
        with pytest.raises(SystemdDeploymentValidationError) as raised:
            build_systemd_deployment_candidate(_candidate_pairs(timeout_stop_sec=invalid))

        assert raised.value.classification is SystemdDeploymentFailure.INVALID_VALUE
        assert str(raised.value) == "invalid_candidate_value"
        assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    "field",
    (
        "send_sigkill",
        "no_new_privileges",
        "private_tmp",
        "lock_personality",
        "restrict_suid_sgid",
    ),
)
@pytest.mark.parametrize("invalid", (0, 1, "true", "false", None))
def test_boolean_directives_require_exact_bool(field: str, invalid: object) -> None:
    result = validate_systemd_deployment_candidate(_candidate_pairs(**{field: invalid}))

    assert result.valid is False
    assert result.failure is SystemdDeploymentFailure.INVALID_VALUE


@pytest.mark.parametrize("field", ("working_directory", "environment_file"))
@pytest.mark.parametrize(
    "invalid",
    (
        "relative/path",
        "/path/../escape",
        "/path/./entry",
        "/path//entry",
        "/path/entry/",
        "/path with space/entry",
        "/path/%n/entry",
        "/path\nDirective=yes",
        "/path\x00entry",
        "",
    ),
)
def test_absolute_paths_reject_traversal_and_injection(field: str, invalid: str) -> None:
    result = validate_systemd_deployment_candidate(_candidate_pairs(**{field: invalid}))

    assert result.valid is False
    assert result.failure is SystemdDeploymentFailure.INVALID_VALUE


@pytest.mark.parametrize(
    "invalid",
    (
        ("relative/python", "-m", "discord_ai_reminder_bot"),
        ("/safe/python", "-c", "discord_ai_reminder_bot"),
        ("/safe/python", "-m", "other_module"),
        ("/safe/python\nEnvironment=x", "-m", "discord_ai_reminder_bot"),
        ("/safe/python", "-m"),
        ["/safe/python", "-m", "discord_ai_reminder_bot"],
    ),
)
def test_exec_start_is_shell_free_and_exact(invalid: object) -> None:
    result = validate_systemd_deployment_candidate(_candidate_pairs(exec_start=invalid))

    assert result.valid is False
    assert result.failure is SystemdDeploymentFailure.INVALID_VALUE


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("service_user", "root\nEnvironment=SECRET"),
        ("service_user", "root"),
        ("service_group", "daemon"),
        ("service_group", "group name"),
        ("kill_signal", "SIGKILL"),
        ("kill_signal", "sigint"),
        ("kill_mode", "process"),
        ("restart", "always"),
        ("protect_system", "yes"),
        ("protect_home", "false"),
        ("readiness_journal_marker", "database_schema_verified"),
        ("umask", "0000"),
        ("capability_bounding_set", ("CAP_UNKNOWN",)),
        ("capability_bounding_set", ()),
        ("capability_bounding_set", []),
        ("capability_bounding_set", "CAP_NET_BIND_SERVICE"),
        ("capability_bounding_set", {"CAP_NET_BIND_SERVICE"}),
        ("capability_bounding_set", ("CAP_NET_BIND_SERVICE", "CAP_NET_BIND_SERVICE")),
        ("restrict_address_families", ("AF_PACKET",)),
        ("restrict_address_families", ()),
        ("restrict_address_families", ("AF_INET", "AF_INET")),
    ),
)
def test_text_and_tuple_fields_use_exact_allowlists(field: str, invalid: object) -> None:
    result = validate_systemd_deployment_candidate(_candidate_pairs(**{field: invalid}))

    assert result.valid is False
    assert result.failure is SystemdDeploymentFailure.INVALID_VALUE


def test_candidate_snapshot_is_immutable_canonical_and_order_stable() -> None:
    first = _candidate()
    reversed_pairs = tuple(reversed(_candidate_pairs()))
    second = build_systemd_deployment_candidate(reversed_pairs)
    first_snapshot = snapshot_systemd_deployment_candidate(first)
    second_snapshot = snapshot_systemd_deployment_candidate(second)

    assert first_snapshot == second_snapshot
    assert first_snapshot.fingerprint == second_snapshot.fingerprint
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.timeout_stop_sec = 1  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        first_snapshot.fingerprint = "changed"  # type: ignore[misc]


def test_candidate_and_snapshot_are_factory_only_private_nominal_types() -> None:
    canary = "PRIVATE_FIELD_NAME_CANARY"

    for constructor in (
        systemd_deployment._SystemdDeploymentCandidate,
        systemd_deployment._SystemdCandidateSnapshot,
    ):
        with pytest.raises(SystemdDeploymentValidationError) as raised:
            constructor(**{canary: canary})

        assert raised.value.classification is SystemdDeploymentFailure.INVALID_CONTAINER
        assert canary not in str(raised.value)
        assert canary not in repr(raised.value)
        assert raised.value.__cause__ is None
        assert canary not in "".join(
            traceback.format_exception(type(raised.value), raised.value, raised.value.__traceback__)
        )

    assert "SystemdDeploymentCandidate" not in systemd_deployment.__all__
    assert "SystemdCandidateSnapshot" not in systemd_deployment.__all__
    assert not hasattr(systemd_deployment, "SystemdDeploymentCandidate")
    assert not hasattr(systemd_deployment, "SystemdCandidateSnapshot")


def test_private_nominal_types_reject_subclassing_and_fingerprint_injection() -> None:
    with pytest.raises(TypeError):

        class CandidateSubclass(systemd_deployment._SystemdDeploymentCandidate):
            pass

    with pytest.raises(TypeError):

        class SnapshotSubclass(systemd_deployment._SystemdCandidateSnapshot):
            pass

    with pytest.raises(SystemdDeploymentValidationError) as raised:
        systemd_deployment._SystemdCandidateSnapshot((), "caller-supplied-fingerprint")
    assert raised.value.classification is SystemdDeploymentFailure.INVALID_CONTAINER


def test_capability_bounding_set_is_nonempty_duplicate_free_and_canonical() -> None:
    candidate = _candidate(
        capability_bounding_set=("CAP_NET_RAW", "CAP_NET_BIND_SERVICE"),
    )

    assert candidate.capability_bounding_set == ("CAP_NET_BIND_SERVICE", "CAP_NET_RAW")
    assert (
        validate_systemd_deployment_candidate(
            _candidate_pairs(capability_bounding_set=("CAP_NET_RAW", "CAP_NET_RAW"))
        ).failure
        is SystemdDeploymentFailure.INVALID_VALUE
    )


def test_snapshot_detects_candidate_change_without_reflecting_values() -> None:
    original = _candidate()
    snapshot = snapshot_systemd_deployment_candidate(original)
    changed = _candidate(restart_sec=6)

    with pytest.raises(SystemdDeploymentValidationError) as raised:
        verify_systemd_candidate_snapshot(changed, snapshot)

    assert raised.value.classification is SystemdDeploymentFailure.SNAPSHOT_MISMATCH
    assert str(raised.value) == "candidate_snapshot_mismatch"
    assert "6" not in str(raised.value)


def test_invalid_private_looking_input_and_repr_are_non_reflecting() -> None:
    canary = "PRIVATE_VALUE_CANARY"
    result = validate_systemd_deployment_candidate(
        _candidate_pairs(service_user=f"bad\nEnvironment={canary}")
    )
    with pytest.raises(SystemdDeploymentValidationError) as raised:
        build_systemd_deployment_candidate(
            _candidate_pairs(service_user=f"bad\nEnvironment={canary}")
        )

    assert result.valid is False
    assert canary not in repr(result)
    assert canary not in str(result.failure)
    assert raised.value.__cause__ is None
    assert canary not in "".join(
        traceback.format_exception(type(raised.value), raised.value, raised.value.__traceback__)
    )
    assert repr(_candidate()) == "<SystemdDeploymentCandidate redacted>"
    assert repr(snapshot_systemd_deployment_candidate(_candidate())) == (
        "<SystemdCandidateSnapshot redacted>"
    )


def test_source_governance_blocks_output_and_environment_cannot_open_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLIENT_SHUTDOWN_STRATEGY_APPROVED", "true")
    monkeypatch.setenv("SYSTEMD_DEPLOYMENT_ACTIVATION_APPROVED", "true")

    assert CLIENT_SHUTDOWN_STRATEGY_APPROVED is False
    assert INSTALLABLE_SYSTEMD_ARTIFACT_GENERATION_APPROVED is False
    assert PRODUCTION_REAL_PROVIDER_GATE_OPEN is False
    with pytest.raises(SystemdDeploymentValidationError) as raised:
        require_installable_systemd_artifact_approval()
    assert raised.value.classification is SystemdDeploymentFailure.GOVERNANCE_CLOSED

    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert not function_names & {
        "render_unit",
        "write_unit",
        "install_unit",
        "enable_unit",
        "start_unit",
        "stop_unit",
        "restart_unit",
        "daemon_reload",
    }


def test_template_is_explicitly_non_installable_and_contains_no_private_values() -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    placeholders = set(re.findall(r"@@([A-Z0-9_]+)@@", template))

    assert TEMPLATE_PATH.suffix == ".in"
    assert template.startswith("# DIRECT INSTALL IS PROHIBITED")
    assert "ExecStart=@@ABSOLUTE_EXEC_START@@" in template
    assert "TimeoutStopSec=@@TIMEOUT_STOP_SEC@@" in template
    assert "@@" in template
    assert "[Install]" not in template
    assert "WantedBy=" not in template
    assert "sudo" not in template
    assert "systemctl" not in template
    assert "$" not in template
    assert "PRIVATE_VALUE_CANARY" not in template
    assert "StandardOutput=journal" in template
    assert "StandardError=journal" in template
    assert "Empty CapabilityBoundingSet semantics are unapproved" in template
    assert placeholders == {
        "ABSOLUTE_ENVIRONMENT_FILE_REFERENCE",
        "ABSOLUTE_EXEC_START",
        "ABSOLUTE_IMMUTABLE_WORKING_DIRECTORY",
        "CAPABILITY_BOUNDING_SET",
        "KILL_MODE",
        "KILL_SIGNAL",
        "LOCK_PERSONALITY",
        "NO_NEW_PRIVILEGES",
        "PRIVATE_TMP",
        "PROTECT_HOME",
        "PROTECT_SYSTEM",
        "READINESS_JOURNAL_MARKER",
        "RELEASE_IDENTIFIER",
        "RESTART_POLICY",
        "RESTART_SEC",
        "RESTRICT_ADDRESS_FAMILIES",
        "RESTRICT_SUID_SGID",
        "ROLLBACK_TARGET",
        "SEND_SIGKILL",
        "SERVICE_GROUP",
        "SERVICE_USER",
        "START_LIMIT_BURST",
        "START_LIMIT_INTERVAL_SEC",
        "TIMEOUT_STOP_SEC",
        "UMASK",
    }


def test_candidate_validation_has_no_resource_or_network_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def blocked(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls.append("blocked")
        raise AssertionError("forbidden resource access")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setattr(subprocess, "run", blocked)
    monkeypatch.setattr(httpx, "AsyncClient", blocked)
    monkeypatch.setattr(openai, "AsyncOpenAI", blocked)
    monkeypatch.setattr(discord, "Client", blocked)
    monkeypatch.setattr(sqlalchemy, "create_engine", blocked)

    candidate = _candidate()
    snapshot = snapshot_systemd_deployment_candidate(candidate)
    verify_systemd_candidate_snapshot(candidate, snapshot)

    assert calls == []


def test_production_module_has_no_ambient_or_service_manager_imports() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.split(".")[0])

    assert imported_roots.isdisjoint(
        {
            "asyncio",
            "discord",
            "httpx",
            "openai",
            "os",
            "socket",
            "sqlalchemy",
            "subprocess",
        }
    )


def test_test_support_is_not_added_to_production_package() -> None:
    pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    production_sources = tuple((REPOSITORY_ROOT / "src").rglob("*.py"))

    assert 'packages = ["src/discord_ai_reminder_bot"]' in pyproject
    assert all(
        "tests.support" not in path.read_text(encoding="utf-8") for path in production_sources
    )
    assert not (REPOSITORY_ROOT / "src/tests").exists()


def test_docs_keep_fail_closed_systemd_contract_and_acceptance_totals() -> None:
    texts = tuple(path.read_text(encoding="utf-8") for path in DOCUMENT_PATHS)

    for text in texts:
        assert "確認済み55件／未確認17件（合計72件）" in text
        assert "CLIENT_SHUTDOWN_STRATEGY_APPROVED=false" in text
        assert "Real Provider gate" in text
    combined = "\n".join(texts)
    assert "`.service.in`" in combined
    assert "直接install禁止" in combined
    assert "startup_recovery_complete" in combined
    assert "DB downgradeを自動実行しない" in combined
    assert "current boot" in combined
    assert "current invocation" in combined
    assert "systemdが記録した今回の起動時刻以後" in combined
    assert "database_schema_verified" in combined
    assert "bounded retention" in combined
    assert "virtual disk" in combined
    assert "cloud/VPS" in combined
    assert "課金停止を確認" in combined
    assert "systemd unitを生成済み" not in combined
    assert "systemd運用を承認済み" not in combined
