"""Import containment policy for the isolated synthetic measurement child.

This is test support only.  The child loads this exact file from its verified
execution snapshot, and focused tests exercise these same validators directly.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

_CHILD_MODULE = "discord_ai_reminder_bot.infrastructure.shutdown_measurement_harness"
_REQUIRED_CONTRACT_KEYS = frozenset(
    {
        "abi",
        "base_prefix",
        "dynamic_load_root",
        "interpreter_identity",
        "interpreter_uid",
        "implementation",
        "prefix",
        "python_version",
        "stdlib_root",
    }
)
_FILE_FINDER_HOOK: object | None = None
_SYNTHETIC_IMPORT_GUARD_TYPE: type[object] | None = None
_TEST_PROBES = frozenset(
    {
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
    }
)
_PROBE_REJECT_EXIT_CODE = 86


class SnapshotBootstrapPolicyError(RuntimeError):
    """Fixed failure used for every rejected bootstrap import state."""

    def __init__(self) -> None:
        super().__init__("snapshot import blocked")


@dataclass(frozen=True, slots=True)
class _PolicyContext:
    snapshot_root: Path
    source_root: Path
    stdlib_root: Path
    dynamic_load_root: Path
    policy_path: Path


class _SnapshotAuditGuard:
    """Test-support-only guard for capability probes in the snapshot child."""

    _BLOCKED_EVENTS = frozenset(
        {
            "socket.bind",
            "socket.connect",
            "socket.connect_ex",
            "socket.sendmsg",
            "socket.sendto",
            "socket.getaddrinfo",
            "socket.getnameinfo",
            "socket.gethostbyaddr",
            "socket.gethostbyname",
            "socket.gethostbyname_ex",
            "subprocess.Popen",
            "os.system",
            "os.posix_spawn",
        }
    )

    def __call__(self, event: str, args: tuple[object, ...]) -> None:
        del args
        if event in self._BLOCKED_EVENTS:
            print("SNAPSHOT_AUDIT_GUARD_BLOCKED", flush=True)
            raise RuntimeError("snapshot capability blocked")


class _SnapshotImportGuard:
    """Reject optional network-client imports before snapshot path resolution."""

    _BLOCKED_MODULES = frozenset({"http.client", "urllib.request", "requests", "httpx", "openai"})

    @classmethod
    def _blocked(cls, fullname: object) -> bool:
        return type(fullname) is str and any(
            fullname == item or fullname.startswith(f"{item}.") for item in cls._BLOCKED_MODULES
        )

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        del path, target
        if self._blocked(fullname):
            print("SNAPSHOT_IMPORT_GUARD_BLOCKED", flush=True)
            raise RuntimeError("snapshot import blocked")


def _from_http_client_probe() -> None:
    from http import client

    del client


def _alias_http_client_probe() -> None:
    import http.client as alias

    del alias


def _reject() -> NoReturn:
    raise SnapshotBootstrapPolicyError


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _forbidden_component(path: Path) -> bool:
    return any(
        part.lower() in {"site-packages", "dist-packages"} or part.lower().endswith(".zip")
        for part in path.parts
    )


def _absolute_file_origin(value: object) -> Path:
    if type(value) is not str or not value or not os.path.isabs(value):
        _reject()
    try:
        return Path(value).resolve(strict=True)
    except OSError, RuntimeError:
        _reject()


def _allowed_origin(value: object, context: _PolicyContext) -> Path:
    origin = _absolute_file_origin(value)
    if _forbidden_component(origin):
        _reject()
    if origin == context.policy_path or any(
        _under(origin, root)
        for root in (context.source_root, context.stdlib_root, context.dynamic_load_root)
    ):
        return origin
    _reject()


def _validate_root(path: Path, required_parent: Path, interpreter_uid: object) -> None:
    try:
        value = path.stat()
        if (
            path.lstat() != value
            or not stat.S_ISDIR(value.st_mode)
            or type(interpreter_uid) is not int
            or value.st_uid != interpreter_uid
            or stat.S_IMODE(value.st_mode) & 0o022
            or not _under(path, required_parent)
        ):
            _reject()
    except OSError, RuntimeError:
        _reject()


def _context_from_contract(contract_json: object) -> _PolicyContext:
    if type(contract_json) is not str:
        _reject()
    try:
        contract = json.loads(contract_json)
    except TypeError, ValueError:
        _reject()
    if type(contract) is not dict or set(contract) != _REQUIRED_CONTRACT_KEYS:
        _reject()
    try:
        snapshot_root = Path.cwd().resolve(strict=True)
        source_root = (snapshot_root / "src").resolve(strict=True)
        stdlib_root = Path(contract["stdlib_root"]).resolve(strict=True)
        dynamic_load_root = Path(contract["dynamic_load_root"]).resolve(strict=True)
        base_prefix = Path(contract["base_prefix"]).resolve(strict=True)
        prefix = Path(contract["prefix"]).resolve(strict=True)
        policy_path = Path(__file__).resolve(strict=True)
        actual = os.stat("/proc/self/exe")
        identity = contract["interpreter_identity"]
        version = contract["python_version"]
        if (
            type(identity) is not list
            or len(identity) != 2
            or any(type(value) is not int for value in identity)
            or type(version) is not list
            or tuple(version) != tuple(sys.version_info[:3])
            or type(contract["abi"]) is not str
            or type(contract["implementation"]) is not str
            or sys.implementation.name != contract["implementation"]
            or sys.implementation.cache_tag != contract["abi"]
            or (actual.st_dev, actual.st_ino) != tuple(identity)
            or sys.prefix != str(prefix)
            or sys.base_prefix != str(base_prefix)
            or dynamic_load_root != stdlib_root / "lib-dynload"
            or any(
                _forbidden_component(root) for root in (base_prefix, stdlib_root, dynamic_load_root)
            )
            or not source_root.is_dir()
            or source_root.is_symlink()
            or not _under(source_root, snapshot_root)
            or policy_path != snapshot_root / "tests/support/snapshot_child_bootstrap_policy.py"
        ):
            _reject()
        _validate_root(base_prefix, base_prefix.parent, contract["interpreter_uid"])
        _validate_root(stdlib_root, base_prefix, contract["interpreter_uid"])
        _validate_root(dynamic_load_root, stdlib_root, contract["interpreter_uid"])
    except OSError, RuntimeError, TypeError, ValueError:
        _reject()
    return _PolicyContext(snapshot_root, source_root, stdlib_root, dynamic_load_root, policy_path)


def _validate_spec(spec: object, context: _PolicyContext) -> object | None:
    if spec is None:
        return None
    try:
        origin = spec.origin
        loader = spec.loader
        locations = spec.submodule_search_locations
    except AttributeError:
        _reject()
    if origin in {"built-in", "frozen"}:
        expected = (
            importlib.machinery.BuiltinImporter
            if origin == "built-in"
            else importlib.machinery.FrozenImporter
        )
        if loader is not expected or locations is not None:
            _reject()
        return spec
    if origin is None:
        if type(loader) is not importlib.machinery.NamespaceLoader or locations is None:
            _reject()
        try:
            for location in locations:
                _allowed_origin(location, context)
        except TypeError:
            _reject()
        return spec
    if type(loader) not in {
        importlib.machinery.SourceFileLoader,
        importlib.machinery.ExtensionFileLoader,
    }:
        _reject()
    _allowed_origin(origin, context)
    if locations is not None:
        try:
            for location in locations:
                _allowed_origin(location, context)
        except TypeError:
            _reject()
    return spec


class _SnapshotFinder:
    _context: _PolicyContext | None = None

    @classmethod
    def find_spec(cls, fullname: str, path: object = None, target: object = None) -> object | None:
        if cls._context is None:
            _reject()
        return _validate_spec(
            importlib.machinery.PathFinder.find_spec(fullname, path, target), cls._context
        )


def _configure_import_containment(context: _PolicyContext) -> None:
    global _FILE_FINDER_HOOK
    loader_details = (
        (importlib.machinery.SourceFileLoader, importlib.machinery.SOURCE_SUFFIXES),
        (importlib.machinery.ExtensionFileLoader, importlib.machinery.EXTENSION_SUFFIXES),
    )
    _FILE_FINDER_HOOK = importlib.machinery.FileFinder.path_hook(*loader_details)
    sys.path[:] = [
        str(context.source_root),
        str(context.stdlib_root),
        str(context.dynamic_load_root),
    ]
    sys.path_hooks[:] = [_FILE_FINDER_HOOK]
    sys.path_importer_cache.clear()
    _SnapshotFinder._context = context
    sys.meta_path[:] = [
        importlib.machinery.BuiltinImporter,
        importlib.machinery.FrozenImporter,
        _SnapshotFinder,
    ]


def _validate_import_containment(context: _PolicyContext) -> None:
    expected_meta_path: list[object] = [
        importlib.machinery.BuiltinImporter,
        importlib.machinery.FrozenImporter,
        _SnapshotFinder,
    ]
    if (
        _SYNTHETIC_IMPORT_GUARD_TYPE is not None
        and sys.meta_path
        and type(sys.meta_path[0]) is _SYNTHETIC_IMPORT_GUARD_TYPE
    ):
        expected_meta_path.insert(0, sys.meta_path[0])
    if (
        _FILE_FINDER_HOOK is None
        or sys.path
        != [
            str(context.source_root),
            str(context.stdlib_root),
            str(context.dynamic_load_root),
        ]
        or sys.path_hooks != [_FILE_FINDER_HOOK]
        or sys.meta_path != expected_meta_path
    ):
        _reject()


def _audit_loaded_modules(context: _PolicyContext) -> None:
    _validate_import_containment(context)
    main_module = sys.modules.get("__main__")
    for loaded in tuple(sys.modules.values()):
        if loaded is None:
            continue
        spec = getattr(loaded, "__spec__", None)
        origin = getattr(spec, "origin", None) if spec is not None else None
        if origin in {"built-in", "frozen"}:
            _validate_spec(spec, context)
            continue
        if origin is None:
            if loaded is not main_module:
                _reject()
            continue
        _validate_spec(spec, context)
        filename = getattr(loaded, "__file__", None)
        if filename is not None:
            _allowed_origin(filename, context)
        locations = getattr(loaded, "__path__", None)
        if locations is not None:
            try:
                for location in locations:
                    _allowed_origin(location, context)
            except TypeError:
                _reject()


def _install_synthetic_probe_guards() -> None:
    global _SYNTHETIC_IMPORT_GUARD_TYPE
    guard = _SnapshotImportGuard()
    if any(guard._blocked(name) for name in sys.modules):
        print("SNAPSHOT_IMPORT_GUARD_BLOCKED", flush=True)
        _reject()
    sys.addaudithook(_SnapshotAuditGuard())
    sys.meta_path.insert(0, guard)
    _SYNTHETIC_IMPORT_GUARD_TYPE = _SnapshotImportGuard


def _run_test_probe(probe: str) -> NoReturn:
    """Exercise fixed local-only capabilities; guards must abort before side effects."""

    if probe.startswith("import-"):
        module = {
            "import-http-client": "http.client",
            "import-urllib-request": "urllib.request",
            "import-requests": "requests",
            "import-httpx": "httpx",
            "import-openai": "openai",
        }[probe]
        __import__(module)
    elif probe == "importlib-http-client":
        importlib.import_module("http.client")
    elif probe == "from-http-client":
        _from_http_client_probe()
    elif probe == "submodule-http-client":
        __import__("http.client.denied_submodule")
    elif probe == "alias-http-client":
        _alias_http_client_probe()
    elif probe == "subprocess-popen":
        __import__("subprocess").Popen(("/bin/true",))
    elif probe == "os-system":
        os.system("/bin/true")
    elif probe == "posix-spawn":
        if not hasattr(os, "posix_spawn"):
            _reject()
        os.posix_spawn("/bin/true", ("/bin/true",), {})
    else:
        socket = __import__("socket")
        if probe in {"socket-sendto", "socket-sendmsg"}:
            sender, receiver = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                if probe == "socket-sendto":
                    sender.sendto(b"x", "")
                else:
                    if not hasattr(socket.socket, "sendmsg"):
                        _reject()
                    sender.sendmsg([b"x"])
            finally:
                sender.close()
                receiver.close()
        elif probe == "socket-connect":
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM).connect(
                "/tmp/snapshot-guard-no-socket"
            )
        elif probe == "socket-connect-ex":
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM).connect_ex(
                "/tmp/snapshot-guard-no-socket"
            )
        elif probe == "socket-getaddrinfo":
            socket.getaddrinfo("localhost", 9)
        elif probe == "socket-getnameinfo":
            socket.getnameinfo(("127.0.0.1", 9), 0)
        elif probe == "socket-gethostbyname":
            socket.gethostbyname("localhost")
        elif probe == "socket-gethostbyaddr":
            socket.gethostbyaddr("127.0.0.1")
        else:
            _reject()
    _reject()


def _load_child_module(context: _PolicyContext) -> object:
    module = importlib.import_module(_CHILD_MODULE)
    for name in (
        "discord_ai_reminder_bot",
        "discord_ai_reminder_bot.infrastructure",
        _CHILD_MODULE,
    ):
        loaded = sys.modules.get(name)
        if loaded is None:
            _reject()
        try:
            if not _under(_allowed_origin(loaded.__file__, context), context.source_root):
                _reject()
            if not _under(_allowed_origin(loaded.__spec__.origin, context), context.source_root):
                _reject()
        except AttributeError:
            _reject()
    return module


def _run_with_final_audit(main: object, argv: tuple[str, ...], context: _PolicyContext) -> object:
    primary: BaseException | None = None
    result: object = None
    try:
        result = main(argv)
    except BaseException as error:  # noqa: BLE001 - primary identity is part of the contract
        primary = error
    finally:
        try:
            _audit_loaded_modules(context)
        except BaseException:
            if primary is None:
                raise
    if primary is not None:
        raise primary
    return result


def bootstrap(contract_json: object, argv: tuple[str, ...], test_probe: object = None) -> NoReturn:
    """Start the one fixed child after configuring and auditing containment."""

    if (
        type(argv) is not tuple
        or any(type(argument) is not str for argument in argv)
        or (
            test_probe is not None
            and (type(test_probe) is not str or test_probe not in _TEST_PROBES)
        )
    ):
        _reject()
    sys.excepthook = sys.__excepthook__
    context = _context_from_contract(contract_json)
    _configure_import_containment(context)
    _audit_loaded_modules(context)
    try:
        if test_probe == "preloaded-http-client":
            importlib.import_module("http.client")
        _install_synthetic_probe_guards()
        _audit_loaded_modules(context)
    except SnapshotBootstrapPolicyError:
        if test_probe is not None:
            raise SystemExit(_PROBE_REJECT_EXIT_CODE) from None
        raise
    module = _load_child_module(context)
    _audit_loaded_modules(context)
    if test_probe is not None:
        try:
            _run_test_probe(test_probe)
        except RuntimeError:
            raise SystemExit(_PROBE_REJECT_EXIT_CODE) from None
    try:
        main = module.main
    except AttributeError:
        _reject()
    if not callable(main):
        _reject()
    raise SystemExit(_run_with_final_audit(main, argv, context))
