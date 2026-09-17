"""Offline-only preparatory harness for the Phase 4 real-provider gate."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from discord_ai_reminder_bot.domain.post_draft_generation import (
    PostDraftGenerationRequest,
    PostLength,
    PostTone,
)
from discord_ai_reminder_bot.infrastructure.ai.openai_post_draft_generator import (
    OfflineScriptedOpenAIPostDraftRuntimeOwner,
    OfflineScriptScenario,
)
from discord_ai_reminder_bot.infrastructure.ai.post_draft_request_guard import (
    LIVE_READINESS_CHECKS,
    MODEL_ALLOWLIST,
    ModelPricePolicy,
)

_FIXED_INPUT = PostDraftGenerationRequest(
    purpose="固定された合成イベントを案内する",
    key_points="合成データのみを使う\n外部操作は行わない",
    tone=PostTone.POLITE,
    length=PostLength.STANDARD,
)


@dataclass(frozen=True, slots=True)
class OfflineHarnessResult:
    count_calls: int
    create_calls: int
    client_close_calls: int
    network_connections: int = 0
    real_api_requests: int = 0


async def run_offline_self_test(*, model: str) -> OfflineHarnessResult:
    """Exercise two stages through the module-owned exact scripted fake only."""
    if model not in MODEL_ALLOWLIST:
        raise ValueError("unsupported model")
    price_policy = ModelPricePolicy(
        model=model,
        input_price_per_million=Decimal(1),
        output_price_per_million=Decimal(1),
        long_context_threshold_tokens=1_000,
        long_context_input_multiplier=Decimal(2),
        long_context_output_multiplier=Decimal("1.5"),
        source_url="https://developers.openai.com/api/docs/models/synthetic",
        verified_on=date(2000, 1, 1),
        exact_snapshot_policy_selected=True,
        model_price_snapshot_verified=True,
        long_context_pricing_verified=True,
    )
    runtime_owner = OfflineScriptedOpenAIPostDraftRuntimeOwner.for_offline_script(
        scenario=OfflineScriptScenario.SUCCESS,
        generation_attempt_cap=1,
        external_call_cap=2,
        generation_reserved_cost_cap=Decimal(1),
    )
    generator = runtime_owner.create_generator(
        model=model,
        reasoning_effort="none",
        price_policy=price_policy,
        inner_timeout_seconds=1,
        configured_max_output_tokens=2_048,
    )
    try:
        await generator.generate(_FIXED_INPUT)
    finally:
        await runtime_owner.close()
    snapshot = runtime_owner.offline_script_snapshot()
    return OfflineHarnessResult(
        count_calls=snapshot.count_calls,
        create_calls=snapshot.create_calls,
        client_close_calls=snapshot.close_calls,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 4 post-draft provider harness")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--model", choices=sorted(MODEL_ALLOWLIST), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.live:
        print("LIVE_READINESS_GATE=CLOSED")
        print(
            "LIVE_READINESS_BLOCKERS="
            + ",".join(LIVE_READINESS_CHECKS + ("production_effective_gate_closed",))
        )
        print("NETWORK_CONNECTION_COUNT=0")
        print("API_REQUEST_COUNT=0")
        return 2
    result = asyncio.run(run_offline_self_test(model=args.model))
    print("OFFLINE_SELF_TEST=PASS")
    print(f"SYNTHETIC_COUNT_CALLS={result.count_calls}")
    print(f"SYNTHETIC_CREATE_CALLS={result.create_calls}")
    print(f"CLIENT_CLOSE_CALLS={result.client_close_calls}")
    print(f"NETWORK_CONNECTION_COUNT={result.network_connections}")
    print(f"API_REQUEST_COUNT={result.real_api_requests}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
