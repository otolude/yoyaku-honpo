from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from discord_ai_reminder_bot.application.name_generation import (
    NameGeneratorError,
    NameGeneratorInvalidResponseError,
    NameGeneratorUnavailableError,
)
from discord_ai_reminder_bot.domain.name_generation import NameGenerationRequest
from discord_ai_reminder_bot.infrastructure.ai.openai_name_generator import (
    INSTRUCTIONS,
    LUNA_ALIAS,
    NANO_SNAPSHOT,
    OPENAI_MODEL_CATALOG,
    OpenAIErrorTypes,
    OpenAINameGenerator,
    OpenAINameGeneratorConfig,
    _initialize_sdk_platform_cache,
    create_openai_generator,
)


class TimeoutCanary(Exception):
    pass


class ConnectionCanary(Exception):
    pass


class StatusCanary(Exception):
    def __init__(self, status_code: int, private: str = "private-exception-canary") -> None:
        super().__init__(private)
        self.status_code = status_code


def test_sdk_platform_cache_initialization_is_instance_local_and_idempotent() -> None:
    clients = [SimpleNamespace(_platform=None) for _ in range(16)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda client: _initialize_sdk_platform_cache(client, "Linux"), clients))
    for client in clients:
        assert client._platform == "Linux"
        _initialize_sdk_platform_cache(client, "Linux")
        assert client._platform == "Linux"


def test_sdk_platform_cache_preserves_existing_valid_value() -> None:
    client = SimpleNamespace(_platform="MacOS")
    _initialize_sdk_platform_cache(client, "Linux")
    assert client._platform == "MacOS"


@pytest.mark.parametrize(
    "client",
    [SimpleNamespace(), SimpleNamespace(_platform=object()), SimpleNamespace(_platform="")],
)
def test_sdk_platform_cache_rejects_unknown_private_layout(client: object) -> None:
    with pytest.raises(RuntimeError, match="OpenAI SDK platform cache"):
        _initialize_sdk_platform_cache(client, "Linux")


def config(model: str = LUNA_ALIAS) -> OpenAINameGeneratorConfig:
    spec = OPENAI_MODEL_CATALOG[model]
    return OpenAINameGeneratorConfig(
        model=model,
        reasoning_effort="none",
        input_price_micro_usd_per_million_tokens=(spec.input_price_micro_usd_per_million_tokens),
        output_price_micro_usd_per_million_tokens=(spec.output_price_micro_usd_per_million_tokens),
        usd_jpy_rate_microunits=150_000_000,
        cost_safety_basis_points=12_500,
        max_input_characters=2_000,
        max_input_bytes=8_000,
        max_input_tokens=8_512,
        max_output_tokens=64,
    )


def response(name: str = "朝の確認") -> object:
    text = SimpleNamespace(type="output_text", text=json.dumps({"name": name}))
    return SimpleNamespace(output=[SimpleNamespace(type="message", content=[text])])


def generator(
    result: object | BaseException | None = None,
) -> tuple[OpenAINameGenerator, AsyncMock]:
    if result is None:
        result = response()
    create = AsyncMock(side_effect=result if isinstance(result, BaseException) else None)
    if not isinstance(result, BaseException):
        create.return_value = result
    client = SimpleNamespace(responses=SimpleNamespace(create=create), close=AsyncMock())
    adapter = OpenAINameGenerator(
        client=client,
        config=config(),
        error_types=OpenAIErrorTypes(
            timeout=(TimeoutCanary,),
            unavailable=(ConnectionCanary,),
            status=(StatusCanary,),
        ),
    )
    return adapter, create


@pytest.mark.asyncio
async def test_openai_adapter_sends_only_content_and_fixed_constraints() -> None:
    adapter, create = generator()
    request = NameGenerationRequest(content="private-content-canary")
    generated = await adapter.generate(request)

    assert generated.value == "朝の確認"
    create.assert_awaited_once()
    sent = create.await_args.kwargs
    assert sent == {
        "model": LUNA_ALIAS,
        "instructions": INSTRUCTIONS,
        "input": "private-content-canary",
        "reasoning": {"effort": "none"},
        "max_output_tokens": 64,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "schedule_name",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 32}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            }
        },
        "store": False,
    }
    serialized = repr(sent)
    for forbidden in ("guild_id", "user_id", "channel_id", "schedule_id", "version", "plan"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (TimeoutCanary(), TimeoutError),
        (ConnectionCanary(), NameGeneratorUnavailableError),
        (StatusCanary(429), NameGeneratorUnavailableError),
        (StatusCanary(503), NameGeneratorUnavailableError),
        (StatusCanary(401), NameGeneratorError),
        (StatusCanary(400), NameGeneratorError),
    ],
)
@pytest.mark.asyncio
async def test_openai_adapter_uses_typed_error_classification(
    result: BaseException, expected: type[BaseException]
) -> None:
    adapter, create = generator(result)
    with pytest.raises(expected):
        await adapter.generate(NameGenerationRequest(content="secret-body"))
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_openai_adapter_does_not_log_provider_or_payload_canaries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, _ = generator(StatusCanary(401, "provider-secret-exception"))
    with pytest.raises(NameGeneratorError):
        await adapter.generate(NameGenerationRequest(content="private-content-canary"))
    assert "provider-secret-exception" not in caplog.text
    assert "private-content-canary" not in caplog.text


@pytest.mark.parametrize(
    "provider_response",
    [
        SimpleNamespace(output=[]),
        SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(type="output_text", text="not-json")],
                )
            ]
        ),
        SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(type="output_text", text='{"name":""}')],
                )
            ]
        ),
        response("a\nb"),
        response("x" * 33),
        response("control\x00character"),
        response("zero\u200bwidth"),
        response("surrogate\ud800value"),
        SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(type="output_text", text='{"name":"a"}')],
                ),
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(type="output_text", text='{"name":"b"}')],
                ),
            ]
        ),
    ],
)
@pytest.mark.asyncio
async def test_openai_adapter_rejects_untrusted_responses(provider_response: object) -> None:
    adapter, _ = generator(provider_response)
    with pytest.raises(NameGeneratorInvalidResponseError):
        await adapter.generate(NameGenerationRequest(content="safe"))


@pytest.mark.asyncio
async def test_openai_adapter_rejects_character_byte_and_token_limits_without_call() -> None:
    adapter, create = generator()
    adapter._config = OpenAINameGeneratorConfig(
        model=LUNA_ALIAS,
        reasoning_effort="none",
        input_price_micro_usd_per_million_tokens=200_000,
        output_price_micro_usd_per_million_tokens=1_200_000,
        usd_jpy_rate_microunits=150_000_000,
        cost_safety_basis_points=12_500,
        max_input_characters=2,
        max_input_bytes=6,
        max_input_tokens=518,
        max_output_tokens=64,
    )
    with pytest.raises(NameGeneratorInvalidResponseError):
        await adapter.generate(NameGenerationRequest(content="あいう"))
    create.assert_not_awaited()


@pytest.mark.parametrize("model", [LUNA_ALIAS, NANO_SNAPSHOT])
def test_openai_model_catalog_accepts_only_audited_candidates(model: str) -> None:
    assert config(model).model == model


@pytest.mark.parametrize("model", ["", " gpt-5.6-luna", "gpt-5-nano", "gpt-5.4-nano", "unknown"])
def test_openai_model_catalog_rejects_deprecated_unpinned_and_unknown_models(model: str) -> None:
    with pytest.raises(ValueError):
        OpenAINameGeneratorConfig(
            model=model,
            reasoning_effort="none",
            input_price_micro_usd_per_million_tokens=200_000,
            output_price_micro_usd_per_million_tokens=1_200_000,
            usd_jpy_rate_microunits=150_000_000,
            cost_safety_basis_points=12_500,
            max_input_characters=2_000,
            max_input_bytes=8_000,
            max_input_tokens=8_512,
            max_output_tokens=64,
        )


def test_openai_maximum_cost_is_pessimistic_and_bounded() -> None:
    assert config().maximum_cost_microunits == 333_600
    unsafe = OpenAINameGeneratorConfig(
        model=LUNA_ALIAS,
        reasoning_effort="none",
        input_price_micro_usd_per_million_tokens=200_000,
        output_price_micro_usd_per_million_tokens=1_200_000,
        usd_jpy_rate_microunits=9_223_372_036_854_775_807,
        cost_safety_basis_points=9_223_372_036_854_775_807,
        max_input_characters=2_000,
        max_input_bytes=8_000,
        max_input_tokens=8_512,
        max_output_tokens=64,
    )
    with pytest.raises(ValueError):
        _ = unsafe.maximum_cost_microunits


@pytest.mark.asyncio
async def test_openai_cancel_propagates_and_double_close_is_safe() -> None:
    started = asyncio.Event()

    async def wait_forever(**unused: object) -> object:
        started.set()
        await asyncio.Event().wait()

    adapter, create = generator()
    create.side_effect = wait_forever
    task = asyncio.create_task(adapter.generate(NameGenerationRequest(content="cancel")))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await adapter.close()
    await adapter.close()
    adapter._client.close.assert_awaited_once()


def test_official_sdk_factory_disables_retries_and_bounds_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_client = SimpleNamespace(responses=SimpleNamespace(), close=AsyncMock(), _platform=None)
    constructor = MagicMock(return_value=sdk_client)
    timeout = MagicMock(return_value=object())
    fake_module = SimpleNamespace(
        APIConnectionError=ConnectionCanary,
        APIStatusError=StatusCanary,
        APITimeoutError=TimeoutCanary,
        AsyncOpenAI=constructor,
        Timeout=timeout,
    )
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    adapter = create_openai_generator(
        api_key="secret-canary",
        config=config(),
        connect_timeout_seconds=1.0,
        request_timeout_seconds=4.0,
    )
    timeout.assert_called_once_with(4.0, connect=1.0, read=4.0, write=4.0, pool=1.0)
    constructor.assert_called_once_with(
        api_key="secret-canary", max_retries=0, timeout=timeout.return_value
    )
    assert adapter.available
