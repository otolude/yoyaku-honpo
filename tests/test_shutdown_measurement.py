from __future__ import annotations

import ast
import asyncio
import os
import select
import signal
import subprocess
import traceback
from dataclasses import fields
from pathlib import Path
from typing import BinaryIO
from unittest.mock import MagicMock

import pytest

from discord_ai_reminder_bot.application.shutdown_measurement import (
    MAX_MILLISECONDS,
    SHUTDOWN_STAGE_ORDER,
    ActiveProviderCompletion,
    ActiveProviderPoint,
    MeasurementEvidenceKind,
    ProviderShutdownClassification,
    ShutdownDeadlineInputs,
    ShutdownMeasurementError,
    ShutdownMeasurementValue,
    ShutdownStage,
    SyntheticActiveProviderPolicy,
    SyntheticShutdownExecutor,
    SyntheticShutdownPlan,
    SyntheticStageClassification,
    SyntheticStageScenario,
    SyntheticStageSpec,
    calculate_shutdown_deadline,
)
from discord_ai_reminder_bot.infrastructure import shutdown_measurement_harness
from tests.support import shutdown_process_harness
from tests.support.shutdown_process_harness import (
    ChildSignal,
    SyntheticProcessError,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _bound(value: object = 1) -> ShutdownMeasurementValue:
    return ShutdownMeasurementValue(
        milliseconds=value,  # type: ignore[arg-type]
        evidence_kind=MeasurementEvidenceKind.CONFIGURED_HARD_BOUND,
    )


def _deadline_inputs(value: int = 1) -> ShutdownDeadlineInputs:
    return ShutdownDeadlineInputs(
        **{field.name: _bound(value) for field in fields(ShutdownDeadlineInputs)}
    )


def _stage_spec(
    stage: ShutdownStage,
    *,
    scenario: SyntheticStageScenario = SyntheticStageScenario.IMMEDIATE_SUCCESS,
    duration: int = 0,
    allowance: int = 1,
) -> SyntheticStageSpec:
    return SyntheticStageSpec(
        stage=stage,
        scenario=scenario,
        logical_duration_milliseconds=duration,
        allowance_milliseconds=allowance,
    )


def _plan(
    overrides: dict[ShutdownStage, SyntheticStageSpec] | None = None,
) -> SyntheticShutdownPlan:
    overrides = overrides or {}
    return SyntheticShutdownPlan(
        tuple(overrides.get(stage, _stage_spec(stage)) for stage in SHUTDOWN_STAGE_ORDER)
    )


def test_deadline_requires_every_configured_hard_bound() -> None:
    inputs = _deadline_inputs(2)
    result = calculate_shutdown_deadline(inputs)

    assert result.total_milliseconds == 26
    assert result.systemd_seconds_ceiling == 1
    assert result.approved_production_value is False

    values = {field.name: getattr(inputs, field.name) for field in fields(ShutdownDeadlineInputs)}
    for name in values:
        incomplete = values | {name: None}
        with pytest.raises(ShutdownMeasurementError, match="not configured"):
            calculate_shutdown_deadline(ShutdownDeadlineInputs(**incomplete))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kind",
    [
        MeasurementEvidenceKind.SYNTHETIC_LOGICAL_OBSERVATION,
        MeasurementEvidenceKind.WSL_WALL_CLOCK_OBSERVATION,
        MeasurementEvidenceKind.LINUX_REAL_HOST_OBSERVATION,
        MeasurementEvidenceKind.APPROVED_PRODUCTION_VALUE,
    ],
)
def test_observations_and_approved_values_are_not_hard_bounds(
    kind: MeasurementEvidenceKind,
) -> None:
    inputs = _deadline_inputs()
    values = {field.name: getattr(inputs, field.name) for field in fields(ShutdownDeadlineInputs)}
    values["provider_close_allowance"] = ShutdownMeasurementValue(1, kind)

    with pytest.raises(ShutdownMeasurementError, match="not configured"):
        calculate_shutdown_deadline(ShutdownDeadlineInputs(**values))


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        -1,
        1.0,
        "",
        " 1",
        "1 ",
        "+1",
        "1e3",
        "1,000",
        "NaN",
        "Infinity",
        "UNKNOWN",
    ],
)
def test_shutdown_milliseconds_reject_invalid_numeric_forms(value: object) -> None:
    with pytest.raises(ShutdownMeasurementError, match="invalid shutdown measurement"):
        _bound(value)


def test_deadline_rounds_toward_safety_and_rejects_overflow() -> None:
    values = {field.name: _bound(0) for field in fields(ShutdownDeadlineInputs)}
    values["active_provider_remaining_bound"] = _bound(1_001)
    result = calculate_shutdown_deadline(ShutdownDeadlineInputs(**values))
    assert result.total_milliseconds == 1_001
    assert result.systemd_seconds_ceiling == 2

    values["active_provider_remaining_bound"] = _bound(MAX_MILLISECONDS)
    values["provider_close_allowance"] = _bound(1)
    with pytest.raises(ShutdownMeasurementError, match="overflow"):
        calculate_shutdown_deadline(ShutdownDeadlineInputs(**values))


def test_shutdown_stage_order_is_complete_and_fixed() -> None:
    assert len(SHUTDOWN_STAGE_ORDER) == 10
    assert len(set(SHUTDOWN_STAGE_ORDER)) == 10
    assert SHUTDOWN_STAGE_ORDER == tuple(ShutdownStage)

    reversed_specs = tuple(_stage_spec(stage) for stage in reversed(SHUTDOWN_STAGE_ORDER))
    with pytest.raises(ShutdownMeasurementError, match="stage order"):
        SyntheticShutdownPlan(reversed_specs)
    with pytest.raises(ShutdownMeasurementError, match="stage order"):
        SyntheticShutdownPlan(tuple(_stage_spec(stage) for stage in SHUTDOWN_STAGE_ORDER[:-1]))
    duplicate_specs = tuple(
        _stage_spec(SHUTDOWN_STAGE_ORDER[0] if index == 1 else stage)
        for index, stage in enumerate(SHUTDOWN_STAGE_ORDER)
    )
    with pytest.raises(ShutdownMeasurementError, match="stage order"):
        SyntheticShutdownPlan(duplicate_specs)
    with pytest.raises(ValueError):
        ShutdownStage("unknown")


@pytest.mark.asyncio
async def test_stage_failure_cancellation_and_timeout_do_not_skip_later_stages() -> None:
    overrides = {
        SHUTDOWN_STAGE_ORDER[0]: _stage_spec(
            SHUTDOWN_STAGE_ORDER[0], scenario=SyntheticStageScenario.FIXED_FAILURE
        ),
        SHUTDOWN_STAGE_ORDER[1]: _stage_spec(
            SHUTDOWN_STAGE_ORDER[1], scenario=SyntheticStageScenario.FIXED_CANCELLATION
        ),
        SHUTDOWN_STAGE_ORDER[2]: _stage_spec(
            SHUTDOWN_STAGE_ORDER[2],
            scenario=SyntheticStageScenario.ALLOWANCE_EXCEEDED,
            duration=2,
            allowance=1,
        ),
    }
    results = await SyntheticShutdownExecutor(_plan(overrides)).run()

    assert tuple(result.stage for result in results) == SHUTDOWN_STAGE_ORDER
    assert tuple(result.classification for result in results[:3]) == (
        SyntheticStageClassification.FAILED,
        SyntheticStageClassification.CANCELLED,
        SyntheticStageClassification.TIMED_OUT,
    )
    assert all(
        result.classification is SyntheticStageClassification.COMPLETED for result in results[3:]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "duration", "allowance", "expected"),
    [
        (
            SyntheticStageScenario.COMPLETION_JUST_BELOW_ALLOWANCE,
            9,
            10,
            SyntheticStageClassification.COMPLETED,
        ),
        (
            SyntheticStageScenario.COMPLETION_EXACTLY_AT_ALLOWANCE,
            10,
            10,
            SyntheticStageClassification.COMPLETED,
        ),
        (
            SyntheticStageScenario.COMPLETION_JUST_ABOVE_ALLOWANCE,
            11,
            10,
            SyntheticStageClassification.TIMED_OUT,
        ),
    ],
)
async def test_allowance_boundary_is_logical_not_wall_clock(
    scenario: SyntheticStageScenario,
    duration: int,
    allowance: int,
    expected: SyntheticStageClassification,
) -> None:
    first = SHUTDOWN_STAGE_ORDER[0]
    executor = SyntheticShutdownExecutor(
        _plan(
            {first: _stage_spec(first, scenario=scenario, duration=duration, allowance=allowance)}
        )
    )
    result = await executor.run()
    assert result[0].classification is expected
    assert result[0].finished_at_milliseconds == duration


@pytest.mark.asyncio
async def test_controlled_wait_uses_internal_event_without_sleep() -> None:
    first = SHUTDOWN_STAGE_ORDER[0]
    executor = SyntheticShutdownExecutor(
        _plan(
            {
                first: _stage_spec(
                    first,
                    scenario=SyntheticStageScenario.CONTROLLED_COOPERATIVE_WAIT,
                    duration=1,
                    allowance=1,
                )
            }
        )
    )
    task = asyncio.create_task(executor.run())
    await executor.wait_until_started(first)
    assert not task.done()
    executor.release_controlled_stage(first)
    results = await task
    assert results[0].classification is SyntheticStageClassification.COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "point",
    [
        ActiveProviderPoint.INPUT_TOKEN_COUNT,
        ActiveProviderPoint.CREATE,
        ActiveProviderPoint.BUDGET_CHECK,
        ActiveProviderPoint.BEFORE_CREATE,
    ],
)
async def test_active_provider_operation_waits_and_rejects_new_work(
    point: ActiveProviderPoint,
) -> None:
    policy = SyntheticActiveProviderPolicy(pause_after_closing=True)
    policy.start_operation(
        point, remaining_bound_milliseconds=10, logical_completion_milliseconds=10
    )
    first = asyncio.create_task(policy.close())
    second = asyncio.create_task(policy.close())
    await policy.wait_until_closing()

    with pytest.raises(RuntimeError, match="closing"):
        policy.start_operation(
            ActiveProviderPoint.CREATE,
            remaining_bound_milliseconds=1,
            logical_completion_milliseconds=1,
        )
    policy.release_close()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result is second_result
    assert first_result.classification is ProviderShutdownClassification.ACTIVE_OPERATION_COMPLETED
    assert first_result.close_start_count == 1
    assert (
        first_result.retry_count,
        first_result.blind_retry_count,
        first_result.refund_count,
    ) == (0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("completion", "duration", "expected"),
    [
        (
            ActiveProviderCompletion.COOPERATIVE,
            11,
            ProviderShutdownClassification.ACTIVE_OPERATION_TIMED_OUT,
        ),
        (
            ActiveProviderCompletion.CANCELLED,
            0,
            ProviderShutdownClassification.ACTIVE_OPERATION_CANCELLED,
        ),
    ],
)
async def test_active_provider_timeout_and_cancellation_are_fixed_classifications(
    completion: ActiveProviderCompletion,
    duration: int,
    expected: ProviderShutdownClassification,
) -> None:
    policy = SyntheticActiveProviderPolicy()
    policy.start_operation(
        ActiveProviderPoint.CREATE,
        completion,
        remaining_bound_milliseconds=10,
        logical_completion_milliseconds=duration,
    )
    assert (await policy.close()).classification is expected


@pytest.mark.asyncio
async def test_provider_shutdown_without_operation_is_classified() -> None:
    policy = SyntheticActiveProviderPolicy()
    result = await policy.close()
    assert result.classification is ProviderShutdownClassification.NO_ACTIVE_OPERATION


@pytest.mark.asyncio
async def test_provider_close_caller_cancellation_does_not_cancel_shared_cleanup() -> None:
    policy = SyntheticActiveProviderPolicy(pause_after_closing=True)
    policy.start_operation(
        ActiveProviderPoint.CREATE,
        remaining_bound_milliseconds=10,
        logical_completion_milliseconds=10,
    )
    cancelled_caller = asyncio.create_task(policy.close())
    observing_caller = asyncio.create_task(policy.close())
    await policy.wait_until_closing()
    cancelled_caller.cancel()
    policy.release_close()

    with pytest.raises(asyncio.CancelledError):
        await cancelled_caller
    observed = await observing_caller
    assert observed.classification is ProviderShutdownClassification.ACTIVE_OPERATION_COMPLETED
    assert observed.close_start_count == 1


@pytest.mark.asyncio
async def test_close_sets_closing_before_shared_close_body_can_start() -> None:
    policy = SyntheticActiveProviderPolicy(pause_before_close_body=True)
    close_task = asyncio.create_task(policy.close())
    await policy.wait_until_closing()

    assert policy.closing is True
    with pytest.raises(RuntimeError, match="closing"):
        policy.start_operation(
            ActiveProviderPoint.CREATE,
            remaining_bound_milliseconds=1,
            logical_completion_milliseconds=1,
        )
    assert policy._close_start_count == 0
    assert policy._active_point is ActiveProviderPoint.NONE

    policy.release_close_body()
    result = await close_task
    assert result.close_start_count == 1


@pytest.mark.asyncio
async def test_close_task_creation_failure_keeps_runtime_closing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = SyntheticActiveProviderPolicy()

    def fail_create_task(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("fixed task creation failure")

    monkeypatch.setattr(asyncio, "create_task", fail_create_task)
    with pytest.raises(RuntimeError, match="fixed task creation failure"):
        await policy.close()
    assert policy.closing is True
    with pytest.raises(RuntimeError, match="closing"):
        policy.start_operation(
            ActiveProviderPoint.CREATE,
            remaining_bound_milliseconds=1,
            logical_completion_milliseconds=1,
        )


def _launch(
    scenario: shutdown_measurement_harness.SyntheticChildScenario,
) -> shutdown_process_harness.SyntheticChildHandle:
    return shutdown_process_harness.launch_child(REPOSITORY_ROOT, scenario)


def _assert_child_resources_closed(
    child: shutdown_process_harness.SyntheticChildHandle,
) -> None:
    assert child.process.returncode is not None
    assert child.process.stdout is not None and child.process.stdout.closed
    assert child.process.stderr is not None and child.process.stderr.closed
    assert shutdown_process_harness.residual_count(child) == 0


def _cleanup_and_reraise_primary(
    child: shutdown_process_harness.SyntheticChildHandle,
    primary: BaseException,
    cleanup_classifications: list[str],
) -> None:
    try:
        shutdown_process_harness.stop_and_reap(child)
    except SyntheticProcessError:
        cleanup_classifications.append("synthetic_child_cleanup_failed")
    raise primary from None


def _assert_fixed_exception_is_non_reflecting(
    exception: SyntheticProcessError,
    canary: str,
) -> None:
    rendered = "".join(traceback.format_exception(exception))
    assert exception.__cause__ is None
    assert exception.__suppress_context__ is True
    assert exception.__context__ is not None
    assert canary not in str(exception)
    assert canary not in rendered


class _MarkerPipeProxy:
    def __init__(self, wrapped: BinaryIO, *, payload: bytes, read_fails: bool = False) -> None:
        self._wrapped = wrapped
        self._payload = payload
        self._read_fails = read_fails

    @property
    def closed(self) -> bool:
        return self._wrapped.closed

    def close(self) -> None:
        self._wrapped.close()

    def readline(self) -> bytes:
        if self._read_fails:
            raise OSError("SENSITIVE_READLINE_CANARY")
        return self._payload


def test_signal_synthetic_child_handles_sigint_and_exits_cleanly() -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.IMMEDIATE_SUCCESS)
    try:
        assert (
            shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
        )
        shutdown_process_harness.send_signal(child, ChildSignal.INTERRUPT)
        stdout, stderr = shutdown_process_harness.collect_output(child)
    finally:
        shutdown_process_harness.stop_and_reap(child)

    output = stdout.splitlines()
    assert child.process.returncode == 0
    assert stderr == ""
    assert "SYNTHETIC_SIGNAL=SIGINT" in output
    assert shutdown_measurement_harness.CLEANUP_START_MARKER in output
    assert shutdown_measurement_harness.CLEANUP_COMPLETE_MARKER in output
    assert shutdown_measurement_harness.EXIT_MARKER in output
    assert sum(line.startswith("SYNTHETIC_STAGE_COMPLETED=") for line in output) == 10
    assert not any(private in (stdout + stderr) for private in ("credential", "token", "private"))
    _assert_child_resources_closed(child)


def test_signal_child_timeout_is_killed_and_reaped_without_zombie() -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    try:
        assert (
            shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
        )
        shutdown_process_harness.send_signal(child, ChildSignal.INTERRUPT)
        assert shutdown_process_harness.read_marker(child) == "SYNTHETIC_SIGNAL=SIGINT"
        assert (
            shutdown_process_harness.read_marker(child)
            == shutdown_measurement_harness.CLEANUP_TASK_CREATED_MARKER
        )
        assert (
            shutdown_process_harness.read_marker(child)
            == shutdown_measurement_harness.CLEANUP_START_MARKER
        )
        with pytest.raises(SyntheticProcessError, match="output wait timed out"):
            shutdown_process_harness.collect_output(child, timeout=0.05)
    finally:
        shutdown_process_harness.stop_and_reap(child)

    _assert_child_resources_closed(child)


def test_signal_before_ready_is_fixed_and_reaped() -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.READY_BEFORE_HANDLER)
    try:
        assert (
            shutdown_process_harness.read_marker(child)
            == shutdown_measurement_harness.PRE_READY_MARKER
        )
        shutdown_process_harness.send_signal(child, ChildSignal.INTERRUPT)
        stdout, stderr = shutdown_process_harness.collect_output(child)
    finally:
        shutdown_process_harness.stop_and_reap(child)

    assert child.process.returncode == shutdown_measurement_harness.PRE_READY_SIGNAL_EXIT_CODE
    assert shutdown_measurement_harness.CLEANUP_START_MARKER not in stdout
    assert stderr == ""
    _assert_child_resources_closed(child)


def test_second_sigint_during_cleanup_does_not_duplicate_stages() -> None:
    child = _launch(
        shutdown_measurement_harness.SyntheticChildScenario.SECOND_SIGNAL_DURING_CLEANUP
    )
    observed_markers: list[str] = []
    try:
        observed_markers.append(shutdown_process_harness.read_marker(child))
        assert observed_markers[-1] == shutdown_measurement_harness.READY_MARKER
        shutdown_process_harness.send_signal(child, ChildSignal.INTERRUPT)
        observed_markers.append(shutdown_process_harness.read_marker(child))
        assert observed_markers[-1] == "SYNTHETIC_SIGNAL=SIGINT"
        observed_markers.append(shutdown_process_harness.read_marker(child))
        assert observed_markers[-1] == shutdown_measurement_harness.CLEANUP_TASK_CREATED_MARKER
        observed_markers.append(shutdown_process_harness.read_marker(child))
        assert observed_markers[-1] == shutdown_measurement_harness.CLEANUP_START_MARKER
        shutdown_process_harness.send_signal(child, ChildSignal.INTERRUPT)
        stdout, stderr = shutdown_process_harness.collect_output(child)
    finally:
        shutdown_process_harness.stop_and_reap(child)

    output = observed_markers + stdout.splitlines()
    assert child.process.returncode == 0
    assert output.count("SYNTHETIC_SIGNAL=SIGINT") == 1
    assert output.count(shutdown_measurement_harness.EXIT_MARKER) == 1
    assert output.count(shutdown_measurement_harness.CLEANUP_TASK_CREATED_MARKER) == 1
    assert output.count(shutdown_measurement_harness.SECOND_SIGNAL_MARKER) == 1
    assert output.count(shutdown_measurement_harness.CLEANUP_START_MARKER) == 1
    assert output.count(shutdown_measurement_harness.CLEANUP_COMPLETE_MARKER) == 1
    stage_markers = [line for line in output if line.startswith("SYNTHETIC_STAGE_COMPLETED=")]
    assert len(stage_markers) == 10
    assert all(stage_markers.count(marker) == 1 for marker in stage_markers)
    assert stderr == ""
    _assert_child_resources_closed(child)


@pytest.mark.parametrize("state", ["pre_ready", "ready", "cleanup"])
@pytest.mark.parametrize("primary_kind", ["failure", "cancellation"])
def test_parent_interruption_preserves_primary_and_reaps_child(
    state: str, primary_kind: str
) -> None:
    scenario = (
        shutdown_measurement_harness.SyntheticChildScenario.READY_BEFORE_HANDLER
        if state == "pre_ready"
        else shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION
    )
    child = _launch(scenario)
    primary: BaseException = (
        RuntimeError("fixed parent interruption")
        if primary_kind == "failure"
        else asyncio.CancelledError("fixed parent cancellation")
    )
    cleanup_classifications: list[str] = []
    with pytest.raises((RuntimeError, asyncio.CancelledError)) as raised:
        try:
            if state == "pre_ready":
                assert (
                    shutdown_process_harness.read_marker(child)
                    == shutdown_measurement_harness.PRE_READY_MARKER
                )
            else:
                assert (
                    shutdown_process_harness.read_marker(child)
                    == shutdown_measurement_harness.READY_MARKER
                )
                if state == "cleanup":
                    shutdown_process_harness.send_signal(child, ChildSignal.INTERRUPT)
                    assert shutdown_process_harness.read_marker(child) == "SYNTHETIC_SIGNAL=SIGINT"
                    assert (
                        shutdown_process_harness.read_marker(child)
                        == shutdown_measurement_harness.CLEANUP_TASK_CREATED_MARKER
                    )
                    assert (
                        shutdown_process_harness.read_marker(child)
                        == shutdown_measurement_harness.CLEANUP_START_MARKER
                    )
            raise primary
        finally:
            _cleanup_and_reraise_primary(child, primary, cleanup_classifications)

    assert raised.value is primary
    assert cleanup_classifications == []
    _assert_child_resources_closed(child)


@pytest.mark.parametrize("primary_kind", ["failure", "cancellation"])
def test_parent_primary_identity_survives_fixed_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    primary_kind: str,
) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.EXIT_BEFORE_SIGNAL)
    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    primary: BaseException = (
        RuntimeError("fixed parent interruption")
        if primary_kind == "failure"
        else asyncio.CancelledError("fixed parent cancellation")
    )
    cleanup_classifications: list[str] = []
    real_stop_and_reap = shutdown_process_harness.stop_and_reap

    def cleanup_then_report_fixed_failure(
        handle: shutdown_process_harness.SyntheticChildHandle,
    ) -> None:
        real_stop_and_reap(handle)
        raise SyntheticProcessError("synthetic child cleanup failed")

    monkeypatch.setattr(
        shutdown_process_harness,
        "stop_and_reap",
        cleanup_then_report_fixed_failure,
    )
    with pytest.raises((RuntimeError, asyncio.CancelledError)) as raised:
        try:
            raise primary
        finally:
            _cleanup_and_reraise_primary(child, primary, cleanup_classifications)

    assert raised.value is primary
    assert cleanup_classifications == ["synthetic_child_cleanup_failed"]
    _assert_child_resources_closed(child)


@pytest.mark.parametrize(
    "snapshot",
    [
        shutdown_process_harness._TargetSnapshot(0, 2, 9, 8, 7, True, True),
        shutdown_process_harness._TargetSnapshot(-1, 2, 9, 8, 7, True, True),
        shutdown_process_harness._TargetSnapshot(1, 2, 9, 8, 7, True, True),
        shutdown_process_harness._TargetSnapshot(True, 2, 9, 8, 7, True, True),
        shutdown_process_harness._TargetSnapshot(3, 8, 9, 8, 7, True, True),
        shutdown_process_harness._TargetSnapshot(3, 7, 9, 8, 7, True, True),
        shutdown_process_harness._TargetSnapshot(3, 4, 9, 8, 7, True, True),
        shutdown_process_harness._TargetSnapshot(3, 3, 9, 8, 7, False, True),
        shutdown_process_harness._TargetSnapshot(3, 3, 9, 8, 7, True, False),
    ],
)
def test_dangerous_process_group_targets_are_rejected_without_signal(
    snapshot: shutdown_process_harness._TargetSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killpg = MagicMock()
    monkeypatch.setattr(os, "killpg", killpg)

    with pytest.raises(SyntheticProcessError, match="invalid synthetic child target"):
        shutdown_process_harness._validate_target_snapshot(snapshot)
    killpg.assert_not_called()


def test_process_group_snapshot_accepts_only_isolated_child_identity() -> None:
    snapshot = shutdown_process_harness._TargetSnapshot(3, 3, 9, 8, 7, True, True)
    assert shutdown_process_harness._validate_target_snapshot(snapshot) == 3


def test_unrelated_process_object_is_rejected_without_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killpg = MagicMock()
    monkeypatch.setattr(os, "killpg", killpg)
    with pytest.raises(SyntheticProcessError, match="invalid synthetic child handle"):
        shutdown_process_harness.send_signal(object(), ChildSignal.INTERRUPT)  # type: ignore[arg-type]
    killpg.assert_not_called()


def test_process_lookup_race_still_waits_kills_and_reaps(monkeypatch: pytest.MonkeyPatch) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    real_killpg = os.killpg
    calls: list[int] = []

    def raced_killpg(pgid: int, signum: int) -> None:
        calls.append(signum)
        if signum == signal.SIGTERM:
            raise ProcessLookupError
        real_killpg(pgid, signum)

    try:
        assert (
            shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
        )
        monkeypatch.setattr(os, "killpg", raced_killpg)
        shutdown_process_harness.stop_and_reap(child)
    finally:
        monkeypatch.setattr(os, "killpg", real_killpg)
        if not child.reaped:
            shutdown_process_harness.stop_and_reap(child)

    assert calls == [signal.SIGTERM, signal.SIGKILL]
    _assert_child_resources_closed(child)


def test_exit_between_poll_and_group_lookup_sends_no_signal_and_is_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    real_getpgid = os.getpgid
    real_killpg = os.killpg
    killpg = MagicMock()

    def raced_getpgid(pid: int) -> int:
        if pid == child.process.pid:
            raise ProcessLookupError
        return real_getpgid(pid)

    try:
        assert (
            shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
        )
        monkeypatch.setattr(os, "getpgid", raced_getpgid)
        monkeypatch.setattr(os, "killpg", killpg)
        assert shutdown_process_harness.send_signal(child, ChildSignal.INTERRUPT) is False
        killpg.assert_not_called()
    finally:
        monkeypatch.setattr(os, "getpgid", real_getpgid)
        monkeypatch.setattr(os, "killpg", real_killpg)
        shutdown_process_harness.stop_and_reap(child)

    _assert_child_resources_closed(child)


def test_terminated_child_is_waited_without_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.EXIT_BEFORE_SIGNAL)
    calls: list[int] = []
    real_killpg = os.killpg

    def tracked_killpg(pgid: int, signum: int) -> None:
        calls.append(signum)
        real_killpg(pgid, signum)

    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    child.process.wait(timeout=1)
    monkeypatch.setattr(os, "killpg", tracked_killpg)
    shutdown_process_harness.stop_and_reap(child)
    assert calls == []
    _assert_child_resources_closed(child)


def test_terminate_success_does_not_escalate_to_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    real_killpg = os.killpg
    calls: list[int] = []

    def tracked_killpg(pgid: int, signum: int) -> None:
        calls.append(signum)
        real_killpg(pgid, signum)

    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    monkeypatch.setattr(os, "killpg", tracked_killpg)
    shutdown_process_harness.stop_and_reap(child)

    assert calls == [signal.SIGTERM]
    _assert_child_resources_closed(child)


def test_wait_failure_still_executes_final_wait_and_reaps(monkeypatch: pytest.MonkeyPatch) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.EXIT_BEFORE_SIGNAL)
    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    real_wait = child.process.wait
    calls = 0

    def fail_once_wait(timeout: float | None = None) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("sensitive wait detail")
        return real_wait(timeout=timeout)

    monkeypatch.setattr(child.process, "wait", fail_once_wait)
    with pytest.raises(SyntheticProcessError, match="synthetic child wait failed") as caught:
        shutdown_process_harness.stop_and_reap(child)
    assert "sensitive wait detail" not in str(caught.value)
    _assert_fixed_exception_is_non_reflecting(caught.value, "sensitive wait detail")
    assert calls >= 2
    _assert_child_resources_closed(child)


def test_target_validation_error_suppresses_raw_cause_and_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    real_getpgid = os.getpgid
    canary = "SENSITIVE_VALIDATION_CANARY"

    def fail_child_group_lookup(pid: int) -> int:
        if pid == child.process.pid:
            raise OSError(canary)
        return real_getpgid(pid)

    try:
        monkeypatch.setattr(os, "getpgid", fail_child_group_lookup)
        with pytest.raises(SyntheticProcessError, match="invalid synthetic child target") as caught:
            shutdown_process_harness.send_signal(child, ChildSignal.INTERRUPT)
        _assert_fixed_exception_is_non_reflecting(caught.value, canary)
    finally:
        monkeypatch.setattr(os, "getpgid", real_getpgid)
        shutdown_process_harness.stop_and_reap(child)

    _assert_child_resources_closed(child)


def test_signal_error_suppresses_raw_cause_and_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    real_killpg = os.killpg
    canary = "SENSITIVE_SIGNAL_CANARY"

    def fail_signal(pgid: int, signum: int) -> None:
        del pgid, signum
        raise PermissionError(canary)

    try:
        monkeypatch.setattr(os, "killpg", fail_signal)
        with pytest.raises(SyntheticProcessError, match="synthetic child signal failed") as caught:
            shutdown_process_harness.send_signal(child, ChildSignal.INTERRUPT)
        _assert_fixed_exception_is_non_reflecting(caught.value, canary)
    finally:
        monkeypatch.setattr(os, "killpg", real_killpg)
        shutdown_process_harness.stop_and_reap(child)

    _assert_child_resources_closed(child)


def test_output_error_suppresses_raw_cause_and_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    real_communicate = child.process.communicate
    canary = "SENSITIVE_OUTPUT_CANARY"

    def fail_output(*, timeout: float) -> tuple[bytes, bytes]:
        raise subprocess.TimeoutExpired(canary, timeout)

    try:
        monkeypatch.setattr(child.process, "communicate", fail_output)
        with pytest.raises(SyntheticProcessError, match="output wait timed out") as caught:
            shutdown_process_harness.collect_output(child)
        _assert_fixed_exception_is_non_reflecting(caught.value, canary)
    finally:
        monkeypatch.setattr(child.process, "communicate", real_communicate)
        shutdown_process_harness.stop_and_reap(child)

    _assert_child_resources_closed(child)


def test_marker_select_error_is_fixed_and_non_reflecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    real_select = select.select
    canary = "SENSITIVE_SELECT_CANARY"

    def fail_select(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError(canary)

    try:
        monkeypatch.setattr(select, "select", fail_select)
        with pytest.raises(
            SyntheticProcessError, match="synthetic child marker read failed"
        ) as caught:
            shutdown_process_harness.read_marker(child)
        _assert_fixed_exception_is_non_reflecting(caught.value, canary)
    finally:
        monkeypatch.setattr(select, "select", real_select)
        shutdown_process_harness.stop_and_reap(child)

    _assert_child_resources_closed(child)


def test_marker_readline_error_is_fixed_and_non_reflecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    proxy = _MarkerPipeProxy(child.process.stdout, payload=b"", read_fails=True)
    child.process.stdout = proxy  # type: ignore[assignment]
    canary = "SENSITIVE_READLINE_CANARY"
    monkeypatch.setattr(select, "select", lambda *args, **kwargs: ((proxy,), (), ()))

    try:
        with pytest.raises(
            SyntheticProcessError, match="synthetic child marker read failed"
        ) as caught:
            shutdown_process_harness.read_marker(child)
        _assert_fixed_exception_is_non_reflecting(caught.value, canary)
    finally:
        shutdown_process_harness.stop_and_reap(child)

    _assert_child_resources_closed(child)


def test_marker_invalid_utf8_is_fixed_and_non_reflecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    canary = "SENSITIVE_MARKER_BYTES"
    proxy = _MarkerPipeProxy(child.process.stdout, payload=canary.encode() + b"\xff")
    child.process.stdout = proxy  # type: ignore[assignment]
    monkeypatch.setattr(select, "select", lambda *args, **kwargs: ((proxy,), (), ()))

    try:
        with pytest.raises(
            SyntheticProcessError, match="synthetic child marker decode failed"
        ) as caught:
            shutdown_process_harness.read_marker(child)
        _assert_fixed_exception_is_non_reflecting(caught.value, canary)
    finally:
        shutdown_process_harness.stop_and_reap(child)

    _assert_child_resources_closed(child)


def test_output_communicate_error_is_fixed_and_non_reflecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    real_communicate = child.process.communicate
    canary = "SENSITIVE_COMMUNICATE_CANARY"

    def fail_output(*, timeout: float) -> tuple[bytes, bytes]:
        del timeout
        raise OSError(canary)

    try:
        monkeypatch.setattr(child.process, "communicate", fail_output)
        with pytest.raises(
            SyntheticProcessError, match="synthetic child output read failed"
        ) as caught:
            shutdown_process_harness.collect_output(child)
        _assert_fixed_exception_is_non_reflecting(caught.value, canary)
    finally:
        monkeypatch.setattr(child.process, "communicate", real_communicate)
        shutdown_process_harness.stop_and_reap(child)

    _assert_child_resources_closed(child)


@pytest.mark.parametrize("invalid_stream", ["stdout", "stderr"])
def test_output_invalid_utf8_is_fixed_and_non_reflecting(
    monkeypatch: pytest.MonkeyPatch,
    invalid_stream: str,
) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    real_communicate = child.process.communicate
    canary = f"SENSITIVE_{invalid_stream.upper()}_BYTES"
    invalid_bytes = canary.encode() + b"\xff"
    output = (invalid_bytes, b"") if invalid_stream == "stdout" else (b"", invalid_bytes)

    def invalid_output(*, timeout: float) -> tuple[bytes, bytes]:
        del timeout
        return output

    try:
        monkeypatch.setattr(child.process, "communicate", invalid_output)
        with pytest.raises(
            SyntheticProcessError, match="synthetic child output decode failed"
        ) as caught:
            shutdown_process_harness.collect_output(child)
        _assert_fixed_exception_is_non_reflecting(caught.value, canary)
    finally:
        monkeypatch.setattr(child.process, "communicate", real_communicate)
        shutdown_process_harness.stop_and_reap(child)

    _assert_child_resources_closed(child)


@pytest.mark.parametrize("operation", ["marker", "output"])
def test_output_boundaries_do_not_reclassify_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    cancellation = asyncio.CancelledError("fixed output cancellation")
    real_select = select.select
    real_communicate = child.process.communicate

    def cancel_operation(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise cancellation

    try:
        if operation == "marker":
            monkeypatch.setattr(select, "select", cancel_operation)
            with pytest.raises(asyncio.CancelledError) as caught:
                shutdown_process_harness.read_marker(child)
        else:
            monkeypatch.setattr(child.process, "communicate", cancel_operation)
            with pytest.raises(asyncio.CancelledError) as caught:
                shutdown_process_harness.collect_output(child)
        assert caught.value is cancellation
    finally:
        monkeypatch.setattr(select, "select", real_select)
        monkeypatch.setattr(child.process, "communicate", real_communicate)
        shutdown_process_harness.stop_and_reap(child)

    _assert_child_resources_closed(child)


@pytest.mark.parametrize("primary_kind", ["failure", "cancellation"])
def test_output_failure_does_not_override_existing_primary(
    monkeypatch: pytest.MonkeyPatch,
    primary_kind: str,
) -> None:
    child = _launch(shutdown_measurement_harness.SyntheticChildScenario.CONTROLLED_NON_COMPLETION)
    assert shutdown_process_harness.read_marker(child) == shutdown_measurement_harness.READY_MARKER
    primary: BaseException = (
        RuntimeError("fixed primary failure")
        if primary_kind == "failure"
        else asyncio.CancelledError("fixed primary cancellation")
    )
    real_communicate = child.process.communicate
    classifications: list[str] = []

    def fail_output(*, timeout: float) -> tuple[bytes, bytes]:
        del timeout
        raise OSError("SENSITIVE_OUTPUT_CLEANUP_CANARY")

    monkeypatch.setattr(child.process, "communicate", fail_output)
    with pytest.raises((RuntimeError, asyncio.CancelledError)) as caught:
        try:
            raise primary
        finally:
            try:
                shutdown_process_harness.collect_output(child)
            except SyntheticProcessError:
                classifications.append("synthetic_child_output_failed")
            finally:
                monkeypatch.setattr(child.process, "communicate", real_communicate)
                shutdown_process_harness.stop_and_reap(child)
            raise primary from None

    assert caught.value is primary
    assert classifications == ["synthetic_child_output_failed"]
    _assert_child_resources_closed(child)


def test_pipe_close_failure_does_not_skip_the_other_pipe() -> None:
    class FakePipe:
        def __init__(self, *, fails: bool) -> None:
            self.closed = False
            self.close_count = 0
            self._fails = fails

        def close(self) -> None:
            self.close_count += 1
            if self._fails:
                raise OSError("SENSITIVE_PIPE_CANARY")
            self.closed = True

    first = FakePipe(fails=True)
    second = FakePipe(fails=False)
    process = MagicMock(stdout=first, stderr=second)

    assert shutdown_process_harness._close_pipes(process) is False
    assert first.close_count == 1
    assert second.close_count == 1
    assert second.closed is True


def test_synthetic_child_has_fixed_modes_and_fails_closed_capability_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = shutdown_measurement_harness._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--synthetic-child", "--scenario", "unknown"])

    assert shutdown_measurement_harness._SyntheticAuditGuard._BLOCKED_EVENTS == {
        "socket.bind",
        "socket.connect",
        "socket.connect_ex",
        "socket.sendmsg",
        "socket.sendto",
        "socket.getaddrinfo",
        "socket.getnameinfo",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.gethostbyname_ex",
        "subprocess.Popen",
        "os.system",
        "os.posix_spawn",
        "os.spawn",
    }
    audit_guard = shutdown_measurement_harness._SyntheticAuditGuard()
    for event in shutdown_measurement_harness._SyntheticAuditGuard._BLOCKED_EVENTS:
        canary = f"SENSITIVE_AUDIT_CANARY_{event}"
        with pytest.raises(RuntimeError, match="synthetic child capability blocked") as caught:
            audit_guard(event, (canary,))
        rendered = "".join(traceback.format_exception(caught.value))
        assert caught.value.__cause__ is None
        assert canary not in str(caught.value)
        assert canary not in rendered

    registered: list[object] = []

    def suppress_child(coroutine: object) -> int:
        coroutine.close()  # type: ignore[union-attr]
        return 0

    monkeypatch.setattr(shutdown_measurement_harness.sys, "addaudithook", registered.append)
    monkeypatch.setattr(shutdown_measurement_harness.asyncio, "run", suppress_child)
    assert (
        shutdown_measurement_harness.main(("--synthetic-child", "--scenario", "immediate-success"))
        == 0
    )
    assert len(registered) == 1
    assert type(registered[0]) is shutdown_measurement_harness._SyntheticAuditGuard

    registered.clear()
    with pytest.raises(SystemExit, match="synthetic child mode"):
        shutdown_measurement_harness.main(())
    assert registered == []


def test_synthetic_child_source_has_no_resource_or_escape_imports() -> None:
    path = Path(shutdown_measurement_harness.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported_roots.isdisjoint(
        {"discord", "openai", "httpx", "socket", "subprocess", "sqlalchemy", "psycopg"}
    )


def test_shutdown_docs_have_selected_option_a_without_stale_current_state() -> None:
    documents = (
        "docs/manual-acceptance-ai-post-drafting.md",
        "docs/operations.md",
        "docs/development-roadmap.md",
        "docs/requirements-beta.md",
        "docs/technical-design-beta.md",
    )
    text = "\n".join((REPOSITORY_ROOT / path).read_text(encoding="utf-8") for path in documents)
    forbidden_current_state = (
        "process終了期限／subprocess隔離は将来判断",
        "process終了期限またはsubprocess隔離は将来の運用判断",
        "process終了期限／subprocess isolationは将来の運用判断",
        "`PROCESS_TERMINATION_DEADLINE`とsubprocess isolationは将来のsupervisor運用判断",
        "systemd未選択",
    )

    assert all(stale not in text for stale in forbidden_current_state)
    assert "process終了方式はOption A" in text
    assert "Option Bのsubprocess isolationは未採用" in text
    assert "CLIENT_SHUTDOWN_STRATEGY_APPROVED=false" in text
    assert "確認済み55件／未確認17件（合計72件）" in text
