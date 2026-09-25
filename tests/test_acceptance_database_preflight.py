from __future__ import annotations

import asyncio
import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT / "scripts" / "acceptance_database_preflight.py"
ACCEPTANCE_COMPOSE = ROOT / "compose.acceptance.yaml"


@pytest.fixture
def launcher(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "acceptance_database_preflight_test_module", LAUNCHER_PATH
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    descriptor = os.open(os.devnull, os.O_RDONLY)
    held = SimpleNamespace(
        descriptor=descriptor, descriptors=(descriptor,), revalidate=lambda: None
    )
    monkeypatch.setattr(module, "_hold_compose_executable", lambda _: held)
    return module


def _write_private_file(path: Path, value: str = "private") -> None:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def _create_valid_private_inputs(parent: Path) -> Path:
    grandparent = parent / "grandparent"
    grandparent.mkdir()
    grandparent.chmod(0o700)
    repository_parent = grandparent / "parent"
    repository_parent.mkdir()
    repository_parent.chmod(0o700)
    root = repository_parent / "repository"
    root.mkdir()
    root.chmod(0o700)
    compose = root / "compose.acceptance.yaml"
    compose.write_bytes(ACCEPTANCE_COMPOSE.read_bytes())
    compose.chmod(0o600)
    _write_private_file(root / ".env.acceptance-postgres")
    secret_directory = root / ".acceptance-postgres-secrets"
    secret_directory.mkdir()
    secret_directory.chmod(0o700)
    for name in ("postgres-user", "postgres-password", "postgres-database"):
        _write_private_file(secret_directory / name)
    return root


def _completed_process(returncode: int = 0) -> subprocess.CompletedProcess[object]:
    return subprocess.CompletedProcess(args=(), returncode=returncode)


def _fixed_resolver() -> Path:
    return Path("/usr/lib/docker/cli-plugins/docker-compose")


def test_valid_metadata_invokes_only_fixed_config_argv_once_with_sanitized_env(
    launcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    monkeypatch.setenv("DOCKER_HOST", "ambient-canary")
    monkeypatch.setenv("COMPOSE_FILE", "ambient-canary")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "ambient-canary")
    monkeypatch.setenv("PATH", "/tmp/ambient-canary")
    monkeypatch.setenv("PYTHONPATH", "ambient-canary")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        calls.append((args, kwargs))
        return _completed_process()

    launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert len(args) == 1
    assert args[0][1] == "--project-directory"
    assert args[0][2] == kwargs["cwd"]
    assert args[0][3] == "--env-file"
    assert args[0][4].startswith("/proc/self/fd/")
    assert args[0][5] == "-f"
    assert args[0][6].startswith("/proc/self/fd/")
    assert args[0][7:] == ("config", "--quiet")
    assert isinstance(args[0][0], str)
    assert args[0][0].startswith("/proc/self/fd/")
    assert "docker" not in args[0][1:]
    assert kwargs | {"cwd": "fixed", "env": "fixed", "pass_fds": "fixed"} == {
        "check": False,
        "cwd": "fixed",
        "shell": False,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": "fixed",
        "pass_fds": "fixed",
        "timeout": launcher.SUBPROCESS_TIMEOUT_SECONDS,
    }
    assert isinstance(kwargs["cwd"], str)
    assert kwargs["cwd"].startswith("/proc/self/fd/")
    assert args[0][4].startswith("/proc/self/fd/")
    assert args[0][6].startswith("/proc/self/fd/")
    assert kwargs["env"][launcher.SECRET_DIRECTORY_FD_ENVIRONMENT_NAME].startswith("/proc/self/fd/")
    expected_fds = {
        int(args[0][0].rsplit("/", maxsplit=1)[1]),
        int(args[0][2].rsplit("/", maxsplit=1)[1]),
        int(args[0][4].rsplit("/", maxsplit=1)[1]),
        int(args[0][6].rsplit("/", maxsplit=1)[1]),
        int(
            kwargs["env"][launcher.SECRET_DIRECTORY_FD_ENVIRONMENT_NAME].rsplit("/", maxsplit=1)[1]
        ),
    }
    assert set(kwargs["pass_fds"]) == expected_fds
    assert "ambient-canary" not in repr(kwargs)


def test_child_fd_contract_rejects_each_missing_referenced_descriptor(
    launcher: ModuleType,
) -> None:
    arguments = (
        "/proc/self/fd/10",
        "--project-directory",
        "/proc/self/fd/11",
        "--env-file",
        "/proc/self/fd/12",
        "-f",
        "/proc/self/fd/13",
        "config",
        "--quiet",
    )
    environment = {
        **launcher.SANITIZED_ENV,
        launcher.SECRET_DIRECTORY_FD_ENVIRONMENT_NAME: "/proc/self/fd/14",
    }
    required = (10, 11, 12, 13, 14)
    launcher._require_child_fd_contract(arguments, environment, required)
    for missing in required:
        with pytest.raises(launcher.PreflightFailure):
            launcher._require_child_fd_contract(
                arguments, environment, tuple(value for value in required if value != missing)
            )


def test_os_child_can_use_exactly_the_five_passed_fd_references(tmp_path: Path) -> None:
    """Exercise the kernel pass_fds contract without invoking Compose or Docker."""

    root = tmp_path / "root"
    root.mkdir()
    secret_directory = root / "secrets"
    secret_directory.mkdir()
    executable = root / "executable"
    environment = root / "environment"
    compose = root / "compose"
    for target in (executable, environment, compose):
        target.write_text("non-secret fixture", encoding="utf-8")
    descriptors = (
        os.open(executable, os.O_RDONLY),
        os.open(root, os.O_RDONLY | os.O_DIRECTORY),
        os.open(environment, os.O_RDONLY),
        os.open(compose, os.O_RDONLY),
        os.open(secret_directory, os.O_RDONLY | os.O_DIRECTORY),
    )
    expected = tuple((os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in descriptors)
    program = (
        "import os, sys\n"
        "for item in sys.argv[1:]:\n"
        "    fd, dev, ino = (int(value) for value in item.split(':'))\n"
        "    info = os.fstat(fd)\n"
        "    probe = os.open('/proc/self/fd/' + str(fd), os.O_RDONLY)\n"
        "    os.close(probe)\n"
        "    if (info.st_dev, info.st_ino) != (dev, ino): raise SystemExit(3)\n"
        "print('OK')\n"
    )
    arguments = tuple(
        f"{fd}:{dev}:{ino}" for fd, (dev, ino) in zip(descriptors, expected, strict=True)
    )
    try:
        valid = subprocess.run(
            (sys.executable, "-c", program, *arguments),
            check=False,
            capture_output=True,
            pass_fds=descriptors,
            timeout=5,
            text=True,
        )
        assert valid.returncode == 0
        assert valid.stdout == "OK\n"
        assert valid.stderr == ""
        for missing in descriptors:
            result = subprocess.run(
                (sys.executable, "-c", program, *arguments),
                check=False,
                capture_output=True,
                pass_fds=tuple(fd for fd in descriptors if fd != missing),
                timeout=5,
                text=True,
            )
            assert result.returncode != 0
            assert result.stdout != "OK\n"
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@pytest.mark.parametrize(
    "malicious",
    (
        b'{"name":"discord-ai-reminder-bot-acceptance-db","services":{"acceptance_postgres":{"privileged":true}},"volumes":{"acceptance_postgres_data":{"name":"discord-ai-reminder-bot-acceptance-postgres-data"}},"networks":{"acceptance_postgres_network":{"name":"discord-ai-reminder-bot-acceptance-postgres-network"}}}',
        b'{"name":"discord-ai-reminder-bot-acceptance-db","name":"duplicate","services":{},"volumes":{},"networks":{}}',
        b'{"name":"discord-ai-reminder-bot-acceptance-db","services":{"acceptance_postgres":{"healthcheck":{"retries":true}}},"volumes":{"acceptance_postgres_data":{"name":"discord-ai-reminder-bot-acceptance-postgres-data"}},"networks":{"acceptance_postgres_network":{"name":"discord-ai-reminder-bot-acceptance-postgres-network"}}}',
    ),
)
def test_production_compose_validator_rejects_malicious_bytes_before_runner(
    launcher: ModuleType, tmp_path: Path, malicious: bytes
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    compose = root / "compose.acceptance.yaml"
    compose.write_bytes(malicious)
    compose.chmod(0o600)
    calls = 0

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        nonlocal calls
        calls += 1
        return _completed_process()

    with pytest.raises(launcher.PreflightFailure) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert calls == 0
    assert captured.value.args == (launcher.FAILURE_MARKER,)


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_environment",
        "empty_secret",
        "unexpected_secret",
        "directory_mode",
        "file_mode",
        "parent_mode",
        "secret_symlink",
        "secret_directory_type",
    ),
)
def test_invalid_private_metadata_never_calls_docker(
    launcher: ModuleType, tmp_path: Path, mutation: str
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    environment = root / ".env.acceptance-postgres"
    secret_directory = root / ".acceptance-postgres-secrets"
    secret = secret_directory / "postgres-password"
    if mutation == "missing_environment":
        environment.unlink()
    elif mutation == "empty_secret":
        secret.write_text("", encoding="utf-8")
    elif mutation == "unexpected_secret":
        _write_private_file(secret_directory / "unexpected")
    elif mutation == "directory_mode":
        secret_directory.chmod(0o755)
    elif mutation == "file_mode":
        secret.chmod(0o644)
    elif mutation == "parent_mode":
        root.chmod(0o775)
    elif mutation == "secret_symlink":
        secret.unlink()
        secret.symlink_to(secret_directory / "postgres-user")
    elif mutation == "secret_directory_type":
        for child in secret_directory.iterdir():
            child.unlink()
        secret_directory.rmdir()
        _write_private_file(secret_directory)
    else:
        raise AssertionError(mutation)
    calls = 0

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        nonlocal calls
        calls += 1
        return _completed_process()

    with pytest.raises(launcher.PreflightFailure):
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert calls == 0


@pytest.mark.parametrize("nlink", (0, 1, -1, True, "invalid"))
def test_private_directory_rejects_invalid_nlink(launcher: ModuleType, nlink: object) -> None:
    metadata = SimpleNamespace(
        st_dev=1,
        st_ino=1,
        st_mode=stat.S_IFDIR | 0o700,
        st_uid=os.getuid(),
        st_gid=os.getgid(),
        st_nlink=nlink,
        st_size=1,
    )
    with pytest.raises(launcher.PreflightFailure):
        launcher._require_private_directory(metadata)


@pytest.mark.parametrize("nlink", (2, 3))
def test_private_directory_accepts_linux_valid_link_counts(
    launcher: ModuleType, nlink: int
) -> None:
    metadata = SimpleNamespace(
        st_dev=1,
        st_ino=1,
        st_mode=stat.S_IFDIR | 0o700,
        st_uid=os.getuid(),
        st_gid=os.getgid(),
        st_nlink=nlink,
        st_size=1,
    )
    launcher._require_private_directory(metadata)


@pytest.mark.parametrize("nlink", (1, 0, 2, True, "invalid"))
def test_private_file_requires_exactly_one_strict_integer_link(
    launcher: ModuleType, nlink: object
) -> None:
    metadata = SimpleNamespace(
        st_dev=1,
        st_ino=1,
        st_mode=stat.S_IFREG | 0o600,
        st_uid=os.getuid(),
        st_gid=os.getgid(),
        st_nlink=nlink,
        st_size=1,
    )
    if type(nlink) is int and nlink == 1:
        launcher._require_private_file(metadata)
    else:
        with pytest.raises(launcher.PreflightFailure):
            launcher._require_private_file(metadata)


def _replace_private_path(root: Path, target: Path) -> None:
    discarded = target.with_name(f"discarded-{target.name}")
    target.rename(discarded)
    if target.name == ".acceptance-postgres-secrets":
        target.mkdir()
        target.chmod(0o700)
        for name in ("postgres-user", "postgres-password", "postgres-database"):
            _write_private_file(target / name)
    else:
        _write_private_file(target)


@pytest.mark.parametrize(
    "relative_target",
    (
        ".env.acceptance-postgres",
        ".acceptance-postgres-secrets",
        ".acceptance-postgres-secrets/postgres-user",
        ".acceptance-postgres-secrets/postgres-password",
        ".acceptance-postgres-secrets/postgres-database",
    ),
)
def test_post_call_identity_swap_discards_success(
    launcher: ModuleType, tmp_path: Path, relative_target: str
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    calls = 0

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        nonlocal calls
        calls += 1
        _replace_private_path(root, root / relative_target)
        return _completed_process()

    with pytest.raises(launcher.PreflightFailure) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert captured.value.args == (launcher.FAILURE_MARKER,)
    assert calls == 1


def test_pre_call_identity_swap_prevents_docker_call(launcher: ModuleType, tmp_path: Path) -> None:
    root = _create_valid_private_inputs(tmp_path)
    calls = 0

    def resolver() -> Path:
        _replace_private_path(root, root / ".env.acceptance-postgres")
        return _fixed_resolver()

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        nonlocal calls
        calls += 1
        return _completed_process()

    with pytest.raises(launcher.PreflightFailure):
        launcher.run_config_validation(root, runner=runner, compose_resolver=resolver)
    assert calls == 0


def test_post_call_symlink_swap_discards_success_without_reflecting_target(
    launcher: ModuleType, tmp_path: Path
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    environment = root / ".env.acceptance-postgres"
    discarded = root / "discarded-environment"
    calls = 0

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        nonlocal calls
        calls += 1
        environment.rename(discarded)
        environment.symlink_to(discarded)
        return _completed_process()

    with pytest.raises(launcher.PreflightFailure) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert calls == 1
    assert captured.value.args == (launcher.FAILURE_MARKER,)
    assert "discarded-environment" not in repr(captured.value)


def test_post_call_repository_root_rename_swap_discards_success(
    launcher: ModuleType, tmp_path: Path
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    discarded = tmp_path / "discarded-repository"
    calls = 0

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        nonlocal calls
        calls += 1
        root.rename(discarded)
        root.mkdir(mode=0o700)
        return _completed_process()

    with pytest.raises(launcher.PreflightFailure) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert calls == 1
    assert captured.value.args == (launcher.FAILURE_MARKER,)
    assert "discarded-repository" not in repr(captured.value)


def test_post_call_repository_parent_rename_swap_discards_success(
    launcher: ModuleType, tmp_path: Path
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    repository_parent = root.parent
    discarded = repository_parent.with_name("discarded-parent")
    calls = 0

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        nonlocal calls
        calls += 1
        repository_parent.rename(discarded)
        repository_parent.mkdir(mode=0o700)
        return _completed_process()

    with pytest.raises(launcher.PreflightFailure) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert calls == 1
    assert captured.value.args == (launcher.FAILURE_MARKER,)
    assert "discarded-parent" not in repr(captured.value)


def test_post_call_repository_grandparent_rename_swap_discards_success_from_root_anchor(
    launcher: ModuleType, tmp_path: Path
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    grandparent = root.parent.parent
    discarded = grandparent.with_name("discarded-grandparent")
    calls = 0

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        nonlocal calls
        calls += 1
        grandparent.rename(discarded)
        grandparent.mkdir(mode=0o700)
        return _completed_process()

    with pytest.raises(launcher.PreflightFailure) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert calls == 1
    assert captured.value.args == (launcher.FAILURE_MARKER,)
    assert "discarded-grandparent" not in repr(captured.value)


def test_pre_call_repository_grandparent_swap_prevents_runner_from_root_anchor(
    launcher: ModuleType, tmp_path: Path
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    grandparent = root.parent.parent
    discarded = grandparent.with_name("discarded-grandparent")
    calls = 0

    def resolver() -> Path:
        grandparent.rename(discarded)
        grandparent.mkdir(mode=0o700)
        return _fixed_resolver()

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        nonlocal calls
        calls += 1
        return _completed_process()

    with pytest.raises(launcher.PreflightFailure):
        launcher.run_config_validation(root, runner=runner, compose_resolver=resolver)
    assert calls == 0


def _swap_ancestor_to_temporary_symlink(ancestor: Path, temporary_root: Path) -> str:
    """Replace only a test-tree ancestor; no target is outside pytest storage."""

    canary = f"ancestor-symlink-target-{ancestor.name}"
    target = temporary_root / canary
    target.mkdir(mode=0o700)
    ancestor.rename(ancestor.with_name(f"discarded-{ancestor.name}"))
    ancestor.symlink_to(target, target_is_directory=True)
    return canary


@pytest.mark.parametrize("ancestor_kind", ("grandparent", "parent"))
def test_pre_call_ancestor_symlink_swap_prevents_runner_from_root_anchor(
    launcher: ModuleType, tmp_path: Path, ancestor_kind: str
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    ancestor = root.parent.parent if ancestor_kind == "grandparent" else root.parent
    calls = 0
    canary = ""

    def resolver() -> Path:
        nonlocal canary
        canary = _swap_ancestor_to_temporary_symlink(ancestor, tmp_path)
        return _fixed_resolver()

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        nonlocal calls
        calls += 1
        return _completed_process()

    with pytest.raises(launcher.PreflightFailure) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=resolver)
    assert calls == 0
    assert captured.value.args == (launcher.FAILURE_MARKER,)
    assert canary not in repr(captured.value)


@pytest.mark.parametrize("ancestor_kind", ("grandparent", "parent"))
def test_post_call_ancestor_symlink_swap_discards_success_from_root_anchor(
    launcher: ModuleType, tmp_path: Path, ancestor_kind: str
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    ancestor = root.parent.parent if ancestor_kind == "grandparent" else root.parent
    calls = 0
    canary = ""

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        nonlocal calls, canary
        calls += 1
        canary = _swap_ancestor_to_temporary_symlink(ancestor, tmp_path)
        return _completed_process()

    with pytest.raises(launcher.PreflightFailure) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert calls == 1
    assert captured.value.args == (launcher.FAILURE_MARKER,)
    assert canary not in repr(captured.value)


@pytest.mark.parametrize(
    "relative_target",
    (
        ".",
        ".env.acceptance-postgres",
        ".acceptance-postgres-secrets",
        ".acceptance-postgres-secrets/postgres-user",
        ".acceptance-postgres-secrets/postgres-password",
        ".acceptance-postgres-secrets/postgres-database",
    ),
)
def test_post_call_every_target_symlink_swap_discards_success(
    launcher: ModuleType, tmp_path: Path, relative_target: str
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    target = root if relative_target == "." else root / relative_target
    discarded = target.with_name(f"discarded-{target.name}")
    calls = 0

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        nonlocal calls
        calls += 1
        target.rename(discarded)
        target.symlink_to(discarded, target_is_directory=discarded.is_dir())
        return _completed_process()

    with pytest.raises(launcher.PreflightFailure) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert calls == 1
    assert captured.value.args == (launcher.FAILURE_MARKER,)
    assert "discarded-" not in repr(captured.value)


@pytest.mark.parametrize(
    "field", ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
)
def test_private_metadata_rejects_non_strict_identity_field(
    launcher: ModuleType, field: str
) -> None:
    metadata = SimpleNamespace(
        st_dev=1,
        st_ino=2,
        st_mode=stat.S_IFREG | 0o600,
        st_uid=os.getuid(),
        st_gid=os.getgid(),
        st_nlink=1,
        st_size=1,
    )
    setattr(metadata, field, True)
    with pytest.raises(launcher.PreflightFailure):
        launcher._require_private_file(metadata)


@pytest.mark.parametrize(
    "field", ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
)
def test_parent_and_compose_metadata_reject_non_strict_identity_field(
    launcher: ModuleType, field: str
) -> None:
    parent = SimpleNamespace(
        st_dev=1,
        st_ino=2,
        st_mode=stat.S_IFDIR | 0o700,
        st_uid=os.getuid(),
        st_gid=os.getgid(),
        st_nlink=2,
        st_size=1,
    )
    compose = SimpleNamespace(
        st_dev=1,
        st_ino=2,
        st_mode=stat.S_IFREG | 0o600,
        st_uid=os.getuid(),
        st_gid=os.getgid(),
        st_nlink=1,
        st_size=1,
    )
    setattr(parent, field, "invalid")
    setattr(compose, field, "invalid")
    with pytest.raises(launcher.PreflightFailure):
        launcher._require_parent_directory(parent)
    with pytest.raises(launcher.PreflightFailure):
        launcher._require_compose_file(compose)


@pytest.mark.parametrize(
    "kind",
    ("symlink", "unsafe_mode", "unsafe_owner", "non_regular", "non_executable", "invalid_gid"),
)
def test_compose_executable_rejects_untrusted_metadata(launcher: ModuleType, kind: str) -> None:
    metadata = SimpleNamespace(
        st_dev=1,
        st_ino=2,
        st_mode=stat.S_IFREG | 0o755,
        st_uid=0,
        st_gid=0,
        st_nlink=1,
        st_size=1,
    )
    if kind == "symlink":
        metadata.st_mode = stat.S_IFLNK | 0o777
    elif kind == "unsafe_mode":
        metadata.st_mode = stat.S_IFREG | 0o775
    elif kind == "unsafe_owner":
        metadata.st_uid = os.getuid()
    elif kind == "non_regular":
        metadata.st_mode = stat.S_IFDIR | 0o755
    elif kind == "non_executable":
        metadata.st_mode = stat.S_IFREG | 0o644
    elif kind == "invalid_gid":
        metadata.st_gid = True
    with pytest.raises(launcher.PreflightFailure):
        launcher._require_compose_executable(metadata)


def test_identity_comparison_includes_gid(launcher: ModuleType) -> None:
    left = SimpleNamespace(
        st_dev=1,
        st_ino=2,
        st_mode=stat.S_IFREG | 0o755,
        st_uid=0,
        st_gid=0,
        st_nlink=1,
        st_size=1,
    )
    right = SimpleNamespace(
        st_dev=1,
        st_ino=2,
        st_mode=stat.S_IFREG | 0o755,
        st_uid=0,
        st_gid=1,
        st_nlink=1,
        st_size=1,
    )
    assert not launcher._same_identity(left, right)


def test_fixed_candidate_symlink_is_unsupported_and_never_resolved(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempted: list[Path] = []

    def reject(candidate: Path) -> object:
        attempted.append(candidate)
        raise launcher.PreflightFailure(launcher.FAILURE_MARKER)

    monkeypatch.setattr(launcher, "_hold_compose_executable", reject)
    with pytest.raises(launcher.PreflightFailure):
        launcher.resolve_compose_executable()
    assert attempted == list(launcher.COMPOSE_EXECUTABLE_CANDIDATES)


@pytest.mark.parametrize(
    "failure", (FileNotFoundError("docker-canary"), RuntimeError("docker-canary"))
)
def test_docker_failures_are_fixed_and_non_reflecting(
    launcher: ModuleType, tmp_path: Path, failure: Exception
) -> None:
    root = _create_valid_private_inputs(tmp_path)

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        raise failure

    with pytest.raises(launcher.PreflightFailure) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert "docker-canary" not in str(captured.value)
    assert "docker-canary" not in repr(captured.value)


@pytest.mark.parametrize("special", (asyncio.CancelledError(), KeyboardInterrupt(), SystemExit()))
def test_special_docker_failure_identity_is_preserved(
    launcher: ModuleType, tmp_path: Path, special: BaseException
) -> None:
    root = _create_valid_private_inputs(tmp_path)

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        raise special

    with pytest.raises(type(special)) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert captured.value is special


def test_close_attempts_every_descriptor_and_preserves_special_primary(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary = KeyboardInterrupt()
    held = launcher._HeldInputs(1, ("a", "b", "c"), (2, 3, 4), 4, 5, 6, 7, (8, 9, 10), ())
    close_calls: list[int] = []

    def close(descriptor: int) -> None:
        close_calls.append(descriptor)
        if descriptor in {9, 7}:
            raise RuntimeError("close-canary")

    monkeypatch.setattr(launcher.os, "close", close)
    launcher._close_held(held, primary)
    assert close_calls == [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    assert len(close_calls) == len(set(close_calls))
    assert primary.args == ()


@pytest.mark.parametrize("primary", (asyncio.CancelledError(), KeyboardInterrupt(), SystemExit()))
def test_runner_special_primary_survives_real_held_descriptor_close_failures(
    launcher: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primary: BaseException,
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    original_close = launcher.os.close
    close_calls: list[int] = []

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        def close(descriptor: int) -> None:
            close_calls.append(descriptor)
            original_close(descriptor)
            raise RuntimeError("close-canary")

        monkeypatch.setattr(launcher.os, "close", close)
        raise primary

    with pytest.raises(type(primary)) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert captured.value is primary
    assert len(close_calls) == 15
    assert len(close_calls) == len(set(close_calls))


def test_runner_ordinary_primary_is_fixed_after_held_descriptor_close_failures(
    launcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    original_close = launcher.os.close
    close_calls: list[int] = []

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        def close(descriptor: int) -> None:
            close_calls.append(descriptor)
            original_close(descriptor)
            raise RuntimeError("close-canary")

        monkeypatch.setattr(launcher.os, "close", close)
        raise RuntimeError("primary-canary")

    with pytest.raises(launcher.PreflightFailure) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert captured.value.args == (launcher.FAILURE_MARKER,)
    assert "close-canary" not in repr(captured.value)
    assert "primary-canary" not in repr(captured.value)
    assert len(close_calls) == 15
    assert len(close_calls) == len(set(close_calls))


def test_post_call_compose_executable_replacement_discards_success(
    launcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _create_valid_private_inputs(tmp_path)
    executable = tmp_path / "trusted-compose"
    executable.write_bytes(b"test")
    executable.chmod(0o700)
    descriptor = os.open(executable, os.O_RDONLY)
    replacement_seen = False

    def revalidate() -> None:
        if replacement_seen:
            raise launcher.PreflightFailure(launcher.FAILURE_MARKER)

    held = SimpleNamespace(descriptor=descriptor, descriptors=(descriptor,), revalidate=revalidate)
    monkeypatch.setattr(launcher, "_hold_compose_executable", lambda _: held)
    discarded = tmp_path / "discarded-compose"
    calls = 0

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        nonlocal calls, replacement_seen
        calls += 1
        executable.rename(discarded)
        executable.write_bytes(b"replacement")
        executable.chmod(0o700)
        replacement_seen = True
        return _completed_process()

    with pytest.raises(launcher.PreflightFailure) as captured:
        launcher.run_config_validation(root, runner=runner, compose_resolver=_fixed_resolver)
    assert calls == 1
    assert captured.value.args == (launcher.FAILURE_MARKER,)
    assert "discarded-compose" not in repr(captured.value)


def test_main_rejects_arbitrary_arguments_without_calling_launcher(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = 0

    def run_config_validation(_: Path) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(launcher, "run_config_validation", run_config_validation)
    assert launcher.main(("--compose=path-canary",)) == launcher.FAILURE_EXIT_CODE
    captured = capsys.readouterr()
    assert captured.out == f"{launcher.FAILURE_MARKER}\n"
    assert "path-canary" not in captured.out
    assert captured.err == ""
    assert calls == 0
