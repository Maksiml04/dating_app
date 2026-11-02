#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v docker >/dev/null || { echo 'Для E2E требуется Docker Compose' >&2; exit 2; }
compose=(docker compose -f docker-compose.e2e.yml)
cleanup() { "${compose[@]}" down --volumes --remove-orphans >/dev/null || true; }
trap cleanup EXIT
"${compose[@]}" up --build --abort-on-container-exit --exit-code-from e2e e2e
