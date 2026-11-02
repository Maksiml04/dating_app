# v0.18 — изолированный сквозной тест

## Добавлено

- `.dockerignore`: исключает локальные ключи и Telethon-сессии из Docker build context.
- `docker-compose.e2e.yml`: отдельная PostgreSQL + pgvector (`dateideas_test`) без внешних портов и постоянных томов.
- `Dockerfile.e2e`: контейнер с dev-зависимостями, применением Alembic и запуском pytest E2E.
- `scripts/e2e-compose.sh`: запуск, сбор статуса и очистка тестовых контейнеров/томов.
- `tests/test_v018_e2e.py`: реальный тест цепочки API → Inbox → FSM/поколения → dialogue worker → SQL FTS + бюджет → Outbox → HTTP mock Telegram → Impression → callback → Feedback; повторные webhook проверяются на идемпотентность.
- `tests/test_v018_harness.py`: локальная проверка изоляции и очистки тестового стенда.

## Запуск

```bash
bash scripts/e2e-compose.sh
```

Не нужны настоящие Telegram API/LLM секреты. Требуются Docker Engine и Compose с возможностью загрузки контейнеров.

## Ограничения

Сам контейнерный прогон не подтверждён в окружении сборки без Docker. Тест использует живую PostgreSQL, а вызов Telegram API заменяет мок. E2E не подтверждает реальную доставку Telegram или корректность Telethon-импорта. После проверки контейнеров следует протестировать ручную модерацию в SQLAdmin.
