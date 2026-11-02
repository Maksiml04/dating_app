#!/usr/bin/env bash
# Run a one-shot Telegram sync plus bounded look-back scan; no auto-publication.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose run --rm telegram_ingest python -m app.telethon_ingest sync --limit 200 --edit-scan-limit 100
