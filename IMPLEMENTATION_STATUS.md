# Статус MVP v0.22

Исправлены подтверждённые проблемы вектора индексации и атомарности публикации, добавлена read-only диагностика webhook Telegram. Подробности — `AUDIT-v0.22.md`.

**Проверено:** локальный pytest, синтаксис Python, офлайн генерация SQL Alembic.

**Не проверено:** реальные Docker/PostgreSQL E2E, подключение live Telegram/Telethon, работа SQLAdmin в контейнере, эмбеддинг-сервис и его фактический индекс.

**Известные пробелы:** `answerCallbackQuery`, правки старых сообщений в Telegram, MinIO/медиа, действия модератора в SQLAdmin, HTTPS и регистрация webhook.


## v0.23

Добавлены callback-worker, Telethon watch/edit reconciliation, MinIO media (private admin preview), HTTPS Caddy и регистрация webhook. См. CHANGELOG-v0.23.md. Live E2E без Docker и секретов не выполнялся.
