# v0.17 — интеграционный smoke-стенд

Добавлен `python -m app.integration_smoke`, проверяющий PostgreSQL, pgvector, Alembic head 0009_worker_fencing, таблицы, `/health`, `/ready`, запрет административного API без ключа и отклонение Telegram webhook без корректного секрета. Скрипт не отправляет сообщения в Telegram и не записывает тестовые данные в БД.

На машине с Docker: `cp .env.example .env`, заполнить секреты и выполнить `bash scripts/smoke-compose.sh`. Для полного запуска worker: `docker compose up --build -d`.

**Ограничения:** здесь Docker недоступен, поэтому сквозной прогон PostgreSQL/Telegram НЕ подтверждён. Это инфраструктурный smoke-тест, а не полноценный Telegram E2E; в следующей версии нужны тесты с реальной БД, моками Telegram API и проверкой всей цепочки обработки.
