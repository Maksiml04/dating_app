# v0.28 — агентное ядро без Telegram (hexagonal + agent patterns)

## «Переезд» из Telegram: ядро и адаптеры

Ядро `app/core/` больше не знает о мессенджерах. Telegram — один из адаптеров
(`app/adapters/`). Контракты независимости проверяются тестами: в `app/core` нет
ни `api.telegram.org`, ни `telethon`, ни `chat_id` — только транспортно-независимые
`user_ref`.

## Паттерны агентного дизайна

- **State Machine** (`core/session.py`): явная таблица переходов `TRANSITIONS`;
  невалидный переход — доменная ошибка `IllegalTransition`, а не тихая коррекция.
- **Working Memory**: условия запроса, показы, уточнения — только JSON-типы;
  сериализуется в существующий `DialogueSession.constraints` без миграций.
  Поддержан и унаследованный плоский формат хранения.
- **Tool pattern** (`core/tools.py`): `ToolSpec` (имя, схема аргументов, флаг
  destructive), `ToolRegistry` (вызов только зарегистрированных инструментов,
  валидация типов аргументов), `ToolResult`. Инструменты: `search_catalog`,
  `assess_weather`, `relax_condition`, `remember_preference`.
- **Facts-only guardrail**: карточки строятся только из `CandidateOut`
  инструментов; LLM/ядро не придумывают цены, адреса, URL (FR-09).
- **Bounded tool use**: `Guardrails(max_tool_calls_per_turn, max_clarifications,
  allow_auto_relax=False)` — уточнения ограничены (FR-03), авто-ослабление
  условий запрещено (FR-08).
- **Tool Trace**: каждый вызов инструмента записывается в сессию (аудит пути
  запроса, NFR-07) и хранится в JSONB вместе с памятью.
- **Ports & Adapters** (`core/transports.py`): `Transport` (порт доставки),
  `DeliveryReceipt` (успех только после подтверждения транспорта),
  `InMemoryTransport` для тестов и демо.
- **Adapter Telegram** (`adapters/telegram.py`): `user_ref = 'telegram:<chat_id>'`,
  `TelegramOutboxTransport` (доставка через существующий `telegram_outbox`),
  маппинг кнопок `relax:*` в callback-data, конвертация `DialogueSession` ↔
  `AgentSession` без смены схемы БД.

## Поворот диалога (`core/agent.py`)

`handle_text` / `handle_more` / `handle_relax` — управляемый пайплайн:
interpret → инструменты → compose. Пустая выдача даёт кнопки ослабления только
по явным условиям пользователя; сбой инструмента — честный
`DEPENDENCY_FAILURE` без выдумок; доменные события (`recommendation_delivered`,
`no_results`, `condition_relaxed`, …) эмитятся для аналитики.

## Демо

`python scripts/agent_demo.py` — разговор агента через `InMemoryTransport`
с фейковым каталогом: видно текст карточек, кнопки и tool trace.

## Проверено в этой среде

- `pytest -q`: 224 passed, 2 skipped; из них 18 новых тестов ядра, включая
  контракты гексагональности.
- `ruff check` чисто на `app/core/`, `app/adapters/`, новых тестах.

**Не сделано здесь (намеренно):** переключение живого webhook-пути на ядро.
Существующий боевой поток (fencing, generations, outbox) сохранён без изменений;
следующий шаг — перевести `dialogue.py` на вызов ядра через адаптер, что требует
отдельной приёмки. Live-проверка кнопок и доставки — в плане внешней приёмки.
