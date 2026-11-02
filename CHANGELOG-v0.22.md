# v0.22 — интеграционный аудит

- Исправлен самостоятельный CLI индексатора (инициализация SQLAlchemy engine).
- Добавлен периодический `indexer_worker` в Compose; при пустом `EMBEDDING_MODEL` он не индексирует и не вызывает API.
- Публикация новых и обновлённых карточек и статус черновика теперь фиксируются на границе вызывающего API, одним commit.
- Добавлена read-only диагностика Telegram Bot API `getWebhookInfo` (`python -m app.webhook_probe`, `scripts/check-telegram-webhook.sh`).
- Добавлены регрессионные тесты на атомарность, индексатор, Compose и диагностику webhook.
- Обновлена документация и составлен список оставшихся интеграционных пробелов (`AUDIT-v0.22.md`).

Проверки: локальные unit-тесты, compileall, офлайн Alembic SQL. **Реальный Docker/PostgreSQL/Telegram не запускался.**
