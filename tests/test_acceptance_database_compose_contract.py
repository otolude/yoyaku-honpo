from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_COMPOSE = ROOT / "compose.acceptance.yaml"
DEVELOPMENT_COMPOSE = ROOT / "compose.yaml"


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-key")
        result[key] = value
    return result


def _load_compose(source: str) -> dict[str, object]:
    document = json.loads(source, object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(document, dict):
        raise TypeError("invalid-topology")
    return document


def _require_fixed_topology(document: object) -> None:
    if not isinstance(document, dict) or set(document) != {
        "name",
        "services",
        "volumes",
        "networks",
    }:
        raise ValueError("invalid-topology")
    services = document["services"]
    volumes = document["volumes"]
    networks = document["networks"]
    if (
        not isinstance(services, dict)
        or set(services) != {"acceptance_postgres"}
        or not isinstance(volumes, dict)
        or set(volumes) != {"acceptance_postgres_data"}
        or not isinstance(networks, dict)
        or set(networks) != {"acceptance_postgres_network"}
    ):
        raise ValueError("invalid-topology")


def test_acceptance_compose_is_json_compatible_yaml_with_only_fixed_structure() -> None:
    document = _load_compose(ACCEPTANCE_COMPOSE.read_text(encoding="utf-8"))
    _require_fixed_topology(document)
    assert set(document) == {"name", "services", "volumes", "networks"}
    assert document["name"] == "discord-ai-reminder-bot-acceptance-db"
    services = document["services"]
    volumes = document["volumes"]
    networks = document["networks"]
    assert isinstance(services, dict)
    assert isinstance(volumes, dict)
    assert isinstance(networks, dict)
    assert set(services) == {"acceptance_postgres"}
    assert set(volumes) == {"acceptance_postgres_data"}
    assert set(networks) == {"acceptance_postgres_network"}
    assert volumes["acceptance_postgres_data"] == {
        "name": "discord-ai-reminder-bot-acceptance-postgres-data"
    }
    assert networks["acceptance_postgres_network"] == {
        "name": "discord-ai-reminder-bot-acceptance-postgres-network"
    }

    service = services["acceptance_postgres"]
    assert isinstance(service, dict)
    assert set(service) == {
        "image",
        "environment",
        "ports",
        "volumes",
        "networks",
        "healthcheck",
        "restart",
        "stop_grace_period",
    }
    assert service["image"] == (
        "postgres@${ACCEPTANCE_POSTGRES_IMAGE_DIGEST:?set an immutable sha256 digest}"
    )
    assert service["environment"] == {
        "POSTGRES_DB_FILE": "/run/acceptance-postgres-secrets/postgres-database",
        "POSTGRES_USER_FILE": "/run/acceptance-postgres-secrets/postgres-user",
        "POSTGRES_PASSWORD_FILE": "/run/acceptance-postgres-secrets/postgres-password",
        "POSTGRES_INITDB_ARGS": "--auth=scram-sha-256",
    }
    assert service["ports"] == [
        "127.0.0.1:${ACCEPTANCE_POSTGRES_HOST_PORT:?set an acceptance loopback host port}:5432"
    ]
    assert service["volumes"] == [
        "acceptance_postgres_data:/var/lib/postgresql/data",
        {
            "type": "bind",
            "source": "${ACCEPTANCE_POSTGRES_SECRET_DIRECTORY_FD_PATH:?set only by the acceptance preflight launcher}",
            "target": "/run/acceptance-postgres-secrets",
            "read_only": True,
            "bind": {"create_host_path": False},
        },
    ]
    assert service["networks"] == ["acceptance_postgres_network"]
    assert service["healthcheck"] == {
        "test": ["CMD-SHELL", "pg_isready -q"],
        "interval": "5s",
        "timeout": "3s",
        "retries": 5,
        "start_period": "10s",
    }
    assert service["restart"] == "no"
    assert service["stop_grace_period"] == "20s"


@pytest.mark.parametrize(
    "source",
    (
        '{"name":"one","name":"two","services":{},"volumes":{},"networks":{}}',
        '{"name":"x","services":{"unexpected":{}},"volumes":{},"networks":{}}',
        '{"name":"x","services":{},"volumes":{},"networks":{},"version":"3"}',
        '{"name":"x","services":{"acceptance_postgres":{}},"volumes":{"unsafe":{}},"networks":{}}',
        '{"name":"x","services":{"acceptance_postgres":{}},"volumes":{},"networks":{"unsafe":{}}}',
        '{"name":"x","restart":"no","services":{"acceptance_postgres":{}},"volumes":{},"networks":{}}',
        '["not-a-compose-object"]',
    ),
)
def test_structural_parser_rejects_duplicate_or_unsafe_compose_shapes(source: str) -> None:
    if '"name":"one"' in source:
        with pytest.raises(ValueError, match="duplicate-key"):
            _load_compose(source)
    else:
        with pytest.raises((TypeError, ValueError), match="invalid-topology"):
            _require_fixed_topology(_load_compose(source))


def test_development_compose_preserves_baseline_volume_identity_and_private_mount_contract() -> (
    None
):
    source = DEVELOPMENT_COMPOSE.read_text(encoding="utf-8")
    baseline = subprocess.run(
        ("git", "show", "HEAD:compose.yaml"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    baseline_volume = re.search(r"(?m)^\s*- postgres_data:(/[^\s]+)$", baseline)
    assert baseline_volume is not None
    baseline_tmpfs = re.search(r"(?m)^\s*- (/var/lib/postgresql[^\s]*)$", baseline)
    assert baseline_tmpfs is not None
    assert not re.search(r"(?m)^name:\s*", source)
    assert re.search(r"(?m)^\s{2}postgres_data:\s*$", source)
    assert "development_postgres_data" not in source
    assert not re.search(r"(?m)^\s{4}name:\s*", source)
    assert f"postgres_data:{baseline_volume.group(1)}" in source
    assert f"- {baseline_tmpfs.group(1)}" in source
    for environment, directory, target in (
        (".env.development-postgres", ".development-postgres-secrets", "development"),
        (".env.test-postgres", ".test-postgres-secrets", "test"),
    ):
        assert environment in (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert directory in source
        assert f"/run/{target}-postgres-secrets" in source
        assert "create_host_path: false" in source


def test_runbooks_state_private_creation_and_exact_compose_contracts() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    operations = (ROOT / "docs/operations.md").read_text(encoding="utf-8")
    for document in (readme, operations):
        assert "scripts/acceptance_database_preflight.py" in document
        assert "別launcher/別承認" in document
    assert "Docker CLIの`docker compose`" in operations
    assert "standalone Compose" in operations
    for boundary in (
        "symlinkでありunsupported/fail-closed",
        "authoritativeな`config --quiet` validationは未実施",
        "fixed allowlist pathへ配置",
        "binary配置、metadata検証、authoritative config成功の前にprivate acceptance inputsを作成せず",
        "Docker CLIまたはsymlink candidateを代替経路として使わない",
    ):
        assert boundary in operations
    for name in (
        ".env.development-postgres",
        ".development-postgres-secrets/",
        ".env.test-postgres",
        ".test-postgres-secrets/",
        "0700",
        "0600",
        "対話editor",
        "画面共有禁止",
    ):
        assert name in operations
    assert "docker compose --env-file .env.development-postgres" in operations
    assert "docker compose --env-file .env.test-postgres --profile test" in operations
    assert (
        "docker compose --env-file .env.development-postgres -p discord-ai-reminder-bot-portfolio"
        in operations
    )
    assert "/var/lib/postgresql" in operations
    assert ".development-postgres-secrets/`から`/run/development-postgres-secrets" in operations
    assert "固定のhost portを前提にしない" in operations
    assert "127.0.0.1:5432`、既存" not in operations
    for command in (
        "O_CREAT | os.O_EXCL | os.O_NOFOLLOW",
        '"postgres-user", "postgres-password", "postgres-database"',
        ".env.test-postgres",
        ".test-postgres-secrets/",
        "既存file、directory、通常symlink、dangling symlink",
    ):
        assert command in operations


def test_private_paths_are_ignored_and_example_contains_names_only() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in (
        ".env.acceptance-postgres",
        ".acceptance-postgres-secrets/",
        ".env.development-postgres",
        ".development-postgres-secrets/",
        ".env.test-postgres",
        ".test-postgres-secrets/",
    ):
        assert name in gitignore
    assert not re.search(r"(?m)^(?:ACCEPTANCE|DEVELOPMENT|TEST)_POSTGRES_[A-Z_]+=", example)
