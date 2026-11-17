# Статус MVP v0.22

Исправлены подтверждённые проблемы вектора индексации и атомарности публикации, добавлена read-only диагностика webhook Telegram. Подробности — `AUDIT-v0.22.md`.

**Проверено:** локальный pytest, синтаксис Python, офлайн генерация SQL Alembic.

**Не проверено:** реальные Docker/PostgreSQL E2E, подключение live Telegram/Telethon, работа SQLAdmin в контейнере, эмбеддинг-сервис и его фактический индекс.

**Известные пробелы:** `answerCallbackQuery`, правки старых сообщений в Telegram, MinIO/медиа, действия модератора в SQLAdmin, HTTPS и регистрация webhook.


## v0.23

Добавлены callback-worker, Telethon watch/edit reconciliation, MinIO media (private admin preview), HTTPS Caddy и регистрация webhook. См. CHANGELOG-v0.23.md. Live E2E без Docker и секретов не выполнялся.

## v0.25

Фото в пользовательских карточках (приватное MinIO → `sendPhoto` best-effort), `/metrics` (Prometheus), rate limiting webhook, `/liked`, скрипты бэкапа/восстановления, миграция 0016. См. CHANGELOG-v0.25.md. Реальная доставка фото и применение миграции на PostgreSQL — в плане внешней приёмки.

## v0.26

Продуктовая аналитика по разделу 9 спеки (analytics_event, HMAC-псевдонимизация, сводка B1/B5), retention-воркер NFR-08, дневной бюджет LLM NFR-09, миграция 0017. См. CHANGELOG-v0.26.md.

## v0.27

Кнопки ослабления одного условия после пустой выдачи (FR-08/UC-06, `relax:` callback + `reply_markup` в outbox), экспоненциальный backoff источников (FR-12), миграция 0018. См. CHANGELOG-v0.27.md.

## v0.28

Агентное ядро `app/core/` без Telegram: state machine сессии, working memory, ToolRegistry с guardrails, транспортный порт и Telegram-адаптер. Боевой webhook-путь пока не переключён (миграция ядра — следующий шаг). См. CHANGELOG-v0.28.md.

## v0.29

Красивая UI-обертка: HTML-карточки с экранированием (parse_mode, миграция 0019) и web-чат на React (`web/`, REST `/api/chat/*` на агентном ядре). См. CHANGELOG-v0.29.md.
