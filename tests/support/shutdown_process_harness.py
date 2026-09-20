"""Safe process-group controller for the synthetic shutdown child only.

The public helpers accept a retained child handle, never a caller-supplied PID
or process-group identifier.  All errors are fixed and non-reflecting.
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import final

from discord_ai_reminder_bot.infrastructure.shutdown_measurement_harness import (
    SyntheticChildScenario,
)

CHILD_MODULE = "discord_ai_reminder_bot.infrastructure.shutdown_measurement_harness"
_SIGNAL_WAIT_SECONDS = 0.2
_FINAL_WAIT_SECONDS = 0.5
_REGISTRY: dict[int, subprocess.Popen[bytes]] = {}


class SyntheticProcessError(RuntimeError):
    """Fixed non-reflecting process-control failure."""


class ChildSignal(Enum):
    INTERRUPT = signal.SIGINT
    TERMINATE = signal.SIGTERM
    KILL = signal.SIGKILL


@dataclass(frozen=True, slots=True)
class _TargetSnapshot:
    pid: object
    pgid: object
    current_pid: int
    current_pgid: int
    parent_pgid: int
    start_new_session: bool
    identity_matches: bool


def _validate_target_snapshot(snapshot: _TargetSnapshot) -> int:
    """Validate target facts without ever signalling; used by the controller and tests."""

    if type(snapshot) is not _TargetSnapshot:
        raise SyntheticProcessError("invalid synthetic child target")
    if type(snapshot.pid) is not int or snapshot.pid <= 1:
        raise SyntheticProcessError("invalid synthetic child target")
    if type(snapshot.pgid) is not int or snapshot.pgid <= 1:
        raise SyntheticProcessError("invalid synthetic child target")
    if not snapshot.start_new_session or not snapshot.identity_matches:
        raise SyntheticProcessError("invalid synthetic child target")
    if snapshot.pgid != snapshot.pid:
        raise SyntheticProcessError("invalid synthetic child target")
    if snapshot.pgid in {
        snapshot.current_pid,
        snapshot.current_pgid,
        snapshot.parent_pgid,
    }:
        raise SyntheticProcessError("invalid synthetic child target")
    return snapshot.pgid


@final
class SyntheticChildHandle:
    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("synthetic child handle subclassing is prohibited")

    def __init__(self, process: subprocess.Popen[bytes], *, start_new_session: bool) -> None:
        if type(process) is not subprocess.Popen or type(start_new_session) is not bool:
            raise SyntheticProcessError("invalid synthetic child handle")
        self._process = process
        self._start_new_session = start_new_session
        self._reaped = False

    @property
    def process(self) -> subprocess.Popen[bytes]:
        return self._process

    @property
    def reaped(self) -> bool:
        return self._reaped


def launch_child(repository_root: Path, scenario: SyntheticChildScenario) -> SyntheticChildHandle:
    if not isinstance(repository_root, Path) or type(scenario) is not SyntheticChildScenario:
        raise SyntheticProcessError("invalid synthetic child launch")
    command = (
        sys.executable,
        "-m",
        CHILD_MODULE,
        "--synthetic-child",
        "--scenario",
        scenario.value,
    )
    process = subprocess.Popen(
        command,
        cwd=repository_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        text=False,
        start_new_session=True,
    )
    _REGISTRY[id(process)] = process
    return SyntheticChildHandle(process, start_new_session=True)


def _validate_handle(handle: SyntheticChildHandle) -> tuple[subprocess.Popen[bytes], int]:
    if type(handle) is not SyntheticChildHandle:
        raise SyntheticProcessError("invalid synthetic child handle")
    process = handle._process
    if type(process) is not subprocess.Popen or _REGISTRY.get(id(process)) is not process:
        raise SyntheticProcessError("invalid synthetic child handle")
    pid = process.pid
    try:
        pgid = os.getpgid(pid) if type(pid) is int and pid > 1 else None
        parent_pgid = os.getpgid(os.getppid())
    except ProcessLookupError:
        raise
    except OSError, TypeError, ValueError:
        raise SyntheticProcessError("invalid synthetic child target") from None
    snapshot = _TargetSnapshot(
        pid=pid,
        pgid=pgid,
        current_pid=os.getpid(),
        current_pgid=os.getpgrp(),
        parent_pgid=parent_pgid,
        start_new_session=handle._start_new_session,
        identity_matches=_REGISTRY.get(id(process)) is process,
    )
    return process, _validate_target_snapshot(snapshot)


def send_signal(handle: SyntheticChildHandle, child_signal: ChildSignal) -> bool:
    """Signal a live validated child group; return false if it already exited."""

    if type(child_signal) is not ChildSignal:
        raise SyntheticProcessError("invalid synthetic child signal")
    if type(handle) is not SyntheticChildHandle:
        raise SyntheticProcessError("invalid synthetic child handle")
    process = handle._process
    if process.poll() is not None:
        return False
    try:
        process, pgid = _validate_handle(handle)
    except ProcessLookupError:
        return False
    try:
        os.killpg(pgid, child_signal.value)
    except ProcessLookupError:
        return False
    except PermissionError, OSError:
        raise SyntheticProcessError("synthetic child signal failed") from None
    return process.poll() is None


def _wait(process: subprocess.Popen[bytes], timeout: float) -> bool:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    except BaseException:  # noqa: BLE001 - cleanup must classify every wait failure
        raise SyntheticProcessError("synthetic child wait failed") from None
    return True


def _close_pipes(process: subprocess.Popen[bytes]) -> bool:
    completed = True
    for stream in (process.stdout, process.stderr):
        if stream is None or stream.closed:
            continue
        try:
            stream.close()
        except BaseException:  # noqa: BLE001 - the other pipe must still be attempted
            completed = False
    return completed


def stop_and_reap(handle: SyntheticChildHandle) -> None:
    """Terminate, escalate if needed, and always wait for the retained child."""

    if type(handle) is not SyntheticChildHandle:
        raise SyntheticProcessError("invalid synthetic child handle")
    process = handle._process
    if _REGISTRY.get(id(process)) is not process:
        raise SyntheticProcessError("invalid synthetic child handle")
    failure: BaseException | None = None
    try:
        if process.poll() is None:
            try:
                send_signal(handle, ChildSignal.TERMINATE)
            except SyntheticProcessError as error:
                failure = error
        if not _wait(process, _SIGNAL_WAIT_SECONDS):
            try:
                send_signal(handle, ChildSignal.KILL)
            except SyntheticProcessError as error:
                failure = failure or error
            if not _wait(process, _FINAL_WAIT_SECONDS):
                failure = failure or SyntheticProcessError("synthetic child reap timed out")
    finally:
        # poll() reports exit but does not replace wait(): always make the final reap call.
        try:
            if not _wait(process, _FINAL_WAIT_SECONDS):
                failure = failure or SyntheticProcessError("synthetic child reap timed out")
        except SyntheticProcessError as error:
            failure = failure or error
        if process.returncode is not None:
            handle._reaped = True
            _REGISTRY.pop(id(process), None)
        if not _close_pipes(process):
            failure = failure or SyntheticProcessError("synthetic child pipe close failed")
    if failure is not None:
        raise SyntheticProcessError("synthetic child cleanup failed") from None
    if not handle._reaped:
        raise SyntheticProcessError("synthetic child cleanup failed")


def read_marker(handle: SyntheticChildHandle, timeout: float = 1.0) -> str:
    if type(handle) is not SyntheticChildHandle or type(timeout) is not float or timeout <= 0:
        raise SyntheticProcessError("invalid synthetic child marker wait")
    process = handle._process
    if _REGISTRY.get(id(process)) is not process or process.stdout is None:
        raise SyntheticProcessError("invalid synthetic child handle")
    try:
        readable, _, _ = select.select((process.stdout,), (), (), timeout)
    except OSError, ValueError:
        raise SyntheticProcessError("synthetic child marker read failed") from None
    if not readable:
        raise SyntheticProcessError("synthetic child marker wait timed out")
    try:
        marker = process.stdout.readline()
    except OSError, ValueError:
        raise SyntheticProcessError("synthetic child marker read failed") from None
    try:
        return marker.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        raise SyntheticProcessError("synthetic child marker decode failed") from None


def collect_output(handle: SyntheticChildHandle, timeout: float = 1.0) -> tuple[str, str]:
    if type(handle) is not SyntheticChildHandle or type(timeout) is not float or timeout <= 0:
        raise SyntheticProcessError("invalid synthetic child output wait")
    try:
        stdout, stderr = handle._process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SyntheticProcessError("synthetic child output wait timed out") from None
    except OSError, ValueError:
        raise SyntheticProcessError("synthetic child output read failed") from None
    try:
        decoded_stdout = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SyntheticProcessError("synthetic child output decode failed") from None
    try:
        decoded_stderr = stderr.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SyntheticProcessError("synthetic child output decode failed") from None
    return decoded_stdout, decoded_stderr


def residual_count(handle: SyntheticChildHandle) -> int:
    if type(handle) is not SyntheticChildHandle:
        raise SyntheticProcessError("invalid synthetic child handle")
    return 0 if handle.reaped and handle.process.returncode is not None else 1
