from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.production.yaml"
PRODUCTION = ROOT / "deployment" / "production"


def test_production_compose_is_private_and_bounded() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "ports:" not in text
    assert "postgres@" in text
    assert "PRODUCTION_APP_IMAGE" in text
    assert "mem_limit: 384m" in text
    assert "mem_limit: 448m" in text
    assert text.count("no-new-privileges:true") == 3
    assert 'cap_drop: ["ALL"]' in text
    assert 'max-size: "10m"' in text
    assert "discord-bot-token" in text
    assert "DISCORD_BOT_TOKEN=" not in text


def test_production_entrypoint_is_non_root_and_migration_is_confirmed() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (PRODUCTION / "app-entrypoint.sh").read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile
    assert "--target production" in entrypoint
    assert '--confirm "production:$db_name:upgrade"' in entrypoint
    assert "downgrade" not in entrypoint


def test_production_defaults_keep_paid_features_and_sync_disabled() -> None:
    env = (PRODUCTION / "production.env.example").read_text(encoding="utf-8")
    assert "DISCORD_GUILD_COMMAND_SYNC_ENABLED=false" in env
    assert "AI_NAME_GENERATION_ENABLED=false" in env
    assert "AI_NAME_GENERATION_PROVIDER=disabled" in env
    assert "AI_POST_DRAFT_ENABLED=false" in env
    assert "AI_POST_DRAFT_PROVIDER_ENABLED=false" in env
    assert "API_KEY" not in env


def test_bootstrap_default_is_non_mutating() -> None:
    text = (PRODUCTION / "bootstrap-ubuntu.sh").read_text(encoding="utf-8")
    assert "--check" in text
    assert "Refusing implicit host mutation" in text
    assert "apt " not in text
    assert "systemctl enable" not in text


def test_backup_is_logical_bounded_and_does_not_restore() -> None:
    text = (PRODUCTION / "backup.sh").read_text(encoding="utf-8")
    assert "pg_dump -Fc" in text
    assert "sha256sum" in text
    assert "BACKUP_RETENTION_DAYS:-7" in text
    assert "pg_restore" not in text
    assert "docker compose down" not in text
