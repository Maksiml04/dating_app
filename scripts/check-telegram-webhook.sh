#!/usr/bin/env bash
# Read-only проверка текущего webhook через Telegram Bot API.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose run --rm --no-deps api python -m app.webhook_probe "$@"
