"""Dedicated synthetic child for shutdown signal measurements.

The module imports no Bot, database, Discord, OpenAI, HTTP, or private-setting
code.  It accepts only fixed synthetic scenarios.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from enum import StrEnum
from typing import NoReturn

from discord_ai_reminder_bot.application.shutdown_measurement import SHUTDOWN_STAGE_ORDER

READY_MARKER = "SYNTHETIC_SHUTDOWN_READY"
PRE_READY_MARKER = "SYNTHETIC_CHILD_PRE_READY"
CLEANUP_START_MARKER = "SYNTHETIC_CLEANUP_STARTED"
CLEANUP_COMPLETE_MARKER = "SYNTHETIC_CLEANUP_COMPLETED"
CLEANUP_TASK_CREATED_MARKER = "SYNTHETIC_CLEANUP_TASK_CREATED"
SECOND_SIGNAL_MARKER = "SYNTHETIC_SECOND_SIGNAL_RECEIVED"
EXIT_MARKER = "SYNTHETIC_CHILD_EXIT_0"
PRE_READY_SIGNAL_EXIT_CODE = 70


class SyntheticChildScenario(StrEnum):
    IMMEDIATE_SUCCESS = "immediate-success"
    CONTROLLED_NON_COMPLETION = "controlled-non-completion"
    READY_BEFORE_HANDLER = "ready-before-handler"
    SECOND_SIGNAL_DURING_CLEANUP = "second-signal-during-cleanup"
    EXIT_BEFORE_SIGNAL = "exit-before-signal"


class _SyntheticAuditGuard:
    """Fail closed if the child unexpectedly attempts external capabilities."""

    _BLOCKED_EVENTS = frozenset(
        {
            "socket.bind",
            "socket.connect",
            "socket.getaddrinfo",
            "socket.gethostbyaddr",
            "socket.gethostbyname",
            "socket.gethostbyname_ex",
            "subprocess.Popen",
        }
    )

    def __init__(self) -> None:
        self.blocked_attempt_count = 0

    def __call__(self, event: str, args: tuple[object, ...]) -> None:
        del args
        if event in self._BLOCKED_EVENTS:
            self.blocked_attempt_count += 1
            raise RuntimeError("synthetic child capability blocked")


class _FixedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise SystemExit("invalid synthetic child arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _FixedArgumentParser(add_help=False)
    parser.add_argument("--synthetic-child", action="store_true")
    parser.add_argument(
        "--scenario",
        choices=tuple(scenario.value for scenario in SyntheticChildScenario),
        default=SyntheticChildScenario.IMMEDIATE_SUCCESS.value,
    )
    return parser


def _marker(value: str) -> None:
    print(value, flush=True)


async def _run_cleanup(
    scenario: SyntheticChildScenario, second_signal_received: asyncio.Event
) -> None:
    _marker(CLEANUP_START_MARKER)
    if scenario is SyntheticChildScenario.CONTROLLED_NON_COMPLETION:
        await asyncio.Event().wait()
    if scenario is SyntheticChildScenario.SECOND_SIGNAL_DURING_CLEANUP:
        await second_signal_received.wait()
        _marker(SECOND_SIGNAL_MARKER)
    for stage in SHUTDOWN_STAGE_ORDER:
        _marker(f"SYNTHETIC_STAGE_COMPLETED={stage.name}")
    _marker(CLEANUP_COMPLETE_MARKER)


async def _child(scenario: SyntheticChildScenario) -> int:
    loop = asyncio.get_running_loop()
    signal_received = asyncio.Event()
    second_signal_received = asyncio.Event()
    signal_count = 0

    def _receive_signal() -> None:
        nonlocal signal_count
        signal_count += 1
        if signal_count == 1:
            signal_received.set()
        else:
            second_signal_received.set()

    loop.add_signal_handler(signal.SIGINT, _receive_signal)
    _marker(READY_MARKER)
    try:
        if scenario is SyntheticChildScenario.EXIT_BEFORE_SIGNAL:
            _marker(EXIT_MARKER)
            return 0
        await signal_received.wait()
        _marker("SYNTHETIC_SIGNAL=SIGINT")
        _marker(CLEANUP_TASK_CREATED_MARKER)
        cleanup_task = asyncio.create_task(
            _run_cleanup(scenario, second_signal_received),
            name="synthetic-shutdown-cleanup",
        )
        await cleanup_task
    finally:
        loop.remove_signal_handler(signal.SIGINT)
    _marker(EXIT_MARKER)
    return 0


def main(argv: tuple[str, ...] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.synthetic_child:
        raise SystemExit("synthetic child mode is required")
    scenario = SyntheticChildScenario(args.scenario)
    guard = _SyntheticAuditGuard()
    sys.addaudithook(guard)
    if scenario is SyntheticChildScenario.READY_BEFORE_HANDLER:

        def _exit_before_ready(signum: int, frame: object) -> NoReturn:
            del signum, frame
            raise SystemExit(PRE_READY_SIGNAL_EXIT_CODE)

        previous_handler = signal.signal(signal.SIGINT, _exit_before_ready)
        _marker(PRE_READY_MARKER)
        try:
            signal.pause()
        finally:
            signal.signal(signal.SIGINT, previous_handler)
        return PRE_READY_SIGNAL_EXIT_CODE
    return asyncio.run(_child(scenario))


def _never_return(message: str) -> NoReturn:
    raise SystemExit(message)


if __name__ == "__main__":
    if signal.SIGINT is None:  # pragma: no cover - defensive platform guard
        _never_return("SIGINT is unavailable")
    raise SystemExit(main())
