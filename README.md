# Куда сходить на свидание — Telegram MVP v0.24

Telegram-бот рекомендаций свиданий по проверенному каталогу Москвы. FastAPI, PostgreSQL/pgvector, SQLAdmin, Telethon, MinIO, фоновые очереди, Docker Compose. Полный список функциональных требований и их доказательств — **[FR_ACCEPTANCE_MATRIX.md](FR_ACCEPTANCE_MATRIX.md)**; изменения — [CHANGELOG-v0.24.md](CHANGELOG-v0.24.md).

> **Статус: кандидат на интеграционную приёмку, не production-ready.** Локальные unit/контрактные тесты не подтверждают работоспособность внешних сервисов. Нельзя утверждать, что все FR окончательно приняты до выполнения реального E2E.

## Локальная подготовка

```bash
cp .env.example .env
# Отредактируйте .env только на своём компьютере, не публикуйте секреты
python -m app.deployment_check --env .env
docker compose up --build -d
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

`PUBLIC_DOMAIN`/`PUBLIC_BASE_URL` и HTTPS необходимы для Telegram webhook. Запуск Compose **не регистрирует** webhook сам по себе. Перед рабочей интеграцией нужно настроить DNS/HTTPS и выполнить `bash scripts/register-webhook.sh`, затем `bash scripts/check-telegram-webhook.sh`.

SQLAdmin доступен в `/admin`, **только при настроенных** `ADMIN_PASSWORD` или `ADMIN_PASSWORD_HASH` и надёжном `ADMIN_SESSION_SECRET`. Используется `sqladmin>=0.21` для маршрутов модерации на ModelView. Модератор должен вручную подтвердить ссылку, адрес, срок действия и ограниченность цены.

Telethon требует отдельную авторизованную сессию и `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`:

```bash
docker compose --profile manual run --rm telegram_ingest python -m app.telethon_ingest login
bash scripts/probe-telegram.sh @kuda_v_moskva 5
docker compose --profile ingest up -d telegram_sync_worker
```

Фотографии исходников хранятся приватно в MinIO и доступны через защищённый API. Это ещё **не** означает отправку фотографий в пользовательские рекомендации: сейчас карточки текстовые.

## Тестирование и приёмка

```bash
python -m pip install -e '.[dev]'
pytest -q
alembic upgrade head --sql >/tmp/dateideas-migrations.sql
# Только на машине с Docker, тестовая БД и фейковый Telegram API:
bash scripts/e2e-compose.sh
```

E2E поднимает отдельную PostgreSQL `dateideas_test` и применяет миграции. Секреты настоящего бота для него не нужны. `pytest` вне Docker пропускает PostgreSQL E2E; это нормально для локальной разработки, но **не является успешной интеграционной приёмкой**.

Документация Telegram-парсера — [TELEGRAM_PARSER_CHECK.md](TELEGRAM_PARSER_CHECK.md); архитектура и исходные FR — `docs/date-ideas-architecture-v2/00-requirements/source-functional.md`.
