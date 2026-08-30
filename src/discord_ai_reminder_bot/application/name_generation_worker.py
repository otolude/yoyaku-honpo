"""Provider-independent name generation coordination with no DB-held I/O."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.name_generation import (
    ClaimedNameGeneration,
    NameGenerationClaimService,
    NameGenerationResultService,
    NameGenerator,
    NameGeneratorError,
    NameGeneratorInvalidResponseError,
    NameGeneratorUnavailableError,
)
from discord_ai_reminder_bot.domain.clock import Clock
from discord_ai_reminder_bot.domain.enums import NameGenerationResultCode
from discord_ai_reminder_bot.domain.name_generation import (
    BudgetPolicy,
    GeneratedScheduleName,
    NameGenerationRequest,
)
from discord_ai_reminder_bot.infrastructure.database.name_generation_repositories import (
    NameGenerationJobRepository,
)


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    result_code: NameGenerationResultCode
    generated: GeneratedScheduleName | None = None


@dataclass(frozen=True, slots=True)
class NameGenerationPollResult:
    selected: int = 0
    generated: int = 0
    failed: int = 0
    internal_errors: int = 0
    result_code: str | None = None


async def generate_without_db(
    *, generator: NameGenerator, request: NameGenerationRequest, timeout_seconds: int
) -> GenerationOutcome:
    """Call only the Generator with immutable input; never retry or log payloads."""
    try:
        async with asyncio.timeout(timeout_seconds):
            generated = await generator.generate(request)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return GenerationOutcome(NameGenerationResultCode.TIMEOUT)
    except NameGeneratorInvalidResponseError:
        return GenerationOutcome(NameGenerationResultCode.INVALID_RESPONSE)
    except NameGeneratorUnavailableError:
        return GenerationOutcome(NameGenerationResultCode.GENERATOR_UNAVAILABLE)
    except NameGeneratorError:
        return GenerationOutcome(NameGenerationResultCode.GENERATOR_ERROR)
    except Exception:  # noqa: BLE001 - provider details must not cross this boundary
        return GenerationOutcome(NameGenerationResultCode.GENERATOR_ERROR)
    if not isinstance(generated, GeneratedScheduleName):
        return GenerationOutcome(NameGenerationResultCode.INVALID_RESPONSE)
    try:
        validated = GeneratedScheduleName(generated.value)
    except TypeError, ValueError:
        return GenerationOutcome(NameGenerationResultCode.INVALID_RESPONSE)
    return GenerationOutcome(NameGenerationResultCode.GENERATED, validated)


class NameGenerationWorker:
    """Claim one Job, close DB resources, generate once, then finalize briefly."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        generator: NameGenerator,
        clock: Clock,
        enabled: bool,
        budget_policy: BudgetPolicy,
        timeout_seconds: int,
        processing_lease_seconds: int,
        logger: logging.Logger | None = None,
    ) -> None:
        self._sessions = session_factory
        self._generator = generator
        self._clock = clock
        self._enabled = enabled
        self._budget_policy = budget_policy
        self._timeout_seconds = timeout_seconds
        self._processing_lease_seconds = processing_lease_seconds
        self._logger = logger
        self._semaphore = asyncio.Semaphore(1)
        self._active_task: asyncio.Task[GenerationOutcome] | None = None
        self._active_job_id: int | None = None
        self._shutdown_lock = asyncio.Lock()
        self._closed = False

    @property
    def available(self) -> bool:
        return self._enabled and self._generator.available

    async def poll_once(self) -> NameGenerationPollResult:
        if not self.available or self._closed:
            return NameGenerationPollResult()
        async with self._semaphore:
            claimed = await self._claim()
            if claimed is None:
                return NameGenerationPollResult()
            self._active_job_id = claimed.job_id
            task = asyncio.create_task(
                generate_without_db(
                    generator=self._generator,
                    request=claimed.request,
                    timeout_seconds=self._timeout_seconds,
                ),
                name="schedule-name-generator",
            )
            self._active_task = task
            try:
                outcome = await task
                finalized_code = await self._finalize(claimed.job_id, outcome)
                self._active_job_id = None
                return NameGenerationPollResult(
                    selected=1,
                    generated=int(finalized_code is NameGenerationResultCode.GENERATED),
                    failed=int(finalized_code is not NameGenerationResultCode.GENERATED),
                    result_code=finalized_code.value,
                )
            finally:
                if task.done():
                    self._active_task = None

    async def shutdown(self) -> None:
        async with self._shutdown_lock:
            if self._closed:
                return
            self._closed = True
            task = self._active_task
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            job_id = self._active_job_id
            if job_id is not None:
                try:
                    async with self._sessions() as session, session.begin():
                        await NameGenerationResultService(session).finalize_failure(
                            job_id=job_id,
                            result_code=NameGenerationResultCode.SHUTDOWN_UNKNOWN,
                            finished_at=self._clock.now(),
                        )
                except Exception:  # noqa: BLE001 - leave processing for startup recovery
                    if self._logger is not None:
                        self._logger.error("name_generation_shutdown_finalize_failed")
            self._active_job_id = None
            self._active_task = None
            close = getattr(self._generator, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001 - provider details stay behind the boundary
                    if self._logger is not None:
                        self._logger.error("name_generation_generator_close_failed")

    async def _claim(self) -> ClaimedNameGeneration | None:
        async with self._sessions() as session, session.begin():
            return await NameGenerationClaimService(
                session,
                enabled=self._enabled,
                generator_available=self._generator.available,
                maximum_cost_microunits=self._generator.maximum_cost_microunits,
                budget_policy=self._budget_policy,
                processing_lease_seconds=self._processing_lease_seconds,
            ).claim_and_reserve(now=self._clock.now())

    async def _finalize(self, job_id: int, outcome: GenerationOutcome) -> NameGenerationResultCode:
        async with self._sessions() as session, session.begin():
            service = NameGenerationResultService(session)
            if outcome.result_code is NameGenerationResultCode.GENERATED:
                assert outcome.generated is not None
                await service.save_success(
                    job_id=job_id,
                    generated=outcome.generated,
                    finished_at=self._clock.now(),
                )
            else:
                await service.finalize_failure(
                    job_id=job_id,
                    result_code=outcome.result_code,
                    finished_at=self._clock.now(),
                )
            result_code = await NameGenerationJobRepository(session).result_code(job_id=job_id)
            return result_code or NameGenerationResultCode.GENERATOR_ERROR
