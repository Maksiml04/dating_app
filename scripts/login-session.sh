#!/usr/bin/env bash
# Авторизация ещё одного Telegram-аккаунта для импорта.
# Каждая сессия = отдельный аккаунт (свой телефон и код подтверждения).
#
# Использование: bash scripts/login-session.sh <имя>
#   bash scripts/login-session.sh work
#   → создаст volume-файл /data/telegram_work.session
#
# После логина профиль запускается так:
#   docker compose --profile ingest run -d --rm \
#     -e TELEGRAM_SESSION=/data/telegram_<имя>.session telegram_sync_worker
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="${1:?Укажите имя сессии, например: bash scripts/login-session.sh work}"
case "$NAME" in
  *[!a-zA-Z0-9_-]*) echo "Имя: только латиница, цифры, _ и -"; exit 1;;
esac

docker compose --profile manual run --rm \
  -e TELEGRAM_SESSION="/data/telegram_${NAME}.session" \
  telegram_ingest python -m app.telethon_ingest login

echo
echo "Готово. Сессия: telegram_${NAME}.session"
echo "Проверить: docker compose --profile manual run --rm \\"
echo "  -e TELEGRAM_SESSION=/data/telegram_${NAME}.session telegram_ingest \\"
echo "  python -m app.telethon_ingest probe --channel @kuda_v_moskva --limit 3"
