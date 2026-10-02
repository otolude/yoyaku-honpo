#!/bin/sh
set -eu
if [ "${1:-}" = --check ]; then
  command -v systemctl >/dev/null
  command -v ufw >/dev/null
  command -v docker >/dev/null
  docker compose version >/dev/null
  exit 0
fi
echo 'Refusing implicit host mutation. Run documented commands interactively after reviewing the release commit.' >&2
echo 'Use --check for a non-mutating prerequisite check.' >&2
exit 64
