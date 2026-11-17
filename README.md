# Куда сходить на свидание — MVP v0.29

Рекомендации свиданий по проверенному каталогу Москвы: **web-чат** и **Telegram-бот**
на одном агентном ядре. FastAPI, PostgreSQL/pgvector, SQLAdmin, Telethon, MinIO,
фоновые очереди, Docker Compose.

- Матрица требований и приёмки — [FR_ACCEPTANCE_MATRIX.md](FR_ACCEPTANCE_MATRIX.md)
- Изменения по версиям — [CHANGELOG-v0.29.md](CHANGELOG-v0.29.md) (и предыдущие)
- Агентное ядро — `app/core/`, адаптеры — `app/adapters/` (Telegram, web)

> **Статус: кандидат на интеграционную приёмку, не production-ready.** Успешные
> unit/контрактные тесты не заменяют живую проверку внешних сервисов.

---

## Быстрый старт: web-чат (без Telegram)

```bash
cp .env.example .env                 # минимум: POSTGRES_PASSWORD
python -m app.deployment_check --env .env
docker compose up --build -d
cd web && npm install && npm run build && cd ..
docker compose build api && docker compose up -d api
```

Сайт: **http://127.0.0.1:8000** (API + UI в одном порту). В этом режиме работает
демо-каталог — реальный подключается после наполнения каталога (шаги 3–4 ниже).

Админка модерации: http://127.0.0.1:8000/admin (нужен `ADMIN_PASSWORD` или
`ADMIN_PASSWORD_HASH` + надёжный `ADMIN_SESSION_SECRET`).

---

## Полный путь до живого Telegram-бота

### Шаг 0. Сеть до Telegram (VPN)

Telegram-серверы могут быть недоступны у провайдера — проверьте с хоста:

```bash
curl -m 6 -o /dev/null -w '%{http_code}\n' https://api.telegram.org
```

Ответ `000`/timeout → включите VPN **в системном режиме (TUN)**, чтобы Docker-контейнеры
тоже попали в туннель. VPN «только в браузере» не поможет. Если VPN — локальный прокси
(clash/sing-box), понадобится поддержка `PROXY_URL` в compose.

Проверка из контейнера:

```bash
docker compose exec api python -c "import httpx; print(httpx.get('https://api.telegram.org', timeout=5).status_code)"
```

### Шаг 1. Секреты в `.env`

Обязательные: `POSTGRES_PASSWORD` (= пароль в `DATABASE_URL`), `TELEGRAM_BOT_TOKEN`
(@BotFather), `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`,
админка (`ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET`), `MINIO_ROOT_USER/PASSWORD`.

Желательные: `ANALYTICS_SALT` (псевдонимизация пользователей в аналитике),
`LLM_API_KEY` (OpenRouter) — без него диалог работает в детерминированном режиме.

После правки `.env` пересоздайте контейнеры: `docker compose up -d`.

### Шаг 2. Сессии Telethon (каждая = отдельный аккаунт)

Ключи `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` — с https://my.telegram.org
(API development tools), это **не** то же, что токен бота. Сессия хранится в
Docker volume — перелогин после перезапуска не нужен.

```bash
bash scripts/login-session.sh main      # телефон → код из Telegram → 2FA
bash scripts/probe-telegram.sh @kuda_v_moskva 5   # проверка сессии
```

TimeoutError = нет сети до Telegram (шаг 0). Ещё аккаунт: `login-session.sh second`
(2–3 максимум; подробности и разбор ошибок — [docs/TELETHON_NGROK_RUNBOOK.md](docs/TELETHON_NGROK_RUNBOOK.md), часть A).

### Шаг 3. Каналы, импорт и модерация

**Обязательно зарегистрируйте каналы** — пустой реестр = пустой импорт:

```bash
curl -X POST http://127.0.0.1:8000/api/admin/sources \
  -H "x-admin-key: $ADMIN_API_KEY" -H 'Content-Type: application/json' \
  -d '{"channel_id": -1001234567890, "username": "kuda_v_moskva"}'
docker compose --profile ingest up -d telegram_sync_worker
```

Затем **ручная модерация** (требование FR-14, автопубликации нет):
`/admin` → SourceMessage → approve → черновик → проверить факты → publish.
Полный цикл и SQL-проверки — [runbook, часть A](docs/TELETHON_NGROK_RUNBOOK.md).

### Шаг 4. Ngrok-туннель и webhook

Webhook сам не регистрируется при `compose up`. Полная инструкция —
[runbook, часть B](docs/TELETHON_NGROK_RUNBOOK.md); кратко:

```bash
brew install ngrok && ngrok config add-authtoken <токен>   # один раз
# статический домен: dashboard.ngrok.com → Domains → прописать в .env:
#   PUBLIC_BASE_URL=https://<домен>   →   docker compose up -d
ngrok http --url=<домен> 8000        # держать запущенным
bash scripts/register-webhook.sh && bash scripts/check-telegram-webhook.sh
```

После этого напишите боту в Telegram — ответ придёт карточками (HTML, с фото,
кнопками 👍/🚫/🔄 и предложениями ослабить условие при пустой выдаче).

---

## Тестирование и приёмка

```bash
python -m pip install -e '.[dev]'
pytest -q                      # 236 passed; 2 PostgreSQL E2E — только с Docker
bash scripts/e2e-compose.sh    # изолированная БД dateideas_test + mock Telegram
bash scripts/backup-db.sh      # логический бэкап (NFR-04)
```

Операционное: `GET /metrics` (Prometheus), `GET /api/admin/analytics/summary`
(B1/B5), retention-воркер (NFR-08), дневной бюджет LLM (NFR-09), rate limit
webhook. Нагрузочные замеры NFR-01/03 — отдельная приёмка на стенде.

Документация парсера — [TELEGRAM_PARSER_CHECK.md](TELEGRAM_PARSER_CHECK.md);
архитектура и исходные FR — `docs/date-ideas-architecture-v2/`.
