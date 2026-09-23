from __future__ import annotations

import asyncio
import importlib
import logging
import math
import socket
import subprocess
import sys
import threading
import traceback
from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from discord_ai_reminder_bot.application.post_draft_generation import (
    GeneratePostDraftService,
    PostDraftInvalidResponseError,
    PostDraftTimeoutError,
    PostDraftUnavailableError,
    PostDraftUnknownError,
)
from discord_ai_reminder_bot.domain.post_draft_generation import (
    PostDraftGenerationRequest,
    PostLength,
    PostTone,
)
from discord_ai_reminder_bot.infrastructure.ai.phase4_post_draft_live_harness import (
    main as harness_main,
)
from discord_ai_reminder_bot.infrastructure.ai.post_draft_request_guard import (
    LIVE_READINESS_CHECKS,
    MODEL_ALLOWLIST,
    LiveReadinessEvidence,
    ModelPricePolicy,
    ProcessGenerationGuard,
    build_post_draft_request_plan,
    fingerprint_count_payload,
    validate_create_payload_fields,
)

CONFIG_MODULE = "discord_ai_reminder_bot.post_draft_provider_config"
ADAPTER_MODULE = "discord_ai_reminder_bot.infrastructure.ai.openai_post_draft_generator"
MODEL = "gpt-5.6-luna"
API_KEY_CANARY = "sk-synthetic-post-draft-secret-canary"
PROMPT_CANARY = "provider-prompt-private-canary"
EXCEPTION_CHAIN_CANARY = "provider-exception-chain-private-canary"


def config_module():
    return importlib.import_module(CONFIG_MODULE)


def adapter_module():
    return importlib.import_module(ADAPTER_MODULE)


def request(length: PostLength = PostLength.STANDARD) -> PostDraftGenerationRequest:
    return PostDraftGenerationRequest(
        purpose="新商品の提供開始を案内する",
        key_points="開始日は9月10日\n詳細は公式案内を参照",
        tone=PostTone.POLITE,
        length=length,
    )


def response(text: object = "案内本文", *, status: str = "completed") -> object:
    item = SimpleNamespace(type="output_text", text=text)
    message = SimpleNamespace(type="message", content=[item])
    return SimpleNamespace(
        status=status,
        output=[SimpleNamespace(type="reasoning"), message],
        output_text=text,
    )


def readiness(**overrides: bool) -> LiveReadinessEvidence:
    values = dict.fromkeys(LIVE_READINESS_CHECKS, True)
    values.update(overrides)
    return LiveReadinessEvidence(**values)


def price_policy(
    *,
    threshold: int = 1_000,
    input_price: str = "1",
    output_price: str = "1",
    input_multiplier: str = "2",
    output_multiplier: str = "1.5",
    source_url: str = "https://developers.openai.com/api/docs/models/synthetic",
) -> ModelPricePolicy:
    return ModelPricePolicy(
        model=MODEL,
        input_price_per_million=Decimal(input_price),
        output_price_per_million=Decimal(output_price),
        long_context_threshold_tokens=threshold,
        long_context_input_multiplier=Decimal(input_multiplier),
        long_context_output_multiplier=Decimal(output_multiplier),
        source_url=source_url,
        verified_on=date(2000, 1, 1),
        exact_snapshot_policy_selected=True,
        model_price_snapshot_verified=True,
        long_context_pricing_verified=True,
    )


def adapter(
    *,
    scenario: object | None = None,
    generation_attempt_cap: int = 10,
    external_call_cap: int = 20,
    generation_reserved_cost_cap: Decimal | str = Decimal(10),
) -> tuple[object, object, ProcessGenerationGuard]:
    runtime_owner = adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner.for_offline_script(
        scenario=scenario or adapter_module().OfflineScriptScenario.SUCCESS,
        generation_attempt_cap=generation_attempt_cap,
        external_call_cap=external_call_cap,
        generation_reserved_cost_cap=generation_reserved_cost_cap,
    )
    generator = runtime_owner.create_generator(
        model=MODEL,
        reasoning_effort="none",
        price_policy=price_policy(),
        inner_timeout_seconds=1,
        configured_max_output_tokens=2_048,
    )
    return generator, runtime_owner, runtime_owner.guard


def plan(length: PostLength = PostLength.STANDARD):
    return build_post_draft_request_plan(
        request=request(length),
        model=MODEL,
        instructions="fixed synthetic instructions",
        reasoning_effort="none",
    )


def assert_detached_exception(error: BaseException, caplog: str) -> None:
    observed = " ".join(
        (
            caplog,
            str(error),
            repr(error),
            repr(error.args),
            repr(vars(error)),
            "".join(traceback.format_exception(error)),
        )
    )
    assert error.__cause__ is None
    assert error.__context__ is None
    assert EXCEPTION_CHAIN_CANARY not in observed
    assert "fixed scripted provider failure" not in observed


def all_provider_values(**overrides: str) -> dict[str, str]:
    values = {
        "AI_POST_DRAFT_PROVIDER_ENABLED": "true",
        "AI_POST_DRAFT_OPENAI_API_KEY": API_KEY_CANARY,
        "AI_POST_DRAFT_OPENAI_MODEL": MODEL,
        "AI_POST_DRAFT_OPENAI_REASONING_EFFORT": "none",
        "AI_POST_DRAFT_OPENAI_INNER_TIMEOUT_SECONDS": "2",
        "AI_POST_DRAFT_OPENAI_OUTER_TIMEOUT_SECONDS": "3",
        "AI_POST_DRAFT_OPENAI_MAX_OUTPUT_TOKENS": "2048",
        "AI_POST_DRAFT_OPENAI_GENERATION_ATTEMPT_CAP": "1",
        "AI_POST_DRAFT_OPENAI_EXTERNAL_CALL_CAP": "2",
        "AI_POST_DRAFT_OPENAI_GENERATION_COST_CAP_USD": "1",
        "AI_POST_DRAFT_OPENAI_INPUT_PRICE_PER_MILLION_USD": "1",
        "AI_POST_DRAFT_OPENAI_OUTPUT_PRICE_PER_MILLION_USD": "1",
        "AI_POST_DRAFT_OPENAI_LONG_CONTEXT_THRESHOLD_TOKENS": "1000",
        "AI_POST_DRAFT_OPENAI_LONG_CONTEXT_INPUT_MULTIPLIER": "2",
        "AI_POST_DRAFT_OPENAI_LONG_CONTEXT_OUTPUT_MULTIPLIER": "1.5",
        "AI_POST_DRAFT_OPENAI_PRICE_SOURCE_URL": (
            "https://developers.openai.com/api/docs/models/synthetic"
        ),
        "AI_POST_DRAFT_OPENAI_PRICE_VERIFIED_ON": "2000-01-01",
        "AI_POST_DRAFT_FORMAL_MODEL_SELECTED": "false",
        "AI_POST_DRAFT_EXACT_MODEL_SNAPSHOT_POLICY_SELECTED": "false",
        "AI_POST_DRAFT_MODEL_PRICE_SNAPSHOT_VERIFIED": "false",
        "AI_POST_DRAFT_LONG_CONTEXT_PRICING_VERIFIED": "false",
        "AI_POST_DRAFT_INPUT_TOKENS_PRICING_VERIFIED": "false",
        "AI_POST_DRAFT_INPUT_TOKENS_RETENTION_REVIEWED": "false",
        "AI_POST_DRAFT_DEDICATED_CREDENTIAL_CONFIGURED": "false",
        "AI_POST_DRAFT_BILLING_BUDGET_CONTROLS_REVIEWED": "false",
        "AI_POST_DRAFT_EXPLICIT_LIVE_AUTHORIZATION_PRESENT": "false",
        "AI_POST_DRAFT_CLIENT_SHUTDOWN_STRATEGY_APPROVED": "false",
    }
    values.update(overrides)
    return values


def clear_provider_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for field in config_module().OpenAIPostDraftProviderSettings.model_fields.values():
        alias = field.validation_alias
        if isinstance(alias, str):
            monkeypatch.delenv(alias, raising=False)


def load(monkeypatch: pytest.MonkeyPatch, **values: str):
    clear_provider_environment(monkeypatch)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return config_module().load_openai_post_draft_provider_settings(env_file=None)


def test_provider_is_disabled_and_unconfigured_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = load(monkeypatch)
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.UNCONFIGURED
    assert result.requested_enabled is False
    assert result.live_ready is False
    assert result.settings is None
    assert "production_effective_gate_closed" in result.blockers
    assert config_module().PRODUCTION_REAL_PROVIDER_GATE_OPEN is False


def test_env_example_documents_the_closed_provider_without_a_credential_value() -> None:
    text = (Path(__file__).parents[1] / ".env.example").read_text(encoding="utf-8")
    assert text.count("AI_POST_DRAFT_PROVIDER_ENABLED=false") == 1
    assert "\nAI_POST_DRAFT_OPENAI_API_KEY=" not in text
    assert API_KEY_CANARY not in text


@pytest.mark.parametrize(
    "missing",
    tuple(
        str(config_module().OpenAIPostDraftProviderSettings.model_fields[name].validation_alias)
        for name in config_module()._REQUIRED_OPERATIONAL_FIELDS
    ),
)
def test_partial_operational_provider_settings_are_invalid(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    values = all_provider_values()
    del values[missing]
    result = load(monkeypatch, **values)
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.INVALID
    assert result.blockers == ("invalid_provider_settings",)


@pytest.mark.parametrize("model", sorted(MODEL_ALLOWLIST))
def test_model_allowlist_is_exact(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    result = load(
        monkeypatch,
        **all_provider_values(AI_POST_DRAFT_OPENAI_MODEL=model),
    )
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.BLOCKED
    assert result.settings is not None and result.settings.model == model


@pytest.mark.parametrize(
    "model",
    [
        "",
        " gpt-5.6-luna",
        "gpt-5.6-luna ",
        "GPT-5.6-LUNA",
        "gpt-5.6",
        "gpt-5.6-luna-extra",
        "gpt-5.6-ⅼuna",  # U+217C SMALL ROMAN NUMERAL FIFTY, visually confusable with l
        "gpt-5.4-nano",
        "unknown",
    ],
)
def test_unknown_model_is_rejected(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    result = load(
        monkeypatch,
        **all_provider_values(AI_POST_DRAFT_OPENAI_MODEL=model),
    )
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.INVALID


def test_unicode_confusable_model_is_not_normalized_or_reflected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    confusable = "gpt-5.6-ⅼuna"  # U+217C, not ASCII U+006C.
    result = load(
        monkeypatch,
        **all_provider_values(AI_POST_DRAFT_OPENAI_MODEL=confusable),
    )
    observed = f"{result!r} {result}"
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.INVALID
    assert confusable not in observed
    assert MODEL not in observed


@pytest.mark.parametrize(
    "field",
    [
        "AI_POST_DRAFT_OPENAI_GENERATION_COST_CAP_USD",
        "AI_POST_DRAFT_OPENAI_INPUT_PRICE_PER_MILLION_USD",
        "AI_POST_DRAFT_OPENAI_OUTPUT_PRICE_PER_MILLION_USD",
        "AI_POST_DRAFT_OPENAI_LONG_CONTEXT_INPUT_MULTIPLIER",
        "AI_POST_DRAFT_OPENAI_LONG_CONTEXT_OUTPUT_MULTIPLIER",
        "AI_POST_DRAFT_OPENAI_LONG_CONTEXT_THRESHOLD_TOKENS",
    ],
)
def test_cost_inputs_reject_float_at_each_settings_entry(field: str) -> None:
    values: dict[str, object] = all_provider_values()
    values[field] = 1.5
    with pytest.raises(ValueError):
        config_module().OpenAIPostDraftProviderSettings(_env_file=None, **values)


@pytest.mark.parametrize(
    "field",
    [
        "AI_POST_DRAFT_OPENAI_GENERATION_COST_CAP_USD",
        "AI_POST_DRAFT_OPENAI_INPUT_PRICE_PER_MILLION_USD",
        "AI_POST_DRAFT_OPENAI_OUTPUT_PRICE_PER_MILLION_USD",
        "AI_POST_DRAFT_OPENAI_LONG_CONTEXT_INPUT_MULTIPLIER",
        "AI_POST_DRAFT_OPENAI_LONG_CONTEXT_OUTPUT_MULTIPLIER",
        "AI_POST_DRAFT_OPENAI_LONG_CONTEXT_THRESHOLD_TOKENS",
        "AI_POST_DRAFT_OPENAI_MAX_OUTPUT_TOKENS",
    ],
)
@pytest.mark.parametrize("value", [True, False])
def test_cost_and_token_inputs_reject_bool_at_each_settings_entry(field: str, value: bool) -> None:
    values: dict[str, object] = all_provider_values()
    values[field] = value
    with pytest.raises(ValueError):
        config_module().OpenAIPostDraftProviderSettings(_env_file=None, **values)


@pytest.mark.parametrize("value", ["1e2", " 1", "1 ", "", "+1", "1,5", "-1", "0"])
def test_cost_decimal_strings_must_be_canonical_and_positive(value: str) -> None:
    values: dict[str, object] = all_provider_values()
    values["AI_POST_DRAFT_OPENAI_INPUT_PRICE_PER_MILLION_USD"] = value
    with pytest.raises(ValueError):
        config_module().OpenAIPostDraftProviderSettings(_env_file=None, **values)


@pytest.mark.parametrize(("inner", "outer"), [("2", "2"), ("3", "2")])
def test_inner_timeout_must_be_less_than_outer(
    monkeypatch: pytest.MonkeyPatch, inner: str, outer: str
) -> None:
    result = load(
        monkeypatch,
        **all_provider_values(
            AI_POST_DRAFT_OPENAI_INNER_TIMEOUT_SECONDS=inner,
            AI_POST_DRAFT_OPENAI_OUTER_TIMEOUT_SECONDS=outer,
        ),
    )
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.INVALID


def test_external_call_cap_must_cover_the_fixed_two_call_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = load(
        monkeypatch,
        **all_provider_values(
            AI_POST_DRAFT_OPENAI_GENERATION_ATTEMPT_CAP="2",
            AI_POST_DRAFT_OPENAI_EXTERNAL_CALL_CAP="3",
        ),
    )
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.INVALID
    assert result.blockers == ("invalid_provider_settings",)


@pytest.mark.parametrize("blocker", LIVE_READINESS_CHECKS)
def test_every_live_readiness_blocker_remains_closed(
    monkeypatch: pytest.MonkeyPatch, blocker: str
) -> None:
    marker_alias = (
        config_module().OpenAIPostDraftProviderSettings.model_fields[blocker].validation_alias
    )
    assert isinstance(marker_alias, str)
    values = all_provider_values()
    for name in LIVE_READINESS_CHECKS:
        if name != "client_shutdown_strategy_approved":
            alias = (
                config_module().OpenAIPostDraftProviderSettings.model_fields[name].validation_alias
            )
            assert isinstance(alias, str)
            values[alias] = "true"
    values[marker_alias] = "false"
    result = load(monkeypatch, **values)
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.BLOCKED
    assert blocker in result.blockers
    assert result.live_ready is False


def test_client_construction_is_rejected_before_sdk_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed = 0

    def forbidden_client(**kwargs: object) -> object:
        nonlocal constructed
        constructed += 1
        raise AssertionError(kwargs)

    fake_openai = SimpleNamespace(
        AsyncOpenAI=forbidden_client,
        APIConnectionError=RuntimeError,
        APIStatusError=RuntimeError,
        APITimeoutError=RuntimeError,
    )
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    settings = config_module().OpenAIPostDraftProviderSettings(
        _env_file=None,
        **all_provider_values(),
    )
    with pytest.raises(ValueError, match="not live-ready"):
        asyncio.run(adapter_module()._create_openai_post_draft_runtime_owner(settings))
    assert constructed == 0


def test_production_owner_does_not_depend_on_the_offline_scripted_runtime() -> None:
    source = __import__("inspect").getsource(adapter_module().ProductionOpenAIPostDraftRuntimeOwner)
    assert "OfflineScripted" not in source
    assert "tests.support" not in source


def _production_owner_for_direct_offline_test(monkeypatch: pytest.MonkeyPatch, client: object):
    config = config_module()
    adapter = adapter_module()
    monkeypatch.setattr(config, "PRODUCTION_REAL_PROVIDER_GATE_OPEN", True)
    monkeypatch.setattr(config, "CLIENT_SHUTDOWN_STRATEGY_APPROVED", True)
    monkeypatch.setattr(adapter, "PRODUCTION_REAL_PROVIDER_GATE_OPEN", True)
    monkeypatch.setattr(adapter, "CLIENT_SHUTDOWN_STRATEGY_APPROVED", True)
    values = all_provider_values(
        AI_POST_DRAFT_OPENAI_INNER_TIMEOUT_SECONDS="0.01",
        AI_POST_DRAFT_OPENAI_OUTER_TIMEOUT_SECONDS="0.02",
    )
    for field in LIVE_READINESS_CHECKS:
        alias = config.OpenAIPostDraftProviderSettings.model_fields[field].validation_alias
        assert isinstance(alias, str)
        values[alias] = "true"
    settings = config.OpenAIPostDraftProviderSettings(_env_file=None, **values)
    owner_type = adapter.ProductionOpenAIPostDraftRuntimeOwner
    return owner_type(
        _construction_token=owner_type._CONSTRUCTION_TOKEN,
        client=client,
        settings=settings,
    )


class _DirectProductionClient:
    def __init__(self, count, create) -> None:
        self.responses = SimpleNamespace(
            input_tokens=SimpleNamespace(count=count),
            create=create,
        )
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_production_close_cancels_and_reaps_the_tracked_inflight_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def count(**_kwargs: object) -> object:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def create(**_kwargs: object) -> object:
        raise AssertionError("create must not start after shutdown")

    client = _DirectProductionClient(count, create)
    owner = _production_owner_for_direct_offline_test(monkeypatch, client)
    task = asyncio.create_task(owner.create_generator().generate(request()))
    await started.wait()

    assert await owner.close() is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.close_calls == 1
    assert owner._active_task is None  # type: ignore[attr-defined]
    assert owner._active_task_cancel_count == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_production_closing_rejects_create_and_a_concurrent_new_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = {"count": 0, "create": 0}

    async def count(**_kwargs: object) -> object:
        calls["count"] += 1
        started.set()
        await release.wait()
        return SimpleNamespace(input_tokens=1, object="response.input_tokens")

    async def create(**_kwargs: object) -> object:
        calls["create"] += 1
        raise AssertionError("create must not start after CLOSING")

    client = _DirectProductionClient(count, create)
    owner = _production_owner_for_direct_offline_test(monkeypatch, client)
    generator = owner.create_generator()
    first = asyncio.create_task(generator.generate(request()))
    await started.wait()
    closing = asyncio.create_task(owner.close())
    await asyncio.sleep(0)
    with pytest.raises(PostDraftUnavailableError):
        await generator.generate(request())
    release.set()
    with pytest.raises(PostDraftUnavailableError):
        await first
    assert await closing is True
    assert calls == {"count": 1, "create": 0}
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_production_close_never_reports_closed_while_cancelled_task_is_still_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def count(**_kwargs: object) -> object:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
        return SimpleNamespace(input_tokens=1, object="response.input_tokens")

    async def create(**_kwargs: object) -> object:
        raise AssertionError("create must not start after CLOSING")

    client = _DirectProductionClient(count, create)
    owner = _production_owner_for_direct_offline_test(monkeypatch, client)
    task = asyncio.create_task(owner.create_generator().generate(request()))
    await started.wait()

    assert await owner.close() is False
    assert owner.closed is False
    assert owner.closing is True
    assert client.close_calls == 0
    assert owner._active_task_cancel_count == 1  # type: ignore[attr-defined]
    release.set()
    with pytest.raises(PostDraftUnavailableError):
        await task


@pytest.mark.asyncio
async def test_production_create_cancel_ignore_cannot_return_a_success_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_started = asyncio.Event()
    calls = {"count": 0, "create": 0}
    canary = "private-create-response-canary"

    async def count(**_kwargs: object) -> object:
        calls["count"] += 1
        return SimpleNamespace(input_tokens=1, object="response.input_tokens")

    async def create(**_kwargs: object) -> object:
        calls["create"] += 1
        create_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            message = SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text=canary)],
            )
            return SimpleNamespace(status="completed", output=[message], output_text=canary)
        raise AssertionError("create must be cancelled exactly once")

    client = _DirectProductionClient(count, create)
    owner = _production_owner_for_direct_offline_test(monkeypatch, client)
    task = asyncio.create_task(owner.create_generator().generate(request()))
    await create_started.wait()

    assert await owner.close() is True
    with pytest.raises(PostDraftUnavailableError) as raised:
        await task
    assert canary not in f"{raised.value!r} {raised.value}"
    assert calls == {"count": 1, "create": 1}
    assert owner._active_task is None  # type: ignore[attr-defined]
    assert owner._active_task_cancel_count == 1  # type: ignore[attr-defined]
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_production_response_property_failure_is_non_reflecting_invalid_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "private-response-property-canary"

    class BrokenResponse:
        @property
        def status(self) -> object:
            raise RuntimeError(canary)

    async def count(**_kwargs: object) -> object:
        return SimpleNamespace(input_tokens=1, object="response.input_tokens")

    async def create(**_kwargs: object) -> object:
        return BrokenResponse()

    owner = _production_owner_for_direct_offline_test(
        monkeypatch, _DirectProductionClient(count, create)
    )
    with pytest.raises(PostDraftInvalidResponseError) as raised:
        await owner.create_generator().generate(request())
    observed = f"{raised.value!r} {raised.value}"
    assert canary not in observed
    assert raised.value.__cause__ is None


@pytest.mark.asyncio
async def test_production_nested_response_iterator_failure_is_non_reflecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "private-response-iterator-canary"

    class BrokenOutput(list[object]):
        def __iter__(self):
            raise RuntimeError(canary)

    class BrokenResponse:
        status = "completed"
        output = BrokenOutput([SimpleNamespace(type="message", content=[])])
        output_text = "ignored"

    async def count(**_kwargs: object) -> object:
        return SimpleNamespace(input_tokens=1, object="response.input_tokens")

    async def create(**_kwargs: object) -> object:
        return BrokenResponse()

    owner = _production_owner_for_direct_offline_test(
        monkeypatch, _DirectProductionClient(count, create)
    )
    with pytest.raises(PostDraftInvalidResponseError) as raised:
        await owner.create_generator().generate(request())
    assert canary not in f"{raised.value!r} {raised.value}"


@pytest.mark.asyncio
async def test_production_count_property_failure_is_non_reflecting_invalid_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "private-count-property-canary"

    class BrokenCount:
        @property
        def input_tokens(self) -> object:
            raise RuntimeError(canary)

    async def count(**_kwargs: object) -> object:
        return BrokenCount()

    async def create(**_kwargs: object) -> object:
        raise AssertionError("create must not start after invalid count")

    owner = _production_owner_for_direct_offline_test(
        monkeypatch, _DirectProductionClient(count, create)
    )
    with pytest.raises(PostDraftInvalidResponseError) as raised:
        await owner.create_generator().generate(request())
    assert canary not in f"{raised.value!r} {raised.value}"


@pytest.mark.asyncio
async def test_production_response_special_exception_identity_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = KeyboardInterrupt("private-keyboard-canary")

    class BrokenResponse:
        @property
        def status(self) -> object:
            raise primary

    async def count(**_kwargs: object) -> object:
        return SimpleNamespace(input_tokens=1, object="response.input_tokens")

    async def create(**_kwargs: object) -> object:
        return BrokenResponse()

    owner = _production_owner_for_direct_offline_test(
        monkeypatch, _DirectProductionClient(count, create)
    )
    try:
        await owner.create_generator().generate(request())
    except KeyboardInterrupt as error:
        assert error is primary
    else:
        raise AssertionError("KeyboardInterrupt must remain authoritative")


def test_shutdown_strategy_cannot_be_approved_by_environment_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = load(
        monkeypatch,
        **all_provider_values(AI_POST_DRAFT_CLIENT_SHUTDOWN_STRATEGY_APPROVED="true"),
    )
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.INVALID
    assert config_module().CLIENT_SHUTDOWN_STRATEGY_APPROVED is False


def test_arbitrary_client_injection_apis_do_not_exist() -> None:
    owner = adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner
    assert not hasattr(owner, "for_borrowed_client")
    assert not hasattr(owner, "for_offline_cooperative_test")
    offline_parameters = __import__("inspect").signature(owner.for_offline_script).parameters
    assert set(offline_parameters) == {
        "scenario",
        "generation_attempt_cap",
        "external_call_cap",
        "generation_reserved_cost_cap",
    }
    for forbidden in (
        "guard",
        "serial_gate",
        "client",
        "client_factory",
        "generator_class",
        "owner_class",
        "callback",
        "awaitable",
        "error_types",
    ):
        assert forbidden not in offline_parameters
    assert (
        "client_factory"
        not in __import__("inspect")
        .signature(adapter_module()._create_openai_post_draft_runtime_owner)
        .parameters
    )


def test_offline_exception_policy_is_module_owned_and_not_caller_configurable() -> None:
    inspect = __import__("inspect")
    module = adapter_module()
    owner = module.OfflineScriptedOpenAIPostDraftRuntimeOwner.for_offline_script(
        scenario=module.OfflineScriptScenario.SUCCESS,
        generation_attempt_cap=1,
        external_call_cap=2,
        generation_reserved_cost_cap=Decimal(1),
    )
    signatures = (
        inspect.signature(module.OfflineScriptedOpenAIPostDraftRuntimeOwner.for_offline_script),
        inspect.signature(module.OfflineScriptedOpenAIPostDraftRuntimeOwner.create_generator),
        inspect.signature(module.OfflineScriptedOpenAIPostDraftGenerator),
    )
    for signature in signatures:
        assert "error_types" not in signature.parameters
        assert all(
            parameter.kind is not inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
    assert not hasattr(module, "OpenAIPostDraftErrorTypes")

    class AwaitableSentinel:
        def __await__(self):
            return iter(())

    forbidden_inputs = (
        (RuntimeError,),
        RuntimeError("private-exception-detail"),
        lambda: None,
        AwaitableSentinel(),
    )
    for forbidden in forbidden_inputs:
        with pytest.raises(TypeError) as create_error:
            owner.create_generator(model=MODEL, error_types=forbidden)
        with pytest.raises(TypeError) as scenario_error:
            module.OfflineScriptedOpenAIPostDraftRuntimeOwner.for_offline_script(
                scenario=forbidden,
                generation_attempt_cap=1,
                external_call_cap=2,
                generation_reserved_cost_cap=Decimal(1),
            )
        observed = f"{create_error.value!s} {scenario_error.value!s}"
        assert "private-exception-detail" not in observed

    with pytest.raises(TypeError):
        owner.create_generator(MODEL, (RuntimeError,))
    with pytest.raises(TypeError):
        module.OfflineScriptedOpenAIPostDraftGenerator(owner, MODEL, (RuntimeError,))
    with pytest.raises(TypeError):
        module.OfflineScriptedOpenAIPostDraftGenerator(
            runtime_owner=owner,
            model=MODEL,
            error_types=(RuntimeError,),
        )

    generator = owner.create_generator(model=MODEL)
    assert not hasattr(generator, "_errors")
    harness_source = __import__("inspect").getsource(
        __import__(
            "discord_ai_reminder_bot.infrastructure.ai.phase4_post_draft_live_harness",
            fromlist=["run_offline_self_test"],
        ).run_offline_self_test
    )
    assert "error_types" not in harness_source


def test_direct_low_level_construction_never_touches_http_capable_sentinel() -> None:
    class HttpCapableSentinel:
        accesses = 0

        @property
        def responses(self) -> object:
            self.accesses += 1
            raise AssertionError("must not be accessed")

    sentinel = HttpCapableSentinel()
    with pytest.raises(TypeError, match="runtime owner"):
        adapter_module().OfflineScriptedOpenAIPostDraftGenerator(
            runtime_owner=sentinel,
            model=MODEL,
        )
    with pytest.raises(TypeError):
        adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner.for_offline_script(
            scenario=adapter_module().OfflineScriptScenario.SUCCESS,
            generation_attempt_cap=1,
            external_call_cap=2,
            generation_reserved_cost_cap=Decimal(1),
            client=sentinel,
        )
    assert sentinel.accesses == 0


def test_security_boundary_concrete_types_reject_runtime_subclassing() -> None:
    boundary_types = (
        adapter_module().ProductionOpenAIPostDraftRuntimeOwner,
        adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner,
        adapter_module().ProductionOpenAIPostDraftGenerator,
        adapter_module().OfflineScriptedOpenAIPostDraftGenerator,
        ProcessGenerationGuard,
        adapter_module()._ScriptedOpenAIClient,
    )
    for boundary in boundary_types:
        with pytest.raises(TypeError, match="subclassing is prohibited"):
            type("SyntheticSubclass", (boundary,), {})


def test_security_factories_construct_exact_types_without_cls_dispatch() -> None:
    inspect = __import__("inspect")
    owner_type = adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner
    factory_descriptor = inspect.getattr_static(owner_type, "for_offline_script")
    assert isinstance(factory_descriptor, staticmethod)
    assert "cls(" not in inspect.getsource(owner_type.for_offline_script)
    assert "cls(" not in inspect.getsource(owner_type.create_generator)

    owner = owner_type.for_offline_script(
        scenario=adapter_module().OfflineScriptScenario.SUCCESS,
        generation_attempt_cap=1,
        external_call_cap=2,
        generation_reserved_cost_cap=Decimal(1),
    )
    generator = owner.create_generator(
        model=MODEL,
    )
    assert type(owner) is owner_type
    assert type(generator) is adapter_module().OfflineScriptedOpenAIPostDraftGenerator
    assert type(owner.guard) is ProcessGenerationGuard


def test_owner_and_generator_subclass_sentinels_are_never_accessed() -> None:
    accesses = 0

    def sentinel_property(unused_self: object) -> bool:
        nonlocal accesses
        accesses += 1
        return True

    for boundary in (
        adapter_module().ProductionOpenAIPostDraftRuntimeOwner,
        adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner,
        adapter_module().ProductionOpenAIPostDraftGenerator,
        adapter_module().OfflineScriptedOpenAIPostDraftGenerator,
    ):
        with pytest.raises(TypeError, match="subclassing is prohibited"):
            type("SyntheticSubclass", (boundary,), {"offline_only": property(sentinel_property)})
    assert accesses == 0


@pytest.mark.parametrize(
    "cap",
    [1, Decimal("1.25"), "1.25"],
)
def test_offline_factory_accepts_only_exact_scenario_and_primitive_caps(cap: object) -> None:
    scenario_type = adapter_module().OfflineScriptScenario

    class ScenarioSubclass(str):
        pass

    for invalid in ("SUCCESS", ScenarioSubclass("SUCCESS"), object(), lambda: None):
        with pytest.raises(TypeError, match="offline provider script"):
            adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner.for_offline_script(
                scenario=invalid,
                generation_attempt_cap=1,
                external_call_cap=2,
                generation_reserved_cost_cap=Decimal(1),
            )
    if type(cap) is int:
        with pytest.raises(TypeError, match="offline provider cap"):
            adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner.for_offline_script(
                scenario=scenario_type.SUCCESS,
                generation_attempt_cap=1,
                external_call_cap=2,
                generation_reserved_cost_cap=cap,
            )
        return
    owner = adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner.for_offline_script(
        scenario=scenario_type.SUCCESS,
        generation_attempt_cap=1,
        external_call_cap=2,
        generation_reserved_cost_cap=cap,
    )
    assert type(owner.guard) is ProcessGenerationGuard
    assert type(owner._client) is adapter_module()._ScriptedOpenAIClient  # type: ignore[attr-defined]


def test_caller_readiness_and_effective_gate_cannot_unlock_owner() -> None:
    signature = __import__("inspect").signature(
        adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner.create_generator
    )
    assert "readiness" not in signature.parameters
    assert "effective_gate_open" not in signature.parameters
    assert "client" not in signature.parameters
    assert "client_factory" not in signature.parameters

    owner = adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner.for_offline_script(
        scenario=adapter_module().OfflineScriptScenario.SUCCESS,
        generation_attempt_cap=1,
        external_call_cap=2,
        generation_reserved_cost_cap=Decimal(1),
    )
    with pytest.raises(TypeError):
        owner.create_generator(
            model=MODEL,
            readiness=readiness(),
            effective_gate_open=True,
        )
    assert owner.offline_script_snapshot().events == ()


def test_scripted_fake_has_no_network_or_subprocess_capability() -> None:
    source = __import__("inspect").getsource(adapter_module()._ScriptedOpenAIClient)
    for forbidden in ("socket", "getaddrinfo", "httpx", "subprocess", "import openai"):
        assert forbidden not in source.lower()


def test_every_fixed_offline_scenario_constructs_without_caller_code() -> None:
    scenario_type = adapter_module().OfflineScriptScenario
    for scenario in scenario_type:
        owner = adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner.for_offline_script(
            scenario=scenario,
            generation_attempt_cap=1,
            external_call_cap=2,
            generation_reserved_cost_cap=Decimal(1),
        )
        assert type(owner._client) is adapter_module()._ScriptedOpenAIClient  # type: ignore[attr-defined]


def test_provider_setting_canaries_are_never_reflected(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    values = all_provider_values(AI_POST_DRAFT_OPENAI_MODEL=f" {MODEL}")
    with caplog.at_level(logging.DEBUG):
        result = load(monkeypatch, **values)
    observed = " ".join((repr(result), str(result), caplog.text))
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.INVALID
    assert API_KEY_CANARY not in observed
    assert MODEL not in observed
    assert caplog.text == ""


def test_request_plan_is_deeply_immutable_and_projections_are_fresh() -> None:
    built = plan()
    with pytest.raises(FrozenInstanceError):
        built.model = "gpt-5.6-terra"  # type: ignore[misc]
    first = built.count_payload()
    second = built.count_payload()
    assert first == second and first is not second
    first_input = first["input"]
    assert isinstance(first_input, list)
    first_input.clear()
    assert built.count_payload()["input"]
    assert built.count_fingerprint() == built.count_fingerprint()


def test_count_and_create_projection_share_input_fields_and_separate_create_fields() -> None:
    built = plan()
    count = built.count_payload()
    create = built.create_payload()
    assert count == {key: create[key] for key in count}
    assert set(create) - set(count) == {"max_output_tokens", "store"}
    assert create["store"] is False
    assert "text" not in create
    assert "tools" not in create
    assert "tool_choice" not in create
    assert "background" not in create
    assert "conversation" not in create
    assert "previous_response_id" not in create


def test_count_fingerprint_is_canonical_but_preserves_meaningful_list_order() -> None:
    payload = plan().count_payload()
    reversed_fields = dict(reversed(tuple(payload.items())))
    assert fingerprint_count_payload(payload) == fingerprint_count_payload(reversed_fields)

    first = {"role": "user", "content": [{"type": "input_text", "text": "A"}]}
    second = {"role": "user", "content": [{"type": "input_text", "text": "B"}]}
    ordered = plan().count_payload()
    swapped = plan().count_payload()
    ordered["input"] = [first, second]
    swapped["input"] = [second, first]
    assert fingerprint_count_payload(ordered) != fingerprint_count_payload(swapped)


def test_count_fingerprint_preserves_canonical_json_types() -> None:
    values: list[object] = ["1", 1, True, None, ["1"], {"value": "1"}]
    fingerprints: set[str] = set()
    for value in values:
        payload = plan().count_payload()
        payload["input"] = [value]
        fingerprints.add(fingerprint_count_payload(payload))
    assert len(fingerprints) == len(values)

    payload = plan().count_payload()
    payload["input"] = [{1, 2}]
    with pytest.raises(ValueError, match="invalid OpenAI count payload"):
        fingerprint_count_payload(payload)


@pytest.mark.parametrize("mutation", ["added", "missing"])
def test_count_fingerprint_rejects_unknown_or_missing_fields(mutation: str) -> None:
    payload = plan().count_payload()
    if mutation == "added":
        payload["future_sdk_field"] = "synthetic"
    else:
        del payload["instructions"]
    with pytest.raises(ValueError, match="count payload field"):
        fingerprint_count_payload(payload)


def test_count_fingerprint_detects_nested_and_count_field_changes() -> None:
    original = plan().count_payload()
    nested_change = plan().count_payload()
    nested_input = nested_change["input"]
    assert isinstance(nested_input, list)
    nested_input[0]["content"][0]["text"] = "changed"  # type: ignore[index]
    model_change = plan().count_payload()
    model_change["model"] = "gpt-5.6-terra"
    assert fingerprint_count_payload(original) != fingerprint_count_payload(nested_change)
    assert fingerprint_count_payload(original) != fingerprint_count_payload(model_change)


def test_create_only_change_does_not_change_count_fingerprint() -> None:
    original = plan()
    changed = replace(original, max_output_tokens=original.max_output_tokens + 1)
    assert original.count_fingerprint() == changed.count_fingerprint()
    assert original.create_payload() != changed.create_payload()


def test_create_projection_rejects_unclassified_future_field() -> None:
    payload = plan().create_payload()
    payload["future_sdk_field"] = "synthetic"
    with pytest.raises(ValueError, match="create payload field"):
        validate_create_payload_fields(payload)


@pytest.mark.parametrize(
    ("length", "expected_tokens"),
    [(PostLength.SHORT, 512), (PostLength.STANDARD, 1_024), (PostLength.LONG, 2_048)],
)
@pytest.mark.asyncio
async def test_count_once_then_create_once_from_matching_plan(
    length: PostLength, expected_tokens: int
) -> None:
    generator, owner, ledger = adapter()
    generated = await generator.generate(request(length))  # type: ignore[attr-defined]
    assert generated.value == "案内本文"
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert script.count_calls == 1
    assert script.create_calls == 1
    assert script.count_timeout == 1
    assert script.create_timeout == 1
    assert script.count_create_input_fields_match is True
    assert script.create_max_output_tokens == expected_tokens
    assert script.create_store is False
    snapshot = await ledger.snapshot()
    assert snapshot.generation_attempts == 1
    assert snapshot.external_calls == 2
    assert snapshot.reserved_generation_cost > 0


@pytest.mark.asyncio
async def test_fingerprint_reservation_and_provider_call_order_is_exact() -> None:
    generator, owner, guard = adapter()
    await generator.generate(request())  # type: ignore[attr-defined]
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    ledger = await guard.snapshot()
    assert script.events == (
        "responses_bind",
        "input_tokens_bind",
        "count_bind",
        "count_call",
        "responses_bind",
        "create_bind",
        "create_call",
    )
    assert ledger.generation_attempts == 1
    assert ledger.external_calls == 2
    assert ledger.reserved_generation_cost > 0


@pytest.mark.asyncio
async def test_provider_callables_are_fully_bound_before_external_reservation() -> None:
    source = __import__("inspect").getsource(
        adapter_module().OfflineScriptedOpenAIPostDraftGenerator._generate_serial
    )
    count_bind = source.index("count_call = count_input_tokens.count")
    count_reserve = source.index("await self._guard.consume_external_call()")
    count_call = source.index("count_pending = count_call(")
    create_bind = source.index("create_call = create_responses.create")
    create_reserve = source.rindex("await self._guard.consume_external_call()")
    create_call = source.index("create_pending = create_call(")
    assert count_bind < count_reserve < count_call
    assert create_bind < create_reserve < create_call
    assert "_invoke_bound" not in source


@pytest.mark.parametrize(
    "scenario",
    [
        "RESPONSES_ACCESS_FAILURE",
        "INPUT_TOKENS_ACCESS_FAILURE",
        "COUNT_BIND_FAILURE",
        "COUNT_NON_CALLABLE",
    ],
)
@pytest.mark.asyncio
async def test_count_callable_binding_failure_consumes_no_external_slot(
    scenario: str,
) -> None:
    generator, owner, guard = adapter(
        scenario=getattr(adapter_module().OfflineScriptScenario, scenario)
    )
    with pytest.raises(PostDraftUnavailableError):
        await generator.generate(request())
    snapshot = await guard.snapshot()
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert snapshot.generation_attempts == 1
    assert snapshot.external_calls == 0
    assert script.count_calls == 0
    assert script.create_calls == 0


@pytest.mark.parametrize("scenario", ["CREATE_BIND_FAILURE", "CREATE_NON_CALLABLE"])
@pytest.mark.asyncio
async def test_create_callable_binding_failure_consumes_only_count_slot(
    scenario: str,
) -> None:
    generator, owner, guard = adapter(
        scenario=getattr(adapter_module().OfflineScriptScenario, scenario)
    )
    with pytest.raises(PostDraftUnavailableError):
        await generator.generate(request())
    snapshot = await guard.snapshot()
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert script.count_calls == 1
    assert script.create_calls == 0
    assert snapshot.external_calls == 1


@pytest.mark.parametrize("stage", ["count", "create"])
@pytest.mark.asyncio
async def test_bound_callable_immediate_failure_consumes_external_slot(stage: str) -> None:
    scenario = getattr(
        adapter_module().OfflineScriptScenario,
        f"{stage.upper()}_IMMEDIATE_FAILURE",
    )
    generator, owner, guard = adapter(scenario=scenario)
    with pytest.raises(PostDraftUnknownError):
        await generator.generate(request())
    snapshot = await guard.snapshot()
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert snapshot.external_calls == (1 if stage == "count" else 2)
    assert script.count_calls == 1
    assert script.create_calls == (0 if stage == "count" else 1)


@pytest.mark.parametrize("stage", ["count", "create"])
@pytest.mark.asyncio
async def test_bound_callable_nonawaitable_consumes_external_slot(stage: str) -> None:
    scenario = getattr(
        adapter_module().OfflineScriptScenario,
        f"{stage.upper()}_NON_AWAITABLE",
    )
    generator, owner, guard = adapter(scenario=scenario)
    with pytest.raises(PostDraftUnavailableError):
        await generator.generate(request())
    snapshot = await guard.snapshot()
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert snapshot.external_calls == (1 if stage == "count" else 2)
    assert script.count_calls == 1
    assert script.create_calls == (0 if stage == "count" else 1)


@pytest.mark.asyncio
async def test_count_failure_never_reaches_create() -> None:
    generator, owner, ledger = adapter(
        scenario=adapter_module().OfflineScriptScenario.COUNT_UNAVAILABLE
    )
    with pytest.raises(PostDraftUnavailableError):
        await generator.generate(request())  # type: ignore[attr-defined]
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert script.count_calls == 1
    assert script.create_calls == 0
    snapshot = await ledger.snapshot()
    assert snapshot.generation_attempts == 1
    assert snapshot.external_calls == 1


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("COUNT_TIMEOUT", TimeoutError),
        ("COUNT_UNAVAILABLE", PostDraftUnavailableError),
        ("COUNT_INVALID_BOOL", PostDraftInvalidResponseError),
        ("CREATE_TIMEOUT", TimeoutError),
        ("CREATE_UNAVAILABLE", PostDraftUnavailableError),
        ("RESPONSE_EMPTY", PostDraftInvalidResponseError),
    ],
)
@pytest.mark.asyncio
async def test_fixed_script_scenarios_use_module_owned_exception_policy(
    scenario: str, expected: type[BaseException]
) -> None:
    generator, owner, _ = adapter(
        scenario=getattr(adapter_module().OfflineScriptScenario, scenario)
    )
    with pytest.raises(expected) as caught:
        await generator.generate(request())  # type: ignore[attr-defined]
    assert "scripted" not in str(caught.value)
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert script.count_calls == 1
    assert script.create_calls == (0 if scenario.startswith("COUNT_") else 1)


@pytest.mark.parametrize(
    "scenario",
    [
        "COUNT_INVALID_MISSING",
        "COUNT_INVALID_BOOL",
        "COUNT_INVALID_ZERO",
        "COUNT_INVALID_OBJECT",
    ],
)
@pytest.mark.asyncio
async def test_invalid_count_never_reaches_create(scenario: str) -> None:
    generator, owner, _ = adapter(
        scenario=getattr(adapter_module().OfflineScriptScenario, scenario)
    )
    with pytest.raises(PostDraftInvalidResponseError):
        await generator.generate(request())  # type: ignore[attr-defined]
    assert owner.offline_script_snapshot().create_calls == 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_budget_rejection_never_reaches_create() -> None:
    generator, owner, ledger = adapter(
        generation_attempt_cap=1,
        external_call_cap=2,
        generation_reserved_cost_cap=Decimal("0.000001"),
    )
    with pytest.raises(PostDraftUnavailableError):
        await generator.generate(request())  # type: ignore[attr-defined]
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert script.count_calls == 1
    assert script.create_calls == 0
    snapshot = await ledger.snapshot()
    assert snapshot.external_calls == 1
    assert snapshot.reserved_generation_cost == 0


@pytest.mark.asyncio
async def test_fingerprint_mismatch_after_count_never_reaches_create() -> None:
    generator, owner, _ = adapter(
        scenario=adapter_module().OfflineScriptScenario.FINGERPRINT_AFTER_COUNT
    )
    with pytest.raises(PostDraftInvalidResponseError):
        await generator.generate(request())  # type: ignore[attr-defined]
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert script.count_calls == 1
    assert script.create_calls == 0


@pytest.mark.asyncio
async def test_fingerprint_mismatch_before_count_consumes_no_external_call() -> None:
    generator, owner, ledger = adapter(
        scenario=adapter_module().OfflineScriptScenario.FINGERPRINT_BEFORE_COUNT
    )
    with pytest.raises(PostDraftInvalidResponseError):
        await generator.generate(request())  # type: ignore[attr-defined]
    snapshot = await ledger.snapshot()
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert script.count_calls == 0
    assert script.create_calls == 0
    assert snapshot.generation_attempts == 1
    assert snapshot.external_calls == 0


@pytest.mark.asyncio
async def test_fingerprint_mismatch_before_create_consumes_only_count_call() -> None:
    generator, owner, ledger = adapter(
        scenario=adapter_module().OfflineScriptScenario.FINGERPRINT_BEFORE_CREATE
    )
    with pytest.raises(PostDraftInvalidResponseError):
        await generator.generate(request())  # type: ignore[attr-defined]
    snapshot = await ledger.snapshot()
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert script.count_calls == 1
    assert script.create_calls == 0
    assert snapshot.external_calls == 1


@pytest.mark.asyncio
async def test_generation_attempt_and_external_call_caps_are_independent() -> None:
    guard = ProcessGenerationGuard(
        generation_attempt_cap=1,
        external_call_cap=2,
        generation_reserved_cost_cap=Decimal(1),
    )
    await guard.consume_generation_attempt()
    with pytest.raises(ValueError, match="generation attempt"):
        await guard.consume_generation_attempt()
    await guard.consume_external_call()
    await guard.consume_external_call()
    with pytest.raises(ValueError, match="external call"):
        await guard.consume_external_call()


@pytest.mark.asyncio
async def test_generation_cap_is_atomic_for_concurrent_guard_reservations() -> None:
    guard = ProcessGenerationGuard(
        generation_attempt_cap=1,
        external_call_cap=2,
        generation_reserved_cost_cap=Decimal(10),
    )
    release = asyncio.Event()

    async def reserve() -> None:
        await release.wait()
        await guard.consume_generation_attempt()

    tasks = [asyncio.create_task(reserve()) for _ in range(2)]
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    snapshot = await guard.snapshot()
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert snapshot.generation_attempts == 1


@pytest.mark.asyncio
async def test_runtime_owner_shares_guard_and_serial_gate_across_generators() -> None:
    owner = adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner.for_offline_script(
        scenario=adapter_module().OfflineScriptScenario.COUNT_BLOCK,
        generation_attempt_cap=1,
        external_call_cap=2,
        generation_reserved_cost_cap=Decimal(10),
    )
    guard = owner.guard
    common = {
        "model": MODEL,
        "reasoning_effort": "none",
        "price_policy": price_policy(),
        "inner_timeout_seconds": 1,
        "configured_max_output_tokens": 2_048,
    }
    first = owner.create_generator(**common)
    second = owner.create_generator(**common)
    assert first._guard is second._guard is owner.guard  # type: ignore[attr-defined]
    assert first._runtime_owner.serial_gate is owner.serial_gate  # type: ignore[attr-defined]
    assert second._runtime_owner.serial_gate is owner.serial_gate  # type: ignore[attr-defined]

    first_task = asyncio.create_task(first.generate(request()))
    await owner.wait_for_offline_count_start()
    with pytest.raises(PostDraftUnavailableError):
        await second.generate(request())
    owner.release_offline_count()
    await first_task
    snapshot = await guard.snapshot()
    script = owner.offline_script_snapshot()
    assert script.maximum_active_provider_operations == 1
    assert script.count_calls == 1
    assert script.create_calls == 1
    assert snapshot.generation_attempts == 1
    assert snapshot.external_calls == 2

    with pytest.raises(PostDraftUnavailableError):
        await second.generate(request())
    assert owner.offline_script_snapshot().count_calls == 1

    await owner.close()
    with pytest.raises(RuntimeError, match="runtime is closing"):
        owner.create_generator(**common)
    with pytest.raises(PostDraftUnavailableError):
        await first.generate(request())


@pytest.mark.asyncio
async def test_shared_guard_external_call_cap_race_never_exceeds_cap() -> None:
    guard = ProcessGenerationGuard(
        generation_attempt_cap=2,
        external_call_cap=3,
        generation_reserved_cost_cap=Decimal(10),
    )
    release = asyncio.Event()

    async def reserve() -> None:
        await release.wait()
        await guard.consume_external_call()

    tasks = [asyncio.create_task(reserve()) for _ in range(4)]
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    snapshot = await guard.snapshot()
    assert sum(not isinstance(result, BaseException) for result in results) == 3
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert snapshot.external_calls == 3


@pytest.mark.asyncio
async def test_shared_guard_cost_cap_race_never_exceeds_cap() -> None:
    one_request_cost = Decimal("0.001124")
    guard = ProcessGenerationGuard(
        generation_attempt_cap=2,
        external_call_cap=3,
        generation_reserved_cost_cap=one_request_cost,
    )
    release = asyncio.Event()

    async def reserve() -> None:
        await release.wait()
        await guard.reserve_generation_cost(one_request_cost)

    tasks = [asyncio.create_task(reserve()) for _ in range(2)]
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    snapshot = await guard.snapshot()
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert snapshot.reserved_generation_cost == one_request_cost
    assert snapshot.reserved_generation_cost <= guard.generation_reserved_cost_cap


@pytest.mark.asyncio
async def test_guard_lock_wait_cancellation_does_not_consume_ledger() -> None:
    guard = ProcessGenerationGuard(
        generation_attempt_cap=1,
        external_call_cap=1,
        generation_reserved_cost_cap=Decimal(1),
    )
    await guard._reservation_lock.acquire()
    waiter = asyncio.create_task(guard.consume_generation_attempt())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    guard._reservation_lock.release()
    assert (await guard.snapshot()).generation_attempts == 0


@pytest.mark.asyncio
async def test_guard_reservation_is_not_refunded_after_cancellation() -> None:
    guard = ProcessGenerationGuard(
        generation_attempt_cap=1,
        external_call_cap=1,
        generation_reserved_cost_cap=Decimal(1),
    )
    await guard.consume_generation_attempt()
    cancelled = asyncio.create_task(asyncio.sleep(10))
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert (await guard.snapshot()).generation_attempts == 1


@pytest.mark.asyncio
async def test_guard_rejects_other_event_loop_without_ledger_change() -> None:
    guard = ProcessGenerationGuard(
        generation_attempt_cap=2,
        external_call_cap=2,
        generation_reserved_cost_cap=Decimal(2),
    )
    await guard.consume_generation_attempt()
    before = await guard.snapshot()
    observed: list[str] = []

    def run_other_loop() -> None:
        async def exercise() -> None:
            for operation in (guard.consume_external_call, guard.snapshot):
                try:
                    await operation()
                except RuntimeError as error:
                    observed.append(str(error))

        asyncio.run(exercise())

    thread = threading.Thread(target=run_other_loop)
    thread.start()
    thread.join()
    assert observed == [
        "generation guard event loop mismatch",
        "generation guard event loop mismatch",
    ]
    assert await guard.snapshot() == before
    await guard.consume_external_call()
    assert (await guard.snapshot()).external_calls == 1


@pytest.mark.asyncio
async def test_generation_cost_and_external_ledgers_are_not_refunded_on_create_failure() -> None:
    generator, owner, ledger = adapter(
        scenario=adapter_module().OfflineScriptScenario.CREATE_UNAVAILABLE
    )
    with pytest.raises(PostDraftUnavailableError):
        await generator.generate(request())  # type: ignore[attr-defined]
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert script.count_calls == 1
    assert script.create_calls == 1
    snapshot = await ledger.snapshot()
    assert snapshot.generation_attempts == 1
    assert snapshot.external_calls == 2
    assert snapshot.reserved_generation_cost > 0


def test_long_context_boundary_uses_uncached_prices_and_decimal_only() -> None:
    policy = price_policy(threshold=100)
    before = policy.reserved_generation_cost(input_tokens=99, max_output_tokens=10)
    exact = policy.reserved_generation_cost(input_tokens=100, max_output_tokens=10)
    after = policy.reserved_generation_cost(input_tokens=101, max_output_tokens=10)
    assert before == Decimal("0.000109")
    assert exact == Decimal("0.00011")
    assert after == Decimal("0.000217")
    assert after > exact
    assert all(isinstance(value, Decimal) for value in (before, exact, after))
    assert not hasattr(policy, "cached_input_price_per_million")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("input_price", "NaN"),
        ("input_price", "Infinity"),
        ("input_price", "-1"),
        ("output_price", "NaN"),
        ("input_multiplier", "0"),
        ("output_multiplier", "1e999"),
    ],
)
def test_nonfinite_negative_and_overflow_price_values_are_rejected(field: str, value: str) -> None:
    kwargs = {
        "input_price": "1",
        "output_price": "1",
        "input_multiplier": "2",
        "output_multiplier": "1.5",
    }
    kwargs[field] = value
    with pytest.raises(ValueError):
        price_policy(**kwargs)


@pytest.mark.parametrize(
    "source_url",
    [
        "http://developers.openai.com/pricing",
        "https://developers.openai.com/pricing?credential=private",
        "https://developers.openai.com/pricing#private",
        "https://platform.openai.com:443/pricing",
        "https://other.example/pricing",
    ],
)
def test_price_source_metadata_rejects_noncanonical_or_value_bearing_urls(
    source_url: str,
) -> None:
    with pytest.raises(ValueError, match="price source"):
        price_policy(source_url=source_url)


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("CREATE_TIMEOUT", TimeoutError),
        ("CREATE_UNAVAILABLE", PostDraftUnavailableError),
        ("CREATE_UNKNOWN", PostDraftUnknownError),
    ],
)
@pytest.mark.asyncio
async def test_provider_failures_are_single_call_and_non_reflecting(
    scenario: str,
    expected: type[BaseException],
    caplog: pytest.LogCaptureFixture,
) -> None:
    generator, owner, _ = adapter(
        scenario=getattr(adapter_module().OfflineScriptScenario, scenario)
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(expected) as caught:
        await generator.generate(request())  # type: ignore[attr-defined]
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert script.count_calls == 1
    assert script.create_calls == 1
    assert_detached_exception(caught.value, caplog.text)


@pytest.mark.asyncio
async def test_application_outer_timeout_remains_provider_neutral() -> None:
    generator, owner, _ = adapter(scenario=adapter_module().OfflineScriptScenario.COUNT_BLOCK)
    service = GeneratePostDraftService(generator=generator, timeout_seconds=0.001)
    with pytest.raises(PostDraftTimeoutError):
        await service.generate(request())
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert script.count_calls == 1
    assert script.create_calls == 0


@pytest.mark.asyncio
async def test_cancellation_propagates_without_retry() -> None:
    generator, owner, ledger = adapter(
        scenario=adapter_module().OfflineScriptScenario.COUNT_CANCELLED
    )
    with pytest.raises(asyncio.CancelledError):
        await generator.generate(request())  # type: ignore[attr-defined]
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert script.count_calls == 1
    assert script.create_calls == 0
    assert (await ledger.snapshot()).external_calls == 1


@pytest.mark.parametrize(
    "scenario",
    [
        "RESPONSE_EMPTY",
        "RESPONSE_WHITESPACE",
        "RESPONSE_NON_STRING",
        "RESPONSE_MENTION",
        "RESPONSE_TOO_LONG",
        "RESPONSE_BIDI",
    ],
)
@pytest.mark.asyncio
async def test_existing_response_domain_validation_is_preserved(scenario: str) -> None:
    generator, _, _ = adapter(scenario=getattr(adapter_module().OfflineScriptScenario, scenario))
    with pytest.raises(PostDraftInvalidResponseError):
        await generator.generate(request())  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "scenario",
    [
        "RESPONSE_INCOMPLETE",
        "RESPONSE_FAILED",
        "RESPONSE_CANCELLED",
        "RESPONSE_QUEUED",
        "RESPONSE_IN_PROGRESS",
    ],
)
@pytest.mark.asyncio
async def test_noncompleted_response_is_rejected(scenario: str) -> None:
    generator, _, _ = adapter(scenario=getattr(adapter_module().OfflineScriptScenario, scenario))
    with pytest.raises(PostDraftInvalidResponseError):
        await generator.generate(request())  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_refusal_multiple_messages_and_multiple_text_are_rejected() -> None:
    for scenario in (
        adapter_module().OfflineScriptScenario.RESPONSE_REFUSAL,
        adapter_module().OfflineScriptScenario.RESPONSE_MULTIPLE_MESSAGES,
        adapter_module().OfflineScriptScenario.RESPONSE_MULTIPLE_TEXT,
    ):
        generator, _, _ = adapter(scenario=scenario)
        with pytest.raises(PostDraftInvalidResponseError):
            await generator.generate(request())  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_scripted_client_close_starts_once_and_borrowed_factory_is_absent() -> None:
    generator, owner, _ = adapter()
    await generator.generate(request())  # type: ignore[attr-defined]
    assert await generator.close() is True  # type: ignore[attr-defined]
    assert await generator.close() is True  # type: ignore[attr-defined]
    assert owner.offline_script_snapshot().close_calls == 1  # type: ignore[attr-defined]
    assert not hasattr(
        adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner,
        "for_borrowed_client",
    )


@pytest.mark.asyncio
async def test_close_failure_never_overwrites_primary_failure() -> None:
    generator, owner, _ = adapter(
        scenario=adapter_module().OfflineScriptScenario.CREATE_UNAVAILABLE_CLOSE_FAILURE
    )
    primary: BaseException | None = None
    try:
        await generator.generate(request())  # type: ignore[attr-defined]
    except BaseException as error:  # noqa: BLE001 - verifying preserved primary
        primary = error
    close_result = await generator.close()  # type: ignore[attr-defined]
    assert isinstance(primary, PostDraftUnavailableError)
    assert close_result is False
    assert str(primary) == "unavailable"
    assert "close-private" not in str(primary)
    assert owner.offline_script_snapshot().close_calls == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cancellation_during_close_finishes_owned_cleanup_then_rethrows() -> None:
    generator, owner, _ = adapter(scenario=adapter_module().OfflineScriptScenario.CLOSE_BLOCK)
    task = asyncio.create_task(generator.close())  # type: ignore[attr-defined]
    while owner.offline_script_snapshot().close_calls == 0:  # type: ignore[attr-defined]
        await asyncio.sleep(0)
    task.cancel()
    owner.release_offline_close()  # type: ignore[attr-defined]
    with pytest.raises(asyncio.CancelledError):
        await task
    assert owner.offline_script_snapshot().close_calls == 1  # type: ignore[attr-defined]


def test_runtime_owner_cancellation_identity_is_preserved() -> None:
    original = asyncio.CancelledError("fixed-cancellation")
    with pytest.raises(asyncio.CancelledError) as raised:
        adapter_module().OfflineScriptedOpenAIPostDraftRuntimeOwner._raise_preserved_cancellation(
            original
        )
    assert raised.value is original


@pytest.mark.asyncio
async def test_cooperative_owned_close_is_shared_and_started_once() -> None:
    generator, owner, _ = adapter(scenario=adapter_module().OfflineScriptScenario.CLOSE_BLOCK)
    first = asyncio.create_task(generator.close())  # type: ignore[attr-defined]
    second = asyncio.create_task(generator.close())  # type: ignore[attr-defined]
    while owner.offline_script_snapshot().close_calls == 0:  # type: ignore[attr-defined]
        await asyncio.sleep(0)
    assert not first.done() and not second.done()
    owner.release_offline_close()  # type: ignore[attr-defined]
    assert await asyncio.gather(first, second) == [True, True]
    assert owner.offline_script_snapshot().close_calls == 1  # type: ignore[attr-defined]
    assert owner.close_start_count == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_closed_generator_fails_before_external_call() -> None:
    generator, owner, _ = adapter()
    await generator.close()  # type: ignore[attr-defined]
    with pytest.raises(PostDraftUnavailableError):
        await generator.generate(request())  # type: ignore[attr-defined]
    script = owner.offline_script_snapshot()  # type: ignore[attr-defined]
    assert script.count_calls == 0
    assert script.create_calls == 0


def test_offline_harness_runs_fake_serial_flow_without_network(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[str] = []

    def forbidden(name: str):
        def reject(*_args: object, **_kwargs: object) -> object:
            attempts.append(name)
            raise AssertionError(f"offline network escape: {name}")

        return reject

    monkeypatch.setattr(socket, "create_connection", forbidden("socket.create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", forbidden("socket.getaddrinfo"))
    monkeypatch.setattr(socket.socket, "connect", forbidden("socket.connect"))
    monkeypatch.setattr(asyncio, "open_connection", forbidden("asyncio.open_connection"))
    monkeypatch.setattr(httpx, "AsyncClient", forbidden("httpx.AsyncClient"))
    monkeypatch.setattr(httpx, "Client", forbidden("httpx.Client"))
    monkeypatch.setattr(subprocess, "run", forbidden("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", forbidden("subprocess.Popen"))
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(
            AsyncOpenAI=forbidden("openai.AsyncOpenAI"),
            OpenAI=forbidden("openai.OpenAI"),
        ),
    )
    assert harness_main(["--self-test", "--model", MODEL]) == 0
    output = capsys.readouterr().out
    assert "OFFLINE_SELF_TEST=PASS" in output
    assert "SYNTHETIC_COUNT_CALLS=1" in output
    assert "SYNTHETIC_CREATE_CALLS=1" in output
    assert "CLIENT_CLOSE_CALLS=1" in output
    assert "NETWORK_CONNECTION_COUNT=0" in output
    assert "API_REQUEST_COUNT=0" in output
    assert attempts == []

    with pytest.raises(AssertionError, match="offline network escape"):
        socket.create_connection(("synthetic.invalid", 1))
    assert attempts == ["socket.create_connection"]


def test_live_harness_is_unconditionally_blocked_without_authorization(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert harness_main(["--live", "--model", MODEL]) == 2
    output = capsys.readouterr().out
    assert "LIVE_READINESS_GATE=CLOSED" in output
    assert "production_effective_gate_closed" in output
    assert "client_shutdown_strategy_approved" in output
    assert "NETWORK_CONNECTION_COUNT=0" in output
    assert "API_REQUEST_COUNT=0" in output
    assert API_KEY_CANARY not in output
    assert PROMPT_CANARY not in output


def test_no_retry_fallback_batch_parallel_or_structured_output_contract() -> None:
    built = plan()
    create = built.create_payload()
    assert built.parallel_tool_calls is False
    assert built.tools == ()
    assert built.text is None
    assert "background" not in create
    assert "stream" not in create
    assert "previous_response_id" not in create
    source = (
        __import__("pathlib")
        .Path("src/discord_ai_reminder_bot/infrastructure/ai/openai_post_draft_generator.py")
        .read_text(encoding="utf-8")
    )
    assert adapter_module().SDK_MAX_RETRIES == 0
    assert "SDK_MAX_RETRIES: Final = 0" in source
    assert "fallback" not in source.lower()
    assert "batch" not in source.lower()


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -1.0, 0.0])
def test_invalid_cost_cap_is_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        ProcessGenerationGuard(
            generation_attempt_cap=1,
            external_call_cap=2,
            generation_reserved_cost_cap=Decimal(str(value)),
        )
