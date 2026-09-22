"""Safe process-group controller for the synthetic shutdown child only.

The public helpers accept a retained child handle, never a caller-supplied PID
or process-group identifier.  All errors are fixed and non-reflecting.
"""

from __future__ import annotations

import asyncio
import json
import os
import select
import signal
import stat
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
_POPEN_TYPE = subprocess.Popen
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
_CHILD_ENV = {
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
    "PYTHONSAFEPATH": "1",
    "TZ": "UTC",
}
_SNAPSHOT_BOOTSTRAP_TEMPLATE = (
    "import importlib.util,pathlib,sys;"
    'p=pathlib.Path.cwd()/"tests/support/snapshot_child_bootstrap_policy.py";'
    's=importlib.util.spec_from_file_location("_snapshot_child_bootstrap_policy",p);'
    "m=importlib.util.module_from_spec(s);"
    "sys.modules[s.name]=m;s.loader.exec_module(m);"
    "m.bootstrap(__CONTRACT_JSON__,tuple(sys.argv[1:]),__TEST_PROBE_JSON__)"
)


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
        if type(process) is not _POPEN_TYPE or type(start_new_session) is not bool:
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


def launch_child(
    repository_root: Path,
    scenario: SyntheticChildScenario,
    *,
    interpreter_fd: int | None = None,
    snapshot_fd: int | None = None,
    runtime_contract: dict[str, object] | None = None,
) -> SyntheticChildHandle:
    if (
        not isinstance(repository_root, Path)
        or repository_root != _REPOSITORY_ROOT
        or type(scenario) is not SyntheticChildScenario
    ):
        raise SyntheticProcessError("invalid synthetic child launch")
    if (interpreter_fd is None) != (snapshot_fd is None):
        raise SyntheticProcessError("invalid synthetic child launch")
    if interpreter_fd is not None and snapshot_fd is not None:
        if runtime_contract is None:
            raise SyntheticProcessError("invalid synthetic child launch")
        return launch_snapshot_child(
            repository_root,
            scenario,
            interpreter_fd=interpreter_fd,
            snapshot_fd=snapshot_fd,
            runtime_contract=runtime_contract,
        )
    if runtime_contract is not None:
        raise SyntheticProcessError("invalid synthetic child launch")
    interpreter = Path(sys.executable)
    module_source = Path(__import__(CHILD_MODULE, fromlist=["__file__"]).__file__).resolve()
    if (
        not interpreter.is_file()
        or not interpreter.is_relative_to(_REPOSITORY_ROOT / ".venv")
        or not module_source.is_relative_to(_SOURCE_ROOT)
    ):
        raise SyntheticProcessError("invalid synthetic child launch")
    command = (
        str(interpreter),
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
        env=_CHILD_ENV,
    )
    _REGISTRY[id(process)] = process
    return SyntheticChildHandle(process, start_new_session=True)


def launch_snapshot_child(
    repository_root: Path,
    scenario: SyntheticChildScenario,
    *,
    interpreter_fd: int,
    snapshot_fd: int,
    runtime_contract: dict[str, object],
    audit_probe: str | None = None,
) -> SyntheticChildHandle:
    """Launch only the fixed child from inherited, verified Linux descriptors."""

    if (
        not isinstance(repository_root, Path)
        or repository_root != _REPOSITORY_ROOT
        or type(scenario) is not SyntheticChildScenario
        or type(interpreter_fd) is not int
        or type(snapshot_fd) is not int
        or type(runtime_contract) is not dict
    ):
        raise SyntheticProcessError("invalid synthetic child launch")
    try:
        if not stat.S_ISREG(os.fstat(interpreter_fd).st_mode) or not stat.S_ISDIR(
            os.fstat(snapshot_fd).st_mode
        ):
            raise SyntheticProcessError("invalid synthetic child launch")
    except OSError:
        raise SyntheticProcessError("invalid synthetic child launch") from None
    required_contract = {
        "base_prefix",
        "stdlib_root",
        "dynamic_load_root",
        "interpreter_identity",
        "interpreter_uid",
        "implementation",
        "python_version",
        "abi",
        "prefix",
    }
    if set(runtime_contract) != required_contract:
        raise SyntheticProcessError("invalid synthetic child launch")
    if audit_probe is not None and audit_probe not in {
        "socket-connect",
        "socket-connect-ex",
        "socket-sendto",
        "socket-sendmsg",
        "socket-getaddrinfo",
        "socket-getnameinfo",
        "socket-gethostbyname",
        "socket-gethostbyaddr",
        "subprocess-popen",
        "os-system",
        "posix-spawn",
        "import-http-client",
        "import-urllib-request",
        "import-requests",
        "import-httpx",
        "import-openai",
        "importlib-http-client",
        "from-http-client",
        "submodule-http-client",
        "preloaded-http-client",
        "alias-http-client",
    }:
        raise SyntheticProcessError("invalid synthetic child launch")
    try:
        contract_json = json.dumps(runtime_contract, ensure_ascii=True, separators=(",", ":"))
    except TypeError, ValueError:
        raise SyntheticProcessError("invalid synthetic child launch") from None
    bootstrap = _SNAPSHOT_BOOTSTRAP_TEMPLATE.replace("__CONTRACT_JSON__", repr(contract_json))
    bootstrap = bootstrap.replace("__TEST_PROBE_JSON__", repr(audit_probe))
    command = (
        f"/proc/self/fd/{interpreter_fd}",
        "-I",
        "-B",
        "-S",
        "-c",
        bootstrap,
        "--synthetic-child",
        "--scenario",
        scenario.value,
    )
    process = subprocess.Popen(
        command,
        cwd=f"/proc/self/fd/{snapshot_fd}",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        text=False,
        start_new_session=True,
        env=_CHILD_ENV,
        pass_fds=(interpreter_fd, snapshot_fd),
        close_fds=True,
    )
    _REGISTRY[id(process)] = process
    return SyntheticChildHandle(process, start_new_session=True)


def _validate_handle(handle: SyntheticChildHandle) -> tuple[subprocess.Popen[bytes], int]:
    if type(handle) is not SyntheticChildHandle:
        raise SyntheticProcessError("invalid synthetic child handle")
    process = handle._process
    if type(process) is not _POPEN_TYPE or _REGISTRY.get(id(process)) is not process:
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
    except asyncio.CancelledError, KeyboardInterrupt, SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - cleanup must classify every wait failure
        raise SyntheticProcessError("synthetic child wait failed") from None
    return True


def _close_pipes(process: subprocess.Popen[bytes]) -> bool:
    completed = True
    special: BaseException | None = None
    for stream in (process.stdout, process.stderr):
        if stream is None or stream.closed:
            continue
        try:
            stream.close()
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit) as error:
            completed = False
            if special is None:
                special = error
        except BaseException:  # noqa: BLE001 - the other pipe must still be attempted
            completed = False
    if special is not None:
        raise special
    return completed


def stop_and_reap(handle: SyntheticChildHandle) -> None:
    """Terminate, escalate if needed, and always wait for the retained child."""

    if type(handle) is not SyntheticChildHandle:
        raise SyntheticProcessError("invalid synthetic child handle")
    process = handle._process
    if _REGISTRY.get(id(process)) is not process:
        raise SyntheticProcessError("invalid synthetic child handle")
    failure: SyntheticProcessError | None = None
    special: BaseException | None = None

    def record(error: BaseException) -> None:
        nonlocal failure, special
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            if special is None:
                special = error
        elif failure is None:
            failure = (
                error
                if isinstance(error, SyntheticProcessError)
                else SyntheticProcessError("synthetic child cleanup failed")
            )

    try:
        if process.poll() is None:
            try:
                send_signal(handle, ChildSignal.TERMINATE)
            except BaseException as error:  # noqa: BLE001 - remaining cleanup must run
                record(error)
        if not _wait(process, _SIGNAL_WAIT_SECONDS):
            try:
                send_signal(handle, ChildSignal.KILL)
            except BaseException as error:  # noqa: BLE001 - remaining cleanup must run
                record(error)
            if not _wait(process, _FINAL_WAIT_SECONDS):
                record(SyntheticProcessError("synthetic child reap timed out"))
    except BaseException as error:  # noqa: BLE001 - finally must still reap and close both pipes
        record(error)
    finally:
        # poll() reports exit but does not replace wait(): always make the final reap call.
        try:
            if not _wait(process, _FINAL_WAIT_SECONDS):
                failure = failure or SyntheticProcessError("synthetic child reap timed out")
        except BaseException as error:  # noqa: BLE001 - pipes must still be closed
            record(error)
        if process.returncode is not None:
            handle._reaped = True
            _REGISTRY.pop(id(process), None)
        try:
            if not _close_pipes(process):
                record(SyntheticProcessError("synthetic child pipe close failed"))
        except BaseException as error:  # noqa: BLE001 - record after both pipes were attempted
            record(error)
    if special is not None:
        raise special
    if failure is not None:
        raise failure from None
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
