#!/usr/bin/env bash
# Explicitly register webhook only after public HTTPS ingress is reachable.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose run --rm --no-deps api python -m app.webhook_setup set
