#!/usr/bin/env bash
set -euo pipefail
# Live MTProto probe: read-only, no Postgres modifications.
# Usage: bash scripts/probe-telegram.sh @kuda_v_moskva 5
CHANNEL="${1:-@kuda_v_moskva}"
LIMIT="${2:-5}"
docker compose --profile ingest run --rm telegram_ingest \
  python -m app.telethon_ingest probe --channel "$CHANNEL" --limit "$LIMIT"
