#!/usr/bin/env bash
# Восстановление логического бэкапа. ОСТОРОЖНО: перезаписывает текущую базу.
# Использование: bash scripts/restore-db.sh backups/dateideas-<stamp>.dump
set -euo pipefail
cd "$(dirname "$0")/.."

DUMP="${1:?Укажите путь к файлу бэкапа}"
[ -f "$DUMP" ] || { echo "Файл не найден: $DUMP"; exit 1; }
read -r -p "База dateideas будет перезаписана. Продолжить? [y/N] " answer
[ "$answer" = "y" ] || exit 1

docker compose exec -T db psql -U dateideas -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='dateideas' AND pid<>pg_backend_pid()"
docker compose exec -T db dropdb -U dateideas --if-exists dateideas
docker compose exec -T db createdb -U dateideas dateideas
docker compose exec -T db pg_restore -U dateideas -d dateideas < "$DUMP"
echo "Восстановление завершено из $DUMP"
