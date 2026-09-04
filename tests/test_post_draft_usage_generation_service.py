import ast
import asyncio
import importlib
import inspect
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from discord_ai_reminder_bot.application.post_draft_generation import (
    GeneratePostDraftService,
    PostDraftTimeoutError,
    PostDraftUnavailableError,
    PostDraftUnknownError,
)
from discord_ai_reminder_bot.application.post_draft_usage import PostDraftUsageReservation
from discord_ai_reminder_bot.domain.post_draft_generation import (
    GeneratedPostDraft,
    PostDraftGenerationRequest,
    PostLength,
    PostTone,
)
from discord_ai_reminder_bot.domain.post_draft_usage import (
    PostDraftGuildId,
    PostDraftOperationKey,
    PostDraftUsagePolicy,
    PostDraftUsageReservationCode,
    PostDraftUsageReservationResult,
    PostDraftUserId,
)

MODULE_NAME = "discord_ai_reminder_bot.application.post_draft_usage_generation"
MODULE_PATH = Path("src/discord_ai_reminder_bot/application/post_draft_usage_generation.py")
PURPOSE_CANARY = "orchestration-purpose-private-canary"
POINTS_CANARY = "orchestration-points-private-canary"
DRAFT_CANARY = "orchestration-draft-private-canary"
ERROR_CANARY = "orchestration-error-private-canary"
OPERATION_CANARY = UUID("3d09e6e7-879b-4019-9162-d67155537b24")
USER_CANARY = 8_123_456_789_012_345
GUILD_CANARY = 7_123_456_789_012_345
COST_CANARY = 456_789_012


def orchestration_module():
    return importlib.import_module(MODULE_NAME)


def service_type():
    module = orchestration_module()
    assert hasattr(module, "GeneratePostDraftWithUsageService")
    return module.GeneratePostDraftWithUsageService


def usage_error_type():
    module = orchestration_module()
    assert hasattr(module, "PostDraftUsageError")
    return module.PostDraftUsageError


def request() -> PostDraftGenerationRequest:
    return PostDraftGenerationRequest(
        purpose=PURPOSE_CANARY,
        key_points=POINTS_CANARY,
        tone=PostTone.POLITE,
        length=PostLength.STANDARD,
    )


def reservation() -> PostDraftUsageReservation:
    return PostDraftUsageReservation.create(
        operation_key=PostDraftOperationKey(OPERATION_CANARY),
        user_id=PostDraftUserId(USER_CANARY),
        guild_id=PostDraftGuildId(GUILD_CANARY),
        maximum_cost_microunits=COST_CANARY,
        now=datetime(2026, 9, 4, 2, 3, 4, tzinfo=UTC),
        policy=PostDraftUsagePolicy(),
    )


class FakeUsageRepository:
    def __init__(
        self,
        result: object = PostDraftUsageReservationResult(PostDraftUsageReservationCode.RESERVED),
        *,
        events: list[str] | None = None,
    ) -> None:
        self.result = result
        self.events = events
        self.calls: list[PostDraftUsageReservation] = []

    async def reserve(self, value: PostDraftUsageReservation) -> PostDraftUsageReservationResult:
        self.calls.append(value)
        if self.events is not None:
            self.events.append("reserve")
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result  # type: ignore[return-value]


class FakeGenerator:
    def __init__(
        self,
        result: object = GeneratedPostDraft(DRAFT_CANARY),
        *,
        events: list[str] | None = None,
    ) -> None:
        self.result = result
        self.events = events
        self.calls: list[PostDraftGenerationRequest] = []
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.active = 0
        self.maximum_active = 0

    async def generate(self, value: PostDraftGenerationRequest) -> GeneratedPostDraft:
        self.calls.append(value)
        if self.events is not None:
            self.events.append("generate")
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.started.set()
        try:
            if self.release is not None:
                await self.release.wait()
            if isinstance(self.result, BaseException):
                raise self.result
            return self.result  # type: ignore[return-value]
        finally:
            self.active -= 1


def service(
    repository: FakeUsageRepository,
    generator: FakeGenerator,
    *,
    enabled: bool = True,
    timeout: float = 1,
):
    return service_type()(
        usage_repository=repository,
        generation_service=GeneratePostDraftService(
            generator=generator,
            timeout_seconds=timeout,
        ),
        enabled=enabled,
    )


@pytest.mark.asyncio
async def test_disabled_calls_neither_repository_nor_generator() -> None:
    repository = FakeUsageRepository()
    generator = FakeGenerator()
    with pytest.raises(
        __import__(
            "discord_ai_reminder_bot.application.post_draft_generation",
            fromlist=["PostDraftDisabledError"],
        ).PostDraftDisabledError
    ):
        await service(repository, generator, enabled=False).generate(request(), reservation())
    assert repository.calls == []
    assert generator.calls == []


@pytest.mark.asyncio
async def test_reserved_calls_repository_then_generator_once() -> None:
    events: list[str] = []
    repository = FakeUsageRepository(events=events)
    expected = GeneratedPostDraft("検証済み本文")
    generator = FakeGenerator(expected, events=events)
    result = await service(repository, generator).generate(request(), reservation())
    assert result is expected
    assert repository.calls == [reservation()]
    assert generator.calls == [request()]
    assert events == ["reserve", "generate"]


@pytest.mark.parametrize(
    "code",
    [
        PostDraftUsageReservationCode.ALREADY_RESERVED,
        PostDraftUsageReservationCode.USER_RATE_LIMITED,
        PostDraftUsageReservationCode.GUILD_RATE_LIMITED,
        PostDraftUsageReservationCode.GLOBAL_DAILY_EXHAUSTED,
        PostDraftUsageReservationCode.GLOBAL_MONTHLY_EXHAUSTED,
        PostDraftUsageReservationCode.GLOBAL_COST_EXHAUSTED,
        PostDraftUsageReservationCode.PRICE_UNKNOWN,
        PostDraftUsageReservationCode.INVALID_POLICY,
        PostDraftUsageReservationCode.USAGE_UNAVAILABLE,
    ],
)
@pytest.mark.asyncio
async def test_non_reserved_code_raises_fixed_usage_error_without_generation(
    code: PostDraftUsageReservationCode,
) -> None:
    repository = FakeUsageRepository(PostDraftUsageReservationResult(code))
    generator = FakeGenerator()
    with pytest.raises(usage_error_type()) as captured:
        await service(repository, generator).generate(request(), reservation())
    assert captured.value.usage_code is code
    assert str(captured.value) == code.value
    assert len(repository.calls) == 1
    assert generator.calls == []


@pytest.mark.asyncio
async def test_unexpected_usage_result_is_fixed_unavailable_without_generation() -> None:
    repository = FakeUsageRepository(object())
    generator = FakeGenerator()
    with pytest.raises(usage_error_type()) as captured:
        await service(repository, generator).generate(request(), reservation())
    assert captured.value.usage_code is PostDraftUsageReservationCode.USAGE_UNAVAILABLE
    assert str(captured.value) == "usage_unavailable"
    assert generator.calls == []


@pytest.mark.asyncio
async def test_repository_database_error_becomes_fixed_usage_unavailable() -> None:
    repository = FakeUsageRepository(RuntimeError(ERROR_CANARY))
    generator = FakeGenerator()
    with pytest.raises(usage_error_type()) as captured:
        await service(repository, generator).generate(request(), reservation())
    assert captured.value.usage_code is PostDraftUsageReservationCode.USAGE_UNAVAILABLE
    assert str(captured.value) == "usage_unavailable"
    assert ERROR_CANARY not in str(captured.value)
    assert len(repository.calls) == 1
    assert generator.calls == []


@pytest.mark.asyncio
async def test_repository_cancellation_is_propagated_without_generation() -> None:
    repository = FakeUsageRepository(asyncio.CancelledError())
    generator = FakeGenerator()
    with pytest.raises(asyncio.CancelledError):
        await service(repository, generator).generate(request(), reservation())
    assert len(repository.calls) == 1
    assert generator.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (PostDraftUnavailableError(ERROR_CANARY), PostDraftUnavailableError),
        (RuntimeError(ERROR_CANARY), PostDraftUnknownError),
    ],
)
async def test_generator_failure_keeps_existing_fixed_classification_without_refund(
    failure: BaseException,
    expected: type[BaseException],
) -> None:
    repository = FakeUsageRepository()
    generator = FakeGenerator(failure)
    with pytest.raises(expected) as captured:
        await service(repository, generator).generate(request(), reservation())
    assert ERROR_CANARY not in str(captured.value)
    assert len(repository.calls) == 1
    assert len(generator.calls) == 1


@pytest.mark.asyncio
async def test_generator_cancellation_propagates_without_refund_or_retry() -> None:
    repository = FakeUsageRepository()
    generator = FakeGenerator(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await service(repository, generator).generate(request(), reservation())
    assert len(repository.calls) == 1
    assert len(generator.calls) == 1


@pytest.mark.asyncio
async def test_timeout_does_not_retry_or_refund() -> None:
    repository = FakeUsageRepository()
    generator = FakeGenerator()
    generator.release = asyncio.Event()
    with pytest.raises(PostDraftTimeoutError):
        await service(repository, generator, timeout=0.001).generate(request(), reservation())
    assert len(repository.calls) == 1
    assert len(generator.calls) == 1


@pytest.mark.asyncio
async def test_two_operations_limit_generator_concurrency_to_one() -> None:
    repository = FakeUsageRepository()
    generator = FakeGenerator()
    generator.release = asyncio.Event()
    orchestrator = service(repository, generator)
    first = asyncio.create_task(orchestrator.generate(request(), reservation()))
    await generator.started.wait()
    second_reservation = PostDraftUsageReservation.create(
        operation_key=PostDraftOperationKey(uuid4()),
        user_id=PostDraftUserId(USER_CANARY),
        guild_id=PostDraftGuildId(GUILD_CANARY),
        maximum_cost_microunits=COST_CANARY,
        now=datetime(2026, 9, 4, 2, 3, 4, tzinfo=UTC),
        policy=PostDraftUsagePolicy(),
    )
    second = asyncio.create_task(orchestrator.generate(request(), second_reservation))
    await asyncio.sleep(0)
    assert len(repository.calls) == 1
    assert generator.maximum_active == 1
    generator.release.set()
    await asyncio.gather(first, second)
    assert len(repository.calls) == 2
    assert len(generator.calls) == 2
    assert generator.maximum_active == 1


@pytest.mark.asyncio
async def test_cancel_while_waiting_for_semaphore_never_calls_repository() -> None:
    repository = FakeUsageRepository()
    generator = FakeGenerator()
    generator.release = asyncio.Event()
    orchestrator = service(repository, generator)
    first = asyncio.create_task(orchestrator.generate(request(), reservation()))
    await generator.started.wait()
    waiting = asyncio.create_task(orchestrator.generate(request(), reservation()))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert len(repository.calls) == 1
    generator.release.set()
    await first


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [None, RuntimeError(ERROR_CANARY), asyncio.CancelledError()],
    ids=["success", "failure", "cancellation"],
)
async def test_semaphore_is_released_after_every_generator_outcome(
    failure: BaseException | None,
) -> None:
    repository = FakeUsageRepository()
    first_generator = FakeGenerator(GeneratedPostDraft("first") if failure is None else failure)
    orchestrator = service(repository, first_generator)
    if failure is None:
        await orchestrator.generate(request(), reservation())
    else:
        with pytest.raises(
            asyncio.CancelledError
            if isinstance(failure, asyncio.CancelledError)
            else PostDraftUnknownError
        ):
            await orchestrator.generate(request(), reservation())
    replacement = FakeGenerator(GeneratedPostDraft("second"))
    orchestrator._generation_service = GeneratePostDraftService(
        generator=replacement, timeout_seconds=1
    )
    await asyncio.wait_for(orchestrator.generate(request(), reservation()), timeout=1)
    assert len(replacement.calls) == 1


def test_constructor_and_public_method_are_application_only_boundaries() -> None:
    parameters = inspect.signature(service_type()).parameters
    assert tuple(parameters) == ("usage_repository", "generation_service", "enabled")
    method = service_type().generate
    assert inspect.iscoroutinefunction(method)
    assert tuple(inspect.signature(method).parameters) == ("self", "request", "reservation")


def test_application_module_has_no_infrastructure_or_external_imports() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any("infrastructure" in name or "schedule" in name for name in imported)
    assert not any(
        name == forbidden or name.startswith(f"{forbidden}.")
        for name in imported
        for forbidden in ("sqlalchemy", "openai", "discord")
    )
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "logging" not in source
    assert "retry" not in source.lower()
    assert "refund" not in source.lower()


def test_enabled_requires_an_actual_bool() -> None:
    repository = FakeUsageRepository()
    generator = FakeGenerator()
    for invalid in (0, 1, None, "true", object()):
        with pytest.raises(TypeError, match="^enabled must be a bool$"):
            service_type()(
                usage_repository=repository,
                generation_service=GeneratePostDraftService(generator=generator, timeout_seconds=1),
                enabled=invalid,
            )


@pytest.mark.asyncio
async def test_repr_errors_and_logs_expose_no_payload_or_identifier_canaries(caplog) -> None:
    repository = FakeUsageRepository(RuntimeError(ERROR_CANARY))
    generator = FakeGenerator()
    orchestrator = service(repository, generator)
    with pytest.raises(usage_error_type()) as captured:
        await orchestrator.generate(request(), reservation())
    logging.getLogger("post-draft-orchestration-test").info("fixed_event")
    observed = " ".join(
        (
            repr(orchestrator),
            repr(request()),
            repr(reservation()),
            str(captured.value),
            caplog.text,
        )
    )
    for canary in (
        PURPOSE_CANARY,
        POINTS_CANARY,
        DRAFT_CANARY,
        ERROR_CANARY,
        OPERATION_CANARY,
        USER_CANARY,
        GUILD_CANARY,
        COST_CANARY,
    ):
        assert str(canary) not in observed
