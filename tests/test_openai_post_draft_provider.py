from __future__ import annotations

import ast
import asyncio
import importlib
import logging
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from discord_ai_reminder_bot.application.post_draft_generation import (
    GeneratePostDraftService,
    PostDraftErrorCode,
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

CONFIG_MODULE = "discord_ai_reminder_bot.post_draft_provider_config"
ADAPTER_MODULE = "discord_ai_reminder_bot.infrastructure.ai.openai_post_draft_generator"
API_KEY_CANARY = "sk-synthetic-post-draft-secret-canary"
MODEL_CANARY = "synthetic-model-canary"
EXCEPTION_CHAIN_CANARY = "provider-exception-chain-private-canary"
ENV_KEYS = (
    "AI_POST_DRAFT_OPENAI_API_KEY",
    "AI_POST_DRAFT_OPENAI_MODEL",
    "AI_POST_DRAFT_OPENAI_TIMEOUT_SECONDS",
)


def config_module():
    return importlib.import_module(CONFIG_MODULE)


def adapter_module():
    return importlib.import_module(ADAPTER_MODULE)


def load(monkeypatch: pytest.MonkeyPatch, **values: str):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return config_module().load_openai_post_draft_provider_settings(env_file=None)


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
        status=status, output=[SimpleNamespace(type="reasoning"), message], output_text=text
    )


def adapter(result: object | BaseException | None = None):
    if result is None:
        result = response()
    create = AsyncMock(side_effect=result if isinstance(result, BaseException) else None)
    if not isinstance(result, BaseException):
        create.return_value = result
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    errors = adapter_module().OpenAIPostDraftErrorTypes(
        timeout=(TimeoutCanary,),
        unavailable=(ConnectionCanary, StatusCanary),
    )
    return (
        adapter_module().OpenAIPostDraftGenerator(
            client=client,
            model=MODEL_CANARY,
            error_types=errors,
        ),
        create,
    )


class TimeoutCanary(Exception):
    pass


class ConnectionCanary(Exception):
    pass


class StatusCanary(Exception):
    pass


def provider_exception(error_type: type[Exception]) -> Exception:
    error = error_type(EXCEPTION_CHAIN_CANARY)
    error.request = {"credential": EXCEPTION_CHAIN_CANARY}  # type: ignore[attr-defined]
    error.response = {"body": EXCEPTION_CHAIN_CANARY}  # type: ignore[attr-defined]
    return error


def assert_detached_exception(error: BaseException, caplog: str) -> None:
    pending: list[BaseException] = [error]
    observed: list[str] = [caplog]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        observed.extend(
            (
                str(current),
                repr(current),
                repr(current.args),
                repr(vars(current)),
                "".join(traceback.format_exception(current)),
            )
        )
        assert current.__cause__ is None
        assert current.__context__ is None
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    assert EXCEPTION_CHAIN_CANARY not in " ".join(observed)


def test_all_provider_settings_unset_is_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    result = load(monkeypatch)
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.UNCONFIGURED
    assert result.settings is None


@pytest.mark.parametrize("missing", ENV_KEYS)
def test_partial_provider_settings_are_invalid(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    values = {
        "AI_POST_DRAFT_OPENAI_API_KEY": API_KEY_CANARY,
        "AI_POST_DRAFT_OPENAI_MODEL": MODEL_CANARY,
        "AI_POST_DRAFT_OPENAI_TIMEOUT_SECONDS": "4.5",
    }
    del values[missing]
    result = load(monkeypatch, **values)
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.INVALID
    assert result.settings is None


def test_synthetic_provider_settings_are_only_syntactically_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = load(
        monkeypatch,
        AI_POST_DRAFT_OPENAI_API_KEY=API_KEY_CANARY,
        AI_POST_DRAFT_OPENAI_MODEL=MODEL_CANARY,
        AI_POST_DRAFT_OPENAI_TIMEOUT_SECONDS="4.5",
    )
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.CONFIGURED
    assert result.settings.timeout_seconds == 4.5
    assert not hasattr(result, "available")
    assert not hasattr(result, "accepted_model")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AI_POST_DRAFT_OPENAI_API_KEY", ""),
        ("AI_POST_DRAFT_OPENAI_API_KEY", "   "),
        ("AI_POST_DRAFT_OPENAI_API_KEY", "placeholder"),
        ("AI_POST_DRAFT_OPENAI_MODEL", ""),
        ("AI_POST_DRAFT_OPENAI_MODEL", " model"),
        ("AI_POST_DRAFT_OPENAI_MODEL", "model "),
        ("AI_POST_DRAFT_OPENAI_TIMEOUT_SECONDS", "0"),
        ("AI_POST_DRAFT_OPENAI_TIMEOUT_SECONDS", "-1"),
        ("AI_POST_DRAFT_OPENAI_TIMEOUT_SECONDS", "nan"),
        ("AI_POST_DRAFT_OPENAI_TIMEOUT_SECONDS", "inf"),
        ("AI_POST_DRAFT_OPENAI_TIMEOUT_SECONDS", " true "),
    ],
)
def test_invalid_provider_settings_fail_closed(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    values = {
        "AI_POST_DRAFT_OPENAI_API_KEY": API_KEY_CANARY,
        "AI_POST_DRAFT_OPENAI_MODEL": MODEL_CANARY,
        "AI_POST_DRAFT_OPENAI_TIMEOUT_SECONDS": "4.5",
    }
    values[key] = value
    result = load(monkeypatch, **values)
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.INVALID
    assert result.settings is None


def test_provider_setting_canaries_are_absent_from_repr_result_and_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        result = load(
            monkeypatch,
            AI_POST_DRAFT_OPENAI_API_KEY=API_KEY_CANARY,
            AI_POST_DRAFT_OPENAI_MODEL=f" {MODEL_CANARY}",
            AI_POST_DRAFT_OPENAI_TIMEOUT_SECONDS="4",
        )
    observed = " ".join((repr(result), str(result), caplog.text))
    assert result.state is config_module().OpenAIPostDraftProviderSettingsState.INVALID
    assert API_KEY_CANARY not in observed
    assert MODEL_CANARY not in observed
    assert caplog.text == ""


@pytest.mark.parametrize(
    ("length", "expected_tokens"),
    [(PostLength.SHORT, 512), (PostLength.STANDARD, 1024), (PostLength.LONG, 2048)],
)
@pytest.mark.asyncio
async def test_responses_create_exact_safe_arguments(
    length: PostLength, expected_tokens: int
) -> None:
    generator, create = adapter()
    generated = await generator.generate(request(length))
    assert generated.value == "案内本文"
    create.assert_awaited_once()
    sent = create.await_args.kwargs
    assert set(sent) == {"model", "instructions", "input", "max_output_tokens", "store"}
    assert sent["store"] is False
    assert sent["max_output_tokens"] == expected_tokens
    assert sent["model"] == MODEL_CANARY
    serialized = repr(sent)
    for forbidden in (
        "user_id",
        "guild_id",
        "channel_id",
        "interaction_id",
        "schedule_id",
        "operation_key",
        "database",
        "credential",
        "bucket",
        "cost",
        "repository",
        "/home/",
    ):
        assert forbidden not in serialized
    assert "purpose" in serialized
    assert "key_points" in serialized
    assert "polite" in serialized
    assert length.value in serialized
    assert "ja-JP" in serialized


@pytest.mark.asyncio
async def test_output_text_helper_is_used_without_assuming_first_output() -> None:
    generator, _ = adapter(response("先頭reasoning後の本文"))
    assert (await generator.generate(request())).value == "先頭reasoning後の本文"


@pytest.mark.parametrize("text", ["", "   ", None, 123, "@everyone", "x" * 2001, "bad\u202etext"])
@pytest.mark.asyncio
async def test_invalid_or_domain_rejected_output_is_fixed(text: object) -> None:
    generator, _ = adapter(response(text))
    with pytest.raises(PostDraftInvalidResponseError):
        await generator.generate(request())


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "queued", "in_progress"])
@pytest.mark.asyncio
async def test_noncompleted_status_is_rejected(status: str) -> None:
    generator, _ = adapter(response("本文", status=status))
    with pytest.raises(PostDraftInvalidResponseError):
        await generator.generate(request())


@pytest.mark.asyncio
async def test_refusal_and_ambiguous_multiple_outputs_are_rejected() -> None:
    refusal = SimpleNamespace(type="refusal", refusal="private-refusal")
    message = SimpleNamespace(type="message", content=[refusal])
    generator, _ = adapter(SimpleNamespace(status="completed", output=[message], output_text=""))
    with pytest.raises(PostDraftInvalidResponseError):
        await generator.generate(request())
    multiple = response("本文")
    multiple.output.append(SimpleNamespace(type="message", content=[]))
    generator, _ = adapter(multiple)
    with pytest.raises(PostDraftInvalidResponseError):
        await generator.generate(request())


@pytest.mark.parametrize(
    "error", [TimeoutCanary("private"), ConnectionCanary("private"), StatusCanary("private")]
)
@pytest.mark.asyncio
async def test_provider_errors_are_fixed_without_retry_or_detail(error: BaseException) -> None:
    generator, create = adapter(error)
    expected = TimeoutError if isinstance(error, TimeoutCanary) else PostDraftUnavailableError
    with pytest.raises(expected) as caught:
        await generator.generate(request())
    assert create.await_count == 1
    assert "private" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.asyncio
async def test_cancellation_is_rethrown_without_retry() -> None:
    generator, create = adapter(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await generator.generate(request())
    assert create.await_count == 1


@pytest.mark.parametrize(
    ("provider_error", "expected"),
    [
        (provider_exception(TimeoutCanary), TimeoutError),
        (provider_exception(ConnectionCanary), PostDraftUnavailableError),
        (provider_exception(StatusCanary), PostDraftUnavailableError),
        (provider_exception(RuntimeError), PostDraftUnknownError),
    ],
)
@pytest.mark.asyncio
async def test_adapter_detaches_provider_exception_chain(
    provider_error: BaseException,
    expected: type[BaseException],
    caplog: pytest.LogCaptureFixture,
) -> None:
    generator, create = adapter(provider_error)
    with caplog.at_level(logging.DEBUG), pytest.raises(expected) as caught:
        await generator.generate(request())
    assert create.await_count == 1
    assert_detached_exception(caught.value, caplog.text)


@pytest.mark.parametrize(
    ("provider_error", "expected", "code"),
    [
        (provider_exception(TimeoutCanary), PostDraftTimeoutError, PostDraftErrorCode.TIMEOUT),
        (
            provider_exception(ConnectionCanary),
            PostDraftUnavailableError,
            PostDraftErrorCode.UNAVAILABLE,
        ),
        (
            provider_exception(StatusCanary),
            PostDraftUnavailableError,
            PostDraftErrorCode.UNAVAILABLE,
        ),
        (provider_exception(RuntimeError), PostDraftUnknownError, PostDraftErrorCode.UNKNOWN),
    ],
)
@pytest.mark.asyncio
async def test_application_detaches_provider_exception_chain_and_preserves_code(
    provider_error: BaseException,
    expected: type[BaseException],
    code: PostDraftErrorCode,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generator, create = adapter(provider_error)
    service = GeneratePostDraftService(generator=generator, timeout_seconds=1)
    with caplog.at_level(logging.DEBUG), pytest.raises(expected) as caught:
        await service.generate(request())
    assert caught.value.code is code
    assert create.await_count == 1
    assert_detached_exception(caught.value, caplog.text)


@pytest.mark.asyncio
async def test_invalid_response_context_is_detached_through_application(
    caplog: pytest.LogCaptureFixture,
) -> None:
    generator, create = adapter(response(f"{EXCEPTION_CHAIN_CANARY}@everyone"))
    service = GeneratePostDraftService(generator=generator, timeout_seconds=1)
    with caplog.at_level(logging.DEBUG), pytest.raises(PostDraftInvalidResponseError) as caught:
        await service.generate(request())
    assert caught.value.code is PostDraftErrorCode.INVALID_RESPONSE
    assert create.await_count == 1
    assert_detached_exception(caught.value, caplog.text)


@pytest.mark.asyncio
async def test_application_rethrows_same_cancellation_object() -> None:
    cancellation = asyncio.CancelledError()
    generator, create = adapter(cancellation)
    service = GeneratePostDraftService(generator=generator, timeout_seconds=1)
    with pytest.raises(asyncio.CancelledError) as caught:
        await service.generate(request())
    assert caught.value is cancellation
    assert create.await_count == 1


def _imports(source: str) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(f"{'.' * node.level}{node.module or ''}")
    return result


@pytest.mark.parametrize(
    "source",
    [
        "import discord",
        "import sqlalchemy as db",
        "from discord.ext import commands",
        "from .database import models",
        "from ..bot import client as bot",
    ],
)
def test_import_guard_rejects_absolute_relative_and_alias_canaries(source: str) -> None:
    allowed = {"__future__", "asyncio", "dataclasses", "json", "math", "re", "typing"}
    assert not _imports(source) <= allowed


@pytest.mark.parametrize(
    "source", ["# import discord", '"""from sqlalchemy import select"""', "text='import openai'"]
)
def test_import_guard_ignores_non_import_text(source: str) -> None:
    assert _imports(source) == set()


def test_production_composition_remains_provider_disabled() -> None:
    source = Path("src/discord_ai_reminder_bot/post_draft_composition.py").read_text(
        encoding="utf-8"
    )
    assert "_POST_DRAFT_PROVIDER_CONFIGURED = False" in source
    assert "DisabledPostDraftGenerator()" in source
    assert "OpenAIPostDraftGenerator" not in source
    assert "create_openai_post_draft_generator" not in source
