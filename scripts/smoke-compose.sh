#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then echo 'Missing .env (copy .env.example and fill it)' >&2; exit 2; fi
python -m app.deployment_check --env .env
docker compose up --build -d db minio migrate api
# Explicitly fail if migrations or API did not start.
docker compose ps
docker compose exec -T api python -m app.integration_smoke
