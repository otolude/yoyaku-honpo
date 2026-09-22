"""Fixed, test-only runner for one synthetic-child shutdown observation.

It is deliberately not a CLI or production package component.  It accepts no
caller input, launches only the established fixed synthetic child, and removes
its anonymous temporary artifact on every outcome.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import stat
import sys
import sysconfig
import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import final

from discord_ai_reminder_bot.application.shutdown_measurement import SHUTDOWN_STAGE_ORDER
from discord_ai_reminder_bot.infrastructure import shutdown_measurement_harness
from tests.support import shutdown_process_harness
from tests.support.shutdown_measurement_source_metadata import (
    ExternalSourceManifest,
    SourceManifestError,
    VerifiedSourceSet,
    load_external_source_manifest,
    read_verified_source_set,
)

OUTPUT_SCHEMA_VERSION = "linux-synthetic-shutdown-measurement-v1"
_FIXED_SCENARIO = shutdown_measurement_harness.SyntheticChildScenario.IMMEDIATE_SUCCESS
_ANONYMOUS_RUN_NUMBER = "run-000001"
_ARTIFACT_NAME = "measurement.json"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_ROOT = Path("/tmp")
_ARTIFACT_RETRY_COUNT = 3


class MeasurementFailureClassification(StrEnum):
    SOURCE_METADATA_INVALID = "source_metadata_invalid"
    SOURCE_METADATA_MISMATCH = "source_metadata_mismatch"
    READY_FAILURE = "ready_failure"
    SIGNAL_FAILURE = "signal_failure"
    OUTPUT_FAILURE = "output_failure"
    MARKER_FAILURE = "marker_failure"
    EXIT_FAILURE = "exit_failure"
    STDERR_FAILURE = "stderr_failure"
    PIPE_CLOSE_FAILURE = "pipe_close_failure"
    REAP_FAILURE = "reap_failure"
    RESIDUAL_FAILURE = "residual_failure"
    CLEANUP_FAILURE = "cleanup_failure"
    ARTIFACT_FAILURE = "artifact_failure"
    ARTIFACT_CLEANUP_FAILURE = "artifact_cleanup_failure"
    INTERNAL_FAILURE = "internal_failure"


class ShutdownMeasurementRunnerError(RuntimeError):
    """Fixed, non-reflecting runner error."""

    def __init__(self, classification: MeasurementFailureClassification) -> None:
        self.classification = classification
        super().__init__(classification.value)


@dataclass(frozen=True, slots=True)
class MeasurementStage:
    stage: str
    classification: str


@dataclass(frozen=True, slots=True)
class ShutdownMeasurementOutput:
    schema_version: str
    commit_identity: str
    fixed_scenario: str
    anonymous_run_number: str
    relative_monotonic_duration_ns: int
    stages: tuple[MeasurementStage, ...]
    exit_classification: str
    residual_count: int


def _external_manifest_path() -> Path:
    """Derive the sole manifest location without caller-controlled input."""

    return _REPOSITORY_ROOT.parent / f"{_REPOSITORY_ROOT.name}.source-manifest.json"


def _external_manifest() -> ExternalSourceManifest:
    try:
        manifest = load_external_source_manifest(_external_manifest_path())
    except asyncio.CancelledError, KeyboardInterrupt, SystemExit:
        raise
    except Exception:  # noqa: BLE001 - ordinary manifest failures are fixed classifications
        raise ShutdownMeasurementRunnerError(
            MeasurementFailureClassification.SOURCE_METADATA_INVALID
        ) from None
    return manifest


def _verified_source_set(manifest: ExternalSourceManifest) -> VerifiedSourceSet:
    try:
        source_set = read_verified_source_set(_REPOSITORY_ROOT)
        if source_set.digest != manifest.source_set_sha256:
            raise SourceManifestError
    except asyncio.CancelledError, KeyboardInterrupt, SystemExit:
        raise
    except Exception:  # noqa: BLE001 - ordinary source failures are fixed classifications
        raise ShutdownMeasurementRunnerError(
            MeasurementFailureClassification.SOURCE_METADATA_INVALID
        ) from None
    return source_set


def _expected_markers() -> tuple[str, ...]:
    return (
        shutdown_measurement_harness.READY_MARKER,
        "SYNTHETIC_SIGNAL=SIGINT",
        shutdown_measurement_harness.CLEANUP_TASK_CREATED_MARKER,
        shutdown_measurement_harness.CLEANUP_START_MARKER,
        *(f"SYNTHETIC_STAGE_COMPLETED={stage.name}" for stage in SHUTDOWN_STAGE_ORDER),
        shutdown_measurement_harness.CLEANUP_COMPLETE_MARKER,
        shutdown_measurement_harness.EXIT_MARKER,
    )


def _fixed_stages() -> tuple[MeasurementStage, ...]:
    return (
        MeasurementStage("ready", "observed"),
        MeasurementStage("sigint", "sent"),
        MeasurementStage("cleanup_start", "observed"),
        *(MeasurementStage(stage.name.lower(), "completed") for stage in SHUTDOWN_STAGE_ORDER),
        MeasurementStage("cleanup_complete", "observed"),
        MeasurementStage("exit", "observed"),
    )


def _validate_markers(stdout: str) -> None:
    observed = (shutdown_measurement_harness.READY_MARKER, *stdout.splitlines())
    if observed != _expected_markers():
        raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.MARKER_FAILURE)


@dataclass(slots=True)
class _ArtifactHandles:
    """Held descriptors and immutable identity needed for fd-relative cleanup."""

    root_fd: int | None = None
    directory_fd: int | None = None
    file_fd: int | None = None
    directory_name: str | None = None
    directory_stat: os.stat_result | None = None
    root_stat: os.stat_result | None = None
    file_stat: os.stat_result | None = None
    file_created: bool = False


def _artifact_open_flags(*, directory: bool) -> int:
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    if any(not hasattr(os, flag) for flag in required):
        raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    if directory:
        flags |= os.O_DIRECTORY
    return flags


def _valid_directory(status: os.stat_result, *, private: bool) -> bool:
    expected_mode = 0o700 if private else 0o1777
    return (
        stat.S_ISDIR(status.st_mode)
        and stat.S_IMODE(status.st_mode) == expected_mode
        and status.st_uid == (os.getuid() if private else 0)
        and status.st_nlink >= (2 if private else 1)
    )


def _close_descriptor(handles: _ArtifactHandles, attribute: str) -> BaseException | None:
    descriptor = getattr(handles, attribute)
    setattr(handles, attribute, None)
    if descriptor is None:
        return None
    try:
        os.close(descriptor)
    except BaseException as error:  # noqa: BLE001 - cleanup must continue and never reuse this fd
        return error
    return None


def _cleanup_artifact(handles: _ArtifactHandles) -> tuple[bool, BaseException | None]:
    """Try every fd-relative cleanup operation once without deleting replacements."""

    failed = False
    special: BaseException | None = None

    def record(error: BaseException) -> None:
        nonlocal failed, special
        failed = True
        if special is None and isinstance(
            error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
        ):
            special = error

    if handles.file_created:
        try:
            if handles.file_stat is None or handles.directory_fd is None:
                raise RuntimeError("artifact file cleanup identity unavailable")
            named_file = os.stat(
                _ARTIFACT_NAME,
                dir_fd=handles.directory_fd,
                follow_symlinks=False,
            )
            held_file = (
                os.fstat(handles.file_fd) if handles.file_fd is not None else handles.file_stat
            )
            same_file = (
                stat.S_ISREG(named_file.st_mode)
                and stat.S_ISREG(held_file.st_mode)
                and named_file.st_dev == held_file.st_dev
                and named_file.st_ino == held_file.st_ino
                and named_file.st_uid == held_file.st_uid
                and named_file.st_nlink == held_file.st_nlink == handles.file_stat.st_nlink
                and held_file.st_dev == handles.file_stat.st_dev
                and held_file.st_ino == handles.file_stat.st_ino
            )
            if not same_file:
                raise RuntimeError("artifact file identity changed")
            os.unlink(_ARTIFACT_NAME, dir_fd=handles.directory_fd)
        except BaseException as error:  # noqa: BLE001 - cleanup must protect directory and close fds
            record(error)

    if (
        handles.root_fd is not None
        and handles.directory_name is not None
        and handles.directory_stat is not None
    ):
        try:
            named_stat = os.stat(
                handles.directory_name,
                dir_fd=handles.root_fd,
                follow_symlinks=False,
            )
            held_stat = (
                os.fstat(handles.directory_fd)
                if handles.directory_fd is not None
                else handles.directory_stat
            )
            same_directory = (
                stat.S_ISDIR(named_stat.st_mode)
                and stat.S_ISDIR(held_stat.st_mode)
                and named_stat.st_dev == held_stat.st_dev
                and named_stat.st_ino == held_stat.st_ino
                and named_stat.st_uid == held_stat.st_uid
                and held_stat.st_dev == handles.directory_stat.st_dev
                and held_stat.st_ino == handles.directory_stat.st_ino
            )
            if not same_directory:
                raise RuntimeError("artifact directory identity changed")
            os.rmdir(handles.directory_name, dir_fd=handles.root_fd)
        except BaseException as error:  # noqa: BLE001 - no replacement is removed on mismatch
            record(error)

    for attribute in ("file_fd", "directory_fd", "root_fd"):
        error = _close_descriptor(handles, attribute)
        if error is not None:
            record(error)
    return failed, special


def _prepare_artifact_directory(handles: _ArtifactHandles) -> None:
    root_flags = _artifact_open_flags(directory=True)
    handles.root_fd = os.open(_ARTIFACT_ROOT, root_flags)
    root_stat = os.fstat(handles.root_fd)
    if not _valid_directory(root_stat, private=False):
        raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
    handles.root_stat = root_stat

    for _attempt in range(_ARTIFACT_RETRY_COUNT):
        name = f"shutdown-measurement-{secrets.token_hex(16)}"
        try:
            os.mkdir(name, 0o700, dir_fd=handles.root_fd)
        except FileExistsError:
            continue
        handles.directory_name = name
        break
    else:
        raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)

    created_stat = os.stat(
        handles.directory_name,
        dir_fd=handles.root_fd,
        follow_symlinks=False,
    )
    if not _valid_directory(created_stat, private=True) or created_stat.st_uid != os.getuid():
        raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
    handles.directory_stat = created_stat
    handles.directory_fd = os.open(
        handles.directory_name,
        _artifact_open_flags(directory=True),
        dir_fd=handles.root_fd,
    )
    os.fchmod(handles.directory_fd, 0o700)
    directory_stat = os.fstat(handles.directory_fd)
    if not _valid_directory(directory_stat, private=True) or directory_stat.st_uid != os.getuid():
        raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
    handles.directory_stat = directory_stat


def _write_and_verify_artifact(output: ShutdownMeasurementOutput) -> None:
    handles = _ArtifactHandles()
    primary: BaseException | None = None
    try:
        _prepare_artifact_directory(handles)
        if handles.directory_fd is None:
            raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        if not hasattr(os, "O_CLOEXEC"):
            raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
        file_flags |= os.O_CLOEXEC
        handles.file_fd = os.open(
            _ARTIFACT_NAME,
            file_flags,
            0o600,
            dir_fd=handles.directory_fd,
        )
        handles.file_created = True
        file_stat = os.fstat(handles.file_fd)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_uid != os.getuid()
            or stat.S_IMODE(file_stat.st_mode) != 0o600
            or file_stat.st_nlink != 1
        ):
            raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
        handles.file_stat = file_stat
        payload = json.dumps(asdict(output), ensure_ascii=True, separators=(",", ":")).encode(
            "ascii"
        )
        if os.write(handles.file_fd, payload) != len(payload):
            raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
        os.fsync(handles.file_fd)
        read_fd = os.open(
            _ARTIFACT_NAME, _artifact_open_flags(directory=False), dir_fd=handles.directory_fd
        )
        try:
            read_stat = os.fstat(read_fd)
            if (
                not stat.S_ISREG(read_stat.st_mode)
                or read_stat.st_uid != file_stat.st_uid
                or stat.S_IMODE(read_stat.st_mode) != 0o600
                or read_stat.st_nlink != file_stat.st_nlink
                or read_stat.st_dev != file_stat.st_dev
                or read_stat.st_ino != file_stat.st_ino
            ):
                raise ShutdownMeasurementRunnerError(
                    MeasurementFailureClassification.ARTIFACT_FAILURE
                )
            if os.read(read_fd, len(payload) + 1) != payload:
                raise ShutdownMeasurementRunnerError(
                    MeasurementFailureClassification.ARTIFACT_FAILURE
                )
        finally:
            os.close(read_fd)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit) as error:
        primary = error
    except ShutdownMeasurementRunnerError as error:
        primary = error
    except Exception:  # noqa: BLE001 - ordinary artifact failures are fixed classifications
        primary = ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
    finally:
        cleanup_failed, cleanup_special = _cleanup_artifact(handles)
    if primary is not None:
        raise primary from None
    if cleanup_special is not None:
        raise cleanup_special
    if cleanup_failed:
        raise ShutdownMeasurementRunnerError(
            MeasurementFailureClassification.ARTIFACT_CLEANUP_FAILURE
        )


@dataclass(slots=True)
class _SnapshotFile:
    relative_path: str
    name: str
    parent_fd: int
    descriptor: int
    status: os.stat_result
    expected_sha256: str


@dataclass(slots=True)
class _SnapshotDirectory:
    name: str
    parent_fd: int
    descriptor: int
    status: os.stat_result


@dataclass(slots=True)
class _ExecutionSnapshot:
    handles: _ArtifactHandles
    directories: list[_SnapshotDirectory]
    files: list[_SnapshotFile]


def _snapshot_file_flags(*, write: bool) -> int:
    if not hasattr(os, "O_CLOEXEC") or not hasattr(os, "O_NOFOLLOW"):
        raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
    return (
        (os.O_WRONLY | os.O_CREAT | os.O_EXCL if write else os.O_RDONLY)
        | os.O_NOFOLLOW
        | os.O_CLOEXEC
    )


def _snapshot_file_matches(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        stat.S_ISREG(left.st_mode)
        and stat.S_ISREG(right.st_mode)
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_uid == right.st_uid == os.getuid()
        and stat.S_IMODE(left.st_mode) == stat.S_IMODE(right.st_mode) == 0o600
        and left.st_nlink == right.st_nlink == 1
    )


def _snapshot_directory(
    snapshot: _ExecutionSnapshot,
    parent_fd: int,
    name: str,
) -> int:
    os.mkdir(name, 0o700, dir_fd=parent_fd)
    created = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not _valid_directory(created, private=True):
        raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
    descriptor = os.open(name, _artifact_open_flags(directory=True), dir_fd=parent_fd)
    os.fchmod(descriptor, 0o700)
    status = os.fstat(descriptor)
    if not _valid_directory(status, private=True):
        raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
    snapshot.directories.append(_SnapshotDirectory(name, parent_fd, descriptor, status))
    return descriptor


def _write_snapshot_file(
    snapshot: _ExecutionSnapshot,
    parent_fd: int,
    name: str,
    relative_path: str,
    payload: bytes,
) -> None:
    descriptor = os.open(
        name,
        (_snapshot_file_flags(write=True) & ~os.O_WRONLY) | os.O_RDWR,
        0o600,
        dir_fd=parent_fd,
    )
    try:
        status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_uid != os.getuid()
            or stat.S_IMODE(status.st_mode) != 0o600
            or status.st_nlink != 1
            or os.write(descriptor, payload) != len(payload)
        ):
            raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        observed = os.read(descriptor, len(payload) + 1)
        if not _snapshot_file_matches(status, os.fstat(descriptor)) or observed != payload:
            raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
        snapshot.files.append(
            _SnapshotFile(
                relative_path=relative_path,
                name=name,
                parent_fd=parent_fd,
                descriptor=descriptor,
                status=status,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
    except BaseException:
        os.close(descriptor)
        raise


def _build_execution_snapshot(source_set: VerifiedSourceSet) -> _ExecutionSnapshot:
    """Copy exactly the already-digested repository bytes into a private snapshot."""

    handles = _ArtifactHandles()
    snapshot = _ExecutionSnapshot(handles, [], [])
    primary: BaseException | None = None
    try:
        _prepare_artifact_directory(handles)
        if handles.directory_fd is None:
            raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
        directories: dict[tuple[str, ...], int] = {(): handles.directory_fd}
        for relative_path, payload in source_set.files:
            parts = tuple(relative_path.split("/"))
            parent_parts = parts[:-1]
            for depth in range(1, len(parent_parts) + 1):
                key = parent_parts[:depth]
                if key not in directories:
                    directories[key] = _snapshot_directory(snapshot, directories[key[:-1]], key[-1])
            _write_snapshot_file(
                snapshot, directories[parent_parts], parts[-1], relative_path, payload
            )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit) as error:
        primary = error
    except ShutdownMeasurementRunnerError as error:
        primary = error
    except Exception:  # noqa: BLE001 - ordinary snapshot failures are fixed classifications
        primary = ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
    if primary is not None:
        cleanup_failed, cleanup_special = _cleanup_execution_snapshot(snapshot)
        if primary is not None:
            raise primary from None
        if cleanup_special is not None:
            raise cleanup_special
        if cleanup_failed:
            raise ShutdownMeasurementRunnerError(
                MeasurementFailureClassification.ARTIFACT_CLEANUP_FAILURE
            )
    return snapshot


def _cleanup_execution_snapshot(snapshot: _ExecutionSnapshot) -> tuple[bool, BaseException | None]:
    """Remove only snapshot entries that still match their held identities."""

    failed = False
    special: BaseException | None = None

    def record(error: BaseException) -> None:
        nonlocal failed, special
        failed = True
        if special is None and isinstance(
            error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
        ):
            special = error

    for item in reversed(snapshot.files):
        try:
            named = os.stat(item.name, dir_fd=item.parent_fd, follow_symlinks=False)
            held = os.fstat(item.descriptor)
            if not _snapshot_file_matches(item.status, held) or not _snapshot_file_matches(
                held, named
            ):
                raise RuntimeError("snapshot file identity changed")
            os.unlink(item.name, dir_fd=item.parent_fd)
        except BaseException as error:  # noqa: BLE001 - safe cleanup continues
            record(error)
        error = _close_snapshot_descriptor(item.descriptor)
        if error is not None:
            record(error)
    for item in reversed(snapshot.directories):
        try:
            named = os.stat(item.name, dir_fd=item.parent_fd, follow_symlinks=False)
            held = os.fstat(item.descriptor)
            if (
                not _valid_directory(named, private=True)
                or not _valid_directory(held, private=True)
                or named.st_dev != held.st_dev
                or named.st_ino != held.st_ino
                or held.st_dev != item.status.st_dev
                or held.st_ino != item.status.st_ino
            ):
                raise RuntimeError("snapshot directory identity changed")
            os.rmdir(item.name, dir_fd=item.parent_fd)
        except BaseException as error:  # noqa: BLE001 - safe cleanup continues
            record(error)
        error = _close_snapshot_descriptor(item.descriptor)
        if error is not None:
            record(error)
    root_failed, root_special = _cleanup_artifact(snapshot.handles)
    failed = failed or root_failed
    if special is None:
        special = root_special
    return failed, special


def _snapshot_digest(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while chunk := os.read(descriptor, 65536):
        digest.update(chunk)
    return digest.hexdigest()


def _revalidate_execution_snapshot(snapshot: _ExecutionSnapshot) -> None:
    """Fail closed unless every still-held snapshot object is exactly the copied one."""

    handles = snapshot.handles
    if (
        handles.root_fd is None
        or handles.root_stat is None
        or handles.directory_fd is None
        or handles.directory_stat is None
    ):
        raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
    root = os.fstat(handles.root_fd)
    directory = os.fstat(handles.directory_fd)
    if (
        not _valid_directory(root, private=False)
        or root.st_dev != handles.root_stat.st_dev
        or root.st_ino != handles.root_stat.st_ino
        or not _valid_directory(directory, private=True)
        or directory.st_dev != handles.directory_stat.st_dev
        or directory.st_ino != handles.directory_stat.st_ino
    ):
        raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
    for item in snapshot.directories:
        named = os.stat(item.name, dir_fd=item.parent_fd, follow_symlinks=False)
        held = os.fstat(item.descriptor)
        if (
            not _valid_directory(named, private=True)
            or not _valid_directory(held, private=True)
            or named.st_dev != held.st_dev
            or named.st_ino != held.st_ino
            or held.st_dev != item.status.st_dev
            or held.st_ino != item.status.st_ino
        ):
            raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)
    for item in snapshot.files:
        held = os.fstat(item.descriptor)
        named = os.stat(item.name, dir_fd=item.parent_fd, follow_symlinks=False)
        if (
            not _snapshot_file_matches(item.status, held)
            or not _snapshot_file_matches(held, named)
            or _snapshot_digest(item.descriptor) != item.expected_sha256
        ):
            raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.ARTIFACT_FAILURE)


def _close_snapshot_descriptor(descriptor: int) -> BaseException | None:
    try:
        os.close(descriptor)
    except BaseException as error:  # noqa: BLE001 - caller must continue remaining cleanup
        return error
    return None


@dataclass(frozen=True, slots=True)
class _InterpreterIdentity:
    descriptor: int
    status: os.stat_result
    launcher: Path
    launcher_status: os.stat_result
    resolved_path: Path
    base_prefix: str
    stdlib_root: str
    dynamic_load_root: str
    python_version: tuple[int, int, int]
    implementation: str
    abi: str


def _valid_interpreter_status(status: os.stat_result, parent_status: os.stat_result) -> bool:
    return (
        stat.S_ISREG(status.st_mode)
        and status.st_uid == parent_status.st_uid
        and status.st_dev == parent_status.st_dev
        and status.st_ino == parent_status.st_ino
        and stat.S_IMODE(status.st_mode) & 0o111 != 0
        and stat.S_IMODE(status.st_mode) & 0o022 == 0
    )


def _validated_stdlib_roots(
    *, base_prefix: Path, interpreter_status: os.stat_result
) -> tuple[str, str]:
    """Return only the fixed, non-writable stdlib roots for this interpreter."""

    try:
        if not base_prefix.is_absolute() or base_prefix != base_prefix.resolve(strict=True):
            raise OSError
        base_status = base_prefix.stat()
        if (
            base_prefix.lstat() != base_status
            or not stat.S_ISDIR(base_status.st_mode)
            or base_status.st_uid != interpreter_status.st_uid
            or stat.S_IMODE(base_status.st_mode) & 0o022
            or base_prefix.is_relative_to(_REPOSITORY_ROOT)
            or base_prefix.is_relative_to(_ARTIFACT_ROOT)
            or any(part.lower() in {"site-packages", "dist-packages"} for part in base_prefix.parts)
        ):
            raise OSError
        stdlib = Path(sysconfig.get_path("stdlib")).resolve(strict=True)
        dynamic = Path(sysconfig.get_config_var("DESTSHARED")).resolve(strict=True)
        expected_stdlib = (
            base_prefix / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
        )
        if stdlib != expected_stdlib or dynamic != stdlib / "lib-dynload":
            raise OSError
        for root in (stdlib, dynamic):
            root_status = root.stat()
            root_lstat = root.lstat()
            if (
                root_status != root_lstat
                or not stat.S_ISDIR(root_status.st_mode)
                or root_status.st_uid != interpreter_status.st_uid
                or stat.S_IMODE(root_status.st_mode) & 0o022
                or not root.is_relative_to(base_prefix)
                or any(part.lower() in {"site-packages", "dist-packages"} for part in root.parts)
            ):
                raise OSError
        return str(stdlib), str(dynamic)
    except OSError, TypeError, ValueError:
        raise ShutdownMeasurementRunnerError(
            MeasurementFailureClassification.SOURCE_METADATA_INVALID
        ) from None


def _open_verified_interpreter() -> _InterpreterIdentity:
    launcher = _REPOSITORY_ROOT / ".venv" / "bin" / "python"
    try:
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_CLOEXEC"):
            raise OSError
        launcher_status = launcher.lstat()
        if not stat.S_ISLNK(launcher_status.st_mode):
            raise OSError
        resolved = launcher.resolve(strict=True)
        parent = Path(os.path.realpath(sys.executable))
        prefix = Path(os.path.realpath(sys.prefix))
        if parent != resolved or prefix != (_REPOSITORY_ROOT / ".venv").resolve(strict=True):
            raise OSError
        before = resolved.stat()
        parent_stat = parent.stat()
        if not _valid_interpreter_status(before, parent_stat):
            raise OSError
        descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        after = os.fstat(descriptor)
        if after.st_dev != before.st_dev or after.st_ino != before.st_ino:
            os.close(descriptor)
            raise OSError
        base_prefix = Path(os.path.realpath(sys.base_prefix))
        stdlib_root, dynamic_load_root = _validated_stdlib_roots(
            base_prefix=base_prefix, interpreter_status=after
        )
        return _InterpreterIdentity(
            descriptor,
            after,
            launcher,
            launcher_status,
            resolved,
            str(base_prefix),
            stdlib_root,
            dynamic_load_root,
            sys.version_info[:3],
            sys.implementation.name,
            sys.implementation.cache_tag,
        )
    except OSError, TypeError, ValueError:
        raise ShutdownMeasurementRunnerError(
            MeasurementFailureClassification.SOURCE_METADATA_INVALID
        ) from None


def _interpreter_runtime_contract(interpreter: _InterpreterIdentity) -> dict[str, object]:
    """Bind child expectations to the exact verified parent runtime facts."""

    try:
        expected_base_prefix = str(Path(os.path.realpath(sys.base_prefix)))
        expected_stdlib_root = str(
            Path(expected_base_prefix)
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
        )
        if (
            interpreter.base_prefix != expected_base_prefix
            or interpreter.stdlib_root != expected_stdlib_root
            or interpreter.python_version != sys.version_info[:3]
            or interpreter.implementation != sys.implementation.name
            or interpreter.abi != sys.implementation.cache_tag
            or interpreter.dynamic_load_root != str(Path(interpreter.stdlib_root) / "lib-dynload")
        ):
            raise ValueError
        return {
            "base_prefix": interpreter.base_prefix,
            "prefix": interpreter.base_prefix,
            "stdlib_root": interpreter.stdlib_root,
            "dynamic_load_root": interpreter.dynamic_load_root,
            "interpreter_identity": [interpreter.status.st_dev, interpreter.status.st_ino],
            "interpreter_uid": interpreter.status.st_uid,
            "python_version": list(interpreter.python_version),
            "implementation": interpreter.implementation,
            "abi": interpreter.abi,
        }
    except OSError, TypeError, ValueError:
        raise ShutdownMeasurementRunnerError(
            MeasurementFailureClassification.SOURCE_METADATA_INVALID
        ) from None


def _revalidate_interpreter_for_launch(interpreter: _InterpreterIdentity) -> None:
    """Reject replacement of either the fixed launcher pathname or held executable."""

    try:
        launcher = interpreter.launcher.lstat()
        named = interpreter.resolved_path.stat()
        held = os.fstat(interpreter.descriptor)
        if (
            launcher != interpreter.launcher_status
            or not stat.S_ISLNK(launcher.st_mode)
            or not _valid_interpreter_status(named, held)
            or named.st_dev != interpreter.status.st_dev
            or named.st_ino != interpreter.status.st_ino
            or held.st_dev != interpreter.status.st_dev
            or held.st_ino != interpreter.status.st_ino
        ):
            raise OSError
    except OSError, TypeError, ValueError:
        raise ShutdownMeasurementRunnerError(
            MeasurementFailureClassification.SOURCE_METADATA_INVALID
        ) from None


def _cleanup_child(
    child: shutdown_process_harness.SyntheticChildHandle,
) -> tuple[MeasurementFailureClassification | None, BaseException | None]:
    cleanup_failed = False
    special: BaseException | None = None

    def record(error: BaseException) -> None:
        nonlocal cleanup_failed, special
        cleanup_failed = True
        if special is None and isinstance(
            error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
        ):
            special = error

    try:
        shutdown_process_harness.stop_and_reap(child)
    except BaseException as error:  # noqa: BLE001 - cleanup must still inspect child state
        record(error)
    try:
        pipes_closed = (
            child.process.stdout is not None
            and child.process.stdout.closed
            and child.process.stderr is not None
            and child.process.stderr.closed
        )
        reaped = child.reaped
        residual = shutdown_process_harness.residual_count(child)
    except BaseException as error:  # noqa: BLE001 - cleanup must classify every failure
        record(error)
        return MeasurementFailureClassification.CLEANUP_FAILURE, special
    if not pipes_closed:
        return MeasurementFailureClassification.PIPE_CLOSE_FAILURE, special
    if not reaped:
        return MeasurementFailureClassification.REAP_FAILURE, special
    if residual != 0:
        return MeasurementFailureClassification.RESIDUAL_FAILURE, special
    if cleanup_failed:
        return MeasurementFailureClassification.CLEANUP_FAILURE, special
    return None, special


@final
class FixedLinuxShutdownMeasurementRunner:
    """One no-input, no-promotion observation of the first allowed scenario."""

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("shutdown measurement runner subclassing is prohibited")

    def run(self) -> ShutdownMeasurementOutput:
        _artifact_open_flags(directory=True)
        manifest = _external_manifest()
        source_set = _verified_source_set(manifest)
        interpreter = _open_verified_interpreter()
        try:
            snapshot = _build_execution_snapshot(source_set)
        except BaseException:
            _close_snapshot_descriptor(interpreter.descriptor)
            raise
        child: shutdown_process_harness.SyntheticChildHandle | None = None
        stdout = ""
        stderr = ""
        started_at_ns: int | None = None
        finished_at_ns: int | None = None
        primary: BaseException | None = None
        cleanup_failure: MeasurementFailureClassification | None = None
        cleanup_special: BaseException | None = None
        snapshot_failed = False
        snapshot_special: BaseException | None = None
        interpreter_close_error: BaseException | None = None
        try:
            if snapshot.handles.directory_fd is None:
                raise ShutdownMeasurementRunnerError(
                    MeasurementFailureClassification.ARTIFACT_FAILURE
                )
            _revalidate_execution_snapshot(snapshot)
            _revalidate_interpreter_for_launch(interpreter)
            child = shutdown_process_harness.launch_child(
                _REPOSITORY_ROOT,
                _FIXED_SCENARIO,
                interpreter_fd=interpreter.descriptor,
                snapshot_fd=snapshot.handles.directory_fd,
                runtime_contract=_interpreter_runtime_contract(interpreter),
            )
            try:
                ready_marker = shutdown_process_harness.read_marker(child)
            except asyncio.CancelledError:
                raise
            except KeyboardInterrupt, SystemExit:
                raise
            except Exception:  # noqa: BLE001 - ordinary runner failures are fixed classifications
                raise ShutdownMeasurementRunnerError(
                    MeasurementFailureClassification.READY_FAILURE
                ) from None
            if ready_marker != shutdown_measurement_harness.READY_MARKER:
                raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.READY_FAILURE)
            started_at_ns = time.monotonic_ns()
            try:
                signal_sent = shutdown_process_harness.send_signal(
                    child, shutdown_process_harness.ChildSignal.INTERRUPT
                )
            except asyncio.CancelledError:
                raise
            except KeyboardInterrupt, SystemExit:
                raise
            except Exception:  # noqa: BLE001 - ordinary runner failures are fixed classifications
                raise ShutdownMeasurementRunnerError(
                    MeasurementFailureClassification.SIGNAL_FAILURE
                ) from None
            if not signal_sent:
                raise ShutdownMeasurementRunnerError(
                    MeasurementFailureClassification.SIGNAL_FAILURE
                )
            try:
                stdout, stderr = shutdown_process_harness.collect_output(child)
            except asyncio.CancelledError:
                raise
            except ShutdownMeasurementRunnerError:
                raise
            except KeyboardInterrupt, SystemExit:
                raise
            except Exception:  # noqa: BLE001 - ordinary runner failures are fixed classifications
                raise ShutdownMeasurementRunnerError(
                    MeasurementFailureClassification.OUTPUT_FAILURE
                ) from None
        except asyncio.CancelledError as error:
            primary = error
        except ShutdownMeasurementRunnerError as error:
            primary = error
        except (KeyboardInterrupt, SystemExit) as error:
            primary = error
        except Exception:  # noqa: BLE001 - ordinary runner failures are fixed classifications
            primary = ShutdownMeasurementRunnerError(
                MeasurementFailureClassification.INTERNAL_FAILURE
            )
        finally:
            if child is not None:
                cleanup_failure, cleanup_special = _cleanup_child(child)
            if started_at_ns is not None:
                finished_at_ns = time.monotonic_ns()
            snapshot_failed, snapshot_special = _cleanup_execution_snapshot(snapshot)
            interpreter_close_error = _close_snapshot_descriptor(interpreter.descriptor)
        if primary is not None:
            raise primary from None
        if cleanup_special is not None:
            raise cleanup_special
        if snapshot_special is not None:
            raise snapshot_special
        if interpreter_close_error is not None:
            if isinstance(
                interpreter_close_error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
            ):
                raise interpreter_close_error
            raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.CLEANUP_FAILURE)
        if cleanup_failure is not None:
            raise ShutdownMeasurementRunnerError(cleanup_failure)
        if snapshot_failed:
            raise ShutdownMeasurementRunnerError(
                MeasurementFailureClassification.ARTIFACT_CLEANUP_FAILURE
            )
        if child is None or started_at_ns is None or finished_at_ns is None:
            raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.INTERNAL_FAILURE)
        if stderr != "":
            raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.STDERR_FAILURE)
        if child.process.returncode != 0:
            raise ShutdownMeasurementRunnerError(MeasurementFailureClassification.EXIT_FAILURE)
        _validate_markers(stdout)
        output = ShutdownMeasurementOutput(
            schema_version=OUTPUT_SCHEMA_VERSION,
            commit_identity=manifest.commit_sha,
            fixed_scenario=_FIXED_SCENARIO.value,
            anonymous_run_number=_ANONYMOUS_RUN_NUMBER,
            relative_monotonic_duration_ns=finished_at_ns - started_at_ns,
            stages=_fixed_stages(),
            exit_classification="exit_zero",
            residual_count=0,
        )
        _write_and_verify_artifact(output)
        return output


def run_fixed_linux_shutdown_measurement() -> ShutdownMeasurementOutput:
    """Run the sole currently authorized synthetic-child observation."""

    return FixedLinuxShutdownMeasurementRunner().run()
