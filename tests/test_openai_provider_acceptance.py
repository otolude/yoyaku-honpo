from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from discord_ai_reminder_bot.application.name_generation import (
    NameGeneratorError,
    NameGeneratorUnavailableError,
)
from discord_ai_reminder_bot.domain.name_generation import (
    GeneratedScheduleName,
    NameGenerationRequest,
)
from discord_ai_reminder_bot.infrastructure.ai import acceptance

FAKE_KEY = "sk-" + "test-provider-acceptance-key-canary-not-real"


class FakeGenerator:
    def __init__(self, result: str | BaseException = "合成予定名") -> None:
        self.result = result
        self.requests: list[str] = []
        self.close_calls = 0

    async def generate(self, request: NameGenerationRequest) -> GeneratedScheduleName:
        content = request.content
        self.requests.append(content)
        if isinstance(self.result, BaseException):
            raise self.result
        return GeneratedScheduleName(self.result)

    async def close(self) -> None:
        self.close_calls += 1


def live_arguments(plan: acceptance.AcceptancePlan) -> argparse.Namespace:
    return argparse.Namespace(
        live=True,
        dry_run=False,
        provider=acceptance.PROVIDER,
        target=acceptance.TARGET,
        models=",".join(plan.model_names),
        max_requests=plan.maximum_request_count,
        max_jpy_microunits=plan.maximum_cost_microunits,
        confirm=plan.confirmation,
    )


def test_no_arguments_and_dry_run_never_read_key_or_construct_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    constructed = False

    def forbidden(*args: object, **kwargs: object) -> None:
        nonlocal constructed
        constructed = True
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(acceptance, "_build_live_generator", forbidden)
    monkeypatch.setenv(acceptance.ACCEPTANCE_API_KEY_ENV, FAKE_KEY)
    assert acceptance.main([]) == 0
    assert acceptance.main(["--dry-run"]) == 0
    assert not constructed
    output = capsys.readouterr().out
    assert "live communication disabled" in output
    assert FAKE_KEY not in output
    assert "api_key" not in output.lower()


def test_help_does_not_construct_client(monkeypatch: pytest.MonkeyPatch) -> None:
    constructor = AsyncMock(side_effect=AssertionError("client construction forbidden"))
    monkeypatch.setattr(acceptance, "_build_live_generator", constructor)
    with pytest.raises(SystemExit) as captured:
        acceptance.main(["--help"])
    assert captured.value.code == 0
    constructor.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("provider", None, "invalid_acceptance_target"),
        ("target", None, "invalid_acceptance_target"),
        ("models", None, "models_must_be_explicit"),
        ("max_requests", 13, "maximum_requests_mismatch"),
        ("max_requests", 1, "maximum_requests_mismatch"),
        ("max_jpy_microunits", 4_006_801, "maximum_cost_mismatch"),
        ("max_jpy_microunits", 1, "maximum_cost_mismatch"),
        ("confirm", "yes", "confirmation_mismatch"),
        ("confirm", " OPENAI", "confirmation_mismatch"),
    ],
)
def test_live_requires_every_exact_gate(field: str, value: object, code: str) -> None:
    plan = acceptance.build_plan(acceptance.ALLOWED_MODELS)
    arguments = live_arguments(plan)
    setattr(arguments, field, value)
    with pytest.raises(acceptance.AcceptanceFailure, match=code):
        acceptance.validate_live_arguments(arguments, plan)


def test_confirmation_binds_provider_models_requests_cost_and_live() -> None:
    plan = acceptance.build_plan(acceptance.ALLOWED_MODELS)
    assert plan.confirmation == (
        f"openai:gpt-5.6-luna,gpt-5.4-nano-2026-03-17:"
        f"requests={len(acceptance.SYNTHETIC_CASES) * 2}:"
        f"max_jpy_microunits={plan.maximum_cost_microunits}:live"
    )
    assert all(character.isalnum() or character in ".,:=_-" for character in plan.confirmation)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " gpt-5.6-luna",
        "gpt-5.6-luna ",
        "gpt-5-nano",
        "gpt-5.4-nano",
        "unknown",
        "gpt-5.6-luna,gpt-5.6-luna",
        "gpt-5.4-nano-2026-03-17,gpt-5.6-luna",
    ],
)
def test_models_reject_empty_deprecated_unpinned_unknown_and_duplicates(raw: str) -> None:
    with pytest.raises(acceptance.AcceptanceFailure):
        acceptance.parse_models(raw)


def test_single_explicit_model_has_six_requests_and_audited_cost() -> None:
    plan = acceptance.build_plan((acceptance.LUNA_ALIAS,))
    assert plan.maximum_request_count == len(acceptance.SYNTHETIC_CASES) == 6
    assert plan.models[0].request_count == 6
    assert plan.models[0].cost_per_request_microunits > 0
    assert plan.maximum_cost_microunits == (plan.models[0].cost_per_request_microunits * 6)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.openai.com/v1",
        "https://example.com/v1",
        "https://user@api.openai.com/v1",
        "https://api.openai.com:444/v1",
        "https://api.openai.com/v1?query=yes",
        "https://api.openai.com/v1#fragment",
        "https://api.openai.com/v1/extra",
        "https://api.openai.com\\@example.com/v1",
    ],
)
def test_endpoint_accepts_only_exact_official_url(endpoint: str) -> None:
    with pytest.raises(acceptance.AcceptanceFailure, match="invalid_endpoint"):
        acceptance.validate_official_endpoint(endpoint)
    assert (
        acceptance.validate_official_endpoint(acceptance.OFFICIAL_OPENAI_ENDPOINT)
        == acceptance.OFFICIAL_OPENAI_ENDPOINT
    )


@pytest.mark.parametrize(
    "value", [None, "", " short", "short ", "short", "valid-key-but-control\nvalue"]
)
def test_acceptance_key_is_process_only_and_strict(value: str | None) -> None:
    environ = {} if value is None else {acceptance.ACCEPTANCE_API_KEY_ENV: value}
    with pytest.raises(acceptance.AcceptanceFailure, match="api_key_unavailable"):
        acceptance.validate_api_key(environ)
    assert acceptance.validate_api_key({acceptance.ACCEPTANCE_API_KEY_ENV: FAKE_KEY}) == FAKE_KEY


def test_dotenv_is_not_a_key_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".env").write_text(
        f"{acceptance.ACCEPTANCE_API_KEY_ENV}={FAKE_KEY}\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(acceptance.ACCEPTANCE_API_KEY_ENV, raising=False)
    plan = acceptance.build_plan((acceptance.LUNA_ALIAS,))
    arguments = [
        "--live",
        "--provider",
        acceptance.PROVIDER,
        "--target",
        acceptance.TARGET,
        "--models",
        acceptance.LUNA_ALIAS,
        "--max-requests",
        str(plan.maximum_request_count),
        "--max-jpy-microunits",
        str(plan.maximum_cost_microunits),
        "--confirm",
        plan.confirmation,
    ]
    assert acceptance.main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=acceptance_api_key_unavailable\n"
    assert FAKE_KEY not in captured.err


def test_parser_has_no_api_key_or_endpoint_argument() -> None:
    destinations = {action.dest for action in acceptance.build_parser()._actions}
    assert "api_key" not in destinations
    assert "endpoint" not in destinations
    assert "base_url" not in destinations


@pytest.mark.asyncio
async def test_library_live_entry_and_private_executor_cannot_bypass_guards() -> None:
    plan = acceptance.build_plan((acceptance.LUNA_ALIAS,))
    factory = AsyncMock(side_effect=AssertionError("client construction forbidden"))
    arguments = live_arguments(plan)
    arguments.confirm = "wrong"
    with pytest.raises(acceptance.AcceptanceFailure, match="confirmation_mismatch"):
        await acceptance.execute_live(
            arguments,
            {acceptance.ACCEPTANCE_API_KEY_ENV: FAKE_KEY},
            generator_factory=factory,
        )
    with pytest.raises(acceptance.AcceptanceFailure, match="authorization_required"):
        await acceptance._execute_authorized(object(), generator_factory=factory)
    factory.assert_not_called()


@pytest.mark.parametrize(("fails", "expected"), [(False, 0), (True, 1)])
@pytest.mark.asyncio
async def test_live_return_codes_distinguish_success_and_execution_failure(
    fails: bool,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = acceptance.build_plan((acceptance.LUNA_ALIAS,))
    authorization = acceptance._authorize_live(
        live_arguments(plan), {acceptance.ACCEPTANCE_API_KEY_ENV: FAKE_KEY}
    )
    monkeypatch.setattr(acceptance, "_authorize_live", lambda arguments, environ: authorization)

    async def executor(
        authorized: object, *, consumption: acceptance.Consumption
    ) -> acceptance.Consumption:
        assert authorized is authorization
        if fails:
            raise acceptance.AcceptanceFailure("live_run_stopped")
        return consumption

    monkeypatch.setattr(acceptance, "_execute_authorized", executor)
    assert await acceptance._run_live(live_arguments(plan)) == expected
    captured = capsys.readouterr()
    assert "summary requests=0 reserved_jpy_microunits=0" in captured.out
    assert ("error=live_run_stopped" in captured.err) is fails
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--dry-run", "--provider", "other"],
        ["--dry-run", "--target", "development"],
        ["--dry-run", "--max-requests", "12"],
        ["--dry-run", "--confirm", "yes"],
    ],
)
def test_dry_run_rejects_invalid_provider_target_and_live_only_flags(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert acceptance.main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error=")


@pytest.mark.asyncio
async def test_live_run_is_sequential_same_cases_and_closes_each_model() -> None:
    plan = acceptance.build_plan(acceptance.ALLOWED_MODELS)
    generators: dict[str, FakeGenerator] = {}
    active = 0
    maximum_active = 0

    async def factory(model: str) -> FakeGenerator:
        generator = FakeGenerator()
        original = generator.generate

        async def tracked(request: object) -> GeneratedScheduleName:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            try:
                await asyncio.sleep(0)
                return await original(request)
            finally:
                active -= 1

        generator.generate = tracked  # type: ignore[method-assign]
        generators[model] = generator
        return generator

    output: list[str] = []
    consumption = await acceptance.execute_live(
        live_arguments(plan),
        {acceptance.ACCEPTANCE_API_KEY_ENV: FAKE_KEY},
        output=output.append,
        generator_factory=factory,
    )
    expected_contents = [case.content for case in acceptance.SYNTHETIC_CASES]
    assert maximum_active == 1
    assert consumption.requests == plan.maximum_request_count
    assert consumption.cost_microunits == plan.maximum_cost_microunits
    assert set(generators) == set(acceptance.ALLOWED_MODELS)
    for generator in generators.values():
        assert generator.requests == expected_contents
        assert generator.close_calls == 1
    assert all("合成予定名" in line for line in output)
    assert all(case.content not in "\n".join(output) for case in acceptance.SYNTHETIC_CASES)
    assert FAKE_KEY not in "\n".join(output)


@pytest.mark.asyncio
async def test_failure_consumes_request_and_cost_stops_without_fallback() -> None:
    plan = acceptance.build_plan(acceptance.ALLOWED_MODELS)
    generator = FakeGenerator(NameGeneratorUnavailableError("private-exception-canary"))
    factory_calls: list[str] = []

    async def factory(model: str) -> FakeGenerator:
        factory_calls.append(model)
        return generator

    output: list[str] = []
    consumption = acceptance.Consumption()
    with pytest.raises(acceptance.AcceptanceFailure, match="live_run_stopped"):
        await acceptance.execute_live(
            live_arguments(plan),
            {acceptance.ACCEPTANCE_API_KEY_ENV: FAKE_KEY},
            output=output.append,
            generator_factory=factory,
            consumption=consumption,
        )
    assert consumption.requests == 1
    assert consumption.cost_microunits == plan.models[0].cost_per_request_microunits
    assert factory_calls == [acceptance.LUNA_ALIAS]
    assert generator.close_calls == 1
    serialized = "\n".join(output)
    assert "status=generator_unavailable" in serialized
    assert "private-exception-canary" not in serialized
    assert all(case.content not in serialized for case in acceptance.SYNTHETIC_CASES)


@pytest.mark.asyncio
async def test_generator_initialization_failure_exposes_only_fixed_code() -> None:
    plan = acceptance.build_plan((acceptance.LUNA_ALIAS,))

    async def factory(model: str) -> FakeGenerator:
        del model
        raise RuntimeError("private-initialization-canary")

    with pytest.raises(
        acceptance.AcceptanceFailure, match="generator_initialization_failed"
    ) as captured:
        await acceptance.execute_live(
            live_arguments(plan),
            {acceptance.ACCEPTANCE_API_KEY_ENV: FAKE_KEY},
            generator_factory=factory,
        )
    assert "private-initialization-canary" not in str(captured.value)


@pytest.mark.asyncio
async def test_cancel_closes_client_and_does_not_run_remaining_cases() -> None:
    plan = acceptance.build_plan((acceptance.LUNA_ALIAS,))
    started = asyncio.Event()
    blocker = asyncio.Event()
    generator = FakeGenerator()

    async def generate(request: NameGenerationRequest) -> GeneratedScheduleName:
        generator.requests.append(request.content)
        started.set()
        await blocker.wait()
        return GeneratedScheduleName("到達しない")

    generator.generate = generate  # type: ignore[method-assign]

    async def factory(model: str) -> FakeGenerator:
        assert model == acceptance.LUNA_ALIAS
        return generator

    task = asyncio.create_task(
        acceptance.execute_live(
            live_arguments(plan),
            {acceptance.ACCEPTANCE_API_KEY_ENV: FAKE_KEY},
            generator_factory=factory,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(generator.requests) == 1
    assert generator.close_calls == 1


def sdk_response(name: str = "SDK合成名") -> dict[str, object]:
    return {
        "id": "provider-id-canary",
        "created_at": 0,
        "model": acceptance.LUNA_ALIAS,
        "object": "response",
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        "output": [
            {
                "id": "message-canary",
                "content": [
                    {
                        "annotations": [],
                        "text": json.dumps({"name": name}),
                        "type": "output_text",
                    }
                ],
                "role": "assistant",
                "status": "completed",
                "type": "message",
            }
        ],
    }


@pytest.mark.asyncio
async def test_actual_sdk_mock_transport_uses_official_endpoint_without_redirects() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json=sdk_response())

    authorization = acceptance._authorize_live(
        live_arguments(acceptance.build_plan((acceptance.LUNA_ALIAS,))),
        {acceptance.ACCEPTANCE_API_KEY_ENV: FAKE_KEY},
    )
    adapter = await acceptance._build_live_generator(
        authorization,
        model=acceptance.LUNA_ALIAS,
        transport=httpx.MockTransport(handler),
    )
    generated = await adapter.generate(
        acceptance.NameGenerationRequest(content=acceptance.SYNTHETIC_CASES[0].content)
    )
    assert generated.value == "SDK合成名"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    body = json.loads(request.content)
    assert body["store"] is False
    assert body["input"] == acceptance.SYNTHETIC_CASES[0].content
    assert "tools" not in body
    assert "background" not in body
    assert "conversation" not in body
    await adapter.close()
    await adapter.close()


@pytest.mark.asyncio
async def test_redirect_is_not_followed_and_request_is_not_retried() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            307, request=request, headers={"location": "https://example.com/steal"}
        )

    authorization = acceptance._authorize_live(
        live_arguments(acceptance.build_plan((acceptance.LUNA_ALIAS,))),
        {acceptance.ACCEPTANCE_API_KEY_ENV: FAKE_KEY},
    )
    adapter = await acceptance._build_live_generator(
        authorization,
        model=acceptance.LUNA_ALIAS,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(NameGeneratorError):
        await adapter.generate(
            acceptance.NameGenerationRequest(content=acceptance.SYNTHETIC_CASES[0].content)
        )
    assert requests == 1
    await adapter.close()


def test_synthetic_cases_are_fixed_non_identifying_and_not_external_input() -> None:
    assert [case.case_id for case in acceptance.SYNTHETIC_CASES] == [
        "once",
        "daily",
        "weekly",
        "emoji",
        "mention-like",
        "injection-like",
    ]
    serialized = repr(acceptance.SYNTHETIC_CASES)
    for forbidden in (
        "discord.gg",
        "https://",
        "guild",
        "channel",
        "user_id",
        "schedule_id",
        "contract",
    ):
        assert forbidden not in serialized


def test_normal_bot_composition_does_not_import_acceptance_runner() -> None:
    source = Path("src/discord_ai_reminder_bot/__main__.py").read_text(encoding="utf-8")
    worker = Path("src/discord_ai_reminder_bot/application/name_generation_worker.py").read_text(
        encoding="utf-8"
    )
    assert "infrastructure.ai.acceptance" not in source
    assert "infrastructure.ai.acceptance" not in worker
