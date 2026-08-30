import asyncio

import pytest

from discord_ai_reminder_bot.application.name_generation_worker import (
    NameGeneratorError,
    generate_without_db,
)
from discord_ai_reminder_bot.domain.enums import NameGenerationResultCode
from discord_ai_reminder_bot.domain.name_generation import (
    GeneratedScheduleName,
    NameGenerationRequest,
)


class FakeGenerator:
    available = True
    maximum_cost_microunits = 10

    def __init__(self, result: object = GeneratedScheduleName("生成名")) -> None:
        self.result = result
        self.calls = 0
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def generate(self, request: NameGenerationRequest):
        self.calls += 1
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_generate_without_db_success_typed_error_and_invalid_response() -> None:
    request = NameGenerationRequest(content="private-content-canary")
    success = await generate_without_db(
        generator=FakeGenerator(), request=request, timeout_seconds=1
    )
    assert success.result_code is NameGenerationResultCode.GENERATED
    assert success.generated == GeneratedScheduleName("生成名")

    typed = await generate_without_db(
        generator=FakeGenerator(NameGeneratorError()), request=request, timeout_seconds=1
    )
    assert typed.result_code is NameGenerationResultCode.GENERATOR_ERROR
    invalid = await generate_without_db(
        generator=FakeGenerator("unvalidated"), request=request, timeout_seconds=1
    )
    assert invalid.result_code is NameGenerationResultCode.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_generate_without_db_timeout_has_no_retry() -> None:
    generator = FakeGenerator()
    generator.release = asyncio.Event()
    outcome = await generate_without_db(
        generator=generator,
        request=NameGenerationRequest(content="timeout-canary"),
        timeout_seconds=0.001,
    )
    assert outcome.result_code is NameGenerationResultCode.TIMEOUT
    assert generator.calls == 1


@pytest.mark.asyncio
async def test_generate_without_db_cancel_propagates_and_task_is_collected() -> None:
    generator = FakeGenerator()
    generator.release = asyncio.Event()
    task = asyncio.create_task(
        generate_without_db(
            generator=generator,
            request=NameGenerationRequest(content="cancel-canary"),
            timeout_seconds=5,
        )
    )
    await generator.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.done()
