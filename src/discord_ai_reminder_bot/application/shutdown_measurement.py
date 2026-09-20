"""Pure, synthetic-only models for shutdown deadline measurements.

This module contains no resource adapters.  It deliberately cannot promote an
observation into an approved production value and does not select any timeout.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import final

MAX_MILLISECONDS = 9_223_372_036_854_775_807
_CANONICAL_MILLISECONDS = re.compile(r"0|[1-9][0-9]*")


class ShutdownMeasurementError(ValueError):
    """Fixed, non-reflecting shutdown measurement validation failure."""


def _milliseconds(value: object) -> int:
    if type(value) is int:
        parsed = value
    elif type(value) is str and _CANONICAL_MILLISECONDS.fullmatch(value):
        parsed = int(value)
    else:
        raise ShutdownMeasurementError("invalid shutdown measurement")
    if parsed < 0 or parsed > MAX_MILLISECONDS:
        raise ShutdownMeasurementError("invalid shutdown measurement")
    return parsed


class MeasurementEvidenceKind(StrEnum):
    CONFIGURED_HARD_BOUND = "configured_hard_bound"
    SYNTHETIC_LOGICAL_OBSERVATION = "synthetic_logical_observation"
    WSL_WALL_CLOCK_OBSERVATION = "wsl_wall_clock_observation"
    LINUX_REAL_HOST_OBSERVATION = "linux_real_host_observation"
    APPROVED_PRODUCTION_VALUE = "approved_production_value"


@dataclass(frozen=True, slots=True)
class ShutdownMeasurementValue:
    """A typed value; evidence kinds never convert implicitly."""

    milliseconds: int
    evidence_kind: MeasurementEvidenceKind

    def __post_init__(self) -> None:
        if type(self.evidence_kind) is not MeasurementEvidenceKind:
            raise ShutdownMeasurementError("invalid shutdown measurement evidence")
        object.__setattr__(self, "milliseconds", _milliseconds(self.milliseconds))


@dataclass(frozen=True, slots=True)
class ShutdownDeadlineInputs:
    active_provider_remaining_bound: ShutdownMeasurementValue
    provider_close_allowance: ShutdownMeasurementValue
    name_generation_loop_allowance: ShutdownMeasurementValue
    name_generation_worker_allowance: ShutdownMeasurementValue
    schedule_loop_allowance: ShutdownMeasurementValue
    maintenance_loop_allowance: ShutdownMeasurementValue
    notification_loop_allowance: ShutdownMeasurementValue
    startup_recovery_task_allowance: ShutdownMeasurementValue
    view_modal_allowance: ShutdownMeasurementValue
    discord_close_allowance: ShutdownMeasurementValue
    database_dispose_allowance: ShutdownMeasurementValue
    signal_scheduler_allowance: ShutdownMeasurementValue
    explicit_safety_margin: ShutdownMeasurementValue


@dataclass(frozen=True, slots=True)
class ShutdownDeadlineCalculation:
    total_milliseconds: int
    systemd_seconds_ceiling: int
    approved_production_value: bool = False


_DEADLINE_INPUT_FIELDS = (
    "active_provider_remaining_bound",
    "provider_close_allowance",
    "name_generation_loop_allowance",
    "name_generation_worker_allowance",
    "schedule_loop_allowance",
    "maintenance_loop_allowance",
    "notification_loop_allowance",
    "startup_recovery_task_allowance",
    "view_modal_allowance",
    "discord_close_allowance",
    "database_dispose_allowance",
    "signal_scheduler_allowance",
    "explicit_safety_margin",
)


def calculate_shutdown_deadline(
    inputs: ShutdownDeadlineInputs,
) -> ShutdownDeadlineCalculation:
    """Add the serial hard bounds and round systemd seconds upward.

    Only explicitly configured hard bounds are accepted.  Synthetic, WSL and
    Linux observations cannot be promoted by this function.
    """

    if type(inputs) is not ShutdownDeadlineInputs:
        raise ShutdownMeasurementError("invalid shutdown deadline inputs")
    total = 0
    for field_name in _DEADLINE_INPUT_FIELDS:
        value = getattr(inputs, field_name)
        if (
            type(value) is not ShutdownMeasurementValue
            or value.evidence_kind is not MeasurementEvidenceKind.CONFIGURED_HARD_BOUND
        ):
            raise ShutdownMeasurementError("shutdown hard bound is not configured")
        if total > MAX_MILLISECONDS - value.milliseconds:
            raise ShutdownMeasurementError("shutdown deadline overflow")
        total += value.milliseconds
    return ShutdownDeadlineCalculation(
        total_milliseconds=total,
        systemd_seconds_ceiling=(total + 999) // 1_000,
    )


class ShutdownStage(StrEnum):
    POST_DRAFT_PROVIDER_RUNTIME_OWNER = "post_draft_provider_shutdown_failed"
    NAME_GENERATION_POLLING_LOOP = "name_generation_loop_shutdown_failed"
    NAME_GENERATION_WORKER = "name_generation_worker_shutdown_failed"
    SCHEDULE_POLLING_LOOP = "polling_worker_shutdown_failed"
    MAINTENANCE_LOOP = "maintenance_worker_shutdown_failed"
    NOTIFICATION_POLLING_LOOP = "notification_worker_shutdown_failed"
    STARTUP_RECOVERY_TASK = "startup_task_shutdown_failed"
    CONFIRMATION_VIEW_MODAL = "confirmation_view_shutdown_failed"
    DISCORD_CLIENT = "discord_client_close_failed"
    DATABASE_ENGINE_DISPOSE = "database_engine_dispose_failed"


SHUTDOWN_STAGE_ORDER = (
    ShutdownStage.POST_DRAFT_PROVIDER_RUNTIME_OWNER,
    ShutdownStage.NAME_GENERATION_POLLING_LOOP,
    ShutdownStage.NAME_GENERATION_WORKER,
    ShutdownStage.SCHEDULE_POLLING_LOOP,
    ShutdownStage.MAINTENANCE_LOOP,
    ShutdownStage.NOTIFICATION_POLLING_LOOP,
    ShutdownStage.STARTUP_RECOVERY_TASK,
    ShutdownStage.CONFIRMATION_VIEW_MODAL,
    ShutdownStage.DISCORD_CLIENT,
    ShutdownStage.DATABASE_ENGINE_DISPOSE,
)


class SyntheticStageScenario(StrEnum):
    IMMEDIATE_SUCCESS = "immediate_success"
    CONTROLLED_COOPERATIVE_WAIT = "controlled_cooperative_wait"
    FIXED_FAILURE = "fixed_failure"
    FIXED_CANCELLATION = "fixed_cancellation"
    ALLOWANCE_EXCEEDED = "allowance_exceeded"
    COMPLETION_EXACTLY_AT_ALLOWANCE = "completion_exactly_at_allowance"
    COMPLETION_JUST_BELOW_ALLOWANCE = "completion_just_below_allowance"
    COMPLETION_JUST_ABOVE_ALLOWANCE = "completion_just_above_allowance"


class SyntheticStageClassification(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class SyntheticStageSpec:
    stage: ShutdownStage
    scenario: SyntheticStageScenario
    logical_duration_milliseconds: int
    allowance_milliseconds: int

    def __post_init__(self) -> None:
        if (
            type(self.stage) is not ShutdownStage
            or type(self.scenario) is not SyntheticStageScenario
        ):
            raise ShutdownMeasurementError("invalid synthetic shutdown stage")
        object.__setattr__(
            self,
            "logical_duration_milliseconds",
            _milliseconds(self.logical_duration_milliseconds),
        )
        object.__setattr__(
            self, "allowance_milliseconds", _milliseconds(self.allowance_milliseconds)
        )
        duration = self.logical_duration_milliseconds
        allowance = self.allowance_milliseconds
        if self.scenario is SyntheticStageScenario.IMMEDIATE_SUCCESS and duration != 0:
            raise ShutdownMeasurementError("invalid synthetic shutdown duration")
        if (
            self.scenario is SyntheticStageScenario.COMPLETION_EXACTLY_AT_ALLOWANCE
            and duration != allowance
        ):
            raise ShutdownMeasurementError("invalid synthetic shutdown boundary")
        if self.scenario is SyntheticStageScenario.COMPLETION_JUST_BELOW_ALLOWANCE and (
            allowance == 0 or duration != allowance - 1
        ):
            raise ShutdownMeasurementError("invalid synthetic shutdown boundary")
        if (
            self.scenario
            in {
                SyntheticStageScenario.ALLOWANCE_EXCEEDED,
                SyntheticStageScenario.COMPLETION_JUST_ABOVE_ALLOWANCE,
            }
            and duration <= allowance
        ):
            raise ShutdownMeasurementError("invalid synthetic shutdown boundary")


@dataclass(frozen=True, slots=True)
class SyntheticShutdownPlan:
    stages: tuple[SyntheticStageSpec, ...]

    def __post_init__(self) -> None:
        if type(self.stages) is not tuple or any(
            type(stage) is not SyntheticStageSpec for stage in self.stages
        ):
            raise ShutdownMeasurementError("invalid synthetic shutdown plan")
        if tuple(spec.stage for spec in self.stages) != SHUTDOWN_STAGE_ORDER:
            raise ShutdownMeasurementError("invalid synthetic shutdown stage order")


@dataclass(frozen=True, slots=True)
class SyntheticStageResult:
    stage: ShutdownStage
    classification: SyntheticStageClassification
    started_at_milliseconds: int
    finished_at_milliseconds: int


@final
class FakeMonotonicClock:
    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("synthetic clock subclassing is prohibited")

    def __init__(self) -> None:
        self._milliseconds = 0

    @property
    def milliseconds(self) -> int:
        return self._milliseconds

    def advance(self, milliseconds: int) -> None:
        amount = _milliseconds(milliseconds)
        if self._milliseconds > MAX_MILLISECONDS - amount:
            raise ShutdownMeasurementError("synthetic clock overflow")
        self._milliseconds += amount


@final
class SyntheticShutdownExecutor:
    """Run fixed synthetic stages in the production order without resources."""

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("synthetic shutdown executor subclassing is prohibited")

    def __init__(self, plan: SyntheticShutdownPlan) -> None:
        if type(plan) is not SyntheticShutdownPlan:
            raise TypeError("invalid synthetic shutdown plan")
        self._plan = plan
        self._clock = FakeMonotonicClock()
        self._controlled_gates = {
            spec.stage: asyncio.Event()
            for spec in plan.stages
            if spec.scenario is SyntheticStageScenario.CONTROLLED_COOPERATIVE_WAIT
        }
        self._started = {stage: asyncio.Event() for stage in SHUTDOWN_STAGE_ORDER}

    async def wait_until_started(self, stage: ShutdownStage) -> None:
        if type(stage) is not ShutdownStage:
            raise TypeError("invalid synthetic shutdown stage")
        await self._started[stage].wait()

    def release_controlled_stage(self, stage: ShutdownStage) -> None:
        if type(stage) is not ShutdownStage or stage not in self._controlled_gates:
            raise ShutdownMeasurementError("invalid controlled shutdown stage")
        self._controlled_gates[stage].set()

    async def run(self) -> tuple[SyntheticStageResult, ...]:
        results: list[SyntheticStageResult] = []
        for spec in self._plan.stages:
            started = self._clock.milliseconds
            self._started[spec.stage].set()
            if spec.scenario is SyntheticStageScenario.CONTROLLED_COOPERATIVE_WAIT:
                await self._controlled_gates[spec.stage].wait()
            self._clock.advance(spec.logical_duration_milliseconds)
            if spec.scenario is SyntheticStageScenario.FIXED_FAILURE:
                classification = SyntheticStageClassification.FAILED
            elif spec.scenario is SyntheticStageScenario.FIXED_CANCELLATION:
                classification = SyntheticStageClassification.CANCELLED
            elif spec.logical_duration_milliseconds > spec.allowance_milliseconds:
                classification = SyntheticStageClassification.TIMED_OUT
            else:
                classification = SyntheticStageClassification.COMPLETED
            results.append(
                SyntheticStageResult(
                    stage=spec.stage,
                    classification=classification,
                    started_at_milliseconds=started,
                    finished_at_milliseconds=self._clock.milliseconds,
                )
            )
        return tuple(results)


class ActiveProviderPoint(StrEnum):
    NONE = "none"
    INPUT_TOKEN_COUNT = "input_token_count"
    CREATE = "create"
    BUDGET_CHECK = "budget_check"
    BEFORE_CREATE = "before_create"


class ActiveProviderCompletion(StrEnum):
    COOPERATIVE = "cooperative"
    CANCELLED = "cancelled"


class ProviderShutdownClassification(StrEnum):
    NO_ACTIVE_OPERATION = "no_active_operation"
    ACTIVE_OPERATION_COMPLETED = "active_operation_completed"
    ACTIVE_OPERATION_TIMED_OUT = "active_operation_timed_out"
    ACTIVE_OPERATION_CANCELLED = "active_operation_cancelled"


@dataclass(frozen=True, slots=True)
class ProviderShutdownResult:
    classification: ProviderShutdownClassification
    close_start_count: int
    retry_count: int = 0
    blind_retry_count: int = 0
    refund_count: int = 0


@final
class SyntheticActiveProviderPolicy:
    """A resource-free state model for Option A provider shutdown."""

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("synthetic provider policy subclassing is prohibited")

    def __init__(
        self,
        *,
        pause_after_closing: bool = False,
        pause_before_close_body: bool = False,
    ) -> None:
        if type(pause_after_closing) is not bool or type(pause_before_close_body) is not bool:
            raise TypeError("invalid synthetic provider policy")
        self._active_point = ActiveProviderPoint.NONE
        self._active_completion = ActiveProviderCompletion.COOPERATIVE
        self._active_remaining_bound_milliseconds = 0
        self._active_logical_completion_milliseconds = 0
        self._closing = False
        self._closed = False
        self._close_task: asyncio.Task[ProviderShutdownResult] | None = None
        self._close_started = asyncio.Event()
        self._close_release = asyncio.Event()
        self._close_body_release = asyncio.Event()
        if not pause_after_closing:
            self._close_release.set()
        if not pause_before_close_body:
            self._close_body_release.set()
        self._close_start_count = 0

    @property
    def closing(self) -> bool:
        return self._closing

    def start_operation(
        self,
        point: ActiveProviderPoint,
        completion: ActiveProviderCompletion = ActiveProviderCompletion.COOPERATIVE,
        *,
        remaining_bound_milliseconds: int,
        logical_completion_milliseconds: int,
    ) -> None:
        if type(point) is not ActiveProviderPoint or point is ActiveProviderPoint.NONE:
            raise ShutdownMeasurementError("invalid synthetic provider operation")
        if type(completion) is not ActiveProviderCompletion:
            raise ShutdownMeasurementError("invalid synthetic provider completion")
        if self._closing or self._closed:
            raise RuntimeError("provider runtime is closing")
        if self._active_point is not ActiveProviderPoint.NONE:
            raise RuntimeError("provider operation already active")
        self._active_point = point
        self._active_completion = completion
        self._active_remaining_bound_milliseconds = _milliseconds(remaining_bound_milliseconds)
        self._active_logical_completion_milliseconds = _milliseconds(
            logical_completion_milliseconds
        )

    async def wait_until_closing(self) -> None:
        await self._close_started.wait()

    def release_close(self) -> None:
        self._close_release.set()

    def release_close_body(self) -> None:
        self._close_body_release.set()

    async def _run_close_after_gate(self) -> ProviderShutdownResult:
        await self._close_body_release.wait()
        return await self._run_close()

    async def _run_close(self) -> ProviderShutdownResult:
        self._close_start_count += 1
        await self._close_release.wait()
        if self._active_point is ActiveProviderPoint.NONE:
            classification = ProviderShutdownClassification.NO_ACTIVE_OPERATION
        elif self._active_completion is ActiveProviderCompletion.CANCELLED:
            classification = ProviderShutdownClassification.ACTIVE_OPERATION_CANCELLED
        elif (
            self._active_logical_completion_milliseconds > self._active_remaining_bound_milliseconds
        ):
            classification = ProviderShutdownClassification.ACTIVE_OPERATION_TIMED_OUT
        else:
            classification = ProviderShutdownClassification.ACTIVE_OPERATION_COMPLETED
        self._active_point = ActiveProviderPoint.NONE
        self._closed = True
        return ProviderShutdownResult(
            classification=classification,
            close_start_count=self._close_start_count,
        )

    async def close(self) -> ProviderShutdownResult:
        self._closing = True
        self._close_started.set()
        if self._close_task is None:
            close_coroutine = self._run_close_after_gate()
            try:
                self._close_task = asyncio.create_task(
                    close_coroutine, name="synthetic-provider-shutdown"
                )
            except BaseException:
                close_coroutine.close()
                raise
        cancellation: asyncio.CancelledError | None = None
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
        result = self._close_task.result()
        if cancellation is not None:
            raise cancellation
        return result
