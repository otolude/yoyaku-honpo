"""Pure, fail-closed preparation for a future systemd deployment.

The module validates an immutable candidate contract.  It deliberately does
not read files, environment variables, processes, or the network, and it has
no concrete unit renderer or service-manager adapter.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import PurePosixPath
from typing import final

from discord_ai_reminder_bot.post_draft_provider_config import (
    CLIENT_SHUTDOWN_STRATEGY_APPROVED,
)

__all__ = (
    "INSTALLABLE_SYSTEMD_ARTIFACT_GENERATION_APPROVED",
    "REQUIRED_SYSTEMD_CANDIDATE_FIELDS",
    "SystemdCandidateValidationResult",
    "SystemdDeploymentFailure",
    "SystemdDeploymentValidationError",
    "build_systemd_deployment_candidate",
    "require_installable_systemd_artifact_approval",
    "snapshot_systemd_deployment_candidate",
    "validate_systemd_deployment_candidate",
    "verify_systemd_candidate_snapshot",
)

MAX_SYSTEMD_INTEGER = 2_147_483_647
INSTALLABLE_SYSTEMD_ARTIFACT_GENERATION_APPROVED = False
_MAX_SYSTEMD_INTEGER_DIGITS = len(str(MAX_SYSTEMD_INTEGER))
_MAX_SYSTEMD_INTEGER_BIT_LENGTH = MAX_SYSTEMD_INTEGER.bit_length()

_CANONICAL_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*")
_SERVICE_IDENTITY = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_ABSOLUTE_PATH = re.compile(r"/[A-Za-z0-9._/-]+")
_COMMIT_IDENTIFIER = re.compile(r"[0-9a-f]{40}")

_SIGNALS = frozenset({"SIGINT", "SIGTERM"})
_RESERVED_IDENTITIES = frozenset({"daemon", "nobody", "root"})
_KILL_MODES = frozenset({"control-group", "mixed"})
_RESTART_POLICIES = frozenset({"no", "on-failure"})
_PROTECT_SYSTEM_VALUES = frozenset({"full", "strict"})
_PROTECT_HOME_VALUES = frozenset({"yes", "read-only", "tmpfs"})
_UMASK_VALUES = frozenset({"0027", "0077"})
_READINESS_MARKERS = frozenset({"startup_recovery_complete"})
_ADDRESS_FAMILIES = frozenset({"AF_INET", "AF_INET6", "AF_UNIX"})
_CAPABILITIES = frozenset(
    {
        "CAP_AUDIT_CONTROL",
        "CAP_AUDIT_READ",
        "CAP_AUDIT_WRITE",
        "CAP_BLOCK_SUSPEND",
        "CAP_BPF",
        "CAP_CHECKPOINT_RESTORE",
        "CAP_CHOWN",
        "CAP_DAC_OVERRIDE",
        "CAP_DAC_READ_SEARCH",
        "CAP_FOWNER",
        "CAP_FSETID",
        "CAP_IPC_LOCK",
        "CAP_IPC_OWNER",
        "CAP_KILL",
        "CAP_LEASE",
        "CAP_LINUX_IMMUTABLE",
        "CAP_MAC_ADMIN",
        "CAP_MAC_OVERRIDE",
        "CAP_MKNOD",
        "CAP_NET_ADMIN",
        "CAP_NET_BIND_SERVICE",
        "CAP_NET_BROADCAST",
        "CAP_NET_RAW",
        "CAP_PERFMON",
        "CAP_SETFCAP",
        "CAP_SETGID",
        "CAP_SETPCAP",
        "CAP_SETUID",
        "CAP_SYSLOG",
        "CAP_SYS_ADMIN",
        "CAP_SYS_BOOT",
        "CAP_SYS_CHROOT",
        "CAP_SYS_MODULE",
        "CAP_SYS_NICE",
        "CAP_SYS_PACCT",
        "CAP_SYS_PTRACE",
        "CAP_SYS_RAWIO",
        "CAP_SYS_RESOURCE",
        "CAP_SYS_TIME",
        "CAP_SYS_TTY_CONFIG",
        "CAP_WAKE_ALARM",
    }
)


class SystemdDeploymentFailure(StrEnum):
    """Fixed classifications that never reflect caller input."""

    INVALID_CONTAINER = "invalid_candidate_container"
    UNKNOWN_FIELD = "unknown_candidate_field"
    DUPLICATE_FIELD = "duplicate_candidate_field"
    MISSING_FIELD = "missing_candidate_field"
    INVALID_VALUE = "invalid_candidate_value"
    SNAPSHOT_MISMATCH = "candidate_snapshot_mismatch"
    GOVERNANCE_CLOSED = "systemd_deployment_governance_closed"


class SystemdDeploymentValidationError(ValueError):
    """A fixed, non-reflecting validation failure."""

    def __init__(self, classification: SystemdDeploymentFailure) -> None:
        self.classification = classification
        super().__init__(classification.value)


def _reject(classification: SystemdDeploymentFailure) -> None:
    raise SystemdDeploymentValidationError(classification) from None


def _parse_canonical_positive_integer(value: str) -> int:
    """Convert only a pre-bounded, canonical decimal string."""

    return int(value)


def _exact_int_within_systemd_bound(value: int) -> bool:
    """Check size without formatting or comparing a potentially huge integer."""

    return value.bit_length() <= _MAX_SYSTEMD_INTEGER_BIT_LENGTH


def _positive_integer(value: object) -> int:
    if type(value) is int:
        if not _exact_int_within_systemd_bound(value):
            _reject(SystemdDeploymentFailure.INVALID_VALUE)
        parsed = value
    elif type(value) is str:
        # ``len`` is O(1) for str and prevents regex/int work on unbounded input.
        if len(value) > _MAX_SYSTEMD_INTEGER_DIGITS:
            _reject(SystemdDeploymentFailure.INVALID_VALUE)
        if _CANONICAL_POSITIVE_INTEGER.fullmatch(value) is None:
            _reject(SystemdDeploymentFailure.INVALID_VALUE)
        parsed = _parse_canonical_positive_integer(value)
    else:
        _reject(SystemdDeploymentFailure.INVALID_VALUE)
    if parsed <= 0 or parsed > MAX_SYSTEMD_INTEGER:
        _reject(SystemdDeploymentFailure.INVALID_VALUE)
    return parsed


def _strict_boolean(value: object) -> bool:
    if type(value) is not bool:
        _reject(SystemdDeploymentFailure.INVALID_VALUE)
    return value


def _allowlisted_text(value: object, allowed: frozenset[str]) -> str:
    if type(value) is not str or value not in allowed:
        _reject(SystemdDeploymentFailure.INVALID_VALUE)
    return value


def _identity(value: object) -> str:
    if (
        type(value) is not str
        or _SERVICE_IDENTITY.fullmatch(value) is None
        or value in _RESERVED_IDENTITIES
    ):
        _reject(SystemdDeploymentFailure.INVALID_VALUE)
    return value


def _absolute_path(value: object) -> str:
    if type(value) is not str or _ABSOLUTE_PATH.fullmatch(value) is None:
        _reject(SystemdDeploymentFailure.INVALID_VALUE)
    if "%" in value or "//" in value or value.endswith("/"):
        _reject(SystemdDeploymentFailure.INVALID_VALUE)
    raw_parts = value.split("/")[1:]
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        _reject(SystemdDeploymentFailure.INVALID_VALUE)
    normalized = str(PurePosixPath(value))
    if normalized != value or not normalized.startswith("/"):
        _reject(SystemdDeploymentFailure.INVALID_VALUE)
    return value


def _exec_start(value: object) -> tuple[str, str, str]:
    if type(value) is not tuple or len(value) != 3:
        _reject(SystemdDeploymentFailure.INVALID_VALUE)
    executable, module_flag, module_name = value
    return (
        _absolute_path(executable),
        _allowlisted_text(module_flag, frozenset({"-m"})),
        _allowlisted_text(module_name, frozenset({"discord_ai_reminder_bot"})),
    )


def _commit(value: object) -> str:
    if type(value) is not str or _COMMIT_IDENTIFIER.fullmatch(value) is None:
        _reject(SystemdDeploymentFailure.INVALID_VALUE)
    return value


def _allowlisted_tuple(
    value: object,
    allowed: frozenset[str],
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if type(value) is not tuple or any(type(item) is not str for item in value):
        _reject(SystemdDeploymentFailure.INVALID_VALUE)
    if (
        (not allow_empty and not value)
        or len(set(value)) != len(value)
        or any(item not in allowed for item in value)
    ):
        _reject(SystemdDeploymentFailure.INVALID_VALUE)
    return tuple(sorted(value))


@final
@dataclass(frozen=True, slots=True, repr=False, init=False)
class _SystemdDeploymentCandidate:
    """Factory-only complete deployment input; never automatically approved."""

    service_user: str
    service_group: str
    working_directory: str
    exec_start: tuple[str, str, str]
    environment_file: str
    kill_signal: str
    kill_mode: str
    timeout_stop_sec: int
    send_sigkill: bool
    restart: str
    restart_sec: int
    start_limit_interval_sec: int
    start_limit_burst: int
    readiness_journal_marker: str
    release_identifier: str
    rollback_target: str
    no_new_privileges: bool
    private_tmp: bool
    protect_system: str
    protect_home: str
    capability_bounding_set: tuple[str, ...]
    restrict_address_families: tuple[str, ...]
    lock_personality: bool
    restrict_suid_sgid: bool
    umask: str

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        _reject(SystemdDeploymentFailure.INVALID_CONTAINER)

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("systemd deployment candidate subclassing is prohibited")

    def __repr__(self) -> str:
        return "<SystemdDeploymentCandidate redacted>"


REQUIRED_SYSTEMD_CANDIDATE_FIELDS = tuple(
    field.name for field in fields(_SystemdDeploymentCandidate)
)
_REQUIRED_SYSTEMD_CANDIDATE_FIELD_SET = frozenset(REQUIRED_SYSTEMD_CANDIDATE_FIELDS)


@final
@dataclass(frozen=True, slots=True, repr=False, init=False)
class _SystemdCandidateSnapshot:
    """Immutable canonical snapshot plus a one-way comparison digest."""

    canonical_fields: tuple[tuple[str, object], ...]
    fingerprint: str

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        _reject(SystemdDeploymentFailure.INVALID_CONTAINER)

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("systemd candidate snapshot subclassing is prohibited")

    def __repr__(self) -> str:
        return "<SystemdCandidateSnapshot redacted>"


@dataclass(frozen=True, slots=True)
class SystemdCandidateValidationResult:
    valid: bool
    candidate: _SystemdDeploymentCandidate | None
    failure: SystemdDeploymentFailure | None


def _candidate_kwargs(
    pairs: object,
) -> dict[str, object]:
    if type(pairs) is not tuple:
        _reject(SystemdDeploymentFailure.INVALID_CONTAINER)
    values: dict[str, object] = {}
    for pair in pairs:
        if type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not str:
            _reject(SystemdDeploymentFailure.INVALID_CONTAINER)
        name, value = pair
        if name not in _REQUIRED_SYSTEMD_CANDIDATE_FIELD_SET:
            _reject(SystemdDeploymentFailure.UNKNOWN_FIELD)
        if name in values:
            _reject(SystemdDeploymentFailure.DUPLICATE_FIELD)
        values[name] = value
    if values.keys() != _REQUIRED_SYSTEMD_CANDIDATE_FIELD_SET:
        _reject(SystemdDeploymentFailure.MISSING_FIELD)
    return values


def _normalized_candidate_values(values: dict[str, object]) -> dict[str, object]:
    return {
        "service_user": _identity(values["service_user"]),
        "service_group": _identity(values["service_group"]),
        "working_directory": _absolute_path(values["working_directory"]),
        "exec_start": _exec_start(values["exec_start"]),
        "environment_file": _absolute_path(values["environment_file"]),
        "kill_signal": _allowlisted_text(values["kill_signal"], _SIGNALS),
        "kill_mode": _allowlisted_text(values["kill_mode"], _KILL_MODES),
        "timeout_stop_sec": _positive_integer(values["timeout_stop_sec"]),
        "send_sigkill": _strict_boolean(values["send_sigkill"]),
        "restart": _allowlisted_text(values["restart"], _RESTART_POLICIES),
        "restart_sec": _positive_integer(values["restart_sec"]),
        "start_limit_interval_sec": _positive_integer(values["start_limit_interval_sec"]),
        "start_limit_burst": _positive_integer(values["start_limit_burst"]),
        "readiness_journal_marker": _allowlisted_text(
            values["readiness_journal_marker"], _READINESS_MARKERS
        ),
        "release_identifier": _commit(values["release_identifier"]),
        "rollback_target": _commit(values["rollback_target"]),
        "no_new_privileges": _strict_boolean(values["no_new_privileges"]),
        "private_tmp": _strict_boolean(values["private_tmp"]),
        "protect_system": _allowlisted_text(values["protect_system"], _PROTECT_SYSTEM_VALUES),
        "protect_home": _allowlisted_text(values["protect_home"], _PROTECT_HOME_VALUES),
        # Empty systemd CapabilityBoundingSet semantics are not approved.
        "capability_bounding_set": _allowlisted_tuple(
            values["capability_bounding_set"], _CAPABILITIES, allow_empty=False
        ),
        "restrict_address_families": _allowlisted_tuple(
            values["restrict_address_families"], _ADDRESS_FAMILIES, allow_empty=False
        ),
        "lock_personality": _strict_boolean(values["lock_personality"]),
        "restrict_suid_sgid": _strict_boolean(values["restrict_suid_sgid"]),
        "umask": _allowlisted_text(values["umask"], _UMASK_VALUES),
    }


def _make_candidate(normalized: dict[str, object]) -> _SystemdDeploymentCandidate:
    candidate = object.__new__(_SystemdDeploymentCandidate)
    for name in REQUIRED_SYSTEMD_CANDIDATE_FIELDS:
        object.__setattr__(candidate, name, normalized[name])
    return candidate


def build_systemd_deployment_candidate(
    pairs: object,
) -> _SystemdDeploymentCandidate:
    """Build an exact immutable candidate from a duplicate-aware tuple."""

    values = _candidate_kwargs(pairs)
    try:
        return _make_candidate(_normalized_candidate_values(values))
    except SystemdDeploymentValidationError:
        raise
    except TypeError, ValueError:
        _reject(SystemdDeploymentFailure.INVALID_VALUE)


def validate_systemd_deployment_candidate(
    pairs: object,
) -> SystemdCandidateValidationResult:
    """Return a fixed validation result without reflecting inputs."""

    try:
        candidate = build_systemd_deployment_candidate(pairs)
    except SystemdDeploymentValidationError as error:
        return SystemdCandidateValidationResult(
            valid=False,
            candidate=None,
            failure=error.classification,
        )
    return SystemdCandidateValidationResult(valid=True, candidate=candidate, failure=None)


def _canonical_fields(
    candidate: _SystemdDeploymentCandidate,
) -> tuple[tuple[str, object], ...]:
    if type(candidate) is not _SystemdDeploymentCandidate:
        _reject(SystemdDeploymentFailure.INVALID_CONTAINER)
    return tuple((name, getattr(candidate, name)) for name in REQUIRED_SYSTEMD_CANDIDATE_FIELDS)


def snapshot_systemd_deployment_candidate(
    candidate: _SystemdDeploymentCandidate,
) -> _SystemdCandidateSnapshot:
    canonical_fields = _canonical_fields(candidate)
    encoded = json.dumps(
        canonical_fields,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    snapshot = object.__new__(_SystemdCandidateSnapshot)
    object.__setattr__(snapshot, "canonical_fields", canonical_fields)
    object.__setattr__(snapshot, "fingerprint", hashlib.sha256(encoded).hexdigest())
    return snapshot


def verify_systemd_candidate_snapshot(
    candidate: _SystemdDeploymentCandidate,
    snapshot: _SystemdCandidateSnapshot,
) -> None:
    if type(snapshot) is not _SystemdCandidateSnapshot:
        _reject(SystemdDeploymentFailure.INVALID_CONTAINER)
    current = snapshot_systemd_deployment_candidate(candidate)
    if current.canonical_fields != snapshot.canonical_fields or not hmac.compare_digest(
        current.fingerprint, snapshot.fingerprint
    ):
        _reject(SystemdDeploymentFailure.SNAPSHOT_MISMATCH)


def require_installable_systemd_artifact_approval() -> None:
    """Future rendering boundary; closed by source governance, never environment."""

    if (
        CLIENT_SHUTDOWN_STRATEGY_APPROVED is not True
        or INSTALLABLE_SYSTEMD_ARTIFACT_GENERATION_APPROVED is not True
    ):
        _reject(SystemdDeploymentFailure.GOVERNANCE_CLOSED)
    # No renderer exists even if both source constants are changed in the future.
    _reject(SystemdDeploymentFailure.GOVERNANCE_CLOSED)
