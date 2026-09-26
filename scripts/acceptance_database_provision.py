"""Root-owned future helper source for one acceptance PostgreSQL provisioning.

This source is deliberately *not* an executable repository workflow.  It is
reviewed here and must later be copied new-only to a root-owned canonical path.
The existing config-only preflight is not imported or extended.  This helper
will only invoke a held standalone Compose executable once, with ``up`` and
``--pull never``; it never pulls, builds, removes, retries, or accepts argv.
"""

from __future__ import annotations

import grp
import hashlib
import json
import os
import pwd
import re
import socket
import stat
import subprocess
import sys
from asyncio import CancelledError
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote

FAILURE_MARKER = "ACCEPTANCE_DB_PROVISION_FAILED"
SUCCESS_MARKER = "ACCEPTANCE_DB_PROVISIONED"
FAILURE_EXIT_CODE = 64

REPOSITORY_ROOT = Path("/home/yumena/projects/discord-ai-reminder-bot")
PRIVATE_ENV_NAME = ".env.acceptance-postgres"
PRIVATE_SECRET_DIRECTORY_NAME = ".acceptance-postgres-secrets"
SECRET_FILE_NAMES = ("postgres-user", "postgres-password", "postgres-database")
COMPOSE_FILE_NAME = "compose.acceptance.provision.yaml"
CANONICAL_HELPER_PATH = Path(
    "/usr/local/libexec/discord-ai-reminder-bot/acceptance_database_provision.py"
)
CANONICAL_INTERPRETER = Path("/usr/bin/python3.14")
COMPOSE_EXECUTABLE = Path("/usr/lib/docker/cli-plugins/docker-compose")
COMPOSE_SHA256 = "c57ab918abd5b05ca7e7d0f275875dd1330a695074f309dc9eab1b49efafcd4b"
EXPECTED_IMAGE_DIGEST = "sha256:882236b897e39051d2368c5ccc6cda944904723506b2dfc97f2a8f5bc9afa382"
EXPECTED_IMAGE_REFERENCE = f"postgres@{EXPECTED_IMAGE_DIGEST}"
EXPECTED_PORT = 25432
EXPECTED_PRIVATE_UID = 1000
EXPECTED_PRIVATE_GID = 1000
ROOT_ANCHOR_OWNER_UID = 0
SYSTEM_OWNER_UID = 0
EXPECTED_DOCKER_GROUP = "docker"
UNPRIVILEGED_DOCKER_DENIED_USERS = ("yumena",)
EXPECTED_CONTAINER = "discord-ai-reminder-bot-acceptance-postgres"
EXPECTED_VOLUME = "discord-ai-reminder-bot-acceptance-postgres-data"
EXPECTED_NETWORK = "discord-ai-reminder-bot-acceptance-postgres-network"
EXPECTED_PROJECT = "discord-ai-reminder-bot-acceptance-db"
DOCKER_SOCKET = Path("/run/docker.sock")
DOCKER_DATA_ROOT = Path("/var/lib/docker")
RUNTIME_SECRET_DIRECTORY = Path("/run/discord-ai-reminder-bot-acceptance-postgres-secrets")
WINDOWS_PORT_PRECHECK_ENVIRONMENT = "ACCEPTANCE_POSTGRES_WINDOWS_PORT_PRECHECK"
WINDOWS_PORT_PRECHECK_VALUE = "USER_ATTESTED_25432"
MINIMUM_DATA_ROOT_FREE_BYTES = 10 * 1024 * 1024 * 1024
SUBPROCESS_TIMEOUT_SECONDS = 60
DOCKER_API_TIMEOUT_SECONDS = 3
SANITIZED_ENV = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"}
PROHIBITED_ENVIRONMENT_PREFIXES = (
    "DOCKER_",
    "COMPOSE_",
    "HTTP_",
    "HTTPS_",
    "ALL_PROXY",
    "NO_PROXY",
)

EXPECTED_COMPOSE_CONTRACT: dict[str, object] = {
    "name": EXPECTED_PROJECT,
    "services": {
        "acceptance_postgres": {
            "container_name": EXPECTED_CONTAINER,
            "image": "postgres@${ACCEPTANCE_POSTGRES_IMAGE_DIGEST:?set an immutable sha256 digest}",
            "platform": "linux/amd64",
            "pull_policy": "never",
            "environment": {
                "POSTGRES_DB_FILE": "/run/acceptance-postgres-secrets/postgres-database",
                "POSTGRES_USER_FILE": "/run/acceptance-postgres-secrets/postgres-user",
                "POSTGRES_PASSWORD_FILE": "/run/acceptance-postgres-secrets/postgres-password",
                "POSTGRES_INITDB_ARGS": "--auth=scram-sha-256",
            },
            "ports": [
                "127.0.0.1:${ACCEPTANCE_POSTGRES_HOST_PORT:?set an acceptance loopback host port}:5432"
            ],
            "volumes": [
                "acceptance_postgres_data:/var/lib/postgresql/data",
                {
                    "type": "bind",
                    "source": "${ACCEPTANCE_POSTGRES_RUNTIME_SECRET_DIRECTORY:?set only by the root-owned provisioning helper}",
                    "target": "/run/acceptance-postgres-secrets",
                    "read_only": True,
                    "bind": {"create_host_path": False},
                },
            ],
            "networks": ["acceptance_postgres_network"],
            "healthcheck": {
                "test": ["CMD-SHELL", "pg_isready -q"],
                "interval": "5s",
                "timeout": "3s",
                "retries": 5,
                "start_period": "10s",
            },
            "logging": {"driver": "json-file", "options": {"max-size": "1m", "max-file": "3"}},
            "restart": "no",
            "stop_grace_period": "20s",
        }
    },
    "volumes": {"acceptance_postgres_data": {"name": EXPECTED_VOLUME}},
    "networks": {"acceptance_postgres_network": {"name": EXPECTED_NETWORK}},
}


class ProvisioningFailure(Exception):
    """Fixed non-reflecting failure classification."""


def _fail() -> NoReturn:
    raise ProvisioningFailure(FAILURE_MARKER)


def _strict_int(value: object) -> bool:
    return type(value) is int


def _strict_metadata(metadata: os.stat_result) -> bool:
    return all(
        _strict_int(getattr(metadata, field, None))
        for field in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
    )


def _same_identity(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        _strict_metadata(before)
        and _strict_metadata(after)
        and (
            before.st_dev,
            before.st_ino,
            stat.S_IFMT(before.st_mode),
            stat.S_IMODE(before.st_mode),
            before.st_uid,
            before.st_gid,
            before.st_nlink,
            before.st_size,
        )
        == (
            after.st_dev,
            after.st_ino,
            stat.S_IFMT(after.st_mode),
            stat.S_IMODE(after.st_mode),
            after.st_uid,
            after.st_gid,
            after.st_nlink,
            after.st_size,
        )
    )


def _require_directory(metadata: os.stat_result, *, owner: int | None, mode: int | None) -> None:
    if (
        not _strict_metadata(metadata)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_nlink < 2
        or (owner is not None and metadata.st_uid != owner)
        or (mode is not None and stat.S_IMODE(metadata.st_mode) != mode)
        or (mode is None and stat.S_IMODE(metadata.st_mode) & 0o022)
    ):
        _fail()


def _require_regular(
    metadata: os.stat_result, *, owner: int, mode: int | None, nonempty: bool
) -> None:
    if (
        not _strict_metadata(metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != owner
        or metadata.st_nlink != 1
        or (mode is not None and stat.S_IMODE(metadata.st_mode) != mode)
        or (mode is None and stat.S_IMODE(metadata.st_mode) & 0o022)
        or (nonempty and metadata.st_size <= 0)
    ):
        _fail()


def _open_directory(path: str, *, dir_fd: int | None = None) -> int:
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)


def _open_regular(path: str, *, dir_fd: int) -> int:
    return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)


def _require_canonical_system_regular(path: Path, *, mode: int) -> None:
    """Check a root-owned absolute system file through the filesystem root FD."""
    descriptors: list[int] = []
    primary: BaseException | None = None
    try:
        if not path.is_absolute() or not path.parts[1:] or Path(__file__).is_symlink():
            _fail()
        root = _open_directory("/")
        descriptors.append(root)
        _require_directory(os.fstat(root), owner=ROOT_ANCHOR_OWNER_UID, mode=None)
        parent = root
        for component in path.parts[1:-1]:
            parent = _open_directory(component, dir_fd=parent)
            descriptors.append(parent)
            _require_directory(os.fstat(parent), owner=SYSTEM_OWNER_UID, mode=None)
        target = _open_regular(path.name, dir_fd=parent)
        descriptors.append(target)
        _require_regular(os.fstat(target), owner=SYSTEM_OWNER_UID, mode=mode, nonempty=True)
        if os.fstat(target).st_gid != SYSTEM_OWNER_UID:
            _fail()
    except (CancelledError, KeyboardInterrupt, SystemExit) as error:
        primary = error
    except BaseException:  # noqa: BLE001 - system metadata detail is intentionally non-reflecting
        primary = ProvisioningFailure(FAILURE_MARKER)
    finally:
        cleanup = _close_all(tuple(reversed(descriptors)))
    if primary is not None:
        raise primary
    if cleanup is not None:
        _fail()


@dataclass
class _RuntimeSelf:
    helper_root: int
    helper_chain: tuple[int, ...]
    helper: int
    interpreter_root: int
    interpreter_chain: tuple[int, ...]
    interpreter: int
    identities: tuple[os.stat_result, ...]
    helper_hash: str

    @property
    def descriptors(self) -> tuple[int, ...]:
        return (
            self.helper_root,
            *self.helper_chain,
            self.helper,
            self.interpreter_root,
            *self.interpreter_chain,
            self.interpreter,
        )

    def revalidate(self) -> None:
        transient: list[int] = []
        try:
            if any(
                not _same_identity(before, os.fstat(descriptor))
                for before, descriptor in zip(self.identities, self.descriptors, strict=True)
            ):
                _fail()
            if _sha256_at_start(self.helper) != self.helper_hash:
                _fail()
            reopened_helper = _open_canonical_system_regular(CANONICAL_HELPER_PATH, transient)
            reopened_interpreter = _open_canonical_system_regular(CANONICAL_INTERPRETER, transient)
            if not _same_identity(
                os.fstat(self.helper), os.fstat(reopened_helper)
            ) or not _same_identity(os.fstat(self.interpreter), os.fstat(reopened_interpreter)):
                _fail()
            executable = os.stat("/proc/self/exe")
            if not _same_identity(os.fstat(self.interpreter), executable):
                _fail()
        except CancelledError, KeyboardInterrupt, SystemExit:
            raise
        except BaseException:  # noqa: BLE001 - fixed, non-reflecting canonical failure
            _fail()
        finally:
            cleanup = _close_all(tuple(reversed(transient)))
        if cleanup is not None:
            _fail()


def _sha256_at_start(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = _sha256(descriptor)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest


def _open_canonical_system_regular(path: Path, descriptors: list[int]) -> int:
    if not path.is_absolute() or not path.parts[1:] or path.is_symlink():
        _fail()
    root = _open_directory("/")
    descriptors.append(root)
    _require_directory(os.fstat(root), owner=ROOT_ANCHOR_OWNER_UID, mode=None)
    parent = root
    for component in path.parts[1:-1]:
        parent = _open_directory(component, dir_fd=parent)
        descriptors.append(parent)
        _require_directory(os.fstat(parent), owner=SYSTEM_OWNER_UID, mode=None)
    target = _open_regular(path.name, dir_fd=parent)
    descriptors.append(target)
    _require_regular(os.fstat(target), owner=SYSTEM_OWNER_UID, mode=0o755, nonempty=True)
    if os.fstat(target).st_gid != SYSTEM_OWNER_UID:
        _fail()
    return target


def _hold_runtime_self() -> _RuntimeSelf:
    descriptors: list[int] = []
    try:
        if Path(__file__) != CANONICAL_HELPER_PATH or Path(sys.executable) != CANONICAL_INTERPRETER:
            _fail()
        helper = _open_canonical_system_regular(CANONICAL_HELPER_PATH, descriptors)
        interpreter = _open_canonical_system_regular(CANONICAL_INTERPRETER, descriptors)
        # Each canonical open starts with one anchor and ends in its target.
        helper_end = descriptors.index(helper)
        helper_root = descriptors[0]
        helper_chain = tuple(descriptors[1:helper_end])
        interpreter_root = descriptors[helper_end + 1]
        interpreter_chain = tuple(descriptors[helper_end + 2 : -1])
        held = _RuntimeSelf(
            helper_root,
            helper_chain,
            helper,
            interpreter_root,
            interpreter_chain,
            interpreter,
            tuple(os.fstat(descriptor) for descriptor in descriptors),
            _sha256_at_start(helper),
        )
        held.revalidate()
        return held
    except CancelledError, KeyboardInterrupt, SystemExit:
        _close_all(tuple(reversed(descriptors)))
        raise
    except BaseException:  # noqa: BLE001 - canonical metadata is non-reflecting
        _close_all(tuple(reversed(descriptors)))
        _fail()


def _close_all(descriptors: Sequence[int]) -> BaseException | None:
    failure: BaseException | None = None
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except BaseException as error:  # noqa: BLE001 - cleanup preserves BaseException identity
            if failure is None:
                failure = error
    return failure


def _reject_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _exact_json(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _exact_json(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _exact_json(left, right) for left, right in zip(actual, expected, strict=True)
        )
    return actual == expected


def _read_exact(descriptor: int, maximum: int = 1 << 20) -> bytes:
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(descriptor, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if not raw or len(raw) > maximum:
        _fail()
    return raw


def _validate_compose(descriptor: int) -> None:
    try:
        document = json.loads(
            _read_exact(descriptor).decode("utf-8"), object_pairs_hook=_reject_duplicate_object
        )
    except UnicodeDecodeError, json.JSONDecodeError, OSError, ProvisioningFailure:
        _fail()
    if not _exact_json(document, EXPECTED_COMPOSE_CONTRACT):
        _fail()


def _parse_private_environment(descriptor: int) -> None:
    try:
        raw = _read_exact(descriptor, 4096)
        if b"\x00" in raw or not raw.isascii():
            _fail()
        lines = raw.splitlines()
        if not lines or raw.endswith(b"\n\n"):
            _fail()
        parsed: dict[bytes, bytes] = {}
        for line in lines:
            if line.count(b"=") != 1:
                _fail()
            key, value = line.split(b"=", 1)
            if not key or not value or key in parsed:
                _fail()
            parsed[key] = value
        expected = {
            b"ACCEPTANCE_POSTGRES_IMAGE_DIGEST": EXPECTED_IMAGE_DIGEST.encode("ascii"),
            b"ACCEPTANCE_POSTGRES_HOST_PORT": str(EXPECTED_PORT).encode("ascii"),
        }
        if parsed != expected:
            _fail()
    except OSError, ProvisioningFailure:
        _fail()


def _validate_secret(descriptor: int, expression: re.Pattern[bytes]) -> bytes:
    try:
        value = _read_exact(descriptor, 256)
    except OSError, ProvisioningFailure:
        _fail()
    if (
        b"\x00" in value
        or value.endswith(b"\n")
        or not value.isascii()
        or expression.fullmatch(value) is None
    ):
        _fail()
    return value


def _validate_secrets(descriptors: Sequence[int]) -> None:
    if len(descriptors) != 3:
        _fail()
    user = _validate_secret(descriptors[0], re.compile(rb"acceptance_role_[0-9a-f]{16}"))
    password = _validate_secret(descriptors[1], re.compile(rb"[0-9a-f]{64}"))
    database = _validate_secret(descriptors[2], re.compile(rb"acceptance_db_[0-9a-f]{16}"))
    if user == database or not password:
        _fail()


def _sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    while chunk := os.read(descriptor, 65536):
        digest.update(chunk)
    return digest.hexdigest()


def _require_elf_x86_64(descriptor: int) -> None:
    try:
        header = os.read(descriptor, 20)
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError:
        _fail()
    if (
        len(header) != 20
        or header[:4] != b"\x7fELF"
        or header[4:6] != b"\x02\x01"
        or header[18:20] != b">\x00"
    ):
        _fail()


def _open_path(path: Path) -> int:
    """Open any final filesystem object without following a final symlink."""
    return os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)


def _strict_labels(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict) or any(
        type(key) is not str or type(item) is not str for key, item in value.items()
    ):
        return None
    return value


def _exact_digest(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


@dataclass
class _Held:
    root_anchor: int
    repository_chain: tuple[int, ...]
    root: int
    environment: int
    secret_directory: int
    secrets: tuple[int, ...]
    compose: int
    executable_chain: tuple[int, ...]
    executable: int
    identities: tuple[os.stat_result, ...]
    repository_components: tuple[str, ...]
    executable_components: tuple[str, ...]

    @property
    def descriptors(self) -> tuple[int, ...]:
        return (
            self.root_anchor,
            *self.repository_chain,
            self.environment,
            self.secret_directory,
            *self.secrets,
            self.compose,
            *self.executable_chain,
            self.executable,
        )

    def revalidate(self) -> None:
        transient: list[int] = []
        primary: BaseException | None = None
        try:
            if len(self.identities) != len(self.descriptors) or any(
                not _same_identity(old, os.fstat(fd))
                for old, fd in zip(self.identities, self.descriptors, strict=True)
            ):
                _fail()
            root = _open_directory("/")
            transient.append(root)
            _require_directory(os.fstat(root), owner=ROOT_ANCHOR_OWNER_UID, mode=None)
            current = root
            reopened_repo: list[int] = []
            for component in self.repository_components:
                current = _open_directory(component, dir_fd=current)
                transient.append(current)
                reopened_repo.append(current)
            if any(
                not _same_identity(os.fstat(old), os.fstat(new))
                for old, new in zip(self.repository_chain, reopened_repo, strict=True)
            ):
                _fail()
            reopened_env = _open_regular(PRIVATE_ENV_NAME, dir_fd=current)
            reopened_secret_dir = _open_directory(PRIVATE_SECRET_DIRECTORY_NAME, dir_fd=current)
            transient.extend((reopened_env, reopened_secret_dir))
            reopened_secrets = tuple(
                _open_regular(name, dir_fd=reopened_secret_dir) for name in SECRET_FILE_NAMES
            )
            transient.extend(reopened_secrets)
            reopened_compose = _open_regular(COMPOSE_FILE_NAME, dir_fd=current)
            transient.append(reopened_compose)
            if set(os.listdir(reopened_secret_dir)) != set(SECRET_FILE_NAMES):
                _fail()
            expected = (self.environment, self.secret_directory, *self.secrets, self.compose)
            actual = (reopened_env, reopened_secret_dir, *reopened_secrets, reopened_compose)
            if any(
                not _same_identity(os.fstat(old), os.fstat(new))
                for old, new in zip(expected, actual, strict=True)
            ):
                _fail()
            system_root = _open_directory("/")
            transient.append(system_root)
            current = system_root
            reopened_exec_chain: list[int] = []
            for component in self.executable_components[:-1]:
                current = _open_directory(component, dir_fd=current)
                transient.append(current)
                reopened_exec_chain.append(current)
            reopened_exec = _open_regular(self.executable_components[-1], dir_fd=current)
            transient.append(reopened_exec)
            if any(
                not _same_identity(os.fstat(old), os.fstat(new))
                for old, new in zip(self.executable_chain, reopened_exec_chain, strict=True)
            ) or not _same_identity(os.fstat(self.executable), os.fstat(reopened_exec)):
                _fail()
        except (CancelledError, KeyboardInterrupt, SystemExit) as error:
            primary = error
        except BaseException:  # noqa: BLE001 - convert unknown filesystem detail to fixed failure
            primary = ProvisioningFailure(FAILURE_MARKER)
        finally:
            cleanup = _close_all(tuple(reversed(transient)))
        if primary is not None:
            raise primary
        if cleanup is not None:
            _fail()


def _hold_inputs(repository_root: Path | None = None) -> _Held:
    descriptors: list[int] = []
    try:
        if repository_root is None:
            repository_root = REPOSITORY_ROOT
        if os.geteuid() != 0 or not repository_root.is_absolute():
            _fail()
        components = repository_root.parts[1:]
        if not components or any(component in {"", ".", ".."} for component in components):
            _fail()
        root_anchor = _open_directory("/")
        descriptors.append(root_anchor)
        _require_directory(os.fstat(root_anchor), owner=ROOT_ANCHOR_OWNER_UID, mode=None)
        parent = root_anchor
        chain: list[int] = []
        for index, component in enumerate(components):
            parent = _open_directory(component, dir_fd=parent)
            descriptors.append(parent)
            chain.append(parent)
            meta = os.fstat(parent)
            if index < len(components) - 1:
                _require_directory(meta, owner=None, mode=None)
            else:
                _require_directory(meta, owner=EXPECTED_PRIVATE_UID, mode=0o755)
                if meta.st_gid != EXPECTED_PRIVATE_GID:
                    _fail()
        root = chain[-1]
        envfd = _open_regular(PRIVATE_ENV_NAME, dir_fd=root)
        descriptors.append(envfd)
        _require_regular(os.fstat(envfd), owner=EXPECTED_PRIVATE_UID, mode=0o600, nonempty=True)
        if os.fstat(envfd).st_gid != EXPECTED_PRIVATE_GID:
            _fail()
        secret_dir = _open_directory(PRIVATE_SECRET_DIRECTORY_NAME, dir_fd=root)
        descriptors.append(secret_dir)
        _require_directory(os.fstat(secret_dir), owner=EXPECTED_PRIVATE_UID, mode=0o700)
        if os.fstat(secret_dir).st_gid != EXPECTED_PRIVATE_GID or set(
            os.listdir(secret_dir)
        ) != set(SECRET_FILE_NAMES):
            _fail()
        secrets: list[int] = []
        for name in SECRET_FILE_NAMES:
            fd = _open_regular(name, dir_fd=secret_dir)
            descriptors.append(fd)
            secrets.append(fd)
            _require_regular(os.fstat(fd), owner=EXPECTED_PRIVATE_UID, mode=0o600, nonempty=True)
            if os.fstat(fd).st_gid != EXPECTED_PRIVATE_GID:
                _fail()
        compose = _open_regular(COMPOSE_FILE_NAME, dir_fd=root)
        descriptors.append(compose)
        _require_regular(os.fstat(compose), owner=EXPECTED_PRIVATE_UID, mode=None, nonempty=True)
        _validate_compose(compose)
        os.lseek(compose, 0, os.SEEK_SET)
        _parse_private_environment(envfd)
        os.lseek(envfd, 0, os.SEEK_SET)
        _validate_secrets(secrets)
        for fd in secrets:
            os.lseek(fd, 0, os.SEEK_SET)
        exec_components = COMPOSE_EXECUTABLE.parts[1:]
        system_root = _open_directory("/")
        descriptors.append(system_root)
        _require_directory(os.fstat(system_root), owner=ROOT_ANCHOR_OWNER_UID, mode=None)
        parent = system_root
        exec_chain: list[int] = []
        for component in exec_components[:-1]:
            parent = _open_directory(component, dir_fd=parent)
            descriptors.append(parent)
            exec_chain.append(parent)
            _require_directory(os.fstat(parent), owner=SYSTEM_OWNER_UID, mode=None)
        executable = _open_regular(exec_components[-1], dir_fd=parent)
        descriptors.append(executable)
        _require_regular(os.fstat(executable), owner=SYSTEM_OWNER_UID, mode=0o555, nonempty=True)
        if os.fstat(executable).st_gid != SYSTEM_OWNER_UID or _sha256(executable) != COMPOSE_SHA256:
            _fail()
        os.lseek(executable, 0, os.SEEK_SET)
        _require_elf_x86_64(executable)
        os.lseek(executable, 0, os.SEEK_SET)
        held = _Held(
            root_anchor,
            tuple(chain),
            root,
            envfd,
            secret_dir,
            tuple(secrets),
            compose,
            tuple(exec_chain),
            executable,
            tuple(
                os.fstat(fd)
                for fd in (
                    root_anchor,
                    *chain,
                    envfd,
                    secret_dir,
                    *secrets,
                    compose,
                    *exec_chain,
                    executable,
                )
            ),
            tuple(components),
            tuple(exec_components),
        )
        return held
    except CancelledError, KeyboardInterrupt, SystemExit:
        _close_all(tuple(reversed(descriptors)))
        raise
    except BaseException:  # noqa: BLE001 - convert unknown setup detail to fixed failure
        _close_all(tuple(reversed(descriptors)))
        _fail()


def _proc_port_is_free(port: int) -> bool:
    """Fail closed if either Linux TCP table is malformed or has the port."""
    if type(port) is not int or not 1 <= port <= 65535:
        return False
    expected = f"{port:04X}"
    try:
        for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
            raw = table.read_text(encoding="ascii")
            lines = raw.splitlines()
            if not lines or lines[0].split()[:2] != ["sl", "local_address"]:
                return False
            for line in lines[1:]:
                fields = line.split()
                if len(fields) < 10 or ":" not in fields[1]:
                    return False
                address, seen = fields[1].rsplit(":", 1)
                width = 8 if table.name == "tcp" else 32
                if (
                    re.fullmatch(rf"[0-9A-F]{{{width}}}", address) is None
                    or re.fullmatch(r"[0-9A-F]{4}", seen) is None
                ):
                    return False
                if seen == expected:
                    return False
    except OSError, UnicodeError:
        return False
    return True


@dataclass
class _DaemonBoundary:
    root_anchor: int
    socket_parent_chain: tuple[int, ...]
    socket: int
    data_root_chain: tuple[int, ...]
    data_root: int
    identities: tuple[os.stat_result, ...]

    @property
    def descriptors(self) -> tuple[int, ...]:
        return (
            self.root_anchor,
            *self.socket_parent_chain,
            self.socket,
            *self.data_root_chain,
            self.data_root,
        )

    def revalidate(self) -> None:
        transient: list[int] = []
        try:
            if any(
                not _same_identity(before, os.fstat(after))
                for before, after in zip(self.identities, self.descriptors, strict=True)
            ):
                _fail()
            root = _open_directory("/")
            transient.append(root)
            _require_directory(os.fstat(root), owner=ROOT_ANCHOR_OWNER_UID, mode=None)
            current = root
            reopened_socket_chain: list[int] = []
            for component in DOCKER_SOCKET.parts[1:-1]:
                current = _open_directory(component, dir_fd=current)
                transient.append(current)
                reopened_socket_chain.append(current)
            reopened_socket = os.open(
                DOCKER_SOCKET.name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=current
            )
            transient.append(reopened_socket)
            current = root
            reopened_data_chain: list[int] = []
            for component in DOCKER_DATA_ROOT.parts[1:-1]:
                current = _open_directory(component, dir_fd=current)
                transient.append(current)
                reopened_data_chain.append(current)
            reopened_data_root = _open_directory(DOCKER_DATA_ROOT.name, dir_fd=current)
            transient.append(reopened_data_root)
            if (
                any(
                    not _same_identity(os.fstat(left), os.fstat(right))
                    for left, right in zip(
                        self.socket_parent_chain, reopened_socket_chain, strict=True
                    )
                )
                or not _same_identity(os.fstat(self.socket), os.fstat(reopened_socket))
                or any(
                    not _same_identity(os.fstat(left), os.fstat(right))
                    for left, right in zip(self.data_root_chain, reopened_data_chain, strict=True)
                )
                or not _same_identity(os.fstat(self.data_root), os.fstat(reopened_data_root))
            ):
                _fail()
        except CancelledError, KeyboardInterrupt, SystemExit:
            raise
        except BaseException:  # noqa: BLE001 - metadata detail is non-reflecting
            _fail()
        finally:
            cleanup = _close_all(tuple(reversed(transient)))
        if cleanup is not None:
            _fail()


def _hold_daemon_boundary() -> _DaemonBoundary:
    descriptors: list[int] = []
    try:
        root = _open_directory("/")
        descriptors.append(root)
        _require_directory(os.fstat(root), owner=0, mode=None)
        current = root
        socket_chain: list[int] = []
        for component in DOCKER_SOCKET.parts[1:-1]:
            current = _open_directory(component, dir_fd=current)
            descriptors.append(current)
            socket_chain.append(current)
            _require_directory(os.fstat(current), owner=0, mode=None)
        socket_fd = os.open(
            DOCKER_SOCKET.name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=current
        )
        descriptors.append(socket_fd)
        group = grp.getgrnam(EXPECTED_DOCKER_GROUP)
        if (
            type(getattr(group, "gr_name", None)) is not str
            or group.gr_name != EXPECTED_DOCKER_GROUP
            or type(getattr(group, "gr_gid", None)) is not int
            or group.gr_gid < 0
        ):
            _fail()
        socket_meta = os.fstat(socket_fd)
        if (
            not _strict_metadata(socket_meta)
            or not stat.S_ISSOCK(socket_meta.st_mode)
            or socket_meta.st_uid != 0
            or socket_meta.st_gid != group.gr_gid
            or socket_meta.st_nlink != 1
            or stat.S_IMODE(socket_meta.st_mode) != 0o660
        ):
            _fail()
        for username in UNPRIVILEGED_DOCKER_DENIED_USERS:
            account = pwd.getpwnam(username)
            if (
                type(getattr(account, "pw_uid", None)) is not int
                or type(getattr(account, "pw_gid", None)) is not int
            ):
                _fail()
            memberships = os.getgrouplist(username, account.pw_gid)
            if (
                not isinstance(memberships, list)
                or any(type(value) is not int for value in memberships)
                or group.gr_gid in memberships
            ):
                _fail()
        current = root
        data_chain: list[int] = []
        for component in DOCKER_DATA_ROOT.parts[1:-1]:
            current = _open_directory(component, dir_fd=current)
            descriptors.append(current)
            data_chain.append(current)
            _require_directory(os.fstat(current), owner=0, mode=None)
        data_root = _open_directory(DOCKER_DATA_ROOT.name, dir_fd=current)
        descriptors.append(data_root)
        _require_directory(os.fstat(data_root), owner=0, mode=None)
        held = _DaemonBoundary(
            root,
            tuple(socket_chain),
            socket_fd,
            tuple(data_chain),
            data_root,
            tuple(os.fstat(fd) for fd in (root, *socket_chain, socket_fd, *data_chain, data_root)),
        )
        return held
    except CancelledError, KeyboardInterrupt, SystemExit:
        _close_all(tuple(reversed(descriptors)))
        raise
    except BaseException:  # noqa: BLE001 - fail closed without filesystem detail
        _close_all(tuple(reversed(descriptors)))
        _fail()


def _disk_is_sufficient(target: int | Path = DOCKER_DATA_ROOT) -> bool:
    try:
        stats = os.fstatvfs(target) if type(target) is int else os.statvfs(target)
        return (
            type(stats.f_bavail) is int
            and type(stats.f_frsize) is int
            and stats.f_bavail * stats.f_frsize >= MINIMUM_DATA_ROOT_FREE_BYTES
        )
    except OSError:
        return False


def _docker_request(path: str) -> object:
    """Perform a bounded local Unix-socket GET without reflecting response details."""
    request = f"GET {path} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n".encode("ascii")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(DOCKER_API_TIMEOUT_SECONDS)
            client.connect(os.fspath(DOCKER_SOCKET))
            client.sendall(request)
            response = bytearray()
            while len(response) <= 1 << 20:
                chunk = client.recv(65536)
                if not chunk:
                    break
                response.extend(chunk)
            if len(response) > 1 << 20 or b"\r\n\r\n" not in response:
                _fail()
        header, body = bytes(response).split(b"\r\n\r\n", 1)
        if not header.startswith(b"HTTP/1.1 200 "):
            _fail()
        return json.loads(body.decode("utf-8"), object_pairs_hook=_reject_duplicate_object)
    except UnicodeDecodeError, json.JSONDecodeError, OSError, ProvisioningFailure:
        _fail()


def _valid_image(image: object) -> bool:
    if not isinstance(image, dict):
        return False
    digests = image.get("RepoDigests")
    if not isinstance(digests, list) or any(type(item) is not str for item in digests):
        return False
    image_id = image.get("Id")
    return (
        EXPECTED_IMAGE_REFERENCE in digests
        and image.get("Os") == "linux"
        and image.get("Architecture") == "amd64"
        and _exact_digest(image_id)
        and all("@" in item and _exact_digest(item.rsplit("@", 1)[1]) for item in digests)
    )


def _list_items(value: object) -> list[dict[str, object]] | None:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return None
    return value


def _target_labels(item: dict[str, object]) -> dict[str, str] | None:
    return _strict_labels(item.get("Labels", {}))


def _required_labels(actual: dict[str, str] | None, expected: dict[str, str]) -> bool:
    return actual is not None and all(actual.get(key) == value for key, value in expected.items())


def _state_is_pristine() -> bool:
    try:
        image = _docker_request("/images/" + quote(EXPECTED_IMAGE_REFERENCE, safe="") + "/json")
        containers = _docker_request("/containers/json?all=1")
        networks = _docker_request("/networks")
        volumes = _docker_request("/volumes")
        if not _valid_image(image):
            return False
        container_items = _list_items(containers)
        network_items = _list_items(networks)
        if not isinstance(volumes, dict) or container_items is None or network_items is None:
            return False
        volume_items = _list_items(volumes.get("Volumes"))
        if volume_items is None:
            return False
        for item in (*container_items, *network_items, *volume_items):
            if _target_labels(item) is None:
                return False
        if any(
            item.get("Names") in ([f"/{EXPECTED_CONTAINER}"], [EXPECTED_CONTAINER])
            or _target_labels(item).get("com.docker.compose.project") == EXPECTED_PROJECT
            for item in container_items
        ):
            return False
        if any(
            item.get("Name") == EXPECTED_NETWORK
            or _target_labels(item).get("com.docker.compose.project") == EXPECTED_PROJECT
            for item in network_items
        ):
            return False
        return not any(
            item.get("Name") == EXPECTED_VOLUME
            or _target_labels(item).get("com.docker.compose.project") == EXPECTED_PROJECT
            for item in volume_items
        )
    except ProvisioningFailure:
        return False


@dataclass
class _RuntimeSecrets:
    run_directory: int
    directory: int
    files: tuple[int, ...]
    identities: tuple[os.stat_result, ...]

    @property
    def descriptors(self) -> tuple[int, ...]:
        return (self.run_directory, self.directory, *self.files)

    def revalidate(self) -> None:
        reopened: list[int] = []
        try:
            parent = _open_system_directory_path(RUNTIME_SECRET_DIRECTORY.parent, reopened)
            directory = _open_directory(RUNTIME_SECRET_DIRECTORY.name, dir_fd=parent)
            reopened.append(directory)
            _require_directory(os.fstat(directory), owner=SYSTEM_OWNER_UID, mode=0o700)
            files = tuple(_open_regular(name, dir_fd=directory) for name in SECRET_FILE_NAMES)
            reopened.extend(files)
            if set(os.listdir(directory)) != set(SECRET_FILE_NAMES):
                _fail()
            for descriptor in files:
                _require_regular(
                    os.fstat(descriptor), owner=SYSTEM_OWNER_UID, mode=0o400, nonempty=True
                )
            _validate_secrets(files)
            expected = (self.run_directory, self.directory, *self.files)
            actual = (parent, directory, *files)
            if any(
                not _same_identity(os.fstat(before), os.fstat(after))
                for before, after in zip(expected, actual, strict=True)
            ):
                _fail()
        except CancelledError, KeyboardInterrupt, SystemExit:
            raise
        except BaseException:  # noqa: BLE001 - source names and values are non-reflecting
            _fail()
        finally:
            cleanup = _close_all(tuple(reversed(reopened)))
        if cleanup is not None:
            _fail()


def _write_all(descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        count = os.write(descriptor, value[offset:])
        if type(count) is not int or count <= 0:
            _fail()
        offset += count


def _open_system_directory_path(path: Path, descriptors: list[int]) -> int:
    if not path.is_absolute() or not path.parts[1:]:
        _fail()
    root = _open_directory("/")
    descriptors.append(root)
    _require_directory(os.fstat(root), owner=ROOT_ANCHOR_OWNER_UID, mode=None)
    parent = root
    for component in path.parts[1:]:
        parent = _open_directory(component, dir_fd=parent)
        descriptors.append(parent)
        _require_directory(os.fstat(parent), owner=SYSTEM_OWNER_UID, mode=None)
    return parent


def _create_runtime_snapshot(held: _Held) -> _RuntimeSecrets:
    descriptors: list[int] = []
    try:
        run = _open_system_directory_path(RUNTIME_SECRET_DIRECTORY.parent, descriptors)
        # mkdirat is new-only: a file, directory, symlink, or dangling symlink
        # at the fixed name fails without being changed or reused.
        os.mkdir(RUNTIME_SECRET_DIRECTORY.name, 0o700, dir_fd=run)
        directory = _open_directory(RUNTIME_SECRET_DIRECTORY.name, dir_fd=run)
        descriptors.append(directory)
        _require_directory(os.fstat(directory), owner=SYSTEM_OWNER_UID, mode=0o700)
        copied: list[int] = []
        for source, name, expression in zip(
            held.secrets,
            SECRET_FILE_NAMES,
            (
                re.compile(rb"acceptance_role_[0-9a-f]{16}"),
                re.compile(rb"[0-9a-f]{64}"),
                re.compile(rb"acceptance_db_[0-9a-f]{16}"),
            ),
            strict=True,
        ):
            os.lseek(source, 0, os.SEEK_SET)
            value = _validate_secret(source, expression)
            os.lseek(source, 0, os.SEEK_SET)
            target = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o400,
                dir_fd=directory,
            )
            descriptors.append(target)
            _write_all(target, value)
            os.fsync(target)
            copied.append(target)
        if len(copied) != 3:
            _fail()
        for descriptor in copied:
            _require_regular(
                os.fstat(descriptor), owner=SYSTEM_OWNER_UID, mode=0o400, nonempty=True
            )
        os.fsync(directory)
        readbacks = tuple(_open_regular(name, dir_fd=directory) for name in SECRET_FILE_NAMES)
        descriptors.extend(readbacks)
        _validate_secrets(readbacks)
        for descriptor in readbacks:
            _require_regular(
                os.fstat(descriptor), owner=SYSTEM_OWNER_UID, mode=0o400, nonempty=True
            )
        _close_all(tuple(reversed(readbacks)))
        del descriptors[-len(readbacks) :]
        parent_index = descriptors.index(run)
        prefix = tuple(descriptors[:parent_index])
        if _close_all(tuple(reversed(prefix))) is not None:
            _fail()
        del descriptors[:parent_index]
        snapshot = _RuntimeSecrets(
            run,
            directory,
            tuple(copied),
            tuple(os.fstat(fd) for fd in (run, directory, *copied)),
        )
        return snapshot
    except CancelledError, KeyboardInterrupt, SystemExit:
        _close_all(tuple(reversed(descriptors)))
        raise
    except BaseException:  # noqa: BLE001 - no source value or path is reflected
        _close_all(tuple(reversed(descriptors)))
        _fail()


def _post_state_is_exact(runtime_directory: Path) -> bool:
    """Accept only the one expected project after Compose returns success."""
    try:
        image = _docker_request("/images/" + quote(EXPECTED_IMAGE_REFERENCE, safe="") + "/json")
        container = _docker_request("/containers/" + quote(EXPECTED_CONTAINER, safe="") + "/json")
        network = _docker_request("/networks/" + quote(EXPECTED_NETWORK, safe=""))
        volume = _docker_request("/volumes/" + quote(EXPECTED_VOLUME, safe=""))
        containers = _docker_request("/containers/json?all=1")
        networks = _docker_request("/networks")
        volumes = _docker_request("/volumes")
        if not _valid_image(image) or not all(
            isinstance(item, dict) for item in (container, network, volume)
        ):
            return False
        configuration = container.get("Config")
        container_labels = (
            _strict_labels(configuration.get("Labels")) if isinstance(configuration, dict) else None
        )
        network_labels = _strict_labels(network.get("Labels"))
        volume_labels = _strict_labels(volume.get("Labels"))
        if (
            container.get("Name") != f"/{EXPECTED_CONTAINER}"
            or not isinstance(configuration, dict)
            or configuration.get("Image") != EXPECTED_IMAGE_REFERENCE
            or container_labels is None
            or container_labels.get("com.docker.compose.project") != EXPECTED_PROJECT
            or container_labels.get("com.docker.compose.service") != "acceptance_postgres"
            or network.get("Name") != EXPECTED_NETWORK
            or network_labels is None
            or network_labels.get("com.docker.compose.project") != EXPECTED_PROJECT
            or network_labels.get("com.docker.compose.network") != "acceptance_postgres_network"
            or volume.get("Name") != EXPECTED_VOLUME
            or volume_labels is None
            or volume_labels.get("com.docker.compose.project") != EXPECTED_PROJECT
            or volume_labels.get("com.docker.compose.volume") != "acceptance_postgres_data"
        ):
            return False
        state = container.get("State")
        network_settings = container.get("NetworkSettings")
        mounts = container.get("Mounts")
        if (
            not isinstance(state, dict)
            or state.get("Status") != "running"
            or not isinstance(network_settings, dict)
            or not isinstance(mounts, list)
        ):
            return False
        health = state.get("Health")
        attached_networks = network_settings.get("Networks")
        ports = network_settings.get("Ports")
        if (
            not isinstance(health, dict)
            or health.get("Status") != "healthy"
            or not isinstance(attached_networks, dict)
            or set(attached_networks) != {EXPECTED_NETWORK}
            or not isinstance(ports, dict)
            or set(ports) != {"5432/tcp"}
        ):
            return False
        published = ports.get("5432/tcp")
        if (
            not isinstance(published, list)
            or len(published) != 1
            or not isinstance(published[0], dict)
            or published[0].get("HostIp") != "127.0.0.1"
            or published[0].get("HostPort") != str(EXPECTED_PORT)
        ):
            return False
        required_mounts = {
            ("volume", EXPECTED_VOLUME, "/var/lib/postgresql/data", False),
            ("bind", os.fspath(runtime_directory), "/run/acceptance-postgres-secrets", True),
        }
        actual_mounts: set[tuple[str, str, str, bool]] = set()
        for mount in mounts:
            if not isinstance(mount, dict) or any(
                type(mount.get(key)) is not expected
                for key, expected in (
                    ("Type", str),
                    ("Source", str),
                    ("Destination", str),
                    ("RW", bool),
                )
            ):
                return False
            actual_mounts.add(
                (mount["Type"], mount["Source"], mount["Destination"], not mount["RW"])
            )
        if actual_mounts != required_mounts:
            return False
        return _post_global_state_is_exact(containers, networks, volumes)
    except ProvisioningFailure:
        return False


def _post_global_state_is_exact(containers: object, networks: object, volumes: object) -> bool:
    """Classify every global list item; do not accept duplicate project resources."""
    container_items = _list_items(containers)
    network_items = _list_items(networks)
    if not isinstance(volumes, dict):
        return False
    volume_items = _list_items(volumes.get("Volumes"))
    if container_items is None or network_items is None or volume_items is None:
        return False

    def labels(item: dict[str, object]) -> dict[str, str] | None:
        return _strict_labels(item.get("Labels", {}))

    if any(labels(item) is None for item in (*container_items, *network_items, *volume_items)):
        return False
    expected_containers = [
        item
        for item in container_items
        if item.get("Names") in ([f"/{EXPECTED_CONTAINER}"], [EXPECTED_CONTAINER])
        or labels(item).get("com.docker.compose.project") == EXPECTED_PROJECT
    ]
    expected_networks = [
        item
        for item in network_items
        if item.get("Name") == EXPECTED_NETWORK
        or labels(item).get("com.docker.compose.project") == EXPECTED_PROJECT
    ]
    expected_volumes = [
        item
        for item in volume_items
        if item.get("Name") == EXPECTED_VOLUME
        or labels(item).get("com.docker.compose.project") == EXPECTED_PROJECT
    ]
    if len(expected_containers) != 1 or len(expected_networks) != 1 or len(expected_volumes) != 1:
        return False
    container_labels = labels(expected_containers[0])
    network_labels = labels(expected_networks[0])
    volume_labels = labels(expected_volumes[0])
    return (
        expected_containers[0].get("Names") == [f"/{EXPECTED_CONTAINER}"]
        and _required_labels(
            container_labels,
            {
                "com.docker.compose.project": EXPECTED_PROJECT,
                "com.docker.compose.service": "acceptance_postgres",
            },
        )
        and expected_networks[0].get("Name") == EXPECTED_NETWORK
        and _required_labels(
            network_labels,
            {
                "com.docker.compose.project": EXPECTED_PROJECT,
                "com.docker.compose.network": "acceptance_postgres_network",
            },
        )
        and expected_volumes[0].get("Name") == EXPECTED_VOLUME
        and _required_labels(
            volume_labels,
            {
                "com.docker.compose.project": EXPECTED_PROJECT,
                "com.docker.compose.volume": "acceptance_postgres_data",
            },
        )
    )


def _compose_contract(held: _Held) -> tuple[tuple[str, ...], dict[str, str], tuple[int, ...]]:
    fds = (held.executable, held.root, held.environment, held.compose)
    if len(set(fds)) != 4 or any(type(fd) is not int or fd < 0 for fd in fds):
        _fail()
    environment = dict(SANITIZED_ENV)
    environment["ACCEPTANCE_POSTGRES_RUNTIME_SECRET_DIRECTORY"] = os.fspath(
        RUNTIME_SECRET_DIRECTORY
    )
    argv = (
        f"/proc/self/fd/{held.executable}",
        "--project-name",
        EXPECTED_PROJECT,
        "--project-directory",
        f"/proc/self/fd/{held.root}",
        "--env-file",
        f"/proc/self/fd/{held.environment}",
        "-f",
        f"/proc/self/fd/{held.compose}",
        "up",
        "--detach",
        "--no-build",
        "--pull",
        "never",
        "--wait",
        "acceptance_postgres",
    )
    return argv, environment, fds


def _run_compose(argv: Sequence[str], environment: dict[str, str], pass_fds: Sequence[int]) -> None:
    try:
        completed = subprocess.run(
            tuple(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            shell=False,
            pass_fds=tuple(pass_fds),
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except CancelledError, KeyboardInterrupt, SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - subprocess failure is intentionally non-reflecting
        _fail()
    if completed.returncode != 0:
        _fail()


def provision_once(
    *,
    runner: Callable[[Sequence[str], dict[str, str], Sequence[int]], None] = _run_compose,
    disk_ready: Callable[[int | Path], bool] = _disk_is_sufficient,
    port_free: Callable[[int], bool] = _proc_port_is_free,
    pristine: Callable[[], bool] = _state_is_pristine,
    post_state: Callable[[Path], bool] = _post_state_is_exact,
) -> None:
    """Run exactly one fixed no-pull Compose ``up`` or raise a fixed failure."""
    held: _Held | None = None
    daemon: _DaemonBoundary | None = None
    runtime: _RuntimeSecrets | None = None
    runtime_self: _RuntimeSelf | None = None
    primary: BaseException | None = None
    try:
        if os.environ.get(WINDOWS_PORT_PRECHECK_ENVIRONMENT) != WINDOWS_PORT_PRECHECK_VALUE or any(
            name.startswith(PROHIBITED_ENVIRONMENT_PREFIXES) for name in os.environ
        ):
            _fail()
        runtime_self = _hold_runtime_self()
        held = _hold_inputs()
        # The global absent-state and IPv4/IPv6 checks deliberately precede
        # snapshot creation.  The daemon boundary is held after the /run
        # mutation so its saved directory-link identities describe the state
        # Compose will actually observe.
        if not disk_ready(DOCKER_DATA_ROOT) or not port_free(EXPECTED_PORT) or not pristine():
            _fail()
        held.revalidate()
        runtime_self.revalidate()
        runtime = _create_runtime_snapshot(held)
        # Snapshot creation is itself a mutation.  Re-enumerate global daemon
        # state and both TCP tables before holding all final identities.
        if not pristine() or not port_free(EXPECTED_PORT):
            _fail()
        daemon = _hold_daemon_boundary()
        argv, environment, pass_fds = _compose_contract(held)
        held.revalidate()
        daemon.revalidate()
        runtime_self.revalidate()
        # This is the final filesystem check.  Nothing (including a Docker API
        # request or contract construction) runs between it and the one call.
        runtime.revalidate()
        runner(argv, environment, pass_fds)
        held.revalidate()
        daemon.revalidate()
        runtime.revalidate()
        if not post_state(RUNTIME_SECRET_DIRECTORY):
            _fail()
        held.revalidate()
        daemon.revalidate()
        runtime.revalidate()
        runtime_self.revalidate()
    except (CancelledError, KeyboardInterrupt, SystemExit) as error:
        primary = error
    except BaseException:  # noqa: BLE001 - ordinary primary is intentionally non-reflecting
        primary = ProvisioningFailure(FAILURE_MARKER)
    finally:
        cleanup = _close_all(
            tuple(
                reversed(
                    (
                        *(runtime.descriptors if runtime is not None else ()),
                        *(daemon.descriptors if daemon is not None else ()),
                        *(held.descriptors if held is not None else ()),
                        *(runtime_self.descriptors if runtime_self is not None else ()),
                    )
                )
            )
        )
    if primary is not None:
        raise primary
    if cleanup is not None:
        _fail()


def main() -> int:
    if len(sys.argv) != 1:
        print(FAILURE_MARKER, file=sys.stderr)
        return FAILURE_EXIT_CODE
    try:
        provision_once()
    except CancelledError, KeyboardInterrupt, SystemExit:
        raise
    except ProvisioningFailure:
        print(FAILURE_MARKER, file=sys.stderr)
        return FAILURE_EXIT_CODE
    print(SUCCESS_MARKER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
