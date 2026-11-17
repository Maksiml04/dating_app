#!/usr/bin/env bash
# Логический бэкап PostgreSQL через docker compose. Запускается вручную или по cron.
# Секреты берутся из .env (DATABASE_URL сервиса api), файл не покидает машину оператора.
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${1:-./backups}"
mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="$BACKUP_DIR/dateideas-$STAMP.dump"

docker compose exec -T db pg_dump -U dateideas -Fc dateideas > "$TARGET"
echo "Бэкап создан: $TARGET"
echo "Восстановление: bash scripts/restore-db.sh $TARGET"
