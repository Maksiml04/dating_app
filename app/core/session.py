"""Ядро агента: сессия диалога, независимая от транспорта (Telegram и др.).

Паттерны:
- State Machine: явные переходы состояний, невалидный переход — ошибка домена.
- Working Memory: только JSON-безопасные данные; сериализуется в существующее
  хранилище (DialogueSession.constraints) без смены схемы БД.
- Tool Trace: каждое обращение к инструменту фиксируется в сессии — аудируемость
  пути запроса (NFR-07) и запрет «фактов из воздуха».

Ядро НЕ импортирует адаптеры мессенджеров или HTTP-транспорт — контракт
проверяется тестом.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

# Допустимые переходы: (из состояния, событие) -> в состояние.
TRANSITIONS: dict[tuple[str, str], str] = {
    ('idle', 'interpret'): 'clarifying',
    ('idle', 'process'): 'processing',
    ('idle', 'reset'): 'idle',
    ('clarifying', 'interpret'): 'clarifying',
    ('clarifying', 'process'): 'processing',
    ('clarifying', 'satisfy'): 'idle',
    ('processing', 'deliver'): 'awaiting_feedback',
    ('processing', 'no_results'): 'awaiting_feedback',
    ('processing', 'fail'): 'failed',
    ('awaiting_feedback', 'interpret'): 'clarifying',
    ('awaiting_feedback', 'process'): 'processing',
    ('awaiting_feedback', 'reset'): 'idle',
    ('failed', 'interpret'): 'clarifying',
    ('failed', 'reset'): 'idle',
}


class IllegalTransition(RuntimeError):
    """Невалидный переход состояния: ошибка домена, а не тихая коррекция."""


@dataclass
class ToolCallRecord:
    """Аудит одного вызова инструмента в рамках поворота диалога."""
    tool: str
    arguments: dict
    ok: bool
    summary: str
    at: str

    def as_dict(self) -> dict:
        return {'tool': self.tool, 'arguments': self.arguments, 'ok': self.ok,
                'summary': self.summary[:200], 'at': self.at}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class WorkingMemory:
    """Рабочая память: условия запроса, показы, уточнения. Только JSON-типы."""
    constraints: dict = field(default_factory=dict)
    shown_canonical_ids: list[str] = field(default_factory=list)
    clarifications: int = 0
    relax_offers: list[str] = field(default_factory=list)

    def remember_shown(self, canonical_ids: list[str]) -> None:
        seen = set(self.shown_canonical_ids)
        for value in canonical_ids:
            seen.add(str(value))
        self.shown_canonical_ids = sorted(seen)

    def as_dict(self) -> dict:
        return {'constraints': self.constraints,
                '_shown_current': list(self.shown_canonical_ids),
                '_clarifications': self.clarifications,
                '_relax_offers': list(self.relax_offers)}

    @classmethod
    def from_dict(cls, data: dict | None) -> WorkingMemory:
        data = data or {}
        constraints = data.get('constraints')
        if constraints is None:
            # Унаследованный формат: условия лежат на верхнем уровне.
            constraints = {k: v for k, v in data.items() if not k.startswith('_')}
        return cls(constraints=dict(constraints),
                   shown_canonical_ids=list(data.get('_shown_current') or []),
                   clarifications=int(data.get('_clarifications') or 0),
                   relax_offers=list(data.get('_relax_offers') or []))


@dataclass
class AgentSession:
    """Обертка сессии: идентичность пользователя, состояние, память и trace.

    user_ref — транспортно-независимый идентификатор (например, 'tg:12345'
    или 'web:uuid'); ядро никогда не работает с сырыми идентификаторами чатов.
    """
    user_ref: str
    state: str = 'idle'
    generation: int = 0
    memory: WorkingMemory = field(default_factory=WorkingMemory)
    tool_trace: list[ToolCallRecord] = field(default_factory=list)
    last_events: list[dict] = field(default_factory=list)

    @property
    def chat_ref(self) -> str:
        return self.user_ref

    def transition(self, event: str) -> str:
        key = (self.state, event)
        if key not in TRANSITIONS:
            raise IllegalTransition(f'Переход {self.state!r} по {event!r} не разрешён')
        self.state = TRANSITIONS[key]
        return self.state

    def next_generation(self) -> int:
        self.generation += 1
        return self.generation

    def record_tool(self, tool: str, arguments: dict, ok: bool, summary: str) -> ToolCallRecord:
        record = ToolCallRecord(tool=tool, arguments=dict(arguments), ok=ok,
                                summary=summary, at=utc_now_iso())
        self.tool_trace.append(record)
        return record

    def emit(self, name: str, **payload) -> None:
        """Доменное событие поворота; адаптер решает, куда его доставить."""
        self.last_events.append({'name': name, 'user_ref': self.user_ref,
                                 'generation': self.generation,
                                 'occurred_at': utc_now_iso(), 'payload': payload})

    def drain_events(self) -> list[dict]:
        events, self.last_events = self.last_events, []
        return events

    def to_storage(self) -> dict:
        """JSONB-совместимое представление для существующего DialogueSession."""
        return {**self.memory.as_dict(), '_state': self.state,
                '_tool_trace': [call.as_dict() for call in self.tool_trace[-20:]]}

    @classmethod
    def from_storage(cls, user_ref: str, data: dict | None = None,
                     *, generation: int = 0) -> AgentSession:
        data = data or {}
        session = cls(user_ref=user_ref, state=data.get('_state') or 'idle',
                      generation=generation, memory=WorkingMemory.from_dict(data))
        session.tool_trace = [
            ToolCallRecord(tool=item.get('tool', '?'), arguments=item.get('arguments') or {},
                           ok=bool(item.get('ok')), summary=item.get('summary', ''),
                           at=item.get('at') or utc_now_iso())
            for item in (data.get('_tool_trace') or [])]
        return session
