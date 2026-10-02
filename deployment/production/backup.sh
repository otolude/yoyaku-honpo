#!/bin/sh
set -eu
umask 077
backup_root=${BACKUP_ROOT:-/var/backups/discord-ai-reminder-bot}
retention_days=${BACKUP_RETENTION_DAYS:-7}
case "$retention_days" in *[!0-9]*|'') exit 64;; esac
mkdir -p "$backup_root"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
tmp="$backup_root/.backup-$stamp.dump"
final="$backup_root/backup-$stamp.dump"
trap 'rm -f "$tmp"' EXIT HUP INT TERM
docker compose -f compose.production.yaml exec -T postgres sh -eu -c 'export PGPASSWORD=$(cat /run/secrets/postgres_password); exec pg_dump -Fc --no-owner --no-privileges -U "$(cat /run/secrets/postgres_user)" -d "$(cat /run/secrets/postgres_database)"' > "$tmp"
test -s "$tmp"
mv "$tmp" "$final"
sha256sum "$final" > "$final.sha256"
find "$backup_root" -xdev -type f \( -name 'backup-*.dump' -o -name 'backup-*.dump.sha256' \) -mtime "+$retention_days" -delete
trap - EXIT HUP INT TERM
