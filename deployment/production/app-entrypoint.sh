#!/bin/sh
set -eu
read_secret() {
  value=$(cat "/run/secrets/$1")
  [ -n "$value" ] || { echo "required secret is empty" >&2; exit 78; }
  printf '%s' "$value"
}
db_user=$(read_secret postgres_user)
db_password=$(read_secret postgres_password)
db_name=$(read_secret postgres_database)
export DATABASE_URL="postgresql+psycopg://${db_user}:${db_password}@${DATABASE_HOST:-postgres}:${DATABASE_PORT:-5432}/${db_name}"
if [ "${1:-}" = migrate ]; then
  exec python -m discord_ai_reminder_bot.infrastructure.database.migrate --target production --expected-database "$db_name" --confirm "production:$db_name:upgrade" upgrade head
fi
export DISCORD_BOT_TOKEN="$(read_secret discord_bot_token)"
exec python -m discord_ai_reminder_bot
