# v0.26 — продуктовая аналитика, retention, бюджет LLM (по спеке)

## Раздел 9 спеки: аналитика (FR метрики B1–B8)

- Модель `analytics_event` (миграция `0017_analytics`): name, occurred_at, request_id, schema_version, environment, is_test и payload без PII. Пользователь псевдонимизирован HMAC-SHA256 от chat_id с солью `ANALYTICS_SALT`; без соли user_hash не записывается вовсе. Тест запрещает появление колонки chat_id.
- События во всех точках из таблицы спеки:
  - `request_started` (+is_returning, conditions_present), `clarification_asked` (+index), `alternatives_requested` — webhook диалога;
  - `recommendation_delivered` (+count, publication_ids), `no_results` (+reason_code), `request_failed` (+dependency) — воркер поиска;
  - `recommendation_feedback_like|exclude|dislike` — обработчик callback;
  - `candidate_published`, `candidate_reviewed` — модерация черновиков.
- `GET /api/admin/analytics/summary?days=N` — сводка со счётчиками и метриками **B1** (доля подборов с «Подходит») и **B5** (доля пустых выдач). Сырые тексты и chat_id не отдаются.
- Событие пишется в транзакции породившего действия: аналитика согласована с фактом доставки/публикации.

## NFR-08: сроки хранения

- Новый `retention_worker` (docker compose service, `RETENTION_SCAN_SECONDS`): сырые диалоги (`telegram_inbox`) — 30 дней (`RETENTION_DIALOGUE_DAYS`), события аналитики — 90 (`RETENTION_ANALYTICS_DAYS`), терминальные outbox/callback-записи — 30. Статус `ambiguous` и pending не удаляются. Перед outbox сначала чистятся FeedbackAction (FK-порядок).
- Предпочтения, исключения и реакции не удаляются автоматически: это профиль до `/delete_data`.

## NFR-09: стоимость LLM

- Дневной бюджет вызовов `LLM_MAX_CALLS_PER_DAY` (0 — без лимита); при исчерпании — детерминированный разбор без LLM и метрика `llm_budget_exhausted_total` (плюс `llm_calls_total` в `/metrics`).
- Ограничение ответа `max_tokens` через `LLM_MAX_OUTPUT_TOKENS` (по умолчанию 512).

## Проверено в этой среде

- `pytest -q`: 195 passed, 2 skipped (PostgreSQL E2E требуют Docker, как раньше).
- `ruff check` чисто на всех новых/изменённых модулях.
- Offline-генерация SQL миграций до `0017` включительно (контрактный тест).

**Не верифицировано здесь:** применение миграции 0017 на PostgreSQL, работа retention-воркера в контейнере, фактический сбор Promettheus/аналитики на живом трафике — в общем плане внешней приёмки.
