"""Fail-closed, config-only preflight for the acceptance PostgreSQL Compose file.

This module deliberately does not provision anything. Its sole public operation is
the fixed standalone Compose V2 ``config --quiet`` validation below. A future
provisioning operation requires a different launcher and approval.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from asyncio import CancelledError
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

FAILURE_MARKER = "ACCEPTANCE_DB_PREFLIGHT_FAILED"
SUCCESS_MARKER = "ACCEPTANCE_DB_PREFLIGHT_CONFIG_VALIDATED"
FAILURE_EXIT_CODE = 64

PRIVATE_ENV_NAME = ".env.acceptance-postgres"
PRIVATE_SECRET_DIRECTORY_NAME = ".acceptance-postgres-secrets"
SECRET_FILE_NAMES = ("postgres-user", "postgres-password", "postgres-database")
COMPOSE_FILE_NAME = "compose.acceptance.yaml"
SECRET_DIRECTORY_FD_ENVIRONMENT_NAME = "ACCEPTANCE_POSTGRES_SECRET_DIRECTORY_FD_PATH"
COMPOSE_EXECUTABLE_CANDIDATES = (
    Path("/usr/local/lib/docker/cli-plugins/docker-compose"),
    Path("/usr/lib/docker/cli-plugins/docker-compose"),
    Path("/usr/libexec/docker/cli-plugins/docker-compose"),
    Path("/mnt/wsl/docker-desktop/cli-tools/usr/bin/docker-compose"),
)
SANITIZED_ENV = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"}
SUBPROCESS_TIMEOUT_SECONDS = 10

# This is deliberately an exact JSON-compatible YAML contract.  Compose itself
# remains the authoritative interpolation/schema validator, but it must never
# be invoked for a topology the launcher has not already accepted.
EXPECTED_COMPOSE_CONTRACT: dict[str, object] = {
    "name": "discord-ai-reminder-bot-acceptance-db",
    "services": {
        "acceptance_postgres": {
            "image": "postgres@${ACCEPTANCE_POSTGRES_IMAGE_DIGEST:?set an immutable sha256 digest}",
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
                    "source": "${ACCEPTANCE_POSTGRES_SECRET_DIRECTORY_FD_PATH:?set only by the acceptance preflight launcher}",
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
            "restart": "no",
            "stop_grace_period": "20s",
        }
    },
    "volumes": {
        "acceptance_postgres_data": {"name": "discord-ai-reminder-bot-acceptance-postgres-data"}
    },
    "networks": {
        "acceptance_postgres_network": {
            "name": "discord-ai-reminder-bot-acceptance-postgres-network"
        }
    },
}


class PreflightFailure(Exception):
    """A fixed non-reflecting failure classification."""


def _fail() -> NoReturn:
    raise PreflightFailure(FAILURE_MARKER)


def _is_strict_int(value: object) -> bool:
    return type(value) is int


def _has_strict_identity_fields(metadata: os.stat_result) -> bool:
    return all(
        _is_strict_int(getattr(metadata, field, None))
        for field in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
    )


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        _has_strict_identity_fields(left)
        and _has_strict_identity_fields(right)
        and (
            left.st_dev == right.st_dev
            and left.st_ino == right.st_ino
            and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
            and left.st_uid == right.st_uid
            and left.st_gid == right.st_gid
            and stat.S_IMODE(left.st_mode) == stat.S_IMODE(right.st_mode)
            and left.st_nlink == right.st_nlink
            and left.st_size == right.st_size
        )
    )


def _require_parent_directory(metadata: os.stat_result) -> None:
    if (
        not _has_strict_identity_fields(metadata)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not _is_strict_int(metadata.st_nlink)
        or metadata.st_nlink < 2
    ):
        _fail()


def _require_repository_chain_directory(metadata: os.stat_result, root_owner: int) -> None:
    """Validate a held absolute-path component without trusting its pathname."""

    mode = stat.S_IMODE(metadata.st_mode) if _has_strict_identity_fields(metadata) else 0
    sticky_root_owned = bool(mode & stat.S_ISVTX) and metadata.st_uid in {0, root_owner}
    if (
        not _has_strict_identity_fields(metadata)
        or not stat.S_ISDIR(metadata.st_mode)
        or not _is_strict_int(root_owner)
        or metadata.st_uid not in {0, os.getuid(), root_owner}
        or (mode & 0o022 and not sticky_root_owned)
        or metadata.st_nlink < 2
    ):
        _fail()


def _require_private_directory(metadata: os.stat_result) -> None:
    if (
        not _has_strict_identity_fields(metadata)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or not _is_strict_int(metadata.st_nlink)
        or metadata.st_nlink < 2
    ):
        _fail()


def _require_private_file(metadata: os.stat_result) -> None:
    if (
        not _has_strict_identity_fields(metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not _is_strict_int(metadata.st_nlink)
        or metadata.st_nlink != 1
        or not _is_strict_int(metadata.st_size)
        or metadata.st_size <= 0
    ):
        _fail()


def _require_compose_file(metadata: os.stat_result) -> None:
    if (
        not _has_strict_identity_fields(metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not _is_strict_int(metadata.st_nlink)
        or metadata.st_nlink != 1
        or not _is_strict_int(metadata.st_size)
        or metadata.st_size <= 0
    ):
        _fail()


def _require_system_directory(metadata: os.stat_result) -> None:
    if (
        not _has_strict_identity_fields(metadata)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or metadata.st_nlink < 2
    ):
        _fail()


def _require_filesystem_root_anchor(metadata: os.stat_result) -> None:
    if (
        not _has_strict_identity_fields(metadata)
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or metadata.st_nlink < 2
    ):
        _fail()


def _require_compose_executable(metadata: os.stat_result) -> None:
    if (
        not _has_strict_identity_fields(metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not stat.S_IMODE(metadata.st_mode) & 0o111
        or not _is_strict_int(metadata.st_size)
        or metadata.st_size <= 0
    ):
        _fail()


def _open_directory(path: Path | str, *, dir_fd: int | None = None) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    name = os.fspath(path)
    return os.open(name, flags, dir_fd=dir_fd)


def _open_regular(name: str, *, dir_fd: int) -> int:
    return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)


def _close_descriptors(descriptors: Sequence[int]) -> BaseException | None:
    cleanup_failure: BaseException | None = None
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except BaseException as error:  # noqa: BLE001 - cleanup must preserve BaseException identity
            if cleanup_failure is None:
                cleanup_failure = error
    return cleanup_failure


def _child_contract(
    held: _HeldInputs, executable: _HeldComposeExecutable
) -> tuple[tuple[str, ...], dict[str, str], tuple[int, ...]]:
    child_fds = (
        executable.descriptor,
        held.root_descriptor,
        held.environment_descriptor,
        held.compose_descriptor,
        held.secret_directory_descriptor,
    )
    if len(set(child_fds)) != len(child_fds) or any(
        type(descriptor) is not int or descriptor < 0 for descriptor in child_fds
    ):
        _fail()
    project_directory = f"/proc/self/fd/{held.root_descriptor}"
    environment_file = f"/proc/self/fd/{held.environment_descriptor}"
    compose_file = f"/proc/self/fd/{held.compose_descriptor}"
    secret_directory = f"/proc/self/fd/{held.secret_directory_descriptor}"
    environment = dict(SANITIZED_ENV)
    environment[SECRET_DIRECTORY_FD_ENVIRONMENT_NAME] = secret_directory
    return (
        (
            f"/proc/self/fd/{executable.descriptor}",
            "--project-directory",
            project_directory,
            "--env-file",
            environment_file,
            "-f",
            compose_file,
            "config",
            "--quiet",
        ),
        environment,
        child_fds,
    )


def _require_child_fd_contract(
    arguments: Sequence[str], environment: dict[str, str], pass_fds: Sequence[int]
) -> None:
    references = [
        arguments[0],
        arguments[2],
        arguments[4],
        arguments[6],
        environment.get(SECRET_DIRECTORY_FD_ENVIRONMENT_NAME, ""),
    ]
    try:
        referenced_fds = {int(reference.removeprefix("/proc/self/fd/")) for reference in references}
    except AttributeError, TypeError, ValueError:
        _fail()
    if (
        any(not reference.startswith("/proc/self/fd/") for reference in references)
        or len(referenced_fds) != len(references)
        or any(type(descriptor) is not int or descriptor < 0 for descriptor in pass_fds)
        or set(pass_fds) != referenced_fds
    ):
        _fail()


def _reject_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _exact_json_value(actual: object, expected: object) -> bool:
    """Compare parsed JSON without Python's bool-is-int equivalence."""

    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _exact_json_value(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _exact_json_value(left, right) for left, right in zip(actual, expected, strict=True)
        )
    return actual == expected


def _read_compose_contract(descriptor: int) -> None:
    try:
        raw = os.read(descriptor, 1 << 20)
        if not raw or os.read(descriptor, 1):
            _fail()
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_object)
    except UnicodeDecodeError, json.JSONDecodeError, OSError, PreflightFailure:
        _fail()
    if not _exact_json_value(document, EXPECTED_COMPOSE_CONTRACT):
        _fail()


@dataclass
class _HeldInputs:
    filesystem_root_descriptor: int
    repository_components: tuple[str, ...]
    repository_chain_descriptors: tuple[int, ...]
    root_descriptor: int
    compose_descriptor: int
    environment_descriptor: int
    secret_directory_descriptor: int
    secret_descriptors: tuple[int, ...]
    identities: tuple[os.stat_result, ...]

    @property
    def descriptors(self) -> tuple[int, ...]:
        return (
            self.filesystem_root_descriptor,
            *self.repository_chain_descriptors,
            self.compose_descriptor,
            self.environment_descriptor,
            self.secret_directory_descriptor,
            *self.secret_descriptors,
        )

    def revalidate(self) -> None:
        transient: list[int] = []
        primary: BaseException | None = None
        try:
            expected = self.identities
            current = tuple(os.fstat(descriptor) for descriptor in self.descriptors)
            if len(current) != len(expected) or any(
                not _same_identity(old, new) for old, new in zip(expected, current, strict=True)
            ):
                _fail()
            parent = _open_directory(".", dir_fd=self.filesystem_root_descriptor)
            transient.append(parent)
            _require_filesystem_root_anchor(os.fstat(parent))
            for component in self.repository_components:
                parent = _open_directory(component, dir_fd=parent)
                transient.append(parent)
                _require_repository_chain_directory(os.fstat(parent), self.identities[0].st_uid)
            if len(transient) != len(self.repository_chain_descriptors) + 1:
                _fail()
            expected_chain = self.identities[1 : len(transient)]
            if any(
                not _same_identity(old, os.fstat(descriptor))
                for old, descriptor in zip(expected_chain, transient[1:], strict=True)
            ):
                _fail()
            compose = _open_regular(COMPOSE_FILE_NAME, dir_fd=self.root_descriptor)
            transient.append(compose)
            environment = _open_regular(PRIVATE_ENV_NAME, dir_fd=self.root_descriptor)
            transient.append(environment)
            secrets = _open_directory(PRIVATE_SECRET_DIRECTORY_NAME, dir_fd=self.root_descriptor)
            transient.append(secrets)
            for name in SECRET_FILE_NAMES:
                transient.append(_open_regular(name, dir_fd=secrets))
            rechecked = tuple(os.fstat(descriptor) for descriptor in transient[1:])
            if len(rechecked) != len(expected) - 1 or any(
                not _same_identity(old, new)
                for old, new in zip(expected[1:], rechecked, strict=True)
            ):
                _fail()
            if set(os.listdir(secrets)) != set(SECRET_FILE_NAMES):
                _fail()
        except (CancelledError, KeyboardInterrupt, SystemExit) as error:
            primary = error
        except BaseException:  # noqa: BLE001 - ordinary failures are intentionally non-reflecting
            primary = PreflightFailure(FAILURE_MARKER)
        finally:
            cleanup_failure = _close_descriptors(tuple(reversed(transient)))
        if primary is not None:
            raise primary
        if cleanup_failure is not None:
            _fail()


@dataclass
class _HeldComposeExecutable:
    path: Path
    components: tuple[str, ...]
    descriptors: tuple[int, ...]
    identities: tuple[os.stat_result, ...]

    @property
    def descriptor(self) -> int:
        return self.descriptors[-1]

    def revalidate(self) -> None:
        transient: list[int] = []
        primary: BaseException | None = None
        try:
            current = tuple(os.fstat(descriptor) for descriptor in self.descriptors)
            if len(current) != len(self.identities) or any(
                not _same_identity(old, new)
                for old, new in zip(self.identities, current, strict=True)
            ):
                _fail()
            parent = _open_directory("/")
            transient.append(parent)
            _require_system_directory(os.fstat(parent))
            for component in self.components[:-1]:
                parent = _open_directory(component, dir_fd=parent)
                transient.append(parent)
                _require_system_directory(os.fstat(parent))
            executable = os.open(
                self.components[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent,
            )
            transient.append(executable)
            _require_compose_executable(os.fstat(executable))
            rechecked = tuple(os.fstat(descriptor) for descriptor in transient)
            if len(rechecked) != len(self.identities) or any(
                not _same_identity(old, new)
                for old, new in zip(self.identities, rechecked, strict=True)
            ):
                _fail()
        except (CancelledError, KeyboardInterrupt, SystemExit) as error:
            primary = error
        except BaseException:  # noqa: BLE001 - executable detail is intentionally non-reflecting
            primary = PreflightFailure(FAILURE_MARKER)
        finally:
            cleanup_failure = _close_descriptors(tuple(reversed(transient)))
        if primary is not None:
            raise primary
        if cleanup_failure is not None:
            _fail()


def _hold_compose_executable(path: Path) -> _HeldComposeExecutable:
    descriptors: list[int] = []
    try:
        components = path.relative_to("/").parts
        if not components or path not in COMPOSE_EXECUTABLE_CANDIDATES:
            _fail()
        parent = _open_directory("/")
        descriptors.append(parent)
        _require_system_directory(os.fstat(parent))
        for component in components[:-1]:
            parent = _open_directory(component, dir_fd=parent)
            descriptors.append(parent)
            _require_system_directory(os.fstat(parent))
        executable = os.open(
            components[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent,
        )
        descriptors.append(executable)
        _require_compose_executable(os.fstat(executable))
        return _HeldComposeExecutable(
            path, components, tuple(descriptors), tuple(os.fstat(fd) for fd in descriptors)
        )
    except CancelledError, KeyboardInterrupt, SystemExit:
        _close_descriptors(tuple(reversed(descriptors)))
        raise
    except BaseException:  # noqa: BLE001 - executable detail is intentionally non-reflecting
        _close_descriptors(tuple(reversed(descriptors)))
        _fail()


def _hold_private_inputs(repository_root: Path) -> _HeldInputs:
    descriptors: list[int] = []
    try:
        if not repository_root.is_absolute():
            _fail()
        components = repository_root.parts[1:]
        if not components or any(
            not component or component in {".", ".."} for component in components
        ):
            _fail()
        filesystem_root = _open_directory("/")
        descriptors.append(filesystem_root)
        _require_filesystem_root_anchor(os.fstat(filesystem_root))
        parent = filesystem_root
        repository_chain: list[int] = []
        for component in components:
            parent = _open_directory(component, dir_fd=parent)
            descriptors.append(parent)
            repository_chain.append(parent)
            _require_repository_chain_directory(os.fstat(parent), os.fstat(filesystem_root).st_uid)
        root = repository_chain[-1]
        _require_parent_directory(os.fstat(root))
        compose = _open_regular(COMPOSE_FILE_NAME, dir_fd=root)
        descriptors.append(compose)
        _require_compose_file(os.fstat(compose))
        _read_compose_contract(compose)
        environment = _open_regular(PRIVATE_ENV_NAME, dir_fd=root)
        descriptors.append(environment)
        _require_private_file(os.fstat(environment))
        secret_directory = _open_directory(PRIVATE_SECRET_DIRECTORY_NAME, dir_fd=root)
        descriptors.append(secret_directory)
        _require_private_directory(os.fstat(secret_directory))
        if set(os.listdir(secret_directory)) != set(SECRET_FILE_NAMES):
            _fail()
        secret_descriptors: list[int] = []
        for name in SECRET_FILE_NAMES:
            descriptor = _open_regular(name, dir_fd=secret_directory)
            descriptors.append(descriptor)
            secret_descriptors.append(descriptor)
            _require_private_file(os.fstat(descriptor))
        return _HeldInputs(
            filesystem_root,
            tuple(components),
            tuple(repository_chain),
            root,
            compose,
            environment,
            secret_directory,
            tuple(secret_descriptors),
            tuple(os.fstat(descriptor) for descriptor in descriptors),
        )
    except CancelledError, KeyboardInterrupt, SystemExit:
        _close_descriptors(tuple(reversed(descriptors)))
        raise
    except BaseException:  # noqa: BLE001 - ordinary failures are intentionally non-reflecting
        _close_descriptors(tuple(reversed(descriptors)))
        _fail()


def _close_held(held: _HeldInputs, primary: BaseException | None) -> None:
    cleanup_failure = _close_descriptors(tuple(reversed(held.descriptors)))
    if primary is None and cleanup_failure is not None:
        _fail()


def validate_private_inputs(repository_root: Path) -> None:
    held: _HeldInputs | None = None
    primary: BaseException | None = None
    try:
        held = _hold_private_inputs(repository_root)
        held.revalidate()
    except (CancelledError, KeyboardInterrupt, SystemExit) as error:
        primary = error
    except BaseException:  # noqa: BLE001 - ordinary failures are intentionally non-reflecting
        primary = PreflightFailure(FAILURE_MARKER)
    finally:
        if held is not None:
            _close_held(held, primary)
    if primary is not None:
        raise primary


def resolve_compose_executable() -> Path:
    for candidate in COMPOSE_EXECUTABLE_CANDIDATES:
        try:
            probe = _hold_compose_executable(candidate)
        except PreflightFailure:
            continue
        else:
            # The probe descriptor is not used for execution; close it before the
            # run-specific holder is created below.
            _close_descriptors(tuple(reversed(probe.descriptors)))
            return candidate
    _fail()


def run_config_validation(
    repository_root: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    compose_resolver: Callable[[], Path] = resolve_compose_executable,
) -> None:
    held: _HeldInputs | None = None
    executable: _HeldComposeExecutable | None = None
    primary: BaseException | None = None
    executable_cleanup_failure: BaseException | None = None
    try:
        held = _hold_private_inputs(repository_root)
        executable = _hold_compose_executable(compose_resolver())
        held.revalidate()
        executable.revalidate()
        arguments, environment, child_fds = _child_contract(held, executable)
        _require_child_fd_contract(arguments, environment, child_fds)
        try:
            completed = runner(
                arguments,
                check=False,
                cwd=f"/proc/self/fd/{held.root_descriptor}",
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                pass_fds=child_fds,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )
        except CancelledError, KeyboardInterrupt, SystemExit:
            raise
        except Exception:  # noqa: BLE001 - subprocess detail is intentionally non-reflecting
            _fail()
        if completed.returncode != 0:
            _fail()
        held.revalidate()
        executable.revalidate()
    except (CancelledError, KeyboardInterrupt, SystemExit) as error:
        primary = error
    except BaseException:  # noqa: BLE001 - ordinary failures are intentionally non-reflecting
        primary = PreflightFailure(FAILURE_MARKER)
    finally:
        if executable is not None:
            executable_cleanup_failure = _close_descriptors(tuple(reversed(executable.descriptors)))
        if held is not None:
            _close_held(
                held,
                primary if primary is not None else executable_cleanup_failure,
            )
    if primary is None and executable_cleanup_failure is not None:
        primary = PreflightFailure(FAILURE_MARKER)
    if primary is not None:
        raise primary


def main(arguments: Sequence[str] | None = None) -> int:
    if tuple(sys.argv[1:] if arguments is None else arguments):
        print(FAILURE_MARKER)
        return FAILURE_EXIT_CODE
    try:
        run_config_validation(Path(__file__).resolve().parents[1])
    except CancelledError, KeyboardInterrupt, SystemExit:
        raise
    except Exception:  # noqa: BLE001 - CLI detail is intentionally non-reflecting
        print(FAILURE_MARKER)
        return FAILURE_EXIT_CODE
    print(SUCCESS_MARKER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
