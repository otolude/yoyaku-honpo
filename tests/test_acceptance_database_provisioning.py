from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Self

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "scripts" / "acceptance_database_provision.py"
COMPOSE_PATH = ROOT / "compose.acceptance.provision.yaml"
TEST_INTERPRETER = Path(sys.executable).resolve()


def _load_helper() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "acceptance_database_provisioning_test_module", HELPER_PATH
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


@pytest.fixture
def helper(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load_helper()
    for name in tuple(os.environ):
        if name.startswith(module.PROHIBITED_ENVIRONMENT_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    return module


class _DockerResponseSocket:
    """Offline AF_UNIX response fixture; it never opens a real socket."""

    def __init__(self, response: bytes | list[bytes | BaseException]) -> None:
        self._fragments = [response] if isinstance(response, bytes) else list(response)
        self.request = b""
        self.timeout: float | None = None
        self.connected: str | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def connect(self, path: str) -> None:
        self.connected = path

    def sendall(self, value: bytes) -> None:
        self.request += value

    def recv(self, size: int) -> bytes:
        if not self._fragments:
            return b""
        fragment = self._fragments.pop(0)
        if isinstance(fragment, BaseException):
            raise fragment
        return fragment


def _docker_http_response(
    headers: bytes, body: bytes, *, status: bytes = b"HTTP/1.1 200 OK"
) -> bytes:
    return status + b"\r\n" + headers + b"\r\n\r\n" + body


def _docker_chunked(body: bytes) -> bytes:
    midpoint = max(1, len(body) // 2)
    first, second = body[:midpoint], body[midpoint:]
    chunks = [first, second] if second else [first]
    return (
        b"".join(f"{len(chunk):X}".encode("ascii") + b"\r\n" + chunk + b"\r\n" for chunk in chunks)
        + b"0\r\n\r\n"
    )


def _install_docker_socket(
    helper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    response: bytes | list[bytes | BaseException],
) -> _DockerResponseSocket:
    fake = _DockerResponseSocket(response)
    monkeypatch.setattr(helper.socket, "socket", lambda *_: fake)
    return fake


def test_docker_request_decodes_observed_chunked_image_response(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = json.dumps(
        {
            "RepoDigests": [helper.EXPECTED_IMAGE_REFERENCE],
            "Os": "linux",
            "Architecture": "amd64",
            "Id": "sha256:" + "0" * 64,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    fake = _install_docker_socket(
        helper,
        monkeypatch,
        _docker_http_response(b"Transfer-Encoding: chunked", _docker_chunked(body)),
    )
    assert helper._docker_request("/images/example/json") == json.loads(body)
    assert fake.timeout == helper.DOCKER_API_TIMEOUT_SECONDS
    assert fake.connected == str(helper.DOCKER_SOCKET)
    assert (
        fake.request
        == b"GET /images/example/json HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n"
    )


@pytest.mark.parametrize("width", (1, 2, 7, 31))
def test_docker_request_accepts_chunked_framing_across_recv_boundaries(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    body = b'{"Volumes":[]}'
    response = _docker_http_response(b"Transfer-Encoding: chunked", _docker_chunked(body))
    _install_docker_socket(
        helper,
        monkeypatch,
        [response[offset : offset + width] for offset in range(0, len(response), width)],
    )
    assert helper._docker_request("/volumes") == {"Volumes": []}


def test_docker_request_accepts_fragmented_content_length_response(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b'{"Volumes":[]}'
    response = _docker_http_response(f"Content-Length: {len(body)}".encode("ascii"), body)
    _install_docker_socket(
        helper,
        monkeypatch,
        [response[:19], response[19:31], response[31:], b""],
    )
    assert helper._docker_request("/volumes") == {"Volumes": []}


def test_docker_request_preserves_content_length_response_behavior(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b'{"Volumes":[]}'
    _install_docker_socket(
        helper,
        monkeypatch,
        _docker_http_response(f"Content-Length: {len(body)}".encode("ascii"), body),
    )
    assert helper._docker_request("/volumes") == {"Volumes": []}


@pytest.mark.parametrize(
    "headers,body",
    (
        (b"Transfer-Encoding: chunked", b"Z\r\n"),
        (b"Transfer-Encoding: chunked", b"5\r\n{}"),
        (b"Transfer-Encoding: chunked", b"2\r\n{}\n0\r\n\r\n"),
        (b"Transfer-Encoding: chunked", b"2\r\n{}\r\n"),
        (b"Transfer-Encoding: chunked", b"2\r\n{}\r\n0\r\n\r\nX"),
        (b"Transfer-Encoding: chunked", b"2\r\n{}\r\n0\r\nX-Test: value\r\n\r\n"),
        (b"Transfer-Encoding: chunked", b"2;extension\r\n{}\r\n0\r\n\r\n"),
        (b"Transfer-Encoding: chunked\r\nContent-Length: 2", b"2\r\n{}\r\n0\r\n\r\n"),
        (b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked", b"2\r\n{}\r\n0\r\n\r\n"),
        (b"Content-Length: 2\r\nContent-Length: 2", b"{}"),
        (b"Content-Length: 2\r\nContent-Encoding: identity", b"{}"),
        (b"Content-Length: 2\r\n folded: value", b"{}"),
        (b"Content-Length: 2\r\nMalformed", b"{}"),
        (b"Transfer-Encoding: gzip", b"{}"),
        (b"Transfer-Encoding: chunked", f"{(1 << 20) + 1:X}\r\n".encode("ascii")),
    ),
)
def test_docker_request_rejects_invalid_or_oversized_chunked_framing(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, headers: bytes, body: bytes
) -> None:
    _install_docker_socket(helper, monkeypatch, _docker_http_response(headers, body))
    with pytest.raises(helper.ProvisioningFailure):
        helper._docker_request("/images/example/json")


def test_docker_request_rejects_content_length_excess_body(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_docker_socket(
        helper,
        monkeypatch,
        _docker_http_response(b"Content-Length: 2", b"{}X"),
    )
    with pytest.raises(helper.ProvisioningFailure):
        helper._docker_request("/volumes")


@pytest.mark.parametrize(
    "headers,framing",
    (
        (b"Content-Length: 2", b"{}"),
        (b"Transfer-Encoding: chunked", b"2\r\n{}\r\n0\r\n\r\n"),
    ),
)
def test_docker_request_rejects_delayed_extra_data_after_framing(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, headers: bytes, framing: bytes
) -> None:
    response = _docker_http_response(headers, framing)
    _install_docker_socket(helper, monkeypatch, [response, b"X"])
    with pytest.raises(helper.ProvisioningFailure):
        helper._docker_request("/volumes")


@pytest.mark.parametrize(
    "headers,body",
    (
        (b"Content-Type: application/json", b"{}"),
        (b"Content-Length: ", b"{}"),
        (b"Content-Length: +2", b"{}"),
        (b"Content-Length: -2", b"{}"),
        (b"Content-Length: 02", b"{}"),
        (b"Content-Length: 2x", b"{}"),
        (b"Content-Length: 3", b"{}"),
        (b"Content-Length: 2\r\nX-Trace: one\r\nx-trace: two", b"{}"),
        (b"Content-Length: 2\r\nX-Trace:\tvalue", b"{}"),
        (b"Content-Length: 2\r\nX-Trace:  value", b"{}"),
        (b"Content-Length: 2\r\nX-Trace: value ", b"{}"),
        (b"Content-Length: 2\r\nX-Trace: value\x7f", b"{}"),
        (b"Content-Length: 2\r\nX-Trace: \x80", b"{}"),
    ),
)
def test_docker_request_rejects_invalid_content_length_or_headers(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, headers: bytes, body: bytes
) -> None:
    _install_docker_socket(helper, monkeypatch, _docker_http_response(headers, body))
    with pytest.raises(helper.ProvisioningFailure):
        helper._docker_request("/volumes")


@pytest.mark.parametrize(
    "status",
    (b"HTTP/1.0 200 OK", b"HTTP/1.1 200", b"HTTP/1.1 201 OK", b"HTTP/1.1  200 OK"),
)
def test_docker_request_rejects_status_line_variants(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, status: bytes
) -> None:
    _install_docker_socket(
        helper,
        monkeypatch,
        _docker_http_response(b"Content-Length: 2", b"{}", status=status),
    )
    with pytest.raises(helper.ProvisioningFailure):
        helper._docker_request("/volumes")


@pytest.mark.parametrize(
    "body",
    (
        b"2\r\n{}\r\n0\r\n",
        b"2\r\n{}\r\n0\r",
        b"2\r\n{}\r\n0",
        b"2\r\n{}\r\n",
        b"2\r\n{}\r\n0\r\n\r",
    ),
)
def test_docker_request_rejects_chunked_truncation_at_terminal_framing(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    response = _docker_http_response(b"Transfer-Encoding: chunked", body)
    _install_docker_socket(helper, monkeypatch, [response, b""])
    with pytest.raises(helper.ProvisioningFailure):
        helper._docker_request("/volumes")


@pytest.mark.parametrize(
    "body",
    (
        b"2",
        b"2\r",
        b"2\r\n",
        b"2\r\n{",
        b"2\r\n{}\r",
    ),
)
def test_docker_request_rejects_chunked_truncation_before_terminal_chunk(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    response = _docker_http_response(b"Transfer-Encoding: chunked", body)
    _install_docker_socket(helper, monkeypatch, [response, b""])
    with pytest.raises(helper.ProvisioningFailure):
        helper._docker_request("/volumes")


@pytest.mark.parametrize(
    "headers,framing",
    (
        (b"Content-Length: 2", b"{}"),
        (b"Transfer-Encoding: chunked", b"2\r\n{}\r\n0\r\n\r\n"),
    ),
)
def test_docker_request_rejects_timeout_instead_of_eof_after_framing(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, headers: bytes, framing: bytes
) -> None:
    response = _docker_http_response(headers, framing)
    _install_docker_socket(helper, monkeypatch, [response, TimeoutError("fixture timeout")])
    with pytest.raises(helper.ProvisioningFailure):
        helper._docker_request("/volumes")


def _header_boundary_response(helper: ModuleType, header_size: int) -> bytes:
    prefix = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nX-Fill: "
    assert header_size >= len(prefix)
    return prefix + (b"a" * (header_size - len(prefix))) + b"\r\n\r\n{}"


def test_docker_request_accepts_header_at_exact_limit_with_split_delimiter(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _header_boundary_response(helper, helper.DOCKER_API_MAX_HEADER_BYTES)
    boundary = helper.DOCKER_API_MAX_HEADER_BYTES + 3
    _install_docker_socket(helper, monkeypatch, [response[:boundary], response[boundary:], b""])
    assert helper._docker_request("/volumes") == {}


def test_docker_request_rejects_header_over_limit(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _header_boundary_response(helper, helper.DOCKER_API_MAX_HEADER_BYTES + 1)
    _install_docker_socket(helper, monkeypatch, [response, b""])
    with pytest.raises(helper.ProvisioningFailure):
        helper._docker_request("/volumes")


def _chunked_encoded_limit_response(helper: ModuleType) -> bytes:
    header = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
    terminal = b"0\r\n\r\n"
    remaining = helper.DOCKER_API_MAX_ENCODED_BYTES - len(header) - len(terminal)
    five_chunks, remainder = divmod(remaining, 10)
    sizes = [5] * five_chunks
    if remainder:
        sizes.pop()
        remainder += 10
        if remainder in (11, 12, 13, 14):
            sizes.append(remainder - 5)
        elif remainder == 15:
            sizes.extend((1, 4))
        else:  # The fixed constants above must always produce an encodable remainder.
            raise AssertionError("unexpected encoded-boundary remainder")
    body_size = sum(sizes)
    assert body_size <= helper.DOCKER_API_MAX_BODY_BYTES
    body = b'{"x":"' + (b"x" * (body_size - 8)) + b'"}'
    assert len(body) == body_size
    chunks: list[bytes] = []
    offset = 0
    for size in sizes:
        chunk = body[offset : offset + size]
        chunks.append(f"{size:X}".encode("ascii") + b"\r\n" + chunk + b"\r\n")
        offset += size
    response = header + b"".join(chunks) + terminal
    assert len(response) == helper.DOCKER_API_MAX_ENCODED_BYTES
    return response


def test_docker_request_accepts_encoded_response_at_exact_limit(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _chunked_encoded_limit_response(helper)
    _install_docker_socket(helper, monkeypatch, [response, b""])
    assert isinstance(helper._docker_request("/volumes"), dict)


def test_docker_request_rejects_encoded_response_over_limit(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _chunked_encoded_limit_response(helper) + b"X"
    _install_docker_socket(helper, monkeypatch, [response, b""])
    with pytest.raises(helper.ProvisioningFailure):
        helper._docker_request("/volumes")


@pytest.mark.parametrize("over_limit", (False, True))
def test_docker_request_enforces_decoded_body_limit(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, over_limit: bool
) -> None:
    target_size = helper.DOCKER_API_MAX_BODY_BYTES + int(over_limit)
    body = b'{"x":"' + (b"x" * (target_size - 8)) + b'"}'
    response = _docker_http_response(b"Transfer-Encoding: chunked", _docker_chunked(body))
    _install_docker_socket(helper, monkeypatch, [response, b""])
    if over_limit:
        with pytest.raises(helper.ProvisioningFailure):
            helper._docker_request("/volumes")
    else:
        assert isinstance(helper._docker_request("/volumes"), dict)


@pytest.mark.parametrize("over_limit", (False, True))
def test_docker_request_enforces_content_length_body_limit(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, over_limit: bool
) -> None:
    target_size = helper.DOCKER_API_MAX_BODY_BYTES + int(over_limit)
    body = b'{"x":"' + (b"x" * (target_size - 8)) + b'"}'
    response = _docker_http_response(f"Content-Length: {len(body)}".encode("ascii"), body)
    _install_docker_socket(helper, monkeypatch, [response, b""])
    if over_limit:
        with pytest.raises(helper.ProvisioningFailure):
            helper._docker_request("/volumes")
    else:
        assert isinstance(helper._docker_request("/volumes"), dict)


def test_docker_request_uses_fixed_nonreflecting_failure_for_framing_canary(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "canary-response-content-must-not-reflect"
    _install_docker_socket(
        helper,
        monkeypatch,
        _docker_http_response(b"Content-Length: 2", b"{}" + canary.encode("ascii")),
    )
    with pytest.raises(helper.ProvisioningFailure) as error:
        helper._docker_request("/volumes")
    assert str(error.value) == helper.FAILURE_MARKER
    assert canary not in repr(error.value)


@pytest.mark.parametrize(
    "fragments",
    (
        [b"HTTP/1.1 200"],
        [b"HTTP/1.1 200 OK\r\nContent-Len"],
        [b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r"],
    ),
)
def test_docker_request_rejects_header_eof_before_terminator_without_reflection(
    helper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fragments: list[bytes],
) -> None:
    partial_canary = b"header-eof-canary"
    fake = _install_docker_socket(
        helper,
        monkeypatch,
        [*fragments[:-1], fragments[-1] + partial_canary, b""],
    )
    monkeypatch.setattr(
        helper.json,
        "loads",
        lambda *_args, **_kwargs: pytest.fail("header EOF must not reach JSON parsing"),
    )
    with pytest.raises(helper.ProvisioningFailure) as error:
        helper._docker_request("/volumes")
    captured = capsys.readouterr()
    assert str(error.value) == helper.FAILURE_MARKER
    assert partial_canary.decode("ascii") not in repr(error.value)
    assert partial_canary.decode("ascii") not in captured.out
    assert partial_canary.decode("ascii") not in captured.err
    assert captured.out == ""
    assert captured.err == ""
    assert fake.timeout == helper.DOCKER_API_TIMEOUT_SECONDS


def test_docker_request_does_not_reflect_path_canary_on_framing_failure(
    helper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path_canary = "/canary-path-must-not-be-reflected"
    _install_docker_socket(helper, monkeypatch, [b"HTTP/1.1 200 OK\r\n", b""])
    with pytest.raises(helper.ProvisioningFailure) as error:
        helper._docker_request(path_canary)
    captured = capsys.readouterr()
    assert str(error.value) == helper.FAILURE_MARKER
    assert path_canary not in repr(error.value)
    assert path_canary not in captured.out
    assert path_canary not in captured.err
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize("operation", ("connect", "sendall", "recv"))
def test_docker_request_normalizes_socket_exception_detail_without_reflection(
    helper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    detail_canary = f"socket-{operation}-detail-canary"
    failure = OSError(123, detail_canary, f"/{detail_canary}")
    fake = _DockerResponseSocket([failure] if operation == "recv" else b"")
    if operation == "connect":

        def failing_connect(_: str) -> None:
            raise failure

        monkeypatch.setattr(fake, "connect", failing_connect)
    elif operation == "sendall":

        def failing_sendall(_: bytes) -> None:
            raise failure

        monkeypatch.setattr(fake, "sendall", failing_sendall)
    monkeypatch.setattr(helper.socket, "socket", lambda *_: fake)
    with pytest.raises(helper.ProvisioningFailure) as error:
        helper._docker_request("/volumes")
    captured = capsys.readouterr()
    assert str(error.value) == helper.FAILURE_MARKER
    assert detail_canary not in repr(error.value)
    assert detail_canary not in captured.out
    assert detail_canary not in captured.err
    assert captured.out == ""
    assert captured.err == ""


def _write(path: Path, value: bytes, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        assert os.write(descriptor, value) == len(value)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _elf_fixture() -> bytes:
    return b"\x7fELF\x02\x01\x01" + (b"\x00" * 9) + b"\x02\x00" + b">\x00" + b"fixture"


class _Fixture:
    def __init__(self, helper: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        self.helper = helper
        self.base = Path(tempfile.mkdtemp(prefix="provisioning-helper-", dir=ROOT))
        self.repo = self.base / "repository"
        self.repo.mkdir(mode=0o755)
        self.repo.chmod(0o755)
        self.secret_directory = self.repo / ".acceptance-postgres-secrets"
        self.secret_directory.mkdir(mode=0o700)
        self.secret_directory.chmod(0o700)
        _write(
            self.repo / ".env.acceptance-postgres",
            (
                b"ACCEPTANCE_POSTGRES_IMAGE_DIGEST="
                + helper.EXPECTED_IMAGE_DIGEST.encode("ascii")
                + b"\nACCEPTANCE_POSTGRES_HOST_PORT=25432\n"
            ),
            0o600,
        )
        _write(self.secret_directory / "postgres-user", b"acceptance_role_0123456789abcdef", 0o600)
        _write(self.secret_directory / "postgres-password", b"0" * 64, 0o600)
        _write(
            self.secret_directory / "postgres-database", b"acceptance_db_fedcba9876543210", 0o600
        )
        _write(self.repo / "compose.acceptance.provision.yaml", COMPOSE_PATH.read_bytes(), 0o600)
        system = self.base / "system" / "usr" / "lib" / "docker" / "cli-plugins"
        system.mkdir(parents=True, mode=0o700)
        for directory in (
            self.base / "system",
            self.base / "system" / "usr",
            self.base / "system" / "usr" / "lib",
            self.base / "system" / "usr" / "lib" / "docker",
            system,
        ):
            directory.chmod(0o700)
        self.executable = system / "docker-compose"
        _write(self.executable, _elf_fixture(), 0o555)
        self.executable.chmod(0o555)
        monkeypatch.setattr(helper, "REPOSITORY_ROOT", self.repo)
        monkeypatch.setattr(helper, "COMPOSE_EXECUTABLE", self.executable)
        monkeypatch.setattr(helper, "COMPOSE_SHA256", hashlib.sha256(_elf_fixture()).hexdigest())
        monkeypatch.setattr(helper, "EXPECTED_PRIVATE_UID", os.getuid())
        monkeypatch.setattr(helper, "EXPECTED_PRIVATE_GID", os.getgid())
        # The production contract is explicitly root-owned.  The Codex
        # sandbox presents host-owned system inodes as an unmapped UID, so the
        # fixture supplies the direct-WSL root view for these synthetic paths.
        system_owner = 0
        monkeypatch.setattr(helper, "ROOT_ANCHOR_OWNER_UID", system_owner)
        monkeypatch.setattr(helper, "SYSTEM_OWNER_UID", system_owner)
        monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
        # Exercise the real runtime-self routine against canonical test-owned
        # stand-ins. The fixture never replaces that production check.
        system_root = self.base / "system"
        canonical = system_root / "usr" / "local" / "libexec" / "helper"
        interpreter = system_root / "usr" / "bin" / "python3.14"
        canonical.parent.mkdir(parents=True, mode=0o700)
        interpreter.parent.mkdir(parents=True, mode=0o700)
        (system_root / "run").mkdir(mode=0o700)
        data_root = system_root / "var" / "lib" / "docker"
        data_root.mkdir(parents=True, mode=0o700)
        # The production boundary opens a Unix socket with O_PATH.  This
        # sandbox disallows AF_UNIX bind, so retain the production FD-relative
        # path flow while exposing a strict socket-shaped metadata view below.
        self.daemon_socket = system_root / "run" / "docker.sock"
        _write(self.daemon_socket, b"socket-fixture", 0o600)
        _write(canonical, b"canonical helper fixture", 0o755)
        _write(interpreter, b"canonical interpreter fixture", 0o755)
        canonical.chmod(0o755)
        interpreter.chmod(0o755)
        monkeypatch.setattr(helper, "CANONICAL_HELPER_PATH", canonical)
        monkeypatch.setattr(helper, "CANONICAL_INTERPRETER", interpreter)
        monkeypatch.setattr(
            helper,
            "RUNTIME_SECRET_DIRECTORY",
            system_root / "run" / "acceptance-postgres-secrets",
        )
        monkeypatch.setattr(helper, "DOCKER_SOCKET", self.daemon_socket)
        monkeypatch.setattr(helper, "DOCKER_DATA_ROOT", data_root)
        fake_docker_gid = 42424
        monkeypatch.setattr(
            helper.grp,
            "getgrnam",
            lambda _: SimpleNamespace(gr_name="docker", gr_gid=fake_docker_gid),
        )
        monkeypatch.setattr(
            helper.pwd,
            "getpwnam",
            lambda _: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()),
        )
        monkeypatch.setattr(helper.os, "getgrouplist", lambda *_: [os.getgid()])
        monkeypatch.setattr(helper, "__file__", str(canonical))
        monkeypatch.setattr(helper.sys, "executable", str(interpreter))
        monkeypatch.setattr(
            helper.os,
            "environ",
            {helper.WINDOWS_PORT_PRECHECK_ENVIRONMENT: helper.WINDOWS_PORT_PRECHECK_VALUE},
        )
        original_fstat = helper.os.fstat

        def fstat_with_system_view(descriptor: int) -> os.stat_result:
            metadata = original_fstat(descriptor)
            try:
                path = os.readlink(f"/proc/self/fd/{descriptor}")
            except OSError:
                return metadata
            system_prefix = str(system_root)
            if path != "/" and not (
                system_prefix == path
                or system_prefix.startswith(path.rstrip("/") + "/")
                or path.startswith(system_prefix.rstrip("/") + "/")
            ):
                return metadata
            if path == os.fspath(self.daemon_socket):
                return os.stat_result(
                    (
                        stat.S_IFSOCK | 0o660,
                        metadata.st_ino,
                        metadata.st_dev,
                        metadata.st_nlink,
                        system_owner,
                        fake_docker_gid,
                        metadata.st_size,
                        metadata.st_atime,
                        metadata.st_mtime,
                        metadata.st_ctime,
                    )
                )
            return os.stat_result(
                (
                    metadata.st_mode,
                    metadata.st_ino,
                    metadata.st_dev,
                    metadata.st_nlink,
                    system_owner,
                    system_owner,
                    metadata.st_size,
                    metadata.st_atime,
                    metadata.st_mtime,
                    metadata.st_ctime,
                )
            )

        monkeypatch.setattr(helper.os, "fstat", fstat_with_system_view)
        original_stat = helper.os.stat

        def stat_with_interpreter_view(
            path: object, *args: object, **kwargs: object
        ) -> os.stat_result:
            if os.fspath(path) == "/proc/self/exe":
                descriptor = os.open(interpreter, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    return helper.os.fstat(descriptor)
                finally:
                    os.close(descriptor)
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(helper.os, "stat", stat_with_interpreter_view)

    def close(self) -> None:
        shutil.rmtree(self.base)


@pytest.fixture
def fixture(helper: ModuleType, monkeypatch: pytest.MonkeyPatch) -> _Fixture:
    value = _Fixture(helper, monkeypatch)
    try:
        yield value
    finally:
        value.close()


def _invoke(helper: ModuleType, runner: object, **overrides: object) -> None:
    helper.provision_once(
        runner=runner,
        disk_ready=overrides.get("disk_ready", lambda _: True),
        port_free=overrides.get("port_free", lambda _: True),
        pristine=overrides.get("pristine", lambda: True),
        post_state=overrides.get("post_state", lambda _: True),
    )


@pytest.mark.parametrize(
    "ambient_name", ("DOCKER_HOST", "DOCKER_CONTEXT", "COMPOSE_FILE", "HTTPS_PROXY")
)
def test_valid_production_helper_path_calls_exact_no_pull_up_once(
    helper: ModuleType,
    fixture: _Fixture,
    monkeypatch: pytest.MonkeyPatch,
    ambient_name: str,
) -> None:
    monkeypatch.setenv(ambient_name, "ambient-canary")
    calls: list[tuple[tuple[str, ...], dict[str, str], tuple[int, ...]]] = []

    def runner(argv: object, environment: object, pass_fds: object) -> None:
        calls.append((tuple(argv), dict(environment), tuple(pass_fds)))

    # The launcher's own environment must not be inherited by a caller.  A
    # hostile Docker selector fails before file/daemon work and invokes no runner.
    with pytest.raises(helper.ProvisioningFailure):
        _invoke(helper, runner)
    assert calls == []
    monkeypatch.delenv(ambient_name)
    _invoke(helper, runner)
    assert len(calls) == 1
    argv, environment, fds = calls[0]
    assert argv == (
        f"/proc/self/fd/{fds[0]}",
        "--project-name",
        helper.EXPECTED_PROJECT,
        "--project-directory",
        f"/proc/self/fd/{fds[1]}",
        "--env-file",
        f"/proc/self/fd/{fds[2]}",
        "-f",
        f"/proc/self/fd/{fds[3]}",
        "up",
        "--detach",
        "--no-build",
        "--pull",
        "never",
        "--wait",
        "acceptance_postgres",
    )
    assert environment == {
        **helper.SANITIZED_ENV,
        "ACCEPTANCE_POSTGRES_RUNTIME_SECRET_DIRECTORY": str(helper.RUNTIME_SECRET_DIRECTORY),
    }
    assert len(set(fds)) == 4
    assert not {"pull", "build", "down", "rm", "delete", "prune"}.intersection(
        set(argv) - {"--pull", "never", "--no-build"}
    )
    assert "ambient-canary" not in repr((argv, environment, fds))


@pytest.mark.parametrize("failing_precondition", ("disk", "port", "state", "post"))
def test_any_runtime_precondition_failure_is_nonreflective_and_calls_no_runner(
    helper: ModuleType, fixture: _Fixture, failing_precondition: str
) -> None:
    calls = 0

    def runner(*_: object) -> None:
        nonlocal calls
        calls += 1

    values = {
        "disk_ready": lambda _: failing_precondition != "disk",
        "port_free": lambda _: failing_precondition != "port",
        "pristine": lambda: failing_precondition != "state",
        "post_state": lambda _: failing_precondition != "post",
    }
    with pytest.raises(helper.ProvisioningFailure) as captured:
        _invoke(helper, runner, **values)
    assert captured.value.args == (helper.FAILURE_MARKER,)
    assert calls == (1 if failing_precondition == "post" else 0)


@pytest.mark.parametrize(
    "mutation", ("extra_field", "duplicate_key", "secret_mode", "secret_symlink", "env_mode")
)
def test_contract_and_private_metadata_fail_before_runner(
    helper: ModuleType, fixture: _Fixture, mutation: str
) -> None:
    compose = fixture.repo / "compose.acceptance.provision.yaml"
    if mutation == "extra_field":
        document = json.loads(compose.read_text(encoding="utf-8"))
        document["services"]["acceptance_postgres"]["privileged"] = True
        compose.write_text(json.dumps(document), encoding="utf-8")
    elif mutation == "duplicate_key":
        compose.write_bytes(b'{"name":"x","name":"y","services":{},"volumes":{},"networks":{}}')
    elif mutation == "secret_mode":
        (fixture.secret_directory / "postgres-password").chmod(0o640)
    elif mutation == "secret_symlink":
        target = fixture.base / "target"
        _write(target, b"0" * 64, 0o600)
        (fixture.secret_directory / "postgres-password").unlink()
        os.symlink(target, fixture.secret_directory / "postgres-password")
    else:
        (fixture.repo / ".env.acceptance-postgres").chmod(0o644)
    calls = 0
    with pytest.raises(helper.ProvisioningFailure) as captured:
        _invoke(helper, lambda *_: (_ for _ in ()).throw(AssertionError("runner-called")))
    assert captured.value.args == (helper.FAILURE_MARKER,)
    assert calls == 0


def test_pre_and_post_rename_replacement_races_fail_closed_without_retry(
    helper: ModuleType, fixture: _Fixture
) -> None:
    compose = fixture.repo / "compose.acceptance.provision.yaml"
    replacement = fixture.base / "replacement"
    replacement.write_bytes(compose.read_bytes())
    replacement.chmod(0o600)
    calls = 0

    def pre_port(_: int) -> bool:
        compose.rename(fixture.base / "old-compose")
        replacement.rename(compose)
        return True

    with pytest.raises(helper.ProvisioningFailure):
        _invoke(helper, lambda *_: None, port_free=pre_port)
    assert calls == 0

    # Recreate a valid fixture for a post-call replacement that must discard a
    # successful runner result without launching a second command.
    fresh_patch = pytest.MonkeyPatch()
    fresh = _Fixture(helper, fresh_patch)
    try:
        compose = fresh.repo / "compose.acceptance.provision.yaml"
        replacement = fresh.base / "replacement"
        replacement.write_bytes(compose.read_bytes())
        replacement.chmod(0o600)

        def runner(*_: object) -> None:
            nonlocal calls
            calls += 1
            compose.rename(fresh.base / "old-compose")
            replacement.rename(compose)

        with pytest.raises(helper.ProvisioningFailure) as captured:
            _invoke(helper, runner, post_state=lambda _: False)
        assert captured.value.args == (helper.FAILURE_MARKER,)
        assert calls == 1
    finally:
        fresh.close()
        fresh_patch.undo()


def test_secret_directory_symlink_swap_before_runner_and_secret_file_swap_after_runner_fail_closed(
    helper: ModuleType, fixture: _Fixture
) -> None:
    calls = 0
    original_directory = fixture.secret_directory
    replacement_directory = fixture.base / "replacement-secrets"
    replacement_directory.mkdir(mode=0o700)
    for name, value in (
        ("postgres-user", b"acceptance_role_1111111111111111"),
        ("postgres-password", b"1" * 64),
        ("postgres-database", b"acceptance_db_2222222222222222"),
    ):
        _write(replacement_directory / name, value, 0o600)

    def pre_port(_: int) -> bool:
        original_directory.rename(fixture.base / "old-secrets")
        os.symlink(replacement_directory, original_directory)
        return True

    with pytest.raises(helper.ProvisioningFailure):
        _invoke(helper, lambda *_: None, port_free=pre_port)
    assert calls == 0

    fresh_patch = pytest.MonkeyPatch()
    fresh = _Fixture(helper, fresh_patch)
    try:
        target = fresh.secret_directory / "postgres-password"
        replacement = fresh.base / "replacement-password"
        _write(replacement, b"2" * 64, 0o600)

        def runner(*_: object) -> None:
            nonlocal calls
            calls += 1
            target.rename(fresh.base / "old-password")
            replacement.rename(target)

        with pytest.raises(helper.ProvisioningFailure) as captured:
            _invoke(helper, runner, post_state=lambda _: False)
        assert captured.value.args == (helper.FAILURE_MARKER,)
        assert calls == 1
    finally:
        fresh.close()
        fresh_patch.undo()


@pytest.mark.parametrize("primary", (asyncio.CancelledError(), KeyboardInterrupt(), SystemExit(7)))
def test_special_primary_identity_survives_cleanup_failure(
    helper: ModuleType, fixture: _Fixture, monkeypatch: pytest.MonkeyPatch, primary: BaseException
) -> None:
    original_close = helper._close_all
    runner_started = False

    def close_with_failure(descriptors: object) -> BaseException | None:
        original_close(descriptors)
        # The runner raises immediately, so after this flag is set the only
        # production close is the outer cleanup. All revalidation cleanup
        # before the special primary remains active.
        return RuntimeError("close-canary") if runner_started else None

    monkeypatch.setattr(helper, "_close_all", close_with_failure)

    def runner(*_: object) -> None:
        nonlocal runner_started
        runner_started = True
        raise primary

    with pytest.raises(type(primary)) as captured:
        _invoke(helper, runner)
    assert captured.value is primary
    assert "close-canary" not in repr(captured.value)


def test_ordinary_runner_failure_is_fixed_and_nonreflective(
    helper: ModuleType, fixture: _Fixture
) -> None:
    def runner(*_: object) -> None:
        raise RuntimeError("docker-secret-canary")

    with pytest.raises(helper.ProvisioningFailure) as captured:
        _invoke(helper, runner)
    assert captured.value.args == (helper.FAILURE_MARKER,)
    assert "docker-secret-canary" not in repr(captured.value)


@pytest.mark.parametrize(
    ("image", "containers", "networks", "volumes"),
    (
        ({"RepoDigests": []}, [], [], {"Volumes": []}),
        (
            {
                "RepoDigests": [
                    "postgres@"
                    + "sha256:882236b897e39051d2368c5ccc6cda944904723506b2dfc97f2a8f5bc9afa382"
                ]
            },
            [{"Names": ["/discord-ai-reminder-bot-acceptance-postgres"]}],
            [],
            {"Volumes": []},
        ),
        (
            {
                "RepoDigests": [
                    "postgres@"
                    + "sha256:882236b897e39051d2368c5ccc6cda944904723506b2dfc97f2a8f5bc9afa382"
                ]
            },
            [],
            [{"Name": "discord-ai-reminder-bot-acceptance-postgres-network"}],
            {"Volumes": []},
        ),
        (
            {
                "RepoDigests": [
                    "postgres@"
                    + "sha256:882236b897e39051d2368c5ccc6cda944904723506b2dfc97f2a8f5bc9afa382"
                ]
            },
            [],
            [],
            {"Volumes": [{"Name": "discord-ai-reminder-bot-acceptance-postgres-data"}]},
        ),
    ),
)
def test_production_state_probe_rejects_absent_image_or_any_partial_target_state(
    helper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    image: object,
    containers: object,
    networks: object,
    volumes: object,
) -> None:
    responses = iter((image, containers, networks, volumes))
    monkeypatch.setattr(helper, "_docker_request", lambda _: next(responses))
    assert helper._state_is_pristine() is False


def test_production_state_probe_accepts_only_exact_local_digest_and_absent_targets(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = iter(
        (
            {
                "RepoDigests": [helper.EXPECTED_IMAGE_REFERENCE],
                "Os": "linux",
                "Architecture": "amd64",
                "Id": "sha256:" + "0" * 64,
            },
            [],
            [],
            {"Volumes": []},
        )
    )
    monkeypatch.setattr(helper, "_docker_request", lambda _: next(responses))
    assert helper._state_is_pristine() is True


def test_proc_port_parser_rejects_tcp6_listener_and_malformed_input(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = (
        "sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"
    )
    tcp6_listener = (
        good
        + "0: 00000000000000000000000000000000:6358 00000000000000000000000000000000:0000 0A 0 0 0 0 0 0 0 0\n"
    )

    def reader(path: Path, *, encoding: str) -> str:
        return tcp6_listener if path.name == "tcp6" else good

    monkeypatch.setattr(Path, "read_text", reader)
    assert helper._proc_port_is_free(helper.EXPECTED_PORT) is False
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: "bad")
    assert helper._proc_port_is_free(helper.EXPECTED_PORT) is False


@pytest.mark.parametrize(
    "field,value", (("Os", "windows"), ("Architecture", "arm64"), ("Id", True))
)
def test_image_identity_rejects_platform_and_strict_type_mismatch(
    helper: ModuleType, field: str, value: object
) -> None:
    image: dict[str, object] = {
        "RepoDigests": [helper.EXPECTED_IMAGE_REFERENCE],
        "Os": "linux",
        "Architecture": "amd64",
        "Id": "sha256:" + "0" * 64,
    }
    image[field] = value
    assert helper._valid_image(image) is False


def test_state_probe_rejects_volume_project_label_mismatch_and_malformed_item(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = {
        "RepoDigests": [helper.EXPECTED_IMAGE_REFERENCE],
        "Os": "linux",
        "Architecture": "amd64",
        "Id": "sha256:" + "0" * 64,
    }
    responses = iter((image, [], [], {"Volumes": [{"Name": helper.EXPECTED_VOLUME, "Labels": {}}]}))
    monkeypatch.setattr(helper, "_docker_request", lambda _: next(responses))
    assert helper._state_is_pristine() is False
    responses = iter((image, ["malformed"], [], {"Volumes": []}))
    monkeypatch.setattr(helper, "_docker_request", lambda _: next(responses))
    assert helper._state_is_pristine() is False


def test_state_is_checked_twice_and_post_failure_discards_single_runner_success(
    helper: ModuleType, fixture: _Fixture
) -> None:
    state_checks = 0
    calls = 0

    def pristine() -> bool:
        nonlocal state_checks
        state_checks += 1
        return True

    def runner(*_: object) -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(helper.ProvisioningFailure):
        _invoke(helper, runner, pristine=pristine, post_state=lambda _: False)
    assert state_checks == 2
    assert calls == 1


@pytest.mark.parametrize("changed_after_snapshot", ("state", "port"))
def test_snapshot_post_creation_recheck_rejects_race_without_runner(
    helper: ModuleType, fixture: _Fixture, changed_after_snapshot: str
) -> None:
    calls = 0
    state_checks = 0
    port_checks = 0

    def pristine() -> bool:
        nonlocal state_checks
        state_checks += 1
        return not (changed_after_snapshot == "state" and state_checks == 2)

    def port_free(_: int) -> bool:
        nonlocal port_checks
        port_checks += 1
        return not (changed_after_snapshot == "port" and port_checks == 2)

    def runner(*_: object) -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(helper.ProvisioningFailure) as captured:
        _invoke(helper, runner, pristine=pristine, port_free=port_free)
    assert captured.value.args == (helper.FAILURE_MARKER,)
    assert calls == 0
    assert helper.RUNTIME_SECRET_DIRECTORY.is_dir()


@pytest.mark.parametrize(
    ("target", "mutation"),
    (
        ("directory", "replacement"),
        ("postgres-user", "replacement"),
        ("postgres-password", "replacement"),
        ("postgres-database", "replacement"),
        ("postgres-password", "symlink"),
        ("postgres-user", "mode"),
        ("postgres-database", "nlink"),
        ("postgres-password", "size"),
        ("postgres-user", "owner"),
    ),
)
def test_final_runtime_snapshot_revalidation_rejects_all_target_swaps_and_metadata_changes(
    helper: ModuleType,
    fixture: _Fixture,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    mutation: str,
) -> None:
    calls = 0
    port_checks = 0
    active = False
    canary = b"runtime-snapshot-canary"
    original_fstat = helper.os.fstat

    def owner_changed_fstat(descriptor: int) -> os.stat_result:
        metadata = original_fstat(descriptor)
        if not active or mutation != "owner":
            return metadata
        try:
            is_target = os.readlink(f"/proc/self/fd/{descriptor}") == os.fspath(
                helper.RUNTIME_SECRET_DIRECTORY / target
            )
        except OSError:
            is_target = False
        if not is_target:
            return metadata
        return os.stat_result(
            (
                metadata.st_mode,
                metadata.st_ino,
                metadata.st_dev,
                metadata.st_nlink,
                metadata.st_uid + 1,
                metadata.st_gid,
                metadata.st_size,
                metadata.st_atime,
                metadata.st_mtime,
                metadata.st_ctime,
            )
        )

    monkeypatch.setattr(helper.os, "fstat", owner_changed_fstat)

    def mutate_snapshot() -> None:
        nonlocal active
        snapshot = helper.RUNTIME_SECRET_DIRECTORY
        if target == "directory":
            replacement = fixture.base / "replacement-runtime-snapshot"
            shutil.copytree(snapshot, replacement)
            snapshot.rename(fixture.base / "old-runtime-snapshot")
            replacement.rename(snapshot)
        else:
            current = snapshot / target
            if mutation == "replacement":
                replacement = fixture.base / f"replacement-{target}"
                _write(replacement, canary, 0o400)
                current.rename(fixture.base / f"old-{target}")
                replacement.rename(current)
            elif mutation == "symlink":
                replacement = fixture.base / f"symlink-target-{target}"
                _write(replacement, canary, 0o400)
                current.rename(fixture.base / f"old-{target}")
                os.symlink(replacement, current)
            elif mutation == "mode":
                current.chmod(0o440)
            elif mutation == "nlink":
                os.link(current, snapshot / "unexpected-hardlink")
            elif mutation == "size":
                descriptor = os.open(current, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
                try:
                    os.write(descriptor, b"x")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            else:
                active = True

    def port_free(_: int) -> bool:
        nonlocal port_checks
        port_checks += 1
        if port_checks == 2:
            mutate_snapshot()
        return True

    def runner(*_: object) -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(helper.ProvisioningFailure) as captured:
        _invoke(helper, runner, port_free=port_free)
    assert captured.value.args == (helper.FAILURE_MARKER,)
    assert calls == 0
    assert canary.decode("ascii") not in repr(captured.value)


def test_production_invocation_builder_fds_are_visible_to_real_offline_child(
    helper: ModuleType, fixture: _Fixture
) -> None:
    held = helper._hold_inputs()
    try:
        argv, environment, descriptors = helper._compose_contract(held)
        assert environment == {
            **helper.SANITIZED_ENV,
            "ACCEPTANCE_POSTGRES_RUNTIME_SECRET_DIRECTORY": str(helper.RUNTIME_SECRET_DIRECTORY),
        }
        expected_paths = tuple(
            value.removeprefix("/proc/self/fd/") for value in (argv[0], argv[4], argv[6], argv[8])
        )
        assert expected_paths == tuple(str(value) for value in descriptors)
        child = (
            "import json,os,stat,sys; "
            "argv=json.loads(sys.argv[1]); expected=json.loads(sys.argv[2]); "
            "assert dict(os.environ)==expected; "
            "paths=[value for value in argv if value.startswith('/proc/self/fd/')]; "
            "assert len(paths)==4 and len(set(paths))==4; "
            "[(lambda number,path: (lambda before,opened: ("
            "assert_identity(before,os.fstat(opened)), "
            "os.listdir(opened) if stat.S_ISDIR(before.st_mode) else os.read(opened,1), "
            "os.close(opened)))(os.fstat(number),os.open(path,os.O_RDONLY|os.O_CLOEXEC)))("
            "int(path.rsplit('/',1)[1]),path) for path in paths]; "
            "print('PRODUCTION_FD_CHILD_OK')"
        )
        child = (
            "def assert_identity(a,b):\n assert (a.st_dev,a.st_ino,stat.S_IFMT(a.st_mode))==(b.st_dev,b.st_ino,stat.S_IFMT(b.st_mode))\n"
            + child
        )
        command = (
            os.fspath(TEST_INTERPRETER),
            "-c",
            child,
            json.dumps(argv),
            json.dumps(environment, sort_keys=True),
        )
        completed = subprocess.run(
            command,
            env=environment,
            pass_fds=descriptors,
            timeout=5,
            check=False,
            text=True,
            capture_output=True,
        )
        assert completed.returncode == 0
        assert completed.stdout == "PRODUCTION_FD_CHILD_OK\n"
        assert completed.stderr == ""
        for missing in range(len(descriptors)):
            passed = tuple(fd for index, fd in enumerate(descriptors) if index != missing)
            result = subprocess.run(
                command,
                env=environment,
                pass_fds=passed,
                timeout=5,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            assert result.returncode != 0
    finally:
        helper._close_all(tuple(reversed(held.descriptors)))


def test_helper_interpreter_and_repository_ancestor_replacements_discard_success(
    helper: ModuleType, fixture: _Fixture
) -> None:
    calls = 0
    replacement = fixture.base / "replacement-interpreter"
    _write(replacement, b"replacement", 0o755)

    def runner(*_: object) -> None:
        nonlocal calls
        calls += 1
        Path(helper.CANONICAL_INTERPRETER).rename(fixture.base / "old-interpreter")
        replacement.rename(helper.CANONICAL_INTERPRETER)

    with pytest.raises(helper.ProvisioningFailure) as captured:
        _invoke(helper, runner)
    assert captured.value.args == (helper.FAILURE_MARKER,)
    assert calls == 1

    fresh_patch = pytest.MonkeyPatch()
    fresh = _Fixture(helper, fresh_patch)
    old_base = fresh.base.with_name(f"{fresh.base.name}-old")
    replacement_base = fresh.base.with_name(f"{fresh.base.name}-replacement")
    try:

        def ancestor_runner(*_: object) -> None:
            nonlocal calls
            calls += 1
            fresh.base.rename(old_base)
            replacement_base.mkdir(mode=0o755)
            replacement_base.rename(fresh.base)

        with pytest.raises(helper.ProvisioningFailure) as captured:
            _invoke(helper, ancestor_runner)
        assert captured.value.args == (helper.FAILURE_MARKER,)
        assert calls == 2
    finally:
        shutil.rmtree(old_base, ignore_errors=True)
        fresh.close()
        fresh_patch.undo()


def test_helper_symlink_swap_before_runner_and_socket_contract_fail_closed(
    helper: ModuleType, fixture: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    target = fixture.base / "helper-target"
    _write(target, b"helper target", 0o755)

    def port_swap(_: int) -> bool:
        Path(helper.CANONICAL_HELPER_PATH).rename(fixture.base / "old-helper")
        os.symlink(target, helper.CANONICAL_HELPER_PATH)
        return True

    with pytest.raises(helper.ProvisioningFailure) as captured:
        _invoke(
            helper, lambda *_: (_ for _ in ()).throw(AssertionError("runner")), port_free=port_swap
        )
    assert captured.value.args == (helper.FAILURE_MARKER,)
    assert calls == 0

    fresh_patch = pytest.MonkeyPatch()
    fresh = _Fixture(helper, fresh_patch)
    try:
        original_fstat = helper.os.fstat

        def wrong_socket_gid(descriptor: int) -> os.stat_result:
            metadata = original_fstat(descriptor)
            try:
                is_socket = os.readlink(f"/proc/self/fd/{descriptor}") == os.fspath(
                    fresh.daemon_socket
                )
            except OSError:
                is_socket = False
            if not is_socket:
                return metadata
            return os.stat_result(
                (
                    metadata.st_mode,
                    metadata.st_ino,
                    metadata.st_dev,
                    metadata.st_nlink,
                    metadata.st_uid,
                    metadata.st_gid + 1,
                    metadata.st_size,
                    metadata.st_atime,
                    metadata.st_mtime,
                    metadata.st_ctime,
                )
            )

        monkeypatch.setattr(helper.os, "fstat", wrong_socket_gid)
        with pytest.raises(helper.ProvisioningFailure) as captured:
            _invoke(helper, lambda *_: (_ for _ in ()).throw(AssertionError("runner")))
        assert captured.value.args == (helper.FAILURE_MARKER,)
    finally:
        fresh.close()
        fresh_patch.undo()


def test_docker_group_lookup_and_membership_are_required_before_runner(
    helper: ModuleType, fixture: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    monkeypatch.setattr(
        helper.grp,
        "getgrnam",
        lambda _: SimpleNamespace(gr_name="not-docker", gr_gid=42424),
    )
    with pytest.raises(helper.ProvisioningFailure):
        _invoke(helper, lambda *_: (_ for _ in ()).throw(AssertionError("runner")))
    assert calls == 0

    monkeypatch.setattr(
        helper.grp,
        "getgrnam",
        lambda _: SimpleNamespace(gr_name="docker", gr_gid=42424),
    )
    monkeypatch.setattr(helper.os, "getgrouplist", lambda *_: [os.getgid(), 42424])
    with pytest.raises(helper.ProvisioningFailure):
        _invoke(helper, lambda *_: (_ for _ in ()).throw(AssertionError("runner")))
    assert calls == 0


@pytest.mark.parametrize("mutation", ("wrong_gid", "wrong_mode", "wrong_type"))
def test_strict_docker_socket_metadata_rejects_before_runner(
    helper: ModuleType, fixture: _Fixture, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    original_fstat = helper.os.fstat
    calls = 0

    def altered_socket_metadata(descriptor: int) -> os.stat_result:
        metadata = original_fstat(descriptor)
        try:
            is_socket = os.readlink(f"/proc/self/fd/{descriptor}") == os.fspath(
                fixture.daemon_socket
            )
        except OSError:
            is_socket = False
        if not is_socket:
            return metadata
        mode = metadata.st_mode
        gid = metadata.st_gid
        if mutation == "wrong_gid":
            gid += 1
        elif mutation == "wrong_mode":
            mode = stat.S_IFSOCK | 0o640
        else:
            mode = stat.S_IFREG | 0o660
        return os.stat_result(
            (
                mode,
                metadata.st_ino,
                metadata.st_dev,
                metadata.st_nlink,
                metadata.st_uid,
                gid,
                metadata.st_size,
                metadata.st_atime,
                metadata.st_mtime,
                metadata.st_ctime,
            )
        )

    monkeypatch.setattr(helper.os, "fstat", altered_socket_metadata)

    def runner(*_: object) -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(helper.ProvisioningFailure) as captured:
        _invoke(helper, runner)
    assert captured.value.args == (helper.FAILURE_MARKER,)
    assert calls == 0


@pytest.mark.parametrize(
    ("path_attribute", "phase", "replacement_kind"),
    (
        ("CANONICAL_HELPER_PATH", "pre", "rename"),
        ("CANONICAL_HELPER_PATH", "post", "rename"),
        ("CANONICAL_INTERPRETER", "pre", "rename"),
        ("CANONICAL_INTERPRETER", "post", "symlink"),
    ),
)
def test_runtime_self_path_swaps_fail_closed_at_each_boundary(
    helper: ModuleType,
    fixture: _Fixture,
    path_attribute: str,
    phase: str,
    replacement_kind: str,
) -> None:
    target = Path(getattr(helper, path_attribute))
    replacement = fixture.base / f"replacement-{target.name}"
    _write(replacement, b"canonical-replacement", 0o755)
    calls = 0

    def swap() -> None:
        target.rename(fixture.base / f"old-{target.name}")
        if replacement_kind == "symlink":
            os.symlink(replacement, target)
        else:
            replacement.rename(target)

    def runner(*_: object) -> None:
        nonlocal calls
        calls += 1
        if phase == "post":
            swap()

    def port_free(_: int) -> bool:
        if phase == "pre":
            swap()
        return True

    with pytest.raises(helper.ProvisioningFailure) as captured:
        _invoke(helper, runner, port_free=port_free)
    assert captured.value.args == (helper.FAILURE_MARKER,)
    assert calls == (0 if phase == "pre" else 1)


def test_post_up_state_requires_exact_labels_mount_port_and_healthy_status(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = {
        "RepoDigests": [helper.EXPECTED_IMAGE_REFERENCE],
        "Os": "linux",
        "Architecture": "amd64",
        "Id": "sha256:" + "0" * 64,
    }
    container = {
        "Name": f"/{helper.EXPECTED_CONTAINER}",
        "Config": {
            "Image": helper.EXPECTED_IMAGE_REFERENCE,
            "Labels": {
                "com.docker.compose.project": helper.EXPECTED_PROJECT,
                "com.docker.compose.service": "acceptance_postgres",
            },
        },
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "NetworkSettings": {
            "Networks": {helper.EXPECTED_NETWORK: {}},
            "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "25432"}]},
        },
        "Mounts": [
            {
                "Type": "volume",
                "Source": helper.EXPECTED_VOLUME,
                "Destination": "/var/lib/postgresql/data",
                "RW": True,
            },
            {
                "Type": "bind",
                "Source": str(helper.RUNTIME_SECRET_DIRECTORY),
                "Destination": "/run/acceptance-postgres-secrets",
                "RW": False,
            },
        ],
    }
    network = {
        "Name": helper.EXPECTED_NETWORK,
        "Labels": {
            "com.docker.compose.project": helper.EXPECTED_PROJECT,
            "com.docker.compose.network": "acceptance_postgres_network",
        },
    }
    volume = {
        "Name": helper.EXPECTED_VOLUME,
        "Labels": {
            "com.docker.compose.project": helper.EXPECTED_PROJECT,
            "com.docker.compose.volume": "acceptance_postgres_data",
        },
    }
    global_container = {
        "Names": [f"/{helper.EXPECTED_CONTAINER}"],
        "Labels": {
            "com.docker.compose.project": helper.EXPECTED_PROJECT,
            "com.docker.compose.service": "acceptance_postgres",
        },
    }
    global_network = {
        "Name": helper.EXPECTED_NETWORK,
        "Labels": {
            "com.docker.compose.project": helper.EXPECTED_PROJECT,
            "com.docker.compose.network": "acceptance_postgres_network",
        },
    }
    global_volumes = {
        "Volumes": [
            {
                "Name": helper.EXPECTED_VOLUME,
                "Labels": {
                    "com.docker.compose.project": helper.EXPECTED_PROJECT,
                    "com.docker.compose.volume": "acceptance_postgres_data",
                },
            }
        ]
    }
    responses = iter(
        (image, container, network, volume, [global_container], [global_network], global_volumes)
    )
    monkeypatch.setattr(helper, "_docker_request", lambda _: next(responses))
    assert helper._post_state_is_exact(helper.RUNTIME_SECRET_DIRECTORY) is True
    container["State"] = {"Health": {"Status": "starting"}}
    responses = iter(
        (image, container, network, volume, [global_container], [global_network], global_volumes)
    )
    monkeypatch.setattr(helper, "_docker_request", lambda _: next(responses))
    assert helper._post_state_is_exact(helper.RUNTIME_SECRET_DIRECTORY) is False


@pytest.mark.parametrize(
    "mutation", ("extra_project", "duplicate", "unlabeled_conflict", "malformed")
)
def test_post_global_state_rejects_every_extra_or_conflicting_resource(
    helper: ModuleType, mutation: str
) -> None:
    container = {
        "Names": [f"/{helper.EXPECTED_CONTAINER}"],
        "Labels": {
            "com.docker.compose.project": helper.EXPECTED_PROJECT,
            "com.docker.compose.service": "acceptance_postgres",
        },
    }
    network = {
        "Name": helper.EXPECTED_NETWORK,
        "Labels": {
            "com.docker.compose.project": helper.EXPECTED_PROJECT,
            "com.docker.compose.network": "acceptance_postgres_network",
        },
    }
    volume = {
        "Name": helper.EXPECTED_VOLUME,
        "Labels": {
            "com.docker.compose.project": helper.EXPECTED_PROJECT,
            "com.docker.compose.volume": "acceptance_postgres_data",
        },
    }
    containers: object = [container]
    networks: object = [network]
    volumes: object = {"Volumes": [volume]}
    if mutation == "extra_project":
        containers = [container, {**container, "Names": ["/unexpected"]}]
    elif mutation == "duplicate":
        networks = [network, dict(network)]
    elif mutation == "unlabeled_conflict":
        volumes = {"Volumes": [volume, {"Name": helper.EXPECTED_VOLUME, "Labels": {}}]}
    else:
        containers = [container, "not-an-item"]
    assert helper._post_global_state_is_exact(containers, networks, volumes) is False


@pytest.mark.parametrize(
    "ports",
    (
        {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "25432"}]},
        {"5432/tcp": [{"HostIp": "::", "HostPort": "25432"}]},
        {
            "5432/tcp": [
                {"HostIp": "127.0.0.1", "HostPort": "25432"},
                {"HostIp": "127.0.0.1", "HostPort": "25432"},
            ]
        },
        {
            "5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "25432"}],
            "5433/tcp": [{"HostIp": "127.0.0.1", "HostPort": "25433"}],
        },
    ),
)
def test_post_state_rejects_non_exact_port_publication(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, ports: object
) -> None:
    image = {
        "RepoDigests": [helper.EXPECTED_IMAGE_REFERENCE],
        "Os": "linux",
        "Architecture": "amd64",
        "Id": "sha256:" + "0" * 64,
    }
    container = {
        "Name": f"/{helper.EXPECTED_CONTAINER}",
        "Config": {
            "Image": helper.EXPECTED_IMAGE_REFERENCE,
            "Labels": {
                "com.docker.compose.project": helper.EXPECTED_PROJECT,
                "com.docker.compose.service": "acceptance_postgres",
            },
        },
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "NetworkSettings": {"Networks": {helper.EXPECTED_NETWORK: {}}, "Ports": ports},
        "Mounts": [
            {
                "Type": "volume",
                "Source": helper.EXPECTED_VOLUME,
                "Destination": "/var/lib/postgresql/data",
                "RW": True,
            },
            {
                "Type": "bind",
                "Source": str(helper.RUNTIME_SECRET_DIRECTORY),
                "Destination": "/run/acceptance-postgres-secrets",
                "RW": False,
            },
        ],
    }
    network = {
        "Name": helper.EXPECTED_NETWORK,
        "Labels": {
            "com.docker.compose.project": helper.EXPECTED_PROJECT,
            "com.docker.compose.network": "acceptance_postgres_network",
        },
    }
    volume = {
        "Name": helper.EXPECTED_VOLUME,
        "Labels": {
            "com.docker.compose.project": helper.EXPECTED_PROJECT,
            "com.docker.compose.volume": "acceptance_postgres_data",
        },
    }
    global_container = {
        "Names": [f"/{helper.EXPECTED_CONTAINER}"],
        "Labels": container["Config"]["Labels"],
    }
    global_network = {"Name": helper.EXPECTED_NETWORK, "Labels": network["Labels"]}
    global_volume = {"Name": helper.EXPECTED_VOLUME, "Labels": volume["Labels"]}
    responses = iter(
        (
            image,
            container,
            network,
            volume,
            [global_container],
            [global_network],
            {"Volumes": [global_volume]},
        )
    )
    monkeypatch.setattr(helper, "_docker_request", lambda _: next(responses))
    assert helper._post_state_is_exact(helper.RUNTIME_SECRET_DIRECTORY) is False


def test_windows_port_attestation_missing_or_invalid_fails_before_runner(
    helper: ModuleType, fixture: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    for value in (None, "wrong", "USER_ATTESTED_25433"):
        if value is None:
            monkeypatch.delenv(helper.WINDOWS_PORT_PRECHECK_ENVIRONMENT, raising=False)
        else:
            monkeypatch.setenv(helper.WINDOWS_PORT_PRECHECK_ENVIRONMENT, value)
        calls = 0
        with pytest.raises(helper.ProvisioningFailure):
            _invoke(helper, lambda *_: (_ for _ in ()).throw(AssertionError("runner")))
        assert calls == 0


def test_real_offline_child_sees_each_required_fd_and_missing_fd_is_closed() -> None:
    descriptors: list[int] = []
    directory = Path(tempfile.mkdtemp(prefix="provisioning-child-fd-", dir=ROOT))
    try:
        for name in ("compose", "project", "env", "file"):
            path = directory / name
            if name == "project":
                path.mkdir()
                descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            else:
                _write(path, b"fixture", 0o600)
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            descriptors.append(descriptor)
        code = "import os,sys; [os.fstat(int(value)) for value in sys.argv[1:]]"
        argv = (sys.executable, "-c", code, *(str(fd) for fd in descriptors))
        assert (
            subprocess.run(argv, pass_fds=tuple(descriptors), timeout=5, check=False).returncode
            == 0
        )
        result = subprocess.run(
            argv,
            pass_fds=tuple(descriptors[:-1]),
            timeout=5,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert result.returncode != 0
    finally:
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass
        shutil.rmtree(directory)


def test_helper_has_no_systemctl_execution_and_documents_residual_daemon_boundary(
    helper: ModuleType,
) -> None:
    source = HELPER_PATH.read_text(encoding="utf-8")
    operations = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    assert "systemctl" not in source
    assert "root-only daemon" in operations
    assert "check-to-create/check-to-bind race" in operations


def test_runtime_refuses_repository_source_or_wrong_interpreter_without_reflection(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(helper, "CANONICAL_HELPER_PATH", Path("/root/other-helper"))
    with pytest.raises(helper.ProvisioningFailure) as captured:
        helper._hold_runtime_self()
    assert captured.value.args == (helper.FAILURE_MARKER,)


def test_strict_identity_rejects_bool_owner_nlink_and_size_values(helper: ModuleType) -> None:
    valid = os.stat_result((stat.S_IFREG | 0o600, 1, 1, 1, 1000, 1000, 1, 0, 0, 0))
    assert helper._strict_metadata(valid)
    invalid = SimpleNamespace(
        st_dev=True,
        st_ino=1,
        st_mode=stat.S_IFREG | 0o600,
        st_uid=1000,
        st_gid=1000,
        st_nlink=1,
        st_size=1,
    )
    assert not helper._strict_metadata(invalid)
    for metadata in (
        os.stat_result((stat.S_IFREG | 0o600, 1, 1, 2, 1000, 1000, 1, 0, 0, 0)),
        os.stat_result((stat.S_IFREG | 0o600, 1, 1, 1, 99, 1000, 1, 0, 0, 0)),
        os.stat_result((stat.S_IFREG | 0o600, 1, 1, 1, 1000, 1000, 0, 0, 0, 0)),
    ):
        with pytest.raises(helper.ProvisioningFailure):
            helper._require_regular(metadata, owner=1000, mode=0o600, nonempty=True)


def test_main_rejects_arguments_without_running(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(helper.sys, "argv", ["helper", "unsafe"])
    monkeypatch.setattr(
        helper, "provision_once", lambda: (_ for _ in ()).throw(AssertionError("called"))
    )
    assert helper.main() == helper.FAILURE_EXIT_CODE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == helper.FAILURE_MARKER + "\n"
