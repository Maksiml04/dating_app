# Runbook: Telethon-импорт и ngrok/webhook — полная инструкция

Пошагово, с объяснениями и разбором ошибок. Предполагается, что стек поднят
(`docker compose up -d`) и сайт отвечает на http://127.0.0.1:8000.

---

## Часть A. Telethon: импорт каналов в каталог

### A0. Как это устроено

Задействованы два разных ключа Telegram — не путайте:

| Ключ | Где взять | Для чего |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | @BotFather | Работает **бот** (webhook, отправка карточек) |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | https://my.telegram.org → **API development tools** | Работает **Telethon** — чтение каналов как пользователь |

`API_ID`/`API_HASH` выдаются один раз на аккаунт разработчика и общие для всех
сессий. Сессия — файл в Docker volume `telethon_sessions` (`/data/*.session`),
в нём хранится авторизация конкретного аккаунта: логинить заново после
перезапуска не нужно.

### A1. Первый логин (интерактивно)

```bash
bash scripts/login-session.sh main
```

Что произойдёт:
1. Контейнер спросит номер телефона (формат `+7…`)
2. Telegram пришлёт код в чат с Telegram (не SMS) — введите его
3. Если на аккаунте включена 2FA — спросит облачный пароль
4. Появится `/data/telegram_main.session`

Введите команду **в своём терминале**, не через CI: интерактив там не пробросишь.

### A2. Проверка сессии (read-only, БД не трогает)

```bash
bash scripts/probe-telegram.sh @kuda_v_moskva 5
```

Успех = JSON с `id`, датами, текстами и ссылками реальных постов.

**Ошибки и причины:**

| Ошибка | Причина | Лечение |
|---|---|---|
| `Attempt N at connecting failed: TimeoutError` | Нет сети до Telegram | Включите VPN в системном (TUN) режиме — см. README, шаг 0 |
| `Укажите публичный @username канала` | Приватный канал или ссылка на пост | Используйте публичный `@username` |
| `ID источника не соответствует` | Канал с таким username сменил владельца | Проверьте канал вручную; перерегистрируйте |
| `SessionRevoked` / `AuthKeyUnregistered` | Аккаунт завершил сессию или Telegram её отозвал | Удалите файл сессии из volume и залогиньтесь заново |

### A3. Зарегистрировать каналы в реестре (обязательный шаг!)

Sync-воркер читает только таблицу `telegram_source` — **пустой реестр = пустой
импорт без ошибок**. Канал добавляется через админ-API:

```bash
# x-admin-key = ADMIN_API_KEY из .env
curl -X POST http://127.0.0.1:8000/api/admin/sources \
  -H "x-admin-key: $ADMIN_API_KEY" -H 'Content-Type: application/json' \
  -d '{"channel_id": -1001234567890, "username": "kuda_v_moskva"}'
```

- `channel_id` — числовой ID канала (`-100…`); для публичных каналов достаточно
  `username`, ID можно взять из `probe` (`id` в ответе)
- Повторная регистрация того же канала → 409 «уже зарегистрирован»
- Список зарегистрированных: `docker compose exec db psql -U dateideas -d dateideas -c "SELECT username, enabled, last_error FROM telegram_source;"`

### A4. Запуск импорта

```bash
docker compose --profile ingest up -d telegram_sync_worker
# прогресс:
docker compose logs -f telegram_sync_worker
# что упало в базу:
docker compose exec db psql -U dateideas -d dateideas \
  -c "SELECT status, count(*) FROM source_message GROUP BY status;"
```

Воркер читает новые посты, **и правки старых** (ревизии ставят карточку на
повторную проверку — FR-15), фото складывает в MinIO приватно.

Повторный запуск безопасен: чекпоинт `last_message_id` не даёт дубликатов
(FR-12), при ошибке источника — экспоненциальный backoff, остальные каналы
продолжают работать.

### A5. Несколько аккаунтов (сессий)

Каждая сессия = отдельный аккаунт (свой телефон и код):

```bash
bash scripts/login-session.sh second
docker compose --profile ingest run -d --rm \
  -e TELEGRAM_SESSION=/data/telegram_second.session telegram_sync_worker
```

Правила: 2–3 аккаунта максимум (мультиаккаунтинг чреват flood-wait и баном);
разным сессиям — разные каналы (реестр общий, конкурировать чекпоинтами не надо).

### A6. Далнее: модерация

`/admin` → SourceMessage: `approve` → создать черновик → **проверить факты
вручную** (адрес, цена, сеанс — спека запрещает доверять извлечённому) →
publish. Статусы: `pending_review → approved → (draft) → published`.

---

## Часть B. Ngrok: публичный домен и webhook

### B0. Как это устроено

Telegram сам доставляет обновления на `https://<домен>/api/telegram/webhook`
— нужен публичный HTTPS-домен, указывающий на ваш API (порт 8000). Локальный
`127.0.0.1` Telegram не примет — проверяется в `app/webhook_setup.py`.

### B1. Установка и настройка ngrok (один раз)

```bash
brew install ngrok
# authtoken — из личного кабинета https://dashboard.ngrok.com/get-started/your-authtoken
ngrok config add-authtoken <ВАШ_ТОКЕН>
```

Бесплатный статический домен: dashboard.ngrok.com → **Cloud Edge → Domains →
Create domain** (даётся один бесплатно, например `xxx-yyy.ngrok-free.dev`).

### B2. Прописать домен в `.env`

```bash
PUBLIC_DOMAIN=xxx-yyy.ngrok-free.dev
PUBLIC_BASE_URL=https://xxx-yyy.ngrok-free.dev
```

Домен в `PUBLIC_BASE_URL` должен совпадать с доменом туннеля **посимвольно** —
иначе регистрация webhook отклонится. После правки: `docker compose up -d`.

### B3. Поднять туннель

```bash
ngrok http --url=xxx-yyy.ngrok-free.dev 8000
```

Окно ngrok не закрывать — туннель живёт, пока процесс запущен. Проверка
снаружи (можно с телефона в мобильном интернете):

```bash
curl -s https://xxx-yyy.ngrok-free.dev/health   # → {"status":"ok"}
```

### B4. Зарегистрировать и проверить webhook

```bash
bash scripts/register-webhook.sh        # setWebhook c secret_token
bash scripts/check-telegram-webhook.sh  # getWebhookInfo, read-only
```

В `check` должно быть: `url: https://…/api/telegram/webhook`,
`pending_update_count` растёт при сообщениях боту, **нет** `last_error_message`.

### B5. Живой тест

Напишите боту в Telegram: `/start`, затем «мастер-класс до 3000 руб на двоих
завтра вечером». Ожидание: карточки (HTML), кнопки 👍/🚫/🔄; при пустой выдаче —
кнопки ослабления условия. Дубль отправки не повторяет действие (FR-18).

### B6. Ошибки ngrok/webhook

| Симптом | Причина | Лечение |
|---|---|---|
| Домен отвечает 404 | Туннель не запущен | Запустите `ngrok http --url=… 8000` |
| `register-webhook` ругается на домен | `PUBLIC_BASE_URL` ≠ домен туннеля, или http вместо https | Сверьте посимвольно, только https, без пути |
| Бот молчит, `pending_update_count` растёт | Webhook зарегистрирован, но API не отвечает из ngrok | Проверьте `docker compose logs api`; туннель на порт 8000 |
| `last_error_message: Unauthorized` | Неверный `TELEGRAM_BOT_TOKEN` | Проверьте токен из @BotFather, `docker compose up -d` |
| Ngrok показывает страницу-предупреждение | Free-план, браузерный interstitial | На API-запросы Telegram это не влияет |
| Всё было живым, потом «умерло» | Ngrok закрылся/компьютер уснул | Перезапустите туннель; повторный `register-webhook.sh` не нужен, если домен тот же |

### B7. Если ngrok недоступен

Любой другой туннель с HTTPS: cloudflared (`cloudflared tunnel --url
http://localhost:8000`), локальный Caddy с доменом, VPS с nginx + certbot.
Требование одно: публичный HTTPS без пути, домен в `PUBLIC_BASE_URL`.
