from __future__ import annotations

import ast
import asyncio
import hashlib
import http.client
import importlib.machinery
import json
import os
import socket
import stat
import subprocess
import sys
import traceback
import types
import urllib.request
import zipimport
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from discord_ai_reminder_bot.application.shutdown_measurement import SHUTDOWN_STAGE_ORDER
from discord_ai_reminder_bot.infrastructure import shutdown_measurement_harness
from tests.support import linux_shutdown_measurement_evidence as evidence
from tests.support import linux_shutdown_measurement_runner as runner
from tests.support import shutdown_measurement_source_metadata as source_metadata
from tests.support import shutdown_process_harness
from tests.support import snapshot_child_bootstrap_policy as snapshot_policy

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sandbox_tmp_root_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Component-test host representation only; never formal Linux evidence."""

    real_fstat = os.fstat
    root_status = runner._ARTIFACT_ROOT.stat()

    def fstat_with_system_tmp_owner(descriptor: int) -> os.stat_result:
        status = real_fstat(descriptor)
        if (
            status.st_dev == root_status.st_dev
            and status.st_ino == root_status.st_ino
            and stat.S_ISDIR(status.st_mode)
        ):
            fields = list(status)
            fields[4] = 0
            return os.stat_result(fields)
        return status

    monkeypatch.setattr(runner.os, "fstat", fstat_with_system_tmp_owner)


@pytest.fixture
def isolated_artifact_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep deliberately failed artifact cleanup inside pytest-managed storage."""

    root = tmp_path / "artifact-root"
    root.mkdir()
    real_fstat = os.fstat
    root_status = root.stat()

    def fstat_with_system_tmp_owner(descriptor: int) -> os.stat_result:
        status = real_fstat(descriptor)
        if (
            status.st_dev == root_status.st_dev
            and status.st_ino == root_status.st_ino
            and stat.S_ISDIR(status.st_mode)
        ):
            fields = list(status)
            fields[0] = stat.S_IFDIR | 0o1777
            fields[4] = 0
            return os.stat_result(fields)
        return status

    monkeypatch.setattr(runner, "_ARTIFACT_ROOT", root)
    monkeypatch.setattr(runner.os, "fstat", fstat_with_system_tmp_owner)


@pytest.fixture
def authorized_offline_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sandbox_tmp_root_adapter: None,
) -> Path:
    """Explicit component-only success adapter, not formal Linux evidence."""

    manifest = tmp_path / "discord-ai-reminder-bot.source-manifest.json"
    payload = {
        "schema_version": source_metadata.MANIFEST_SCHEMA_VERSION,
        "commit_sha": "0" * 40,
        "git_tree_sha": "1" * 40,
        "transfer_archive_sha256": "2" * 64,
        "source_set_sha256": source_metadata.source_set_digest(REPOSITORY_ROOT),
    }
    manifest.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        encoding="ascii",
    )
    manifest.chmod(0o600)
    monkeypatch.setattr(runner, "_external_manifest_path", lambda: manifest)
    return manifest


@pytest.fixture
def external_manifest(authorized_offline_success: Path) -> Path:
    return authorized_offline_success


def _assert_non_reflecting(error: BaseException, canary: str) -> None:
    rendered = "".join(traceback.format_exception(error))
    assert canary not in str(error)
    assert canary not in rendered


def test_fixed_sibling_manifest_resolver_ignores_argv_environment_and_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected = REPOSITORY_ROOT.parent / f"{REPOSITORY_ROOT.name}.source-manifest.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "other-source"))
    monkeypatch.setattr(sys, "argv", ["caller-controlled", str(tmp_path / "manifest.json")])

    assert runner._external_manifest_path() == expected


def test_adapter_is_explicit_test_only_and_formal_path_has_no_adapter() -> None:
    """The formal path is intentionally fail-closed without a sibling manifest."""

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        )
        for node in tree.body
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "usefixtures"
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and any(
            keyword.arg == "autouse"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        )
        for node in ast.walk(tree)
    )
    formal = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_fixed_sibling_manifest_resolver_ignores_argv_environment_and_cwd"
    )
    assert "authorized_offline_success" not in {argument.arg for argument in formal.args.args}
    assert all(
        "authorized_offline_success" not in path.read_text(encoding="utf-8")
        for path in (REPOSITORY_ROOT / "src").rglob("*.py")
    )
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()
    assert (
        caught.value.classification
        is runner.MeasurementFailureClassification.SOURCE_METADATA_INVALID
    )


def test_fixed_runner_returns_only_anonymous_fixed_output(
    authorized_offline_success: Path,
) -> None:
    """Component success only; not formal Linux measurement evidence."""
    del authorized_offline_success
    output = runner.run_fixed_linux_shutdown_measurement()

    assert output.schema_version == runner.OUTPUT_SCHEMA_VERSION
    assert output.commit_identity == "0" * 40
    assert output.fixed_scenario == "immediate-success"
    assert output.anonymous_run_number == "run-000001"
    assert type(output.relative_monotonic_duration_ns) is int
    assert output.relative_monotonic_duration_ns >= 0
    assert output.exit_classification == "exit_zero"
    assert output.residual_count == 0
    assert output.stages == (
        runner.MeasurementStage("ready", "observed"),
        runner.MeasurementStage("sigint", "sent"),
        runner.MeasurementStage("cleanup_start", "observed"),
        *(
            runner.MeasurementStage(stage.name.lower(), "completed")
            for stage in SHUTDOWN_STAGE_ORDER
        ),
        runner.MeasurementStage("cleanup_complete", "observed"),
        runner.MeasurementStage("exit", "observed"),
    )


def test_component_snapshot_child_uses_private_fd_interpreter_and_snapshot_only(
    authorized_offline_success: Path,
) -> None:
    """Authorized offline component probe; this is not formal Linux evidence."""

    manifest = runner._external_manifest()
    source_set = runner._verified_source_set(manifest)
    interpreter = runner._open_verified_interpreter()
    snapshot = runner._build_execution_snapshot(source_set)
    child: shutdown_process_harness.SyntheticChildHandle | None = None
    try:
        assert snapshot.handles.directory_fd is not None
        runner._revalidate_execution_snapshot(snapshot)
        child = shutdown_process_harness.launch_child(
            REPOSITORY_ROOT,
            shutdown_measurement_harness.SyntheticChildScenario.IMMEDIATE_SUCCESS,
            interpreter_fd=interpreter.descriptor,
            snapshot_fd=snapshot.handles.directory_fd,
            runtime_contract=runner._interpreter_runtime_contract(interpreter),
        )
        assert child.process.args[0] == f"/proc/self/fd/{interpreter.descriptor}"
        assert child.process.args[1:4] == ("-I", "-B", "-S")
        assert (
            shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
        )
        assert shutdown_process_harness.send_signal(
            child, shutdown_process_harness.ChildSignal.INTERRUPT
        )
        stdout, stderr = shutdown_process_harness.collect_output(child)
        assert stderr == "", stderr[-500:]
        assert shutdown_measurement_harness.EXIT_MARKER in stdout
    finally:
        if child is not None:
            try:
                shutdown_process_harness.stop_and_reap(child)
            except shutdown_process_harness.SyntheticProcessError:
                pass
        runner._cleanup_execution_snapshot(snapshot)
        runner._close_snapshot_descriptor(interpreter.descriptor)


def test_measurement_interval_starts_immediately_before_sigint_and_ends_after_residual(
    monkeypatch: pytest.MonkeyPatch,
    authorized_offline_success: Path,
) -> None:
    del authorized_offline_success
    calls: list[str] = []
    values = iter((101, 205))
    real_signal = shutdown_process_harness.send_signal
    real_residual_count = shutdown_process_harness.residual_count

    def monotonic_ns() -> int:
        calls.append("clock")
        return next(values)

    def send_signal(*args: object, **kwargs: object) -> bool:
        calls.append("signal")
        return real_signal(*args, **kwargs)

    def residual_count(*args: object, **kwargs: object) -> int:
        calls.append("residual")
        return real_residual_count(*args, **kwargs)

    monkeypatch.setattr(runner.time, "monotonic_ns", monotonic_ns)
    monkeypatch.setattr(shutdown_process_harness, "send_signal", send_signal)
    monkeypatch.setattr(shutdown_process_harness, "residual_count", residual_count)

    output = runner.run_fixed_linux_shutdown_measurement()

    assert calls[0:2] == ["clock", "signal"]
    assert calls[-2:] == ["residual", "clock"]
    assert output.relative_monotonic_duration_ns == 104


def test_marker_sequence_requires_exactly_one_of_every_fixed_marker(
    monkeypatch: pytest.MonkeyPatch,
    authorized_offline_success: Path,
) -> None:
    del authorized_offline_success
    real_collect = shutdown_process_harness.collect_output

    def duplicate_stage(*args: object, **kwargs: object) -> tuple[str, str]:
        stdout, stderr = real_collect(*args, **kwargs)
        return stdout + "SYNTHETIC_STAGE_COMPLETED=DATABASE_ENGINE_DISPOSE\n", stderr

    monkeypatch.setattr(shutdown_process_harness, "collect_output", duplicate_stage)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()

    assert caught.value.classification is runner.MeasurementFailureClassification.MARKER_FAILURE


@pytest.mark.parametrize(
    ("attribute", "value", "expected"),
    [
        ("collect_output", None, runner.MeasurementFailureClassification.OUTPUT_FAILURE),
        ("send_signal", None, runner.MeasurementFailureClassification.SIGNAL_FAILURE),
    ],
)
def test_signal_and_output_failures_are_fixed_and_non_reflecting(
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    value: object,
    expected: runner.MeasurementFailureClassification,
    authorized_offline_success: Path,
) -> None:
    del authorized_offline_success
    canary = "SENSITIVE_RUNNER_CANARY"

    def fail(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise OSError(canary)

    del value
    monkeypatch.setattr(shutdown_process_harness, attribute, fail)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()

    assert caught.value.classification is expected
    _assert_non_reflecting(caught.value, canary)


def test_timeout_is_fixed_output_failure_and_child_is_reaped(
    monkeypatch: pytest.MonkeyPatch,
    authorized_offline_success: Path,
) -> None:
    del authorized_offline_success
    child: shutdown_process_harness.SyntheticChildHandle | None = None
    real_launch = shutdown_process_harness.launch_child

    def launch(*args: object, **kwargs: object) -> shutdown_process_harness.SyntheticChildHandle:
        nonlocal child
        child = real_launch(*args, **kwargs)
        return child

    def timeout(*args: object, **kwargs: object) -> tuple[str, str]:
        del args, kwargs
        raise shutdown_process_harness.SyntheticProcessError(
            "synthetic child output wait timed out"
        )

    monkeypatch.setattr(shutdown_process_harness, "launch_child", launch)
    monkeypatch.setattr(shutdown_process_harness, "collect_output", timeout)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()

    assert caught.value.classification is runner.MeasurementFailureClassification.OUTPUT_FAILURE
    assert child is not None and child.reaped
    assert shutdown_process_harness.residual_count(child) == 0


def test_exit_stderr_pipe_reap_and_residual_are_required(
    monkeypatch: pytest.MonkeyPatch,
    authorized_offline_success: Path,
) -> None:
    del authorized_offline_success
    real_collect = shutdown_process_harness.collect_output

    def stderr_output(*args: object, **kwargs: object) -> tuple[str, str]:
        stdout, _ = real_collect(*args, **kwargs)
        return stdout, "unexpected"

    monkeypatch.setattr(shutdown_process_harness, "collect_output", stderr_output)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()
    assert caught.value.classification is runner.MeasurementFailureClassification.STDERR_FAILURE


def test_nonzero_exit_never_returns_a_success_artifact(
    monkeypatch: pytest.MonkeyPatch, authorized_offline_success: Path
) -> None:
    del authorized_offline_success
    real_collect = shutdown_process_harness.collect_output

    def nonzero_exit(
        handle: shutdown_process_harness.SyntheticChildHandle, *args: object, **kwargs: object
    ) -> tuple[str, str]:
        stdout, stderr = real_collect(handle, *args, **kwargs)
        handle.process.returncode = 7
        return stdout, stderr

    monkeypatch.setattr(shutdown_process_harness, "collect_output", nonzero_exit)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()
    assert caught.value.classification is runner.MeasurementFailureClassification.EXIT_FAILURE


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "reordered"])
def test_missing_duplicate_or_reordered_markers_never_return_success(
    monkeypatch: pytest.MonkeyPatch, mutation: str, authorized_offline_success: Path
) -> None:
    del authorized_offline_success
    real_collect = shutdown_process_harness.collect_output

    def malformed_output(*args: object, **kwargs: object) -> tuple[str, str]:
        stdout, stderr = real_collect(*args, **kwargs)
        lines = stdout.splitlines()
        if mutation == "missing":
            lines.pop()
        elif mutation == "duplicate":
            lines.append(lines[-1])
        else:
            lines[0], lines[1] = lines[1], lines[0]
        return "\n".join(lines) + "\n", stderr

    monkeypatch.setattr(shutdown_process_harness, "collect_output", malformed_output)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()
    assert caught.value.classification is runner.MeasurementFailureClassification.MARKER_FAILURE


def test_unclosed_pipes_are_rejected_and_real_cleanup_still_runs(
    monkeypatch: pytest.MonkeyPatch,
    authorized_offline_success: Path,
) -> None:
    del authorized_offline_success
    child: shutdown_process_harness.SyntheticChildHandle | None = None
    real_launch = shutdown_process_harness.launch_child
    real_stop = shutdown_process_harness.stop_and_reap

    def launch(*args: object, **kwargs: object) -> shutdown_process_harness.SyntheticChildHandle:
        nonlocal child
        child = real_launch(*args, **kwargs)
        return child

    class OpenPipe:
        closed = False

    def leave_pipes_open(handle: shutdown_process_harness.SyntheticChildHandle) -> None:
        real_stop(handle)
        handle.process.stdout = OpenPipe()
        handle.process.stderr = OpenPipe()

    monkeypatch.setattr(shutdown_process_harness, "launch_child", launch)
    monkeypatch.setattr(shutdown_process_harness, "stop_and_reap", leave_pipes_open)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()
    assert caught.value.classification is runner.MeasurementFailureClassification.PIPE_CLOSE_FAILURE
    assert child is not None and child.reaped


def test_missing_reap_and_nonzero_residual_are_independent_fail_closed_paths(
    monkeypatch: pytest.MonkeyPatch,
    authorized_offline_success: Path,
) -> None:
    del authorized_offline_success
    real_stop = shutdown_process_harness.stop_and_reap

    def reaped_but_unrecorded(handle: shutdown_process_harness.SyntheticChildHandle) -> None:
        real_stop(handle)
        handle._reaped = False

    monkeypatch.setattr(shutdown_process_harness, "stop_and_reap", reaped_but_unrecorded)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()
    assert caught.value.classification is runner.MeasurementFailureClassification.REAP_FAILURE

    monkeypatch.setattr(shutdown_process_harness, "stop_and_reap", real_stop)
    monkeypatch.setattr(shutdown_process_harness, "residual_count", lambda handle: 1)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()
    assert caught.value.classification is runner.MeasurementFailureClassification.RESIDUAL_FAILURE


@pytest.mark.parametrize("primary_kind", ["failure", "cancellation"])
def test_primary_failure_or_cancellation_identity_survives_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    primary_kind: str,
    authorized_offline_success: Path,
) -> None:
    del authorized_offline_success
    primary: BaseException = (
        runner.ShutdownMeasurementRunnerError(
            runner.MeasurementFailureClassification.MARKER_FAILURE
        )
        if primary_kind == "failure"
        else asyncio.CancelledError("fixed cancellation")
    )
    real_stop = shutdown_process_harness.stop_and_reap

    def primary_output_failure(*args: object, **kwargs: object) -> tuple[str, str]:
        del args, kwargs
        raise primary

    def cleanup_after_reap(handle: shutdown_process_harness.SyntheticChildHandle) -> None:
        real_stop(handle)
        raise OSError("SENSITIVE_CLEANUP_CANARY")

    monkeypatch.setattr(shutdown_process_harness, "collect_output", primary_output_failure)
    monkeypatch.setattr(shutdown_process_harness, "stop_and_reap", cleanup_after_reap)
    with pytest.raises((runner.ShutdownMeasurementRunnerError, asyncio.CancelledError)) as caught:
        runner.run_fixed_linux_shutdown_measurement()

    assert caught.value is primary
    _assert_non_reflecting(caught.value, "SENSITIVE_CLEANUP_CANARY")


def test_cleanup_failure_is_fixed_when_it_is_the_only_failure(
    monkeypatch: pytest.MonkeyPatch,
    authorized_offline_success: Path,
) -> None:
    del authorized_offline_success
    real_stop = shutdown_process_harness.stop_and_reap

    def cleanup_after_reap(handle: shutdown_process_harness.SyntheticChildHandle) -> None:
        real_stop(handle)
        raise OSError("SENSITIVE_CLEANUP_CANARY")

    monkeypatch.setattr(shutdown_process_harness, "stop_and_reap", cleanup_after_reap)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()
    assert caught.value.classification is runner.MeasurementFailureClassification.CLEANUP_FAILURE
    _assert_non_reflecting(caught.value, "SENSITIVE_CLEANUP_CANARY")


@pytest.mark.parametrize("payload", [b"", b"{}", b"[]", b'{"extra":"value"}'])
def test_missing_or_invalid_external_manifest_blocks_launch_before_child_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: bytes
) -> None:
    launches = 0

    def launch_forbidden(
        *args: object, **kwargs: object
    ) -> shutdown_process_harness.SyntheticChildHandle:
        nonlocal launches
        del args, kwargs
        launches += 1
        raise AssertionError("child launch must not occur")

    manifest = tmp_path / "invalid-manifest.json"
    if payload:
        manifest.write_bytes(payload)
        manifest.chmod(0o600)
    monkeypatch.setattr(runner, "_external_manifest_path", lambda: manifest)
    monkeypatch.setattr(shutdown_process_harness, "launch_child", launch_forbidden)

    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()

    assert (
        caught.value.classification
        is runner.MeasurementFailureClassification.SOURCE_METADATA_INVALID
    )
    assert launches == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("commit_sha", "not-a-sha"),
        ("git_tree_sha", 3),
        ("transfer_archive_sha256", "0" * 63),
        ("source_set_sha256", "0" * 64),
        ("extra", "forbidden"),
    ],
)
def test_manifest_format_and_source_set_fail_closed_without_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, value: object
) -> None:
    launches = 0

    def launch_forbidden(
        *args: object, **kwargs: object
    ) -> shutdown_process_harness.SyntheticChildHandle:
        nonlocal launches
        del args, kwargs
        launches += 1
        raise AssertionError("child launch must not occur")

    payload = {
        "schema_version": source_metadata.MANIFEST_SCHEMA_VERSION,
        "commit_sha": "0" * 40,
        "git_tree_sha": "1" * 40,
        "transfer_archive_sha256": "2" * 64,
        "source_set_sha256": source_metadata.source_set_digest(REPOSITORY_ROOT),
    }
    payload[field] = value
    manifest = tmp_path / "invalid-manifest.json"
    manifest.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="ascii"
    )
    manifest.chmod(0o600)
    monkeypatch.setattr(runner, "_external_manifest_path", lambda: manifest)
    monkeypatch.setattr(shutdown_process_harness, "launch_child", launch_forbidden)

    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()

    assert (
        caught.value.classification
        is runner.MeasurementFailureClassification.SOURCE_METADATA_INVALID
    )
    assert launches == 0


@pytest.mark.parametrize("mutation", ["mode", "symlink", "digest", "non_callable_loader"])
def test_manifest_security_and_source_mutation_block_launch(
    monkeypatch: pytest.MonkeyPatch, external_manifest: Path, mutation: str
) -> None:
    launches = 0

    def launch_forbidden(
        *args: object, **kwargs: object
    ) -> shutdown_process_harness.SyntheticChildHandle:
        nonlocal launches
        del args, kwargs
        launches += 1
        raise AssertionError("child launch must not occur")

    if mutation == "mode":
        external_manifest.chmod(0o644)
    elif mutation == "symlink":
        target = external_manifest.with_name("manifest-target.json")
        external_manifest.replace(target)
        external_manifest.symlink_to(target)
    elif mutation == "digest":
        verified = source_metadata.read_verified_source_set(REPOSITORY_ROOT)
        monkeypatch.setattr(
            runner,
            "read_verified_source_set",
            lambda root: source_metadata.VerifiedSourceSet("f" * 64, verified.files),
        )
    else:
        monkeypatch.setattr(runner, "load_external_source_manifest", None)
    monkeypatch.setattr(shutdown_process_harness, "launch_child", launch_forbidden)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()
    assert (
        caught.value.classification
        is runner.MeasurementFailureClassification.SOURCE_METADATA_INVALID
    )
    assert launches == 0


def test_local_source_set_digest_binds_actual_bytes_and_rejects_import_path_symlink(
    tmp_path: Path,
) -> None:
    assert (
        "tests/support/snapshot_child_bootstrap_policy.py"
        in source_metadata.SOURCE_SET_RELATIVE_PATHS
    )
    assert len(source_metadata.SOURCE_SET_RELATIVE_PATHS) == len(
        set(source_metadata.SOURCE_SET_RELATIVE_PATHS)
    )
    copied_root = tmp_path / "source-copy"
    for relative_path in source_metadata.SOURCE_SET_RELATIVE_PATHS:
        source = REPOSITORY_ROOT / relative_path
        destination = copied_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    original = source_metadata.read_verified_source_set(copied_root)
    target = (
        copied_root / "src/discord_ai_reminder_bot/infrastructure/shutdown_measurement_harness.py"
    )
    target.write_bytes(target.read_bytes() + b"\n# test-only byte mutation\n")
    assert source_metadata.read_verified_source_set(copied_root).digest != original.digest

    target.unlink()
    target.symlink_to(
        REPOSITORY_ROOT
        / "src/discord_ai_reminder_bot/infrastructure/shutdown_measurement_harness.py"
    )
    with pytest.raises(source_metadata.SourceManifestError):
        source_metadata.read_verified_source_set(copied_root)


def test_consecutive_and_parallel_runs_do_not_share_cleanup_failure_state(
    authorized_offline_success: Path,
) -> None:
    """Component success only; not formal Linux measurement evidence."""
    del authorized_offline_success
    first = runner.run_fixed_linux_shutdown_measurement()
    second = runner.run_fixed_linux_shutdown_measurement()
    with ThreadPoolExecutor(max_workers=2) as workers:
        parallel = list(
            workers.map(lambda _: runner.run_fixed_linux_shutdown_measurement(), range(2))
        )

    assert first.residual_count == second.residual_count == 0
    assert all(result.residual_count == 0 for result in parallel)


def _fixed_output() -> runner.ShutdownMeasurementOutput:
    return runner.ShutdownMeasurementOutput(
        schema_version=runner.OUTPUT_SCHEMA_VERSION,
        commit_identity="0" * 40,
        fixed_scenario="immediate-success",
        anonymous_run_number="run-000001",
        relative_monotonic_duration_ns=1,
        stages=(),
        exit_classification="exit_zero",
        residual_count=0,
    )


def _evidence_identity() -> evidence.EvidenceIdentity:
    return evidence.EvidenceIdentity("manifest-v1", "0" * 40, "1" * 40, "2" * 64, "3" * 64)


def _successful_evidence_output() -> runner.ShutdownMeasurementOutput:
    stages = (
        runner.MeasurementStage("ready", "observed"),
        runner.MeasurementStage("sigint", "sent"),
        runner.MeasurementStage("cleanup_start", "observed"),
        *(
            runner.MeasurementStage(stage.name.lower(), "completed")
            for stage in SHUTDOWN_STAGE_ORDER
        ),
        runner.MeasurementStage("cleanup_complete", "observed"),
        runner.MeasurementStage("exit", "observed"),
    )
    return runner.ShutdownMeasurementOutput(
        runner.OUTPUT_SCHEMA_VERSION,
        "0" * 40,
        evidence.FIXED_SCENARIO,
        "run-000001",
        1,
        stages,
        "exit_zero",
        0,
    )


@pytest.fixture
def evidence_output_adapter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Temporary-root adapter for component tests; never formal evidence."""

    source_root = tmp_path / "source-root"
    source_root.mkdir()
    monkeypatch.setattr(evidence, "_REPOSITORY_ROOT", source_root)
    monkeypatch.setattr(evidence, "_manifest_and_identity", _evidence_identity)
    return source_root.parent / f"{source_root.name}.shutdown-measurement-evidence"


def test_evidence_schema_is_canonical_exact_and_non_reflecting() -> None:
    identity = _evidence_identity()
    output = _successful_evidence_output()
    value = evidence._evidence_from_output(output, identity)
    raw = evidence.serialize_evidence(value)

    assert raw == json.dumps(
        json.loads(raw), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    assert evidence.parse_evidence(raw) == value
    assert b"stdout" not in raw and b"path" not in raw and b"hostname" not in raw

    for altered in (
        raw.replace(b'"duration_ns":1', b'"duration_ns":0'),
        raw.replace(b'"duration_ns":1', b'"duration_ns":true'),
        raw[:-1] + b',"canary":"secret"}',
    ):
        with pytest.raises(evidence.EvidenceContractError) as caught:
            evidence.parse_evidence(altered)
        _assert_non_reflecting(caught.value, "secret")


def test_evidence_receipts_and_hash_sidecar_are_fixed() -> None:
    identity = _evidence_identity()
    prepared = evidence.serialize_prepared(identity)
    completed = evidence.serialize_completed(identity, "a" * 64)

    assert evidence.parse_prepared(prepared)["status"] == "prepared"
    assert evidence.parse_completed(completed)["status"] == "completed"
    assert evidence.SIDECAR_NAME == "measurement-evidence.sha256"
    assert ("a" * 64 + "\n").encode("ascii") == b"a" * 64 + b"\n"
    digest = hashlib.sha256(b"evidence").hexdigest()
    assert evidence.validate_sidecar(digest.encode("ascii") + b"\n", b"evidence") == digest
    with pytest.raises(evidence.EvidenceContractError):
        evidence.validate_sidecar(digest.encode("ascii") + b"\n\n", b"evidence")
    with pytest.raises(evidence.EvidenceContractError):
        evidence.parse_completed(completed.replace(b"a" * 64, b"A" * 64))


def test_evidence_fixed_sibling_ignores_cwd_argv_and_environment(
    monkeypatch: pytest.MonkeyPatch, evidence_output_adapter: Path, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["caller", "other"])
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "untrusted"))

    assert evidence._fixed_evidence_directory() == evidence_output_adapter


def test_evidence_run_once_writes_ordered_one_shot_files(
    monkeypatch: pytest.MonkeyPatch, evidence_output_adapter: Path
) -> None:
    calls = 0

    def fixed_run() -> runner.ShutdownMeasurementOutput:
        nonlocal calls
        calls += 1
        return _successful_evidence_output()

    monkeypatch.setattr(runner, "run_fixed_linux_shutdown_measurement", fixed_run)
    result, digest = evidence.run_once()

    assert calls == 1
    assert result.duration_ns == 1
    assert sorted(item.name for item in evidence_output_adapter.iterdir()) == [
        evidence.COMPLETED_NAME,
        evidence.EVIDENCE_NAME,
        evidence.SIDECAR_NAME,
        evidence.PREPARED_NAME,
    ]
    assert (evidence_output_adapter / evidence.SIDECAR_NAME).read_bytes() == (
        digest.encode("ascii") + b"\n"
    )
    assert (
        evidence.parse_evidence((evidence_output_adapter / evidence.EVIDENCE_NAME).read_bytes())
        == result
    )


def test_existing_evidence_directory_blocks_runner_before_launch(
    monkeypatch: pytest.MonkeyPatch, evidence_output_adapter: Path
) -> None:
    evidence_output_adapter.mkdir()
    calls = 0

    def forbidden() -> runner.ShutdownMeasurementOutput:
        nonlocal calls
        calls += 1
        return _successful_evidence_output()

    monkeypatch.setattr(runner, "run_fixed_linux_shutdown_measurement", forbidden)
    with pytest.raises(evidence.EvidenceContractError):
        evidence.run_once()
    assert calls == 0


@pytest.mark.parametrize("special", [asyncio.CancelledError(), KeyboardInterrupt(), SystemExit(7)])
def test_evidence_preserves_special_runner_failure_identity(
    monkeypatch: pytest.MonkeyPatch, evidence_output_adapter: Path, special: BaseException
) -> None:
    def fail() -> runner.ShutdownMeasurementOutput:
        raise special

    monkeypatch.setattr(runner, "run_fixed_linux_shutdown_measurement", fail)
    with pytest.raises(type(special)) as caught:
        evidence.run_once()
    assert caught.value is special
    assert (evidence_output_adapter / evidence.PREPARED_NAME).is_file()
    assert not (evidence_output_adapter / evidence.COMPLETED_NAME).exists()


def test_evidence_write_failure_never_reinvokes_runner(
    monkeypatch: pytest.MonkeyPatch, evidence_output_adapter: Path
) -> None:
    calls = 0
    real_write = evidence._write_new

    def fixed_run() -> runner.ShutdownMeasurementOutput:
        nonlocal calls
        calls += 1
        return _successful_evidence_output()

    def fail_evidence(handles: object, name: str, payload: bytes) -> None:
        if name == evidence.EVIDENCE_NAME:
            raise evidence.EvidenceContractError()
        real_write(handles, name, payload)

    monkeypatch.setattr(runner, "run_fixed_linux_shutdown_measurement", fixed_run)
    monkeypatch.setattr(evidence, "_write_new", fail_evidence)
    with pytest.raises(evidence.EvidenceContractError):
        evidence.run_once()
    assert calls == 1
    assert (evidence_output_adapter / evidence.PREPARED_NAME).is_file()
    assert not (evidence_output_adapter / evidence.COMPLETED_NAME).exists()


def test_evidence_main_rejects_arguments_without_attempt(
    monkeypatch: pytest.MonkeyPatch,
    evidence_output_adapter: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["module", "unexpected"])
    assert evidence.main() == 2
    assert not evidence_output_adapter.exists()
    assert capsys.readouterr().err == "FORMAL_MEASUREMENT_EVIDENCE_FAILURE\n"


def _parent_status_with_nlink(status: os.stat_result, nlink: object) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        st_mode=status.st_mode,
        st_uid=status.st_uid,
        st_nlink=nlink,
        st_dev=status.st_dev,
        st_ino=status.st_ino,
    )


@pytest.mark.parametrize("nlink", [0, 1, -1, True, "parent-nlink-canary"])
def test_evidence_parent_nlink_rejection_blocks_attempt_before_runner(
    monkeypatch: pytest.MonkeyPatch, evidence_output_adapter: Path, nlink: object
) -> None:
    parent_status = evidence_output_adapter.parent.stat()
    real_fstat = os.fstat
    calls = 0

    def invalid_parent_fstat(descriptor: int) -> os.stat_result | types.SimpleNamespace:
        status = real_fstat(descriptor)
        if status.st_dev == parent_status.st_dev and status.st_ino == parent_status.st_ino:
            return _parent_status_with_nlink(status, nlink)
        return status

    def forbidden() -> runner.ShutdownMeasurementOutput:
        nonlocal calls
        calls += 1
        return _successful_evidence_output()

    monkeypatch.setattr(evidence.os, "fstat", invalid_parent_fstat)
    monkeypatch.setattr(runner, "run_fixed_linux_shutdown_measurement", forbidden)
    with pytest.raises(evidence.EvidenceContractError) as caught:
        evidence.run_once()
    _assert_non_reflecting(caught.value, "parent-nlink-canary")
    assert calls == 0
    assert not evidence_output_adapter.exists()


@pytest.mark.parametrize("nlink", [2, 5])
def test_evidence_parent_nlink_accepts_linux_directory_values(
    monkeypatch: pytest.MonkeyPatch, evidence_output_adapter: Path, nlink: int
) -> None:
    parent_status = evidence_output_adapter.parent.stat()
    real_fstat = os.fstat

    def valid_parent_fstat(descriptor: int) -> os.stat_result | types.SimpleNamespace:
        status = real_fstat(descriptor)
        if status.st_dev == parent_status.st_dev and status.st_ino == parent_status.st_ino:
            return _parent_status_with_nlink(status, nlink)
        return status

    monkeypatch.setattr(evidence.os, "fstat", valid_parent_fstat)
    monkeypatch.setattr(runner, "run_fixed_linux_shutdown_measurement", _successful_evidence_output)
    evidence.run_once()
    assert (evidence_output_adapter / evidence.COMPLETED_NAME).is_file()


def _new_evidence_handles() -> evidence._Handles:
    handles = evidence._Handles()
    evidence._create_attempt_directory(handles)
    return handles


def _close_after_call(
    monkeypatch: pytest.MonkeyPatch, failing_calls: set[int]
) -> tuple[list[int], object]:
    real_close = os.close
    calls: list[int] = []

    def close(descriptor: int) -> None:
        calls.append(descriptor)
        real_close(descriptor)
        if len(calls) in failing_calls:
            raise OSError("close-error-canary")

    monkeypatch.setattr(evidence.os, "close", close)
    return calls, real_close


@pytest.mark.parametrize("failing_calls", [{1}, {2}, {1, 2}])
def test_evidence_write_close_failure_is_fixed_and_closes_each_fd_once(
    monkeypatch: pytest.MonkeyPatch,
    evidence_output_adapter: Path,
    failing_calls: set[int],
) -> None:
    handles = _new_evidence_handles()
    calls, real_close = _close_after_call(monkeypatch, failing_calls)
    try:
        with pytest.raises(evidence.EvidenceContractError) as caught:
            evidence._write_new(handles, "close-only.json", b"payload")
        _assert_non_reflecting(caught.value, "close-error-canary")
        assert len(calls) == 2
        assert calls[0] != calls[1]
    finally:
        monkeypatch.setattr(evidence.os, "close", real_close)
        evidence._close(handles)


@pytest.mark.parametrize("failing_calls", [{1}, {2}])
def test_evidence_write_primary_failure_survives_read_or_write_close_failure(
    monkeypatch: pytest.MonkeyPatch,
    evidence_output_adapter: Path,
    failing_calls: set[int],
) -> None:
    del evidence_output_adapter
    handles = _new_evidence_handles()
    primary = evidence.EvidenceContractError()
    calls, real_close = _close_after_call(monkeypatch, failing_calls)
    monkeypatch.setattr(
        evidence.os, "read", lambda descriptor, size: (_ for _ in ()).throw(primary)
    )
    try:
        with pytest.raises(evidence.EvidenceContractError) as caught:
            evidence._write_new(handles, "primary.json", b"payload")
        assert caught.value is primary
        assert len(calls) == 2
        assert calls[0] != calls[1]
    finally:
        monkeypatch.setattr(evidence.os, "close", real_close)
        evidence._close(handles)


@pytest.mark.parametrize(
    ("primary", "failing_calls"),
    [
        (asyncio.CancelledError("cancelled"), {1}),
        (asyncio.CancelledError("cancelled"), {2}),
        (KeyboardInterrupt("keyboard"), {1}),
        (SystemExit("system-exit"), {2}),
    ],
)
def test_evidence_write_special_primary_identity_survives_close_failure(
    monkeypatch: pytest.MonkeyPatch,
    evidence_output_adapter: Path,
    primary: BaseException,
    failing_calls: set[int],
) -> None:
    del evidence_output_adapter
    handles = _new_evidence_handles()
    calls, real_close = _close_after_call(monkeypatch, failing_calls)
    monkeypatch.setattr(
        evidence.os, "read", lambda descriptor, size: (_ for _ in ()).throw(primary)
    )
    try:
        with pytest.raises(type(primary)) as caught:
            evidence._write_new(handles, "special.json", b"payload")
        assert caught.value is primary
        assert len(calls) == 2
        assert calls[0] != calls[1]
    finally:
        monkeypatch.setattr(evidence.os, "close", real_close)
        evidence._close(handles)


def test_evidence_close_failure_never_reinvokes_runner_or_completes_attempt(
    monkeypatch: pytest.MonkeyPatch, evidence_output_adapter: Path
) -> None:
    calls = 0

    def fixed_run() -> runner.ShutdownMeasurementOutput:
        nonlocal calls
        calls += 1
        return _successful_evidence_output()

    close_calls, _real_close = _close_after_call(monkeypatch, {3})
    monkeypatch.setattr(runner, "run_fixed_linux_shutdown_measurement", fixed_run)
    with pytest.raises(evidence.EvidenceContractError) as caught:
        evidence.run_once()
    _assert_non_reflecting(caught.value, "close-error-canary")
    assert calls == 1
    assert len(close_calls) == 6
    assert len(set(close_calls[:4])) == 2
    assert not (evidence_output_adapter / evidence.COMPLETED_NAME).exists()


def test_evidence_component_success_uses_temporary_adapter_only(
    monkeypatch: pytest.MonkeyPatch,
    authorized_offline_success: Path,
    sandbox_tmp_root_adapter: None,
    evidence_output_adapter: Path,
) -> None:
    """Authorized test-only synthetic child; not formal Linux evidence."""

    del authorized_offline_success, sandbox_tmp_root_adapter
    result, _digest = evidence.run_once()
    assert result.residual_count == 0
    assert (evidence_output_adapter / evidence.COMPLETED_NAME).is_file()


def test_artifact_uses_only_fixed_root_and_fd_relative_operations(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_tmp_root_adapter: None,
) -> None:
    del sandbox_tmp_root_adapter
    calls: list[tuple[str, object, object]] = []
    real_open = os.open
    real_mkdir = os.mkdir
    real_unlink = os.unlink
    real_rmdir = os.rmdir

    def checked_open(*args: object, **kwargs: object) -> int:
        calls.append(("open", args[0], kwargs.get("dir_fd")))
        return real_open(*args, **kwargs)

    def checked_mkdir(*args: object, **kwargs: object) -> None:
        calls.append(("mkdir", args[0], kwargs.get("dir_fd")))
        real_mkdir(*args, **kwargs)

    def checked_unlink(*args: object, **kwargs: object) -> None:
        calls.append(("unlink", args[0], kwargs.get("dir_fd")))
        real_unlink(*args, **kwargs)

    def checked_rmdir(*args: object, **kwargs: object) -> None:
        calls.append(("rmdir", args[0], kwargs.get("dir_fd")))
        real_rmdir(*args, **kwargs)

    monkeypatch.setattr(runner.os, "open", checked_open)
    monkeypatch.setattr(runner.os, "mkdir", checked_mkdir)
    monkeypatch.setattr(runner.os, "unlink", checked_unlink)
    monkeypatch.setattr(runner.os, "rmdir", checked_rmdir)
    runner._write_and_verify_artifact(_fixed_output())

    root_open = calls[0]
    assert root_open == ("open", Path("/tmp"), None)
    mkdir = next(call for call in calls if call[0] == "mkdir")
    directory_open = next(call for call in calls[1:] if call[0] == "open")
    file_open = next(call for call in calls if call[0] == "open" and call[1] == "measurement.json")
    assert isinstance(mkdir[1], str) and mkdir[2] is not None
    assert directory_open[1] == mkdir[1] and directory_open[2] == mkdir[2]
    assert file_open[2] is not None
    assert next(call for call in calls if call[0] == "unlink") == (
        "unlink",
        "measurement.json",
        file_open[2],
    )
    rmdir = next(call for call in calls if call[0] == "rmdir")
    assert rmdir[1] == mkdir[1] and rmdir[2] == mkdir[2]
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "tempfile" not in source
    assert ".read_bytes(" not in source
    assert "artifact.unlink" not in source
    assert "directory.rmdir" not in source


def _directory_stat(
    *, inode: int, mode: int = stat.S_IFDIR | 0o700, uid: int | None = None, nlink: int = 2
) -> os.stat_result:
    return os.stat_result(
        (mode, inode, 1, nlink, os.getuid() if uid is None else uid, 0, 0, 0, 0, 0)
    )


def _file_stat(
    *, inode: int, mode: int = stat.S_IFREG | 0o600, uid: int | None = None
) -> os.stat_result:
    return os.stat_result((mode, inode, 1, 1, os.getuid() if uid is None else uid, 0, 0, 0, 0, 0))


@pytest.mark.parametrize(
    "invalid",
    [
        _directory_stat(inode=1, mode=stat.S_IFREG | 0o1777, uid=0),
        _directory_stat(inode=1, mode=stat.S_IFDIR | 0o1777, uid=os.getuid()),
        _directory_stat(inode=1, mode=stat.S_IFDIR | 0o755, uid=0),
    ],
)
def test_root_validation_rejects_type_owner_and_mode_mismatch(invalid: os.stat_result) -> None:
    assert not runner._valid_directory(invalid, private=False)


@pytest.mark.parametrize(
    "invalid",
    [
        _file_stat(inode=1, mode=stat.S_IFDIR | 0o755),
        _file_stat(inode=1, mode=stat.S_IFREG | 0o644),
        _file_stat(inode=1, mode=stat.S_IFREG | 0o777),
        _file_stat(inode=1, mode=stat.S_IFREG | 0o755, uid=os.getuid() + 1),
        _file_stat(inode=2, mode=stat.S_IFREG | 0o755),
    ],
)
def test_interpreter_identity_rejects_type_mode_owner_and_inode_mismatch(
    invalid: os.stat_result,
) -> None:
    expected = _file_stat(inode=1, mode=stat.S_IFREG | 0o755)
    assert not runner._valid_interpreter_status(invalid, expected)


def test_interpreter_path_mismatch_fails_closed_before_child_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_repository = tmp_path / "repository"
    launcher = fake_repository / ".venv" / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(sys.executable)
    monkeypatch.setattr(runner, "_REPOSITORY_ROOT", fake_repository)

    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner._open_verified_interpreter()

    assert (
        caught.value.classification
        is runner.MeasurementFailureClassification.SOURCE_METADATA_INVALID
    )


def test_artifact_cleanup_never_removes_replaced_directory_and_closes_every_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handles = runner._ArtifactHandles(
        root_fd=10,
        directory_fd=11,
        file_fd=12,
        directory_name="private-dir",
        directory_stat=_directory_stat(inode=100),
        file_stat=_file_stat(inode=200),
        file_created=True,
    )
    calls: list[tuple[str, object]] = []

    def fake_fstat(descriptor: int) -> os.stat_result:
        calls.append(("fstat", descriptor))
        if descriptor == 12:
            return _file_stat(inode=200)
        return _directory_stat(inode=100 if descriptor == 11 else 1)

    def replacement_stat(*args: object, **kwargs: object) -> os.stat_result:
        calls.append(("stat", kwargs.get("dir_fd")))
        if args[0] == "measurement.json":
            return _file_stat(inode=200)
        return _directory_stat(inode=999)

    monkeypatch.setattr(runner.os, "fstat", fake_fstat)
    monkeypatch.setattr(runner.os, "stat", replacement_stat)
    monkeypatch.setattr(
        runner.os, "unlink", lambda *a, **kw: calls.append(("unlink", kw["dir_fd"]))
    )
    monkeypatch.setattr(runner.os, "rmdir", lambda *a, **kw: calls.append(("rmdir", kw["dir_fd"])))
    monkeypatch.setattr(runner.os, "close", lambda fd: calls.append(("close", fd)))

    failed, special = runner._cleanup_artifact(handles)

    assert failed and special is None
    assert ("unlink", 11) in calls
    assert not any(operation == "rmdir" for operation, _ in calls)
    assert [value for operation, value in calls if operation == "close"] == [12, 11, 10]
    assert handles.file_fd is handles.directory_fd is handles.root_fd is None


@pytest.mark.parametrize("exception_type", [asyncio.CancelledError, KeyboardInterrupt, SystemExit])
def test_artifact_primary_baseexception_identity_survives_cleanup(
    monkeypatch: pytest.MonkeyPatch, exception_type: type[BaseException]
) -> None:
    primary = exception_type("identity")

    def fail_prepare(handles: runner._ArtifactHandles) -> None:
        handles.root_fd = 31
        raise primary

    monkeypatch.setattr(runner, "_prepare_artifact_directory", fail_prepare)
    monkeypatch.setattr(runner.os, "close", lambda fd: (_ for _ in ()).throw(OSError("cleanup")))
    with pytest.raises(exception_type) as caught:
        runner._write_and_verify_artifact(_fixed_output())
    assert caught.value is primary


def test_artifact_cleanup_only_failure_is_fixed(
    monkeypatch: pytest.MonkeyPatch,
    isolated_artifact_root: None,
) -> None:
    del isolated_artifact_root

    def cleanup_failure(handles: runner._ArtifactHandles) -> tuple[bool, BaseException | None]:
        del handles
        return True, None

    monkeypatch.setattr(runner, "_cleanup_artifact", cleanup_failure)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner._write_and_verify_artifact(_fixed_output())
    assert (
        caught.value.classification
        is runner.MeasurementFailureClassification.ARTIFACT_CLEANUP_FAILURE
    )


def test_runner_has_no_caller_inputs_or_network_and_production_has_no_test_import(
    monkeypatch: pytest.MonkeyPatch,
    authorized_offline_success: Path,
) -> None:
    del authorized_offline_success
    runner_path = Path(runner.__file__)
    runner_tree = ast.parse(runner_path.read_text(encoding="utf-8"))
    run_function = next(
        node
        for node in runner_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_fixed_linux_shutdown_measurement"
    )
    assert len(run_function.args.args) == 0
    imported_roots = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(runner_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported_roots.isdisjoint({"socket", "subprocess", "urllib", "httpx", "openai"})
    source = runner_path.read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "sys.argv" not in source
    assert "_external_manifest_path" in source
    controller_source = Path(shutdown_process_harness.__file__).read_text(encoding="utf-8")
    assert "os.environ.copy" not in controller_source
    assert shutdown_process_harness._CHILD_ENV == {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
        "TZ": "UTC",
    }
    production_sources = list((REPOSITORY_ROOT / "src").rglob("*.py"))
    assert all(
        "tests.support" not in source.read_text(encoding="utf-8") for source in production_sources
    )

    connection_count = 0

    def network_forbidden(*args: object, **kwargs: object) -> None:
        nonlocal connection_count
        del args, kwargs
        connection_count += 1
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    monkeypatch.setattr(socket.socket, "connect", network_forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", network_forbidden)
    monkeypatch.setattr(socket.socket, "sendto", network_forbidden)
    if hasattr(socket.socket, "sendmsg"):
        monkeypatch.setattr(socket.socket, "sendmsg", network_forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", network_forbidden)
    monkeypatch.setattr(socket, "getnameinfo", network_forbidden)
    monkeypatch.setattr(socket, "gethostbyname", network_forbidden)
    monkeypatch.setattr(socket, "gethostbyname_ex", network_forbidden)
    monkeypatch.setattr(socket, "gethostbyaddr", network_forbidden)
    monkeypatch.setattr(httpx.Client, "request", network_forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", network_forbidden)
    monkeypatch.setattr(http.client.HTTPConnection, "request", network_forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", network_forbidden)

    assert runner.run_fixed_linux_shutdown_measurement().residual_count == 0
    assert connection_count == 0

    with pytest.raises(AssertionError, match="network forbidden"):
        socket.getaddrinfo("negative-control.invalid", 443)
    with pytest.raises(AssertionError, match="network forbidden"):
        socket.socket.sendto(None, b"x", ("127.0.0.1", 9))
    with pytest.raises(AssertionError, match="network forbidden"):
        http.client.HTTPConnection.request(None, "GET", "/")
    assert connection_count == 3


def test_snapshot_policy_guard_helper_unit_rejects_capabilities() -> None:
    """Unit-only helper check; private snapshot probes provide the E2E evidence."""

    guard = snapshot_policy._SnapshotAuditGuard()
    blocked_events = (
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyname_ex",
        "socket.gethostbyaddr",
        "subprocess.Popen",
        "os.system",
    )
    for event in blocked_events:
        with pytest.raises(RuntimeError, match="capability blocked"):
            guard(event, ())
    child_tree = ast.parse(Path(shutdown_measurement_harness.__file__).read_text(encoding="utf-8"))
    child_imports = {
        alias.name.split(".", maxsplit=1)[0]
        for node in child_tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".", maxsplit=1)[0]
        for node in child_tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert child_imports.isdisjoint({"http", "httpx", "openai", "socket", "subprocess", "urllib"})


def test_runner_source_uses_monotonic_ns_only_and_does_not_promote_measurement() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert source.count("time.monotonic_ns()") == 2
    assert "time.time(" not in source
    assert "TimeoutStopSec" not in source
    assert "APPROVED_PRODUCTION_VALUE" not in source
    assert "CONFIGURED_HARD_BOUND" not in source
    assert "calculate_shutdown_deadline" not in source


@pytest.mark.parametrize("missing_flag", ["O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"])
def test_missing_required_fd_safety_flag_fails_closed_before_directory_creation(
    monkeypatch: pytest.MonkeyPatch, missing_flag: str
) -> None:
    launches = 0

    def launch_forbidden(*args: object, **kwargs: object) -> object:
        nonlocal launches
        del args, kwargs
        launches += 1
        raise AssertionError("child launch must not occur")

    monkeypatch.delattr(runner.os, missing_flag, raising=False)
    monkeypatch.setattr(shutdown_process_harness, "launch_child", launch_forbidden)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner.run_fixed_linux_shutdown_measurement()
    assert caught.value.classification is runner.MeasurementFailureClassification.ARTIFACT_FAILURE
    assert launches == 0


@pytest.mark.parametrize(
    ("call_number", "invalid"),
    [
        (1, _directory_stat(inode=1, mode=stat.S_IFREG | 0o600)),
        (1, _directory_stat(inode=1, uid=-1)),
        (2, _directory_stat(inode=2, uid=os.getuid() + 1)),
        (3, _directory_stat(inode=3, mode=stat.S_IFREG | 0o600, nlink=2)),
    ],
)
def test_artifact_fd_validation_rejects_root_directory_and_file_metadata(
    monkeypatch: pytest.MonkeyPatch,
    call_number: int,
    invalid: os.stat_result,
    isolated_artifact_root: None,
) -> None:
    del isolated_artifact_root
    real_fstat = runner.os.fstat
    calls = 0

    def invalid_fstat(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        if calls == call_number:
            return invalid
        return real_fstat(descriptor)

    monkeypatch.setattr(runner.os, "fstat", invalid_fstat)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner._write_and_verify_artifact(_fixed_output())
    assert caught.value.classification is runner.MeasurementFailureClassification.ARTIFACT_FAILURE


@pytest.mark.parametrize("replacement_mode", [stat.S_IFDIR | 0o700, stat.S_IFLNK | 0o777])
def test_cleanup_refuses_directory_or_symlink_name_replacement(
    monkeypatch: pytest.MonkeyPatch, replacement_mode: int
) -> None:
    handles = runner._ArtifactHandles(
        root_fd=40,
        directory_fd=41,
        file_fd=42,
        directory_name="replacement",
        directory_stat=_directory_stat(inode=100),
        file_stat=_file_stat(inode=101),
        file_created=True,
    )
    removed: list[tuple[str, int]] = []

    monkeypatch.setattr(
        runner.os,
        "fstat",
        lambda descriptor: (
            _file_stat(inode=101)
            if descriptor == 42
            else _directory_stat(inode=100 if descriptor == 41 else 1)
        ),
    )
    monkeypatch.setattr(
        runner.os,
        "stat",
        lambda name, *args, **kwargs: (
            _file_stat(inode=101)
            if name == "measurement.json"
            else _directory_stat(inode=200, mode=replacement_mode)
        ),
    )
    monkeypatch.setattr(
        runner.os,
        "unlink",
        lambda name, *, dir_fd: removed.append((name, dir_fd)),
    )
    monkeypatch.setattr(
        runner.os,
        "rmdir",
        lambda name, *, dir_fd: removed.append((name, dir_fd)),
    )
    monkeypatch.setattr(runner.os, "close", lambda descriptor: None)

    failed, _special = runner._cleanup_artifact(handles)

    assert failed
    assert removed == [("measurement.json", 41)]


def test_cleanup_attempts_rmdir_and_each_close_after_unlink_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handles = runner._ArtifactHandles(
        root_fd=50,
        directory_fd=51,
        file_fd=52,
        directory_name="private",
        directory_stat=_directory_stat(inode=100),
        file_stat=_file_stat(inode=101),
        file_created=True,
    )
    calls: list[str] = []

    monkeypatch.setattr(
        runner.os,
        "fstat",
        lambda descriptor: (
            _file_stat(inode=101)
            if descriptor == 52
            else _directory_stat(inode=100 if descriptor == 51 else 1)
        ),
    )
    monkeypatch.setattr(
        runner.os,
        "stat",
        lambda name, *args, **kwargs: (
            _file_stat(inode=101) if name == "measurement.json" else _directory_stat(inode=100)
        ),
    )
    monkeypatch.setattr(
        runner.os,
        "unlink",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unlink failure")),
    )
    monkeypatch.setattr(runner.os, "rmdir", lambda *args, **kwargs: calls.append("rmdir"))
    monkeypatch.setattr(runner.os, "close", lambda descriptor: calls.append(f"close:{descriptor}"))

    failed, special = runner._cleanup_artifact(handles)

    assert failed and special is None
    assert calls == ["rmdir", "close:52", "close:51", "close:50"]


def test_cleanup_attempts_all_closes_after_rmdir_and_close_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handles = runner._ArtifactHandles(
        root_fd=60,
        directory_fd=61,
        file_fd=62,
        directory_name="private",
        directory_stat=_directory_stat(inode=100),
    )
    calls: list[int] = []

    monkeypatch.setattr(
        runner.os,
        "fstat",
        lambda descriptor: _directory_stat(inode=100 if descriptor == 61 else 1),
    )
    monkeypatch.setattr(runner.os, "stat", lambda *args, **kwargs: _directory_stat(inode=100))
    monkeypatch.setattr(
        runner.os,
        "rmdir",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("rmdir failure")),
    )

    def failing_close(descriptor: int) -> None:
        calls.append(descriptor)
        raise RuntimeError("close failure")

    monkeypatch.setattr(runner.os, "close", failing_close)
    failed, special = runner._cleanup_artifact(handles)

    assert failed and special is None
    assert calls == [62, 61, 60]
    assert handles.file_fd is handles.directory_fd is handles.root_fd is None


@pytest.mark.parametrize("replacement", ["regular", "symlink", "missing"])
def test_cleanup_never_unlinks_replaced_or_missing_artifact_file_name(
    monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    handles = runner._ArtifactHandles(
        directory_fd=71,
        file_fd=72,
        file_created=True,
        file_stat=_file_stat(inode=100),
    )
    removed: list[str] = []

    monkeypatch.setattr(runner.os, "fstat", lambda descriptor: _file_stat(inode=100))

    def named_file(name: str, *, dir_fd: int, follow_symlinks: bool) -> os.stat_result:
        assert name == "measurement.json" and dir_fd == 71 and not follow_symlinks
        if replacement == "missing":
            raise FileNotFoundError
        if replacement == "symlink":
            return _file_stat(inode=200, mode=stat.S_IFLNK | 0o777)
        return _file_stat(inode=200)

    monkeypatch.setattr(runner.os, "stat", named_file)
    monkeypatch.setattr(runner.os, "unlink", lambda name, *, dir_fd: removed.append(name))
    monkeypatch.setattr(runner.os, "close", lambda descriptor: None)

    failed, special = runner._cleanup_artifact(handles)

    assert failed and special is None
    assert removed == []


@pytest.mark.parametrize("exception_type", [asyncio.CancelledError, KeyboardInterrupt, SystemExit])
def test_child_cleanup_special_exception_identity_is_retained(
    monkeypatch: pytest.MonkeyPatch, exception_type: type[BaseException]
) -> None:
    special = exception_type("identity")

    class Process:
        stdout = type("Pipe", (), {"closed": True})()
        stderr = type("Pipe", (), {"closed": True})()

    class Child:
        process = Process()
        reaped = True

    monkeypatch.setattr(
        shutdown_process_harness,
        "stop_and_reap",
        lambda child: (_ for _ in ()).throw(special),
    )
    monkeypatch.setattr(shutdown_process_harness, "residual_count", lambda child: 0)

    failure, observed = runner._cleanup_child(Child())  # type: ignore[arg-type]

    assert failure is runner.MeasurementFailureClassification.CLEANUP_FAILURE
    assert observed is special


@pytest.mark.parametrize("stage", ["wait", "pipe", "residual"])
def test_child_cleanup_cancellederror_from_each_stage_preserves_identity(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    special = asyncio.CancelledError(stage)

    class Process:
        stdout = type("Pipe", (), {"closed": True})()
        stderr = type("Pipe", (), {"closed": True})()

    class Child:
        process = Process()
        reaped = True

    if stage == "residual":
        monkeypatch.setattr(shutdown_process_harness, "stop_and_reap", lambda child: None)
        monkeypatch.setattr(
            shutdown_process_harness,
            "residual_count",
            lambda child: (_ for _ in ()).throw(special),
        )
    else:
        monkeypatch.setattr(
            shutdown_process_harness,
            "stop_and_reap",
            lambda child: (_ for _ in ()).throw(special),
        )
        monkeypatch.setattr(shutdown_process_harness, "residual_count", lambda child: 0)

    failure, observed = runner._cleanup_child(Child())  # type: ignore[arg-type]

    assert failure is runner.MeasurementFailureClassification.CLEANUP_FAILURE
    assert observed is special


@pytest.mark.parametrize("stage", ["wait", "pipe"])
def test_controller_child_cleanup_preserves_special_exception_identity(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    child = shutdown_process_harness.launch_child(
        REPOSITORY_ROOT, shutdown_measurement_harness.SyntheticChildScenario.IMMEDIATE_SUCCESS
    )
    special = asyncio.CancelledError(stage)
    if stage == "wait":
        real_wait = shutdown_process_harness._wait
        calls = 0

        def wait_once(process: subprocess.Popen[bytes], timeout: float) -> bool:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise special
            return real_wait(process, timeout)

        monkeypatch.setattr(shutdown_process_harness, "_wait", wait_once)
    else:
        monkeypatch.setattr(
            shutdown_process_harness,
            "_close_pipes",
            lambda process: (_ for _ in ()).throw(special),
        )

    with pytest.raises(asyncio.CancelledError) as caught:
        shutdown_process_harness.stop_and_reap(child)

    assert caught.value is special


@pytest.mark.parametrize(
    "mutation",
    ["replacement", "symlink", "missing", "digest", "mode", "owner", "nlink", "device", "inode"],
)
def test_snapshot_launch_revalidation_rejects_each_file_identity_mutation(
    monkeypatch: pytest.MonkeyPatch,
    authorized_offline_success: Path,
    mutation: str,
) -> None:
    """Component injection: launch must not follow a mutated snapshot entry."""

    snapshot = runner._build_execution_snapshot(
        runner._verified_source_set(runner._external_manifest())
    )
    item = snapshot.files[0]
    real_stat = os.stat
    real_fstat = os.fstat
    launches = 0

    def changed_stat(name: object, *args: object, **kwargs: object) -> os.stat_result:
        observed = real_stat(name, *args, **kwargs)
        if name != item.name or kwargs.get("dir_fd") != item.parent_fd:
            return observed
        fields = list(observed)
        if mutation == "symlink":
            fields[0] = stat.S_IFLNK | 0o777
        elif mutation == "mode":
            fields[0] = stat.S_IFREG | 0o644
        elif mutation == "owner":
            fields[4] += 1
        elif mutation == "nlink":
            fields[3] = 2
        elif mutation == "device":
            fields[2] += 1
        else:
            fields[1] += 1
        if mutation == "missing":
            raise FileNotFoundError
        return os.stat_result(fields)

    monkeypatch.setattr(runner.os, "stat", changed_stat)
    if mutation == "digest":
        monkeypatch.setattr(runner, "_snapshot_digest", lambda descriptor: "0" * 64)
    monkeypatch.setattr(
        shutdown_process_harness,
        "launch_child",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Popen")),
    )
    try:
        with pytest.raises((runner.ShutdownMeasurementRunnerError, FileNotFoundError)):
            runner._revalidate_execution_snapshot(snapshot)
        assert launches == 0
    finally:
        monkeypatch.setattr(runner.os, "stat", real_stat)
        monkeypatch.setattr(runner.os, "fstat", real_fstat)
        runner._cleanup_execution_snapshot(snapshot)


def _snapshot_policy_context(tmp_path: Path) -> snapshot_policy._PolicyContext:
    source = tmp_path / "snapshot" / "src"
    stdlib = tmp_path / "stdlib"
    dynamic = stdlib / "lib-dynload"
    policy_file = tmp_path / "snapshot" / "tests" / "support" / "policy.py"
    source.mkdir(parents=True)
    dynamic.mkdir(parents=True)
    policy_file.parent.mkdir(parents=True)
    policy_file.write_text("# policy\n", encoding="utf-8")
    return snapshot_policy._PolicyContext(
        tmp_path / "snapshot", source, stdlib, dynamic, policy_file
    )


def _policy_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# module\n", encoding="utf-8")
    return path


def _source_spec(path: Path, *, origin: str | None = None, loader: object | None = None) -> object:
    selected_origin = str(path) if origin is None else origin
    selected_loader = (
        importlib.machinery.SourceFileLoader("synthetic_module", selected_origin)
        if loader is None
        else loader
    )
    return types.SimpleNamespace(
        origin=selected_origin,
        loader=selected_loader,
        submodule_search_locations=None,
    )


@pytest.mark.parametrize("component", ["site-packages", "dist-packages"])
def test_snapshot_policy_rejects_site_and_dist_packages_origins(
    tmp_path: Path, component: str
) -> None:
    context = _snapshot_policy_context(tmp_path)
    origin = _policy_file(tmp_path / component / "blocked.py")

    with pytest.raises(snapshot_policy.SnapshotBootstrapPolicyError):
        snapshot_policy._allowed_origin(str(origin), context)


@pytest.mark.parametrize("component", ["site-packages", "dist-packages"])
def test_snapshot_policy_rejects_site_and_dist_packages_namespace_locations(
    tmp_path: Path, component: str
) -> None:
    context = _snapshot_policy_context(tmp_path)
    location = _policy_file(tmp_path / component / "namespace" / "member.py").parent
    loader = importlib.machinery.NamespaceLoader(
        "synthetic_namespace", [str(location)], importlib.machinery.PathFinder.find_spec
    )
    spec = types.SimpleNamespace(
        origin=None, loader=loader, submodule_search_locations=[str(location)]
    )

    with pytest.raises(snapshot_policy.SnapshotBootstrapPolicyError):
        snapshot_policy._validate_spec(spec, context)


def test_snapshot_policy_rejects_realpath_site_packages_symlink(tmp_path: Path) -> None:
    context = _snapshot_policy_context(tmp_path)
    target = _policy_file(tmp_path / "site-packages" / "target.py")
    link = context.source_root / "linked.py"
    link.symlink_to(target)

    with pytest.raises(snapshot_policy.SnapshotBootstrapPolicyError):
        snapshot_policy._allowed_origin(str(link), context)


def test_snapshot_policy_rejects_custom_meta_path_finder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _snapshot_policy_context(tmp_path)
    _isolate_policy_import_state(monkeypatch)
    snapshot_policy._configure_import_containment(context)
    monkeypatch.setattr(
        snapshot_policy.sys, "meta_path", [object(), *snapshot_policy.sys.meta_path]
    )

    with pytest.raises(snapshot_policy.SnapshotBootstrapPolicyError):
        snapshot_policy._validate_import_containment(context)


def test_snapshot_policy_rejects_custom_path_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _snapshot_policy_context(tmp_path)
    _isolate_policy_import_state(monkeypatch)
    snapshot_policy._configure_import_containment(context)
    monkeypatch.setattr(snapshot_policy.sys, "path_hooks", [object()])

    with pytest.raises(snapshot_policy.SnapshotBootstrapPolicyError):
        snapshot_policy._validate_import_containment(context)


def test_snapshot_policy_replaces_ambient_hooks_with_its_limited_file_finder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _snapshot_policy_context(tmp_path)
    _isolate_policy_import_state(monkeypatch)
    ambient_hook = object()
    monkeypatch.setattr(
        snapshot_policy.sys,
        "path_hooks",
        [ambient_hook, zipimport.zipimporter],
    )
    monkeypatch.setattr(snapshot_policy.sys, "path_importer_cache", {"ambient": object()})

    snapshot_policy._configure_import_containment(context)

    assert snapshot_policy._FILE_FINDER_HOOK is not None
    assert snapshot_policy.sys.path_hooks == [snapshot_policy._FILE_FINDER_HOOK]
    assert ambient_hook not in snapshot_policy.sys.path_hooks
    assert zipimport.zipimporter not in snapshot_policy.sys.path_hooks
    assert snapshot_policy.sys.path_importer_cache == {}
    snapshot_policy._validate_import_containment(context)


def test_snapshot_policy_rejects_added_zipimport_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _snapshot_policy_context(tmp_path)
    _isolate_policy_import_state(monkeypatch)
    snapshot_policy._configure_import_containment(context)
    snapshot_policy.sys.path_hooks.append(zipimport.zipimporter)

    with pytest.raises(snapshot_policy.SnapshotBootstrapPolicyError):
        snapshot_policy._validate_import_containment(context)


def test_snapshot_policy_limited_file_finder_imports_snapshot_and_stdlib_modules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _snapshot_policy_context(tmp_path)
    source_name = "allowed_snapshot_module"
    stdlib_name = "allowed_stdlib_module"
    _policy_file(context.source_root / f"{source_name}.py").write_text(
        "VALUE = 'snapshot'\n", encoding="utf-8"
    )
    _policy_file(context.stdlib_root / f"{stdlib_name}.py").write_text(
        "VALUE = 'stdlib'\n", encoding="utf-8"
    )
    _isolate_policy_import_state(monkeypatch)
    monkeypatch.delitem(snapshot_policy.sys.modules, source_name, raising=False)
    monkeypatch.delitem(snapshot_policy.sys.modules, stdlib_name, raising=False)
    snapshot_policy._configure_import_containment(context)

    try:
        assert importlib.import_module(source_name).VALUE == "snapshot"
        assert importlib.import_module(stdlib_name).VALUE == "stdlib"
    finally:
        snapshot_policy.sys.modules.pop(source_name, None)
        snapshot_policy.sys.modules.pop(stdlib_name, None)

    snapshot_policy._validate_import_containment(context)


def test_snapshot_policy_rejects_zip_origin(tmp_path: Path) -> None:
    context = _snapshot_policy_context(tmp_path)
    zip_origin = _policy_file(context.source_root / "archive.zip" / "member.py")

    with pytest.raises(snapshot_policy.SnapshotBootstrapPolicyError):
        snapshot_policy._validate_spec(_source_spec(zip_origin), context)


def test_snapshot_policy_rejects_unknown_loader(tmp_path: Path) -> None:
    context = _snapshot_policy_context(tmp_path)
    allowed = _policy_file(context.source_root / "allowed.py")
    spec = types.SimpleNamespace(
        origin=str(allowed), loader=object(), submodule_search_locations=None
    )

    with pytest.raises(snapshot_policy.SnapshotBootstrapPolicyError):
        snapshot_policy._validate_spec(spec, context)


def test_snapshot_policy_rejects_relative_origin(tmp_path: Path) -> None:
    context = _snapshot_policy_context(tmp_path)
    allowed = _policy_file(context.source_root / "allowed.py")

    with pytest.raises(snapshot_policy.SnapshotBootstrapPolicyError):
        snapshot_policy._validate_spec(_source_spec(allowed, origin="relative.py"), context)


def test_snapshot_policy_rejects_non_builtin_none_origin(tmp_path: Path) -> None:
    context = _snapshot_policy_context(tmp_path)
    spec = types.SimpleNamespace(origin=None, loader=object(), submodule_search_locations=None)

    with pytest.raises(snapshot_policy.SnapshotBootstrapPolicyError):
        snapshot_policy._validate_spec(spec, context)


def test_snapshot_policy_rejects_namespace_location_outside_allowed_roots(tmp_path: Path) -> None:
    context = _snapshot_policy_context(tmp_path)
    allowed = _policy_file(context.source_root / "namespace" / "member.py").parent
    outside = _policy_file(tmp_path / "outside" / "namespace" / "member.py").parent
    loader = importlib.machinery.NamespaceLoader(
        "synthetic_namespace",
        [str(allowed), str(outside)],
        importlib.machinery.PathFinder.find_spec,
    )
    spec = types.SimpleNamespace(
        origin=None, loader=loader, submodule_search_locations=[str(allowed), str(outside)]
    )

    with pytest.raises(snapshot_policy.SnapshotBootstrapPolicyError):
        snapshot_policy._validate_spec(spec, context)


def _contained_policy_modules(
    monkeypatch: pytest.MonkeyPatch, context: snapshot_policy._PolicyContext
) -> dict[str, object]:
    modules: dict[str, object] = {"__main__": types.ModuleType("__main__")}
    monkeypatch.setattr(snapshot_policy.sys, "modules", modules)
    _isolate_policy_import_state(monkeypatch)
    snapshot_policy._configure_import_containment(context)
    return modules


def _isolate_policy_import_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(snapshot_policy.sys, "path", list(snapshot_policy.sys.path))
    monkeypatch.setattr(snapshot_policy.sys, "path_hooks", list(snapshot_policy.sys.path_hooks))
    monkeypatch.setattr(snapshot_policy.sys, "meta_path", list(snapshot_policy.sys.meta_path))
    monkeypatch.setattr(snapshot_policy.sys, "path_importer_cache", {})


def _loaded_policy_module(path: Path) -> object:
    module = types.ModuleType("added_module")
    module.__file__ = str(path)
    module.__spec__ = _source_spec(path)  # type: ignore[assignment]
    return module


def test_snapshot_policy_final_audit_rejects_main_added_snapshot_external_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _snapshot_policy_context(tmp_path)
    modules = _contained_policy_modules(monkeypatch, context)
    outside = _policy_file(tmp_path / "outside" / "added.py")

    def main(argv: tuple[str, ...]) -> int:
        assert argv == ()
        modules["added"] = _loaded_policy_module(outside)
        return 0

    with pytest.raises(snapshot_policy.SnapshotBootstrapPolicyError):
        snapshot_policy._run_with_final_audit(main, (), context)


def test_snapshot_policy_final_audit_allows_main_added_snapshot_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _snapshot_policy_context(tmp_path)
    modules = _contained_policy_modules(monkeypatch, context)
    inside = _policy_file(context.source_root / "added.py")

    def main(argv: tuple[str, ...]) -> int:
        assert argv == ()
        modules["added"] = _loaded_policy_module(inside)
        return 7

    assert snapshot_policy._run_with_final_audit(main, (), context) == 7


def test_snapshot_policy_final_audit_preserves_primary_exception_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _snapshot_policy_context(tmp_path)
    modules = _contained_policy_modules(monkeypatch, context)
    outside = _policy_file(tmp_path / "outside" / "added.py")
    primary = RuntimeError("primary identity")

    def main(argv: tuple[str, ...]) -> int:
        assert argv == ()
        modules["added"] = _loaded_policy_module(outside)
        raise primary

    with pytest.raises(RuntimeError) as caught:
        snapshot_policy._run_with_final_audit(main, (), context)

    assert caught.value is primary


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("base_prefix", "/fixed/incorrect-base"),
        ("python_version", (0, 0, 0)),
        ("implementation", "incorrect-implementation"),
        ("abi", "incorrect-abi"),
        ("stdlib_root", "/fixed/incorrect-stdlib"),
        ("dynamic_load_root", "/fixed/incorrect-dynload"),
    ],
)
def test_parent_interpreter_runtime_contract_rejects_each_mismatch_without_launch(
    attribute: str, value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production contract builder rejects fixed parent facts before Popen."""

    interpreter = runner._open_verified_interpreter()
    popen_calls = 0

    def forbidden_popen(*args: object, **kwargs: object) -> object:
        nonlocal popen_calls
        del args, kwargs
        popen_calls += 1
        raise AssertionError("Popen must not run")

    monkeypatch.setattr(shutdown_process_harness.subprocess, "Popen", forbidden_popen)
    try:
        with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
            runner._interpreter_runtime_contract(replace(interpreter, **{attribute: value}))
    finally:
        runner._close_snapshot_descriptor(interpreter.descriptor)

    assert (
        caught.value.classification
        is runner.MeasurementFailureClassification.SOURCE_METADATA_INVALID
    )
    assert str(value) not in str(caught.value)
    assert popen_calls == 0


@pytest.mark.parametrize("case", ["missing", "regular", "unexpected_symlink", "parent_identity"])
def test_parent_interpreter_launcher_contract_fails_closed_without_popen(
    case: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_repository = tmp_path / "repository"
    launcher = fake_repository / ".venv" / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    if case == "regular":
        launcher.write_text("not an interpreter", encoding="ascii")
    elif case == "unexpected_symlink":
        launcher.symlink_to("/bin/false")
    elif case == "parent_identity":
        launcher.symlink_to(sys.executable)
    monkeypatch.setattr(runner, "_REPOSITORY_ROOT", fake_repository)
    popen_calls = 0

    def forbidden_popen(*args: object, **kwargs: object) -> object:
        nonlocal popen_calls
        del args, kwargs
        popen_calls += 1
        raise AssertionError("Popen must not run")

    monkeypatch.setattr(shutdown_process_harness.subprocess, "Popen", forbidden_popen)
    with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
        runner._open_verified_interpreter()

    assert (
        caught.value.classification
        is runner.MeasurementFailureClassification.SOURCE_METADATA_INVALID
    )
    assert str(fake_repository) not in str(caught.value)
    assert popen_calls == 0


@pytest.mark.parametrize(
    "invalid",
    [
        _file_stat(inode=1, mode=stat.S_IFDIR | 0o755),
        _file_stat(inode=1, mode=stat.S_IFREG | 0o644),
        _file_stat(inode=1, mode=stat.S_IFREG | 0o777),
        _file_stat(inode=1, mode=stat.S_IFREG | 0o755, uid=os.getuid() + 1),
        os.stat_result((stat.S_IFREG | 0o755, 1, 2, 1, os.getuid(), 0, 0, 0, 0, 0)),
        _file_stat(inode=2, mode=stat.S_IFREG | 0o755),
    ],
)
def test_parent_interpreter_status_validator_rejects_file_owner_mode_device_and_inode(
    invalid: os.stat_result,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_calls = 0

    def forbidden_popen(*args: object, **kwargs: object) -> object:
        nonlocal popen_calls
        del args, kwargs
        popen_calls += 1
        raise AssertionError("Popen must not run")

    monkeypatch.setattr(shutdown_process_harness.subprocess, "Popen", forbidden_popen)
    expected = _file_stat(inode=1, mode=stat.S_IFREG | 0o755)
    assert not runner._valid_interpreter_status(invalid, expected)
    assert popen_calls == 0


@pytest.mark.parametrize("mutation", ["pathname", "held_fd"])
def test_launch_revalidation_rejects_interpreter_path_or_held_fd_replacement(
    mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    interpreter = runner._open_verified_interpreter()
    popen_calls = 0
    real_lstat = os.lstat
    real_fstat = os.fstat

    def forbidden_popen(*args: object, **kwargs: object) -> object:
        nonlocal popen_calls
        del args, kwargs
        popen_calls += 1
        raise AssertionError("Popen must not run")

    def changed_lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        observed = real_lstat(path, *args, **kwargs)
        if mutation != "pathname" or Path(path) != interpreter.launcher:
            return observed
        fields = list(observed)
        fields[1] += 1
        return os.stat_result(fields)

    def changed_fstat(descriptor: int) -> os.stat_result:
        observed = real_fstat(descriptor)
        if mutation != "held_fd" or descriptor != interpreter.descriptor:
            return observed
        fields = list(observed)
        fields[1] += 1
        return os.stat_result(fields)

    monkeypatch.setattr(shutdown_process_harness.subprocess, "Popen", forbidden_popen)
    monkeypatch.setattr(runner.os, "lstat", changed_lstat)
    monkeypatch.setattr(runner.os, "fstat", changed_fstat)
    try:
        with pytest.raises(runner.ShutdownMeasurementRunnerError) as caught:
            runner._revalidate_interpreter_for_launch(interpreter)
    finally:
        runner._close_snapshot_descriptor(interpreter.descriptor)

    assert (
        caught.value.classification
        is runner.MeasurementFailureClassification.SOURCE_METADATA_INVALID
    )
    assert popen_calls == 0


def _child_self_check_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[dict[str, object], Path]:
    snapshot = tmp_path / "snapshot"
    source = snapshot / "src"
    policy_file = snapshot / "tests" / "support" / "snapshot_child_bootstrap_policy.py"
    base_prefix = tmp_path / "base"
    stdlib = base_prefix / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
    dynamic = stdlib / "lib-dynload"
    source.mkdir(parents=True)
    policy_file.parent.mkdir(parents=True)
    policy_file.write_text("# policy\n", encoding="ascii")
    dynamic.mkdir(parents=True)
    monkeypatch.setattr(snapshot_policy.Path, "cwd", classmethod(lambda cls: snapshot))
    monkeypatch.setattr(snapshot_policy, "__file__", str(policy_file))
    monkeypatch.setattr(snapshot_policy.sys, "prefix", str(base_prefix))
    monkeypatch.setattr(snapshot_policy.sys, "base_prefix", str(base_prefix))
    implementation = types.SimpleNamespace(
        name=sys.implementation.name, cache_tag=sys.implementation.cache_tag
    )
    monkeypatch.setattr(snapshot_policy.sys, "implementation", implementation)
    contract: dict[str, object] = {
        "base_prefix": str(base_prefix),
        "prefix": str(base_prefix),
        "stdlib_root": str(stdlib),
        "dynamic_load_root": str(dynamic),
        "interpreter_identity": [],
        "interpreter_uid": os.getuid(),
        "python_version": list(sys.version_info[:3]),
        "implementation": sys.implementation.name,
        "abi": sys.implementation.cache_tag,
    }
    contract["interpreter_identity"] = [
        os.stat("/proc/self/exe").st_dev,
        os.stat("/proc/self/exe").st_ino,
    ]
    monkeypatch.setattr(snapshot_policy.sys, "modules", {"__main__": types.ModuleType("__main__")})
    return contract, base_prefix


def test_child_self_check_accepts_isolated_base_runtime_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract, base_prefix = _child_self_check_contract(monkeypatch, tmp_path)

    context = snapshot_policy._context_from_contract(json.dumps(contract))

    assert snapshot_policy.sys.prefix == snapshot_policy.sys.base_prefix == str(base_prefix)
    assert context.stdlib_root == Path(contract["stdlib_root"])
    assert context.dynamic_load_root == Path(contract["dynamic_load_root"])


@pytest.mark.parametrize(
    "case",
    [
        "exe_dev",
        "exe_inode",
        "prefix",
        "base_prefix",
        "prefix_not_base_prefix",
        "python_version",
        "implementation",
        "abi",
        "stdlib",
        "dynload",
        "base",
        "site_root",
    ],
)
def test_child_self_check_rejects_each_contract_mismatch_before_project_import(
    case: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract, base_prefix = _child_self_check_contract(monkeypatch, tmp_path)
    if case == "exe_dev":
        contract["interpreter_identity"] = [0, contract["interpreter_identity"][1]]  # type: ignore[index]
    elif case == "exe_inode":
        contract["interpreter_identity"] = [contract["interpreter_identity"][0], 0]  # type: ignore[index]
    elif case == "prefix":
        monkeypatch.setattr(snapshot_policy.sys, "prefix", str(base_prefix / "other"))
    elif case == "base_prefix":
        monkeypatch.setattr(snapshot_policy.sys, "base_prefix", str(base_prefix / "other"))
    elif case == "prefix_not_base_prefix":
        monkeypatch.setattr(snapshot_policy.sys, "prefix", str(base_prefix / "other"))
    elif case == "python_version":
        contract["python_version"] = [0, 0, 0]
    elif case == "implementation":
        contract["implementation"] = "incorrect-implementation"
    elif case == "abi":
        contract["abi"] = "incorrect-abi"
    elif case == "stdlib":
        contract["stdlib_root"] = str(base_prefix / "lib" / "incorrect-stdlib")
    elif case == "dynload":
        contract["dynamic_load_root"] = str(base_prefix / "lib" / "incorrect-dynload")
    elif case == "base":
        contract["base_prefix"] = str(base_prefix / "incorrect-base")
    else:
        site = base_prefix / "lib" / "site-packages"
        site.mkdir()
        contract["stdlib_root"] = str(site)
        contract["dynamic_load_root"] = str(site / "lib-dynload")
        (site / "lib-dynload").mkdir()

    with pytest.raises(snapshot_policy.SnapshotBootstrapPolicyError) as caught:
        snapshot_policy.bootstrap(json.dumps(contract), ())

    assert (
        "discord_ai_reminder_bot.infrastructure.shutdown_measurement_harness"
        not in snapshot_policy.sys.modules
    )
    rendered = str(caught.value)
    assert str(base_prefix) not in rendered
    assert "incorrect" not in rendered


def test_snapshot_popen_uses_only_verified_interpreter_and_snapshot_fds(
    monkeypatch: pytest.MonkeyPatch, authorized_offline_success: Path
) -> None:
    """Capture the real controller invocation; the wrapper still starts the child."""

    del authorized_offline_success
    source_set = runner._verified_source_set(runner._external_manifest())
    interpreter = runner._open_verified_interpreter()
    snapshot = runner._build_execution_snapshot(source_set)
    child: shutdown_process_harness.SyntheticChildHandle | None = None
    captured: dict[str, object] = {}
    real_popen = shutdown_process_harness.subprocess.Popen

    def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(shutdown_process_harness.subprocess, "Popen", capture_popen)
    try:
        assert snapshot.handles.directory_fd is not None
        child = shutdown_process_harness.launch_child(
            REPOSITORY_ROOT,
            shutdown_measurement_harness.SyntheticChildScenario.IMMEDIATE_SUCCESS,
            interpreter_fd=interpreter.descriptor,
            snapshot_fd=snapshot.handles.directory_fd,
            runtime_contract=runner._interpreter_runtime_contract(interpreter),
        )
        command = captured["args"][0]  # type: ignore[index]
        options = captured["kwargs"]  # type: ignore[assignment]
        assert command[0] == f"/proc/self/fd/{interpreter.descriptor}"  # type: ignore[index]
        assert {"-I", "-B", "-S"}.issubset(command)  # type: ignore[arg-type]
        assert options["close_fds"] is True  # type: ignore[index]
        assert set(options["pass_fds"]) == {interpreter.descriptor, snapshot.handles.directory_fd}  # type: ignore[index]
        assert options["env"] == shutdown_process_harness._CHILD_ENV  # type: ignore[index]
        assert options["cwd"] == f"/proc/self/fd/{snapshot.handles.directory_fd}"  # type: ignore[index]
        assert options.get("shell", False) is False  # type: ignore[union-attr]
        assert command[0] != str(REPOSITORY_ROOT / ".venv" / "bin" / "python")  # type: ignore[index]
    finally:
        if child is not None:
            try:
                shutdown_process_harness.stop_and_reap(child)
            except shutdown_process_harness.SyntheticProcessError:
                pass
        runner._cleanup_execution_snapshot(snapshot)
        runner._close_snapshot_descriptor(interpreter.descriptor)


def test_authorized_component_child_inherits_only_interpreter_and_snapshot_fds(
    authorized_offline_success: Path, tmp_path: Path
) -> None:
    """Authorized offline component check, not formal runner or Linux evidence."""

    manifest = authorized_offline_success
    source = Path(snapshot_policy.__file__)
    artifact = tmp_path / "artifact-canary"
    artifact.write_text("artifact", encoding="ascii")
    source_fd = os.open(source, os.O_RDONLY)
    manifest_fd = os.open(manifest, os.O_RDONLY)
    artifact_fd = os.open(artifact, os.O_RDONLY)
    interpreter = runner._open_verified_interpreter()
    snapshot = runner._build_execution_snapshot(
        runner._verified_source_set(runner._external_manifest())
    )
    child: shutdown_process_harness.SyntheticChildHandle | None = None
    try:
        assert snapshot.handles.directory_fd is not None
        child = shutdown_process_harness.launch_child(
            REPOSITORY_ROOT,
            shutdown_measurement_harness.SyntheticChildScenario.IMMEDIATE_SUCCESS,
            interpreter_fd=interpreter.descriptor,
            snapshot_fd=snapshot.handles.directory_fd,
            runtime_contract=runner._interpreter_runtime_contract(interpreter),
        )
        assert (
            shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
        )
        child_fd_root = Path(f"/proc/{child.process.pid}/fd")

        def matches_child_fd(descriptor: int) -> bool:
            try:
                observed = (child_fd_root / str(descriptor)).stat()
                expected = os.fstat(descriptor)
            except OSError:
                return False
            return (observed.st_dev, observed.st_ino) == (expected.st_dev, expected.st_ino)

        assert matches_child_fd(interpreter.descriptor)
        assert matches_child_fd(snapshot.handles.directory_fd)
        assert not matches_child_fd(source_fd)
        assert not matches_child_fd(manifest_fd)
        assert not matches_child_fd(artifact_fd)
    finally:
        if child is not None:
            try:
                shutdown_process_harness.stop_and_reap(child)
            except shutdown_process_harness.SyntheticProcessError:
                pass
        runner._cleanup_execution_snapshot(snapshot)
        runner._close_snapshot_descriptor(interpreter.descriptor)
        os.close(source_fd)
        os.close(manifest_fd)
        os.close(artifact_fd)


def _run_private_snapshot_probe(
    probe: str, authorized_offline_success: Path
) -> tuple[str, str, int]:
    """Run one test-only probe through the full private snapshot child route."""

    del authorized_offline_success
    source_set = runner._verified_source_set(runner._external_manifest())
    interpreter = runner._open_verified_interpreter()
    snapshot = runner._build_execution_snapshot(source_set)
    child: shutdown_process_harness.SyntheticChildHandle | None = None
    snapshot_failed = True
    snapshot_special: BaseException | None = None
    try:
        assert snapshot.handles.directory_fd is not None
        runner._revalidate_execution_snapshot(snapshot)
        runner._revalidate_interpreter_for_launch(interpreter)
        child = shutdown_process_harness.launch_snapshot_child(
            REPOSITORY_ROOT,
            shutdown_measurement_harness.SyntheticChildScenario.IMMEDIATE_SUCCESS,
            interpreter_fd=interpreter.descriptor,
            snapshot_fd=snapshot.handles.directory_fd,
            runtime_contract=runner._interpreter_runtime_contract(interpreter),
            audit_probe=probe,
        )
        first_marker = shutdown_process_harness.read_marker(child)
        stdout, stderr = shutdown_process_harness.collect_output(child)
        assert child.process.returncode == snapshot_policy._PROBE_REJECT_EXIT_CODE
        shutdown_process_harness.stop_and_reap(child)
        assert child.reaped
        assert shutdown_process_harness.residual_count(child) == 0
        return first_marker + "\n" + stdout, stderr, child.process.returncode
    finally:
        if child is not None:
            if not child.reaped:
                shutdown_process_harness.stop_and_reap(child)
            assert child.reaped
            assert child.process.stdout is not None and child.process.stdout.closed
            assert child.process.stderr is not None and child.process.stderr.closed
            assert shutdown_process_harness.residual_count(child) == 0
        snapshot_failed, snapshot_special = runner._cleanup_execution_snapshot(snapshot)
        runner._close_snapshot_descriptor(interpreter.descriptor)
        assert not snapshot_failed
        assert snapshot_special is None


@pytest.mark.parametrize(
    "probe",
    [
        "socket-connect",
        "socket-connect-ex",
        "socket-sendto",
        "socket-sendmsg",
        "socket-getaddrinfo",
        "socket-getnameinfo",
        "socket-gethostbyname",
        "socket-gethostbyaddr",
        "subprocess-popen",
        "os-system",
        "posix-spawn",
    ],
)
def test_private_snapshot_network_probe_e2e_blocks_before_side_effects(
    probe: str, authorized_offline_success: Path
) -> None:
    """Authorized offline component E2E; no direct repository-src import evidence."""

    stdout, stderr, exit_code = _run_private_snapshot_probe(probe, authorized_offline_success)

    assert "SNAPSHOT_AUDIT_GUARD_BLOCKED" in stdout
    assert stderr == ""
    assert exit_code == snapshot_policy._PROBE_REJECT_EXIT_CODE
    assert "127.0.0.1" not in stdout
    assert "/bin/true" not in stdout


@pytest.mark.parametrize(
    "probe",
    [
        "import-http-client",
        "import-urllib-request",
        "import-requests",
        "import-httpx",
        "import-openai",
        "importlib-http-client",
        "from-http-client",
        "submodule-http-client",
        "preloaded-http-client",
        "alias-http-client",
    ],
)
def test_private_snapshot_denied_import_probe_e2e_blocks_before_path_resolution(
    probe: str, authorized_offline_success: Path
) -> None:
    """The guard, not optional-package availability, supplies the fixed rejection."""

    stdout, stderr, exit_code = _run_private_snapshot_probe(probe, authorized_offline_success)

    assert "SNAPSHOT_IMPORT_GUARD_BLOCKED" in stdout
    assert stderr == ""
    assert exit_code == snapshot_policy._PROBE_REJECT_EXIT_CODE
    assert "ModuleNotFoundError" not in stdout
    assert probe.removeprefix("import-") not in stdout


def test_snapshot_probe_selection_is_test_support_only_and_formal_runner_cannot_select_it() -> None:
    runner_source = Path(runner.__file__).read_text(encoding="utf-8")
    harness_source = Path(shutdown_process_harness.__file__).read_text(encoding="utf-8")
    production_source = Path(shutdown_measurement_harness.__file__).read_text(encoding="utf-8")

    assert "audit_probe" not in runner_source
    assert "audit_probe" not in production_source
    assert "os.environ" not in harness_source
    assert "--audit-probe" not in harness_source
    assert (
        runner._FIXED_SCENARIO
        is shutdown_measurement_harness.SyntheticChildScenario.IMMEDIATE_SUCCESS
    )
