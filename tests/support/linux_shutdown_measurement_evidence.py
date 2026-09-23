"""Persistent, test-only evidence contract for the fixed shutdown observation.

This module is deliberately outside the production wheel.  Its sole public
invocation is ``python -m tests.support.linux_shutdown_measurement_evidence``.
It has no caller-selected path, scenario, manifest, or retry behaviour.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from discord_ai_reminder_bot.application.shutdown_measurement import SHUTDOWN_STAGE_ORDER
from tests.support import linux_shutdown_measurement_runner as runner
from tests.support.shutdown_measurement_source_metadata import ExternalSourceManifest

EVIDENCE_SCHEMA_VERSION: Final = "linux-shutdown-measurement-evidence-v1"
PREPARED_SCHEMA_VERSION: Final = "linux-shutdown-measurement-prepared-v1"
COMPLETED_SCHEMA_VERSION: Final = "linux-shutdown-measurement-completed-v1"
ATTEMPT_NUMBER: Final = 1
FIXED_SCENARIO: Final = "immediate-success"
RESULT_CLASSIFICATION: Final = "success"
PREPARED_NAME: Final = "prepared.json"
EVIDENCE_NAME: Final = "measurement-evidence.json"
SIDECAR_NAME: Final = "measurement-evidence.sha256"
COMPLETED_NAME: Final = "completed.json"
_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]


class EvidenceContractError(RuntimeError):
    """Fixed non-reflecting failure for every ordinary evidence error."""

    def __init__(self) -> None:
        super().__init__("shutdown measurement evidence blocked")


@dataclass(frozen=True, slots=True)
class EvidenceIdentity:
    manifest_schema_version: str
    commit_sha: str
    git_tree_sha: str
    transfer_archive_sha256: str
    source_set_sha256: str


@dataclass(frozen=True, slots=True)
class MeasurementEvidence:
    schema_version: str
    runner_contract_version: str
    manifest_schema_version: str
    commit_sha: str
    git_tree_sha: str
    transfer_archive_sha256: str
    source_set_sha256: str
    fixed_scenario: str
    result_classification: str
    duration_ns: int
    shutdown_stage_count: int
    child_exit_classification: str
    residual_count: int


@dataclass(slots=True)
class _Handles:
    parent_fd: int | None = None
    directory_fd: int | None = None
    parent_stat: os.stat_result | None = None
    directory_stat: os.stat_result | None = None


def _canonical(payload: dict[str, object]) -> bytes:
    try:
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
            "ascii"
        )
    except TypeError, ValueError:
        raise EvidenceContractError from None


def _identity(manifest: ExternalSourceManifest) -> EvidenceIdentity:
    return EvidenceIdentity(
        manifest.schema_version,
        manifest.commit_sha,
        manifest.git_tree_sha,
        manifest.transfer_archive_sha256,
        manifest.source_set_sha256,
    )


def _identity_payload(identity: EvidenceIdentity) -> dict[str, str]:
    return {
        "manifest_schema_version": identity.manifest_schema_version,
        "commit_sha": identity.commit_sha,
        "git_tree_sha": identity.git_tree_sha,
        "transfer_archive_sha256": identity.transfer_archive_sha256,
        "source_set_sha256": identity.source_set_sha256,
    }


def _prepared_payload(identity: EvidenceIdentity) -> dict[str, object]:
    return {
        "schema_version": PREPARED_SCHEMA_VERSION,
        "attempt_number": ATTEMPT_NUMBER,
        "status": "prepared",
        "fixed_scenario": FIXED_SCENARIO,
        "runner_contract_version": runner.OUTPUT_SCHEMA_VERSION,
        "evidence_contract_version": EVIDENCE_SCHEMA_VERSION,
        **_identity_payload(identity),
    }


def _completed_payload(identity: EvidenceIdentity, evidence_sha256: str) -> dict[str, object]:
    return {
        "schema_version": COMPLETED_SCHEMA_VERSION,
        "attempt_number": ATTEMPT_NUMBER,
        "status": "completed",
        "result_classification": RESULT_CLASSIFICATION,
        "evidence_sha256": evidence_sha256,
        **_identity_payload(identity),
    }


def _evidence_payload(evidence: MeasurementEvidence) -> dict[str, object]:
    return {
        "schema_version": evidence.schema_version,
        "runner_contract_version": evidence.runner_contract_version,
        "manifest_schema_version": evidence.manifest_schema_version,
        "commit_sha": evidence.commit_sha,
        "git_tree_sha": evidence.git_tree_sha,
        "transfer_archive_sha256": evidence.transfer_archive_sha256,
        "source_set_sha256": evidence.source_set_sha256,
        "fixed_scenario": evidence.fixed_scenario,
        "result_classification": evidence.result_classification,
        "duration_ns": evidence.duration_ns,
        "shutdown_stage_count": evidence.shutdown_stage_count,
        "child_exit_classification": evidence.child_exit_classification,
        "residual_count": evidence.residual_count,
    }


def serialize_prepared(identity: EvidenceIdentity) -> bytes:
    return _canonical(_prepared_payload(identity))


def serialize_completed(identity: EvidenceIdentity, evidence_sha256: str) -> bytes:
    if not _valid_sha256(evidence_sha256):
        raise EvidenceContractError
    return _canonical(_completed_payload(identity, evidence_sha256))


def serialize_evidence(evidence: MeasurementEvidence) -> bytes:
    _validate_evidence(evidence)
    return _canonical(_evidence_payload(evidence))


def _valid_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_sha1(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _parse_exact(raw: bytes, expected: set[str]) -> dict[str, object]:
    try:
        decoded = raw.decode("ascii", "strict")
        payload = json.loads(decoded)
    except UnicodeError, json.JSONDecodeError, TypeError:
        raise EvidenceContractError from None
    if type(payload) is not dict or set(payload) != expected or _canonical(payload) != raw:
        raise EvidenceContractError
    return payload


def parse_evidence(raw: bytes) -> MeasurementEvidence:
    payload = _parse_exact(raw, set(_evidence_payload(_sample_evidence()).keys()))
    try:
        evidence = MeasurementEvidence(**payload)
    except TypeError:
        raise EvidenceContractError from None
    _validate_evidence(evidence)
    return evidence


def parse_prepared(raw: bytes) -> dict[str, object]:
    payload = _parse_exact(raw, set(_prepared_payload(_sample_identity()).keys()))
    if (
        payload["schema_version"] != PREPARED_SCHEMA_VERSION
        or type(payload["attempt_number"]) is not int
        or payload["attempt_number"] != ATTEMPT_NUMBER
        or payload["status"] != "prepared"
        or payload["fixed_scenario"] != FIXED_SCENARIO
        or payload["runner_contract_version"] != runner.OUTPUT_SCHEMA_VERSION
        or payload["evidence_contract_version"] != EVIDENCE_SCHEMA_VERSION
    ):
        raise EvidenceContractError
    _validate_identity_payload(payload)
    return payload


def parse_completed(raw: bytes) -> dict[str, object]:
    payload = _parse_exact(raw, set(_completed_payload(_sample_identity(), "0" * 64).keys()))
    if (
        payload["schema_version"] != COMPLETED_SCHEMA_VERSION
        or type(payload["attempt_number"]) is not int
        or payload["attempt_number"] != ATTEMPT_NUMBER
        or payload["status"] != "completed"
        or payload["result_classification"] != RESULT_CLASSIFICATION
        or not _valid_sha256(payload["evidence_sha256"])
    ):
        raise EvidenceContractError
    _validate_identity_payload(payload)
    return payload


def _sample_evidence() -> MeasurementEvidence:
    return MeasurementEvidence(
        EVIDENCE_SCHEMA_VERSION,
        runner.OUTPUT_SCHEMA_VERSION,
        "x",
        "0" * 40,
        "0" * 40,
        "0" * 64,
        "0" * 64,
        FIXED_SCENARIO,
        RESULT_CLASSIFICATION,
        1,
        len(SHUTDOWN_STAGE_ORDER),
        "exit_zero",
        0,
    )


def _sample_identity() -> EvidenceIdentity:
    return EvidenceIdentity("x", "0" * 40, "0" * 40, "0" * 64, "0" * 64)


def _validate_identity_payload(payload: dict[str, object]) -> None:
    if (
        type(payload["manifest_schema_version"]) is not str
        or not _valid_sha1(payload["commit_sha"])
        or not _valid_sha1(payload["git_tree_sha"])
        or not _valid_sha256(payload["transfer_archive_sha256"])
        or not _valid_sha256(payload["source_set_sha256"])
    ):
        raise EvidenceContractError


def validate_sidecar(raw: bytes, evidence_raw: bytes) -> str:
    try:
        value = raw.decode("ascii", "strict")
    except UnicodeError:
        raise EvidenceContractError from None
    if not value.endswith("\n") or value.count("\n") != 1:
        raise EvidenceContractError
    digest = value[:-1]
    if not _valid_sha256(digest) or digest != hashlib.sha256(evidence_raw).hexdigest():
        raise EvidenceContractError
    return digest


def _validate_evidence(evidence: MeasurementEvidence) -> None:
    if (
        type(evidence.schema_version) is not str
        or evidence.schema_version != EVIDENCE_SCHEMA_VERSION
        or type(evidence.runner_contract_version) is not str
        or evidence.runner_contract_version != runner.OUTPUT_SCHEMA_VERSION
        or type(evidence.manifest_schema_version) is not str
        or not _valid_sha1(evidence.commit_sha)
        or not _valid_sha1(evidence.git_tree_sha)
        or not _valid_sha256(evidence.transfer_archive_sha256)
        or not _valid_sha256(evidence.source_set_sha256)
        or evidence.fixed_scenario != FIXED_SCENARIO
        or evidence.result_classification != RESULT_CLASSIFICATION
        or type(evidence.duration_ns) is not int
        or evidence.duration_ns <= 0
        or type(evidence.shutdown_stage_count) is not int
        or evidence.shutdown_stage_count != len(SHUTDOWN_STAGE_ORDER)
        or evidence.child_exit_classification != "exit_zero"
        or type(evidence.residual_count) is not int
        or evidence.residual_count != 0
    ):
        raise EvidenceContractError


def _fixed_evidence_directory() -> Path:
    return _REPOSITORY_ROOT.parent / f"{_REPOSITORY_ROOT.name}.shutdown-measurement-evidence"


def _directory_flags() -> int:
    if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")):
        raise EvidenceContractError
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _valid_parent(status: os.stat_result) -> bool:
    try:
        return (
            stat.S_ISDIR(status.st_mode)
            and status.st_uid == os.getuid()
            and not stat.S_IMODE(status.st_mode) & 0o022
            and type(status.st_nlink) is int
            and status.st_nlink >= 2
        )
    except AttributeError, TypeError, ValueError:
        return False


def _valid_private_directory(status: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(status.st_mode)
        and status.st_uid == os.getuid()
        and stat.S_IMODE(status.st_mode) == 0o700
        and status.st_nlink >= 2
    )


def _same(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev and left.st_ino == right.st_ino and left.st_uid == right.st_uid
    )


def _open_parent(handles: _Handles) -> tuple[str, Path]:
    directory = _fixed_evidence_directory()
    parent = directory.parent
    try:
        before = parent.lstat()
        if stat.S_ISLNK(before.st_mode) or not _valid_parent(before):
            raise EvidenceContractError
        handles.parent_fd = os.open(parent, _directory_flags())
        held = os.fstat(handles.parent_fd)
        if not _valid_parent(held) or not _same(before, held):
            raise EvidenceContractError
        handles.parent_stat = held
        return directory.name, parent
    except OSError, TypeError, ValueError:
        raise EvidenceContractError from None


def _create_attempt_directory(handles: _Handles) -> None:
    name, _parent = _open_parent(handles)
    if handles.parent_fd is None:
        raise EvidenceContractError
    try:
        try:
            os.stat(name, dir_fd=handles.parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise EvidenceContractError
        os.mkdir(name, 0o700, dir_fd=handles.parent_fd)
        named = os.stat(name, dir_fd=handles.parent_fd, follow_symlinks=False)
        if not _valid_private_directory(named):
            raise EvidenceContractError
        handles.directory_fd = os.open(name, _directory_flags(), dir_fd=handles.parent_fd)
        held = os.fstat(handles.directory_fd)
        if not _valid_private_directory(held) or not _same(named, held):
            raise EvidenceContractError
        handles.directory_stat = held
    except OSError, TypeError, ValueError:
        raise EvidenceContractError from None


def _revalidate_directory(handles: _Handles) -> None:
    if (
        handles.parent_fd is None
        or handles.directory_fd is None
        or handles.parent_stat is None
        or handles.directory_stat is None
    ):
        raise EvidenceContractError
    name = _fixed_evidence_directory().name
    try:
        parent = os.fstat(handles.parent_fd)
        named = os.stat(name, dir_fd=handles.parent_fd, follow_symlinks=False)
        held = os.fstat(handles.directory_fd)
    except OSError:
        raise EvidenceContractError from None
    if (
        not _valid_parent(parent)
        or not _same(parent, handles.parent_stat)
        or not _valid_private_directory(named)
        or not _valid_private_directory(held)
        or not _same(named, handles.directory_stat)
        or not _same(held, handles.directory_stat)
    ):
        raise EvidenceContractError


def _write_new(handles: _Handles, name: str, payload: bytes) -> None:
    _revalidate_directory(handles)
    if handles.directory_fd is None:
        raise EvidenceContractError
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    write_fd: int | None = None
    read_fd: int | None = None
    primary: BaseException | None = None
    cleanup_failed = False
    try:
        write_fd = os.open(name, flags, 0o600, dir_fd=handles.directory_fd)
        created = os.fstat(write_fd)
        if (
            not stat.S_ISREG(created.st_mode)
            or created.st_uid != os.getuid()
            or stat.S_IMODE(created.st_mode) != 0o600
            or created.st_nlink != 1
        ):
            raise EvidenceContractError
        if os.write(write_fd, payload) != len(payload):
            raise EvidenceContractError
        os.fsync(write_fd)
        named = os.stat(name, dir_fd=handles.directory_fd, follow_symlinks=False)
        if not _same(created, named) or named.st_nlink != 1:
            raise EvidenceContractError
        read_fd = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=handles.directory_fd
        )
        read_status = os.fstat(read_fd)
        if not _same(created, read_status) or os.read(read_fd, len(payload) + 1) != payload:
            raise EvidenceContractError
    except BaseException as error:  # noqa: BLE001 - preserve cancellation and primary identity
        if isinstance(
            error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit, EvidenceContractError)
        ):
            primary = error
        else:
            primary = EvidenceContractError()
    finally:
        for descriptor_name in ("read_fd", "write_fd"):
            descriptor = read_fd if descriptor_name == "read_fd" else write_fd
            if descriptor is None:
                continue
            if descriptor_name == "read_fd":
                read_fd = None
            else:
                write_fd = None
            try:
                os.close(descriptor)
            except BaseException:  # noqa: BLE001 - cleanup cannot replace the primary error
                cleanup_failed = True
    if primary is not None:
        raise primary
    if cleanup_failed:
        raise EvidenceContractError


def _close(handles: _Handles) -> None:
    for descriptor in (handles.directory_fd, handles.parent_fd):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    handles.directory_fd = None
    handles.parent_fd = None


def _manifest_and_identity() -> EvidenceIdentity:
    try:
        manifest = runner._external_manifest()
        runner._verified_source_set(manifest)
        return _identity(manifest)
    except asyncio.CancelledError, KeyboardInterrupt, SystemExit:
        raise
    except Exception:  # noqa: BLE001 - fixed public error boundary
        raise EvidenceContractError from None


def _evidence_from_output(
    output: runner.ShutdownMeasurementOutput, identity: EvidenceIdentity
) -> MeasurementEvidence:
    expected_stages = (
        runner.MeasurementStage("ready", "observed"),
        runner.MeasurementStage("sigint", "sent"),
        runner.MeasurementStage("cleanup_start", "observed"),
        *(
            runner.MeasurementStage(stage.name.lower(), "completed")
            for stage in SHUTDOWN_STAGE_ORDER
        ),
        runner.MeasurementStage("cleanup_complete", "observed"),
        runner.MeasurementStage("exit", "observed"),
    )
    if (
        type(output) is not runner.ShutdownMeasurementOutput
        or output.schema_version != runner.OUTPUT_SCHEMA_VERSION
        or output.commit_identity != identity.commit_sha
        or output.fixed_scenario != FIXED_SCENARIO
        or output.anonymous_run_number != "run-000001"
        or type(output.relative_monotonic_duration_ns) is not int
        or output.relative_monotonic_duration_ns <= 0
        or output.stages != expected_stages
        or output.exit_classification != "exit_zero"
        or type(output.residual_count) is not int
        or output.residual_count != 0
    ):
        raise EvidenceContractError
    return MeasurementEvidence(
        EVIDENCE_SCHEMA_VERSION,
        runner.OUTPUT_SCHEMA_VERSION,
        identity.manifest_schema_version,
        identity.commit_sha,
        identity.git_tree_sha,
        identity.transfer_archive_sha256,
        identity.source_set_sha256,
        FIXED_SCENARIO,
        RESULT_CLASSIFICATION,
        output.relative_monotonic_duration_ns,
        len(SHUTDOWN_STAGE_ORDER),
        output.exit_classification,
        output.residual_count,
    )


def run_once() -> tuple[MeasurementEvidence, str]:
    """Create the non-reusable attempt directory and invoke the runner once."""

    identity = _manifest_and_identity()
    if not callable(runner.run_fixed_linux_shutdown_measurement):
        raise EvidenceContractError
    handles = _Handles()
    try:
        _create_attempt_directory(handles)
        _write_new(handles, PREPARED_NAME, serialize_prepared(identity))
        output = runner.run_fixed_linux_shutdown_measurement()
        evidence = _evidence_from_output(output, identity)
        raw = serialize_evidence(evidence)
        evidence_sha256 = hashlib.sha256(raw).hexdigest()
        if evidence_sha256 != hashlib.sha256(raw).hexdigest():
            raise EvidenceContractError
        _write_new(handles, EVIDENCE_NAME, raw)
        sidecar = evidence_sha256.encode("ascii") + b"\n"
        validate_sidecar(sidecar, raw)
        _write_new(handles, SIDECAR_NAME, sidecar)
        _write_new(handles, COMPLETED_NAME, serialize_completed(identity, evidence_sha256))
        return evidence, evidence_sha256
    except asyncio.CancelledError, KeyboardInterrupt, SystemExit:
        raise
    except EvidenceContractError:
        raise
    except Exception:  # noqa: BLE001 - fixed public error boundary
        raise EvidenceContractError from None
    finally:
        _close(handles)


def main() -> int:
    if len(sys.argv) != 1:
        print("FORMAL_MEASUREMENT_EVIDENCE_FAILURE", file=sys.stderr, flush=True)
        return 2
    try:
        _evidence, evidence_sha256 = run_once()
    except asyncio.CancelledError, KeyboardInterrupt, SystemExit:
        raise
    except EvidenceContractError:
        print("FORMAL_MEASUREMENT_EVIDENCE_FAILURE", file=sys.stderr, flush=True)
        return 1
    print(f"FORMAL_MEASUREMENT_EVIDENCE_SUCCESS {evidence_sha256}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
