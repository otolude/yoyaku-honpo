import asyncio
import inspect
import logging
import math

import pytest
from discord_ai_reminder_bot.application.post_draft_generation import (
    DisabledPostDraftGenerator,
    GeneratePostDraftService,
    PostDraftDisabledError,
    PostDraftErrorCode,
    PostDraftGenerator,
    PostDraftInvalidResponseError,
    PostDraftTimeoutError,
    PostDraftUnavailableError,
    PostDraftUnknownError,
)

from discord_ai_reminder_bot.domain.post_draft_generation import (
    GeneratedPostDraft,
    PostDraftGenerationRequest,
    PostLength,
    PostTone,
)

PURPOSE_CANARY = "application-purpose-private-canary"
KEY_POINTS_CANARY = "application-key-points-private-canary"
DRAFT_CANARY = "application-draft-private-canary"
ERROR_CANARY = "provider-error-private-canary"


def request() -> PostDraftGenerationRequest:
    return PostDraftGenerationRequest(
        purpose=PURPOSE_CANARY,
        key_points=KEY_POINTS_CANARY,
        tone=PostTone.POLITE,
        length=PostLength.STANDARD,
    )


class FakeGenerator:
    def __init__(self, result: object = None) -> None:
        self.result = GeneratedPostDraft("検証済みの下書き") if result is None else result
        self.calls = 0
        self.requests: list[PostDraftGenerationRequest] = []
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def generate(self, value: PostDraftGenerationRequest) -> GeneratedPostDraft:
        self.calls += 1
        self.requests.append(value)
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_disabled_generator_returns_fixed_typed_error_without_io() -> None:
    generator: PostDraftGenerator = DisabledPostDraftGenerator()
    with pytest.raises(PostDraftDisabledError) as captured:
        await generator.generate(request())
    assert captured.value.code is PostDraftErrorCode.DISABLED
    assert str(captured.value) == "disabled"


@pytest.mark.asyncio
async def test_service_returns_only_validated_generated_draft_and_calls_once() -> None:
    expected = GeneratedPostDraft("**お知らせ** 🎉\nhttps://example.invalid/info")
    generator = FakeGenerator(expected)
    result = await GeneratePostDraftService(generator=generator, timeout_seconds=1).generate(
        request()
    )
    assert result is expected
    assert isinstance(result, GeneratedPostDraft)
    assert generator.calls == 1
    assert generator.requests == [request()]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_type", "expected_code"),
    [
        (
            PostDraftUnavailableError(ERROR_CANARY),
            PostDraftUnavailableError,
            PostDraftErrorCode.UNAVAILABLE,
        ),
        (
            PostDraftInvalidResponseError(ERROR_CANARY),
            PostDraftInvalidResponseError,
            PostDraftErrorCode.INVALID_RESPONSE,
        ),
        (
            RuntimeError(ERROR_CANARY),
            PostDraftUnknownError,
            PostDraftErrorCode.UNKNOWN,
        ),
    ],
)
async def test_failure_is_fixed_and_generator_is_never_retried(
    failure: BaseException,
    expected_type: type[BaseException],
    expected_code: PostDraftErrorCode,
) -> None:
    generator = FakeGenerator(failure)
    service = GeneratePostDraftService(generator=generator, timeout_seconds=1)
    with pytest.raises(expected_type) as captured:
        await service.generate(request())
    assert captured.value.code is expected_code  # type: ignore[attr-defined]
    assert ERROR_CANARY not in str(captured.value)
    assert generator.calls == 1


@pytest.mark.asyncio
async def test_timeout_calls_generator_once_and_exposes_no_details() -> None:
    generator = FakeGenerator()
    generator.release = asyncio.Event()
    with pytest.raises(PostDraftTimeoutError) as captured:
        await GeneratePostDraftService(generator=generator, timeout_seconds=0.001).generate(
            request()
        )
    assert captured.value.code is PostDraftErrorCode.TIMEOUT
    assert str(captured.value) == "timeout"
    assert generator.calls == 1


@pytest.mark.asyncio
async def test_cancellation_is_propagated_without_retry() -> None:
    generator = FakeGenerator()
    generator.release = asyncio.Event()
    task = asyncio.create_task(
        GeneratePostDraftService(generator=generator, timeout_seconds=5).generate(request())
    )
    await generator.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert generator.calls == 1


@pytest.mark.asyncio
async def test_invalid_return_type_is_rejected_without_exposing_repr() -> None:
    class InvalidResult:
        def __repr__(self) -> str:
            return DRAFT_CANARY

    generator = FakeGenerator(InvalidResult())
    with pytest.raises(PostDraftInvalidResponseError) as captured:
        await GeneratePostDraftService(generator=generator, timeout_seconds=1).generate(request())
    assert captured.value.code is PostDraftErrorCode.INVALID_RESPONSE
    assert DRAFT_CANARY not in str(captured.value)
    assert generator.calls == 1


@pytest.mark.asyncio
async def test_request_result_and_provider_details_are_absent_from_repr_errors_and_logs(
    caplog,
) -> None:
    generator = FakeGenerator(RuntimeError(ERROR_CANARY))
    service = GeneratePostDraftService(generator=generator, timeout_seconds=1)
    with pytest.raises(PostDraftUnknownError) as captured:
        await service.generate(request())
    logging.getLogger("post-draft-application-test").info("fixed_event")
    observed = " ".join(
        (
            repr(request()),
            repr(GeneratedPostDraft(DRAFT_CANARY)),
            repr(service),
            str(captured.value),
            caplog.text,
        )
    )
    for canary in (PURPOSE_CANARY, KEY_POINTS_CANARY, DRAFT_CANARY, ERROR_CANARY):
        assert canary not in observed


@pytest.mark.parametrize("timeout", [True, False, 0, -1, math.inf, -math.inf, math.nan])
def test_timeout_rejects_bool_nonpositive_and_nonfinite_values(timeout: object) -> None:
    with pytest.raises(ValueError):
        GeneratePostDraftService(generator=FakeGenerator(), timeout_seconds=timeout)  # type: ignore[arg-type]


def test_service_constructor_requires_only_generator_and_timeout() -> None:
    parameters = inspect.signature(GeneratePostDraftService).parameters
    assert tuple(parameters) == ("generator", "timeout_seconds")
    assert all(
        forbidden not in parameters
        for forbidden in (
            "schedule",
            "repository",
            "session",
            "database",
            "discord",
            "interaction",
            "openai",
            "budget",
            "logger",
        )
    )
