"""Fail-closed request planning and process-local guards for post drafts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Final, Literal, final
from urllib.parse import urlsplit

from discord_ai_reminder_bot.domain.post_draft_generation import (
    PostDraftGenerationRequest,
)

MODEL_ALLOWLIST: Final = frozenset({"gpt-5.6-luna", "gpt-5.6-terra"})
PRICE_DENOMINATOR: Final = Decimal(1_000_000)
_MAX_SAFE_DECIMAL_ADJUSTED_EXPONENT: Final = 30
_ALLOWED_REASONING_EFFORTS: Final = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
_ALLOWED_PRICE_SOURCE_HOSTS: Final = frozenset({"developers.openai.com", "platform.openai.com"})
_COUNT_PAYLOAD_REQUIRED_FIELDS: Final = frozenset(
    {"model", "instructions", "input", "parallel_tool_calls", "truncation"}
)
_COUNT_PAYLOAD_OPTIONAL_FIELDS: Final = frozenset({"reasoning"})
_CREATE_ONLY_FIELDS: Final = frozenset({"max_output_tokens", "store"})


def _positive_integer(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"invalid {name}")
    return value


def _safe_positive_decimal(value: object, *, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"invalid {name}")
    if value.adjusted() > _MAX_SAFE_DECIMAL_ADJUSTED_EXPONENT:
        raise ValueError(f"invalid {name}")
    return value


@dataclass(frozen=True, slots=True)
class ResponseInputTextPlan:
    """One immutable text content item with its value hidden from repr."""

    text: str = field(repr=False)
    type: Literal["input_text"] = "input_text"

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("invalid response input")


@dataclass(frozen=True, slots=True)
class ResponseInputMessagePlan:
    """One immutable user input message."""

    content: tuple[ResponseInputTextPlan, ...] = field(repr=False)
    role: Literal["user"] = "user"

    def __post_init__(self) -> None:
        if len(self.content) != 1 or not isinstance(self.content[0], ResponseInputTextPlan):
            raise ValueError("invalid response input")


@dataclass(frozen=True, slots=True)
class OpenAIResponseRequestPlan:
    """Immutable, minimal Responses request shared by count and create projections."""

    model: str
    instructions: str = field(repr=False)
    input: tuple[ResponseInputMessagePlan, ...] = field(repr=False)
    reasoning: tuple[tuple[str, str], ...] | None = None
    text: None = None
    tools: tuple[object, ...] = ()
    tool_choice: None = None
    parallel_tool_calls: Literal[False] = False
    truncation: Literal["disabled"] = "disabled"
    max_output_tokens: int = 0
    store: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.model not in MODEL_ALLOWLIST:
            raise ValueError("unsupported OpenAI post draft model")
        if not isinstance(self.instructions, str) or not self.instructions:
            raise ValueError("invalid OpenAI post draft request plan")
        if len(self.input) != 1 or not isinstance(self.input[0], ResponseInputMessagePlan):
            raise ValueError("invalid OpenAI post draft request plan")
        _positive_integer(self.max_output_tokens, name="max_output_tokens")
        if self.reasoning is not None and (
            len(self.reasoning) != 1
            or self.reasoning[0][0] != "effort"
            or self.reasoning[0][1] not in _ALLOWED_REASONING_EFFORTS
        ):
            raise ValueError("invalid OpenAI post draft reasoning policy")
        if (
            self.text is not None
            or self.tools != ()
            or self.tool_choice is not None
            or self.parallel_tool_calls is not False
            or self.truncation != "disabled"
            or self.store is not False
        ):
            raise ValueError("unsupported OpenAI post draft request field")

    def _fresh_input(self) -> list[dict[str, object]]:
        return [
            {
                "role": message.role,
                "content": [{"type": item.type, "text": item.text} for item in message.content],
            }
            for message in self.input
        ]

    def count_payload(self) -> dict[str, object]:
        """Return a fresh projection containing every supported input-affecting field."""
        payload: dict[str, object] = {
            "model": self.model,
            "instructions": self.instructions,
            "input": self._fresh_input(),
            "parallel_tool_calls": self.parallel_tool_calls,
            "truncation": self.truncation,
        }
        if self.reasoning is not None:
            payload["reasoning"] = dict(self.reasoning)
        return payload

    def create_payload(self) -> dict[str, object]:
        """Return a fresh generation projection with create-only fields added."""
        payload = self.count_payload()
        payload["max_output_tokens"] = self.max_output_tokens
        payload["store"] = self.store
        validate_create_payload_fields(payload)
        return payload

    def count_fingerprint(self) -> str:
        """Hash the canonical count projection without exposing its sensitive contents."""
        return fingerprint_count_payload(self.count_payload())


def fingerprint_count_payload(payload: dict[str, object]) -> str:
    """Hash exactly the allowlisted token-counting fields."""
    if not isinstance(payload, dict):
        raise TypeError("invalid OpenAI count payload")
    fields = frozenset(payload)
    allowed = _COUNT_PAYLOAD_REQUIRED_FIELDS | _COUNT_PAYLOAD_OPTIONAL_FIELDS
    if not _COUNT_PAYLOAD_REQUIRED_FIELDS.issubset(fields) or not fields.issubset(allowed):
        raise ValueError("unsupported OpenAI count payload field")
    if "reasoning" in payload and payload["reasoning"] is None:
        raise ValueError("invalid OpenAI count payload")
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except TypeError, ValueError:
        raise ValueError("invalid OpenAI count payload") from None
    return hashlib.sha256(canonical).hexdigest()


def validate_create_payload_fields(payload: dict[str, object]) -> None:
    """Reject new create fields until their token-counting effect is classified."""
    fields = frozenset(payload)
    count_fields = fields - _CREATE_ONLY_FIELDS
    allowed_count = _COUNT_PAYLOAD_REQUIRED_FIELDS | _COUNT_PAYLOAD_OPTIONAL_FIELDS
    if (
        not _COUNT_PAYLOAD_REQUIRED_FIELDS.issubset(count_fields)
        or not count_fields.issubset(allowed_count)
        or fields - count_fields != _CREATE_ONLY_FIELDS
    ):
        raise ValueError("unsupported OpenAI create payload field")


def build_post_draft_request_plan(
    *,
    request: PostDraftGenerationRequest,
    model: str,
    instructions: str,
    reasoning_effort: str | None,
) -> OpenAIResponseRequestPlan:
    """Build the sole immutable request plan for one generation attempt."""
    if not isinstance(request, PostDraftGenerationRequest):
        raise TypeError("invalid post draft generation request")
    user_input = json.dumps(
        {
            "purpose": request.purpose,
            "key_points": request.key_points,
            "tone": request.tone.value,
            "length": request.length.value,
            "locale": request.locale,
            "max_characters": request.output_limit.max_characters,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    reasoning = None if reasoning_effort is None else (("effort", reasoning_effort),)
    return OpenAIResponseRequestPlan(
        model=model,
        instructions=instructions,
        input=(
            ResponseInputMessagePlan(
                content=(ResponseInputTextPlan(text=user_input),),
            ),
        ),
        reasoning=reasoning,
        max_output_tokens=request.output_limit.max_output_tokens,
    )


@dataclass(frozen=True, slots=True)
class ModelPricePolicy:
    """Explicit, externally audited price snapshot; no production defaults exist."""

    model: str
    input_price_per_million: Decimal
    output_price_per_million: Decimal
    long_context_threshold_tokens: int
    long_context_input_multiplier: Decimal
    long_context_output_multiplier: Decimal
    source_url: str
    verified_on: date
    exact_snapshot_policy_selected: bool
    model_price_snapshot_verified: bool
    long_context_pricing_verified: bool

    def __post_init__(self) -> None:
        if self.model not in MODEL_ALLOWLIST:
            raise ValueError("unsupported OpenAI post draft model")
        _safe_positive_decimal(self.input_price_per_million, name="input price")
        _safe_positive_decimal(self.output_price_per_million, name="output price")
        _positive_integer(
            self.long_context_threshold_tokens,
            name="long context threshold",
        )
        if (
            _safe_positive_decimal(
                self.long_context_input_multiplier,
                name="long context input multiplier",
            )
            < 1
            or _safe_positive_decimal(
                self.long_context_output_multiplier,
                name="long context output multiplier",
            )
            < 1
        ):
            raise ValueError("invalid long context multiplier")
        if not isinstance(self.verified_on, date):
            raise TypeError("invalid price verification date")
        if not isinstance(self.source_url, str):
            raise TypeError("invalid price source")
        parsed_source = urlsplit(self.source_url)
        if (
            parsed_source.scheme != "https"
            or parsed_source.hostname not in _ALLOWED_PRICE_SOURCE_HOSTS
            or parsed_source.username is not None
            or parsed_source.password is not None
            or parsed_source.port is not None
            or not parsed_source.path.startswith("/")
            or parsed_source.query
            or parsed_source.fragment
        ):
            raise ValueError("invalid price source")
        for marker in (
            self.exact_snapshot_policy_selected,
            self.model_price_snapshot_verified,
            self.long_context_pricing_verified,
        ):
            if not isinstance(marker, bool):
                raise TypeError("invalid price verification marker")

    def effective_prices(self, *, input_tokens: int) -> tuple[Decimal, Decimal]:
        _positive_integer(input_tokens, name="input_tokens")
        if input_tokens > self.long_context_threshold_tokens:
            return (
                self.input_price_per_million * self.long_context_input_multiplier,
                self.output_price_per_million * self.long_context_output_multiplier,
            )
        return self.input_price_per_million, self.output_price_per_million

    def reserved_generation_cost(
        self,
        *,
        input_tokens: int,
        max_output_tokens: int,
    ) -> Decimal:
        """Reserve uncached generation cost; count-call cost is deliberately excluded."""
        _positive_integer(input_tokens, name="input_tokens")
        _positive_integer(max_output_tokens, name="max_output_tokens")
        input_price, output_price = self.effective_prices(input_tokens=input_tokens)
        try:
            with localcontext() as context:
                context.prec = 50
                result = (
                    Decimal(input_tokens) * input_price / PRICE_DENOMINATOR
                    + Decimal(max_output_tokens) * output_price / PRICE_DENOMINATOR
                )
        except (InvalidOperation, OverflowError) as error:
            raise ValueError("generation cost cannot be calculated safely") from error
        return _safe_positive_decimal(result, name="reserved generation cost")


LIVE_READINESS_CHECKS: Final = (
    "formal_model_selected",
    "exact_model_snapshot_policy_selected",
    "model_price_snapshot_verified",
    "long_context_pricing_verified",
    "input_tokens_pricing_contract_verified",
    "input_tokens_retention_contract_reviewed",
    "dedicated_credential_configured",
    "billing_budget_account_controls_reviewed",
    "explicit_live_authorization_present",
    "client_shutdown_strategy_approved",
)


@dataclass(frozen=True, slots=True)
class LiveReadinessEvidence:
    """Non-secret governance evidence required before an HTTP-capable client exists."""

    formal_model_selected: bool = False
    exact_model_snapshot_policy_selected: bool = False
    model_price_snapshot_verified: bool = False
    long_context_pricing_verified: bool = False
    input_tokens_pricing_contract_verified: bool = False
    input_tokens_retention_contract_reviewed: bool = False
    dedicated_credential_configured: bool = False
    billing_budget_account_controls_reviewed: bool = False
    explicit_live_authorization_present: bool = False
    client_shutdown_strategy_approved: bool = False

    def __post_init__(self) -> None:
        if any(not isinstance(getattr(self, name), bool) for name in LIVE_READINESS_CHECKS):
            raise ValueError("invalid live readiness evidence")

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(name for name in LIVE_READINESS_CHECKS if not getattr(self, name))

    @property
    def ready(self) -> bool:
        return not self.blockers


@dataclass(frozen=True, slots=True)
class GenerationLedgerSnapshot:
    generation_attempts: int
    external_calls: int
    reserved_generation_cost: Decimal


@final
@dataclass(slots=True)
class ProcessGenerationGuard:
    """Process-local ledgers; every reservation is monotonic and non-refundable."""

    generation_attempt_cap: int
    external_call_cap: int
    generation_reserved_cost_cap: Decimal
    _generation_attempts: int = field(default=0, init=False, repr=False)
    _external_calls: int = field(default=0, init=False, repr=False)
    _reserved_generation_cost: Decimal = field(default=Decimal(0), init=False, repr=False)
    _reservation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _loop_binding_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _bound_loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("process generation guard subclassing is prohibited")

    def __post_init__(self) -> None:
        _positive_integer(self.generation_attempt_cap, name="generation attempt cap")
        _positive_integer(self.external_call_cap, name="external call cap")
        _safe_positive_decimal(
            self.generation_reserved_cost_cap,
            name="generation reserved cost cap",
        )

    def _bind_or_validate_loop(self) -> None:
        """Bind on first async use and reject cross-loop reuse without reflection."""
        running_loop = asyncio.get_running_loop()
        with self._loop_binding_lock:
            if self._bound_loop is None:
                self._bound_loop = running_loop
            elif self._bound_loop is not running_loop:
                raise RuntimeError("generation guard event loop mismatch")

    async def consume_generation_attempt(self) -> None:
        self._bind_or_validate_loop()
        async with self._reservation_lock:
            if self._generation_attempts >= self.generation_attempt_cap:
                raise ValueError("generation attempt cap reached")
            self._generation_attempts += 1

    async def consume_external_call(self) -> None:
        self._bind_or_validate_loop()
        async with self._reservation_lock:
            if self._external_calls >= self.external_call_cap:
                raise ValueError("external call cap reached")
            self._external_calls += 1

    async def reserve_generation_cost(self, amount: Decimal) -> None:
        self._bind_or_validate_loop()
        amount = _safe_positive_decimal(amount, name="reserved generation cost")
        async with self._reservation_lock:
            next_total = self._reserved_generation_cost + amount
            if not next_total.is_finite() or next_total > self.generation_reserved_cost_cap:
                raise ValueError("generation reserved cost cap reached")
            self._reserved_generation_cost = next_total

    async def snapshot(self) -> GenerationLedgerSnapshot:
        self._bind_or_validate_loop()
        async with self._reservation_lock:
            return GenerationLedgerSnapshot(
                generation_attempts=self._generation_attempts,
                external_calls=self._external_calls,
                reserved_generation_cost=self._reserved_generation_cost,
            )
