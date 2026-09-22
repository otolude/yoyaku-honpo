"""Non-cyclic contract for externally attested test-only source identity."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

MANIFEST_SCHEMA_VERSION = "shutdown-measurement-external-manifest-v1"
SOURCE_SET_SCHEMA_VERSION = "shutdown-measurement-source-set-v1"
SOURCE_SET_RELATIVE_PATHS = (
    "src/discord_ai_reminder_bot/__init__.py",
    "src/discord_ai_reminder_bot/application/__init__.py",
    "src/discord_ai_reminder_bot/application/shutdown_measurement.py",
    "src/discord_ai_reminder_bot/infrastructure/__init__.py",
    "src/discord_ai_reminder_bot/infrastructure/shutdown_measurement_harness.py",
    "tests/__init__.py",
    "tests/support/__init__.py",
    "tests/support/linux_shutdown_measurement_runner.py",
    "tests/support/snapshot_child_bootstrap_policy.py",
    "tests/support/shutdown_measurement_source_metadata.py",
    "tests/support/shutdown_process_harness.py",
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "commit_sha",
        "git_tree_sha",
        "transfer_archive_sha256",
        "source_set_sha256",
    }
)
_SHA1_HEX = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")


class SourceManifestError(RuntimeError):
    """Fixed internal failure for external-manifest validation."""


@dataclass(frozen=True, slots=True)
class ExternalSourceManifest:
    schema_version: str
    commit_sha: str
    git_tree_sha: str
    transfer_archive_sha256: str
    source_set_sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedSourceSet:
    """One immutable read of the exact repository bytes approved for execution."""

    digest: str
    files: tuple[tuple[str, bytes], ...]


def _read_regular_file(path: Path, *, required_mode: int | None) -> bytes:
    try:
        path_stat = path.lstat()
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            raise SourceManifestError
        if required_mode is not None and stat.S_IMODE(path_stat.st_mode) != required_mode:
            raise SourceManifestError
        if path_stat.st_uid != os.getuid():
            raise SourceManifestError
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError, TypeError, ValueError:
        raise SourceManifestError from None
    try:
        descriptor_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or descriptor_stat.st_uid != path_stat.st_uid
            or descriptor_stat.st_dev != path_stat.st_dev
            or descriptor_stat.st_ino != path_stat.st_ino
            or (
                required_mode is not None and stat.S_IMODE(descriptor_stat.st_mode) != required_mode
            )
        ):
            raise SourceManifestError
        chunks: list[bytes] = []
        remaining = descriptor_stat.st_size + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != descriptor_stat.st_size:
            raise SourceManifestError
        return payload
    except OSError, TypeError, ValueError:
        raise SourceManifestError from None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def read_verified_source_set(repository_root: Path) -> VerifiedSourceSet:
    if not isinstance(repository_root, Path):
        raise SourceManifestError
    digest = hashlib.sha256()
    digest.update(SOURCE_SET_SCHEMA_VERSION.encode("ascii") + b"\0")
    files: list[tuple[str, bytes]] = []
    for relative_path in SOURCE_SET_RELATIVE_PATHS:
        path = repository_root / relative_path
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            raise SourceManifestError from None
        if not resolved.is_relative_to(repository_root):
            raise SourceManifestError
        payload = _read_regular_file(path, required_mode=None)
        encoded_path = relative_path.encode("ascii")
        digest.update(encoded_path + b"\0" + str(len(payload)).encode("ascii") + b"\0" + payload)
        files.append((relative_path, payload))
    return VerifiedSourceSet(digest.hexdigest(), tuple(files))


def source_set_digest(repository_root: Path) -> str:
    """Return the digest only when callers do not need the verified bytes."""

    return read_verified_source_set(repository_root).digest


def load_external_source_manifest(path: Path) -> ExternalSourceManifest:
    if not isinstance(path, Path):
        raise SourceManifestError
    try:
        raw = _read_regular_file(path, required_mode=0o600)
        decoded = raw.decode("ascii", errors="strict")
        loaded = json.loads(decoded)
        if type(loaded) is not dict or set(loaded) != _MANIFEST_KEYS:
            raise SourceManifestError
        if any(type(value) is not str for value in loaded.values()):
            raise SourceManifestError
        canonical = json.dumps(loaded, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        if raw != canonical.encode("ascii"):
            raise SourceManifestError
        manifest = ExternalSourceManifest(**loaded)
        if (
            manifest.schema_version != MANIFEST_SCHEMA_VERSION
            or _SHA1_HEX.fullmatch(manifest.commit_sha) is None
            or _SHA1_HEX.fullmatch(manifest.git_tree_sha) is None
            or _SHA256_HEX.fullmatch(manifest.transfer_archive_sha256) is None
            or _SHA256_HEX.fullmatch(manifest.source_set_sha256) is None
        ):
            raise SourceManifestError
        return manifest
    except json.JSONDecodeError, TypeError, UnicodeError, SourceManifestError:
        raise SourceManifestError from None
