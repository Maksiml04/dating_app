"""Web-чат на агентном ядре: HTTP-адаптер без Telegram.

Сессии держатся в памяти процесса (MVP для фронтенда); при подключённом
PostgreSQL легко заменить на DialogueSession-хранилище — ядро этого не замечает.
"""
from __future__ import annotations

import uuid
from datetime import UTC

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.agent import DateIdeasAgent, Guardrails, OutboundMessage
from app.core.session import AgentSession
from app.core.tools import RelaxConditionTool, ToolRegistry, ToolResult
from app.core.transports import InMemoryTransport

router = APIRouter(prefix='/api/chat')

# --- Демо-каталог: работает без PostgreSQL; в проде заменяется SearchCatalogTool

class DemoCatalogTool:
    def __init__(self) -> None:
        from types import SimpleNamespace
        self.spec = SimpleNamespace(name='search_catalog', description='demo',
                                    arguments={'limit': int}, destructive=False)

    def execute(self, session: AgentSession, **kwargs) -> ToolResult:
        from datetime import datetime, timedelta
        from uuid import uuid4

        from app.schemas import CandidateOut
        query = str(session.memory.constraints.get('query') or '').lower()
        catalog = {
            'гончарн': ('Гончарная студия на Пятницкой',
                        'Мастер-класс для двоих: лепите кружки и шутите над ними', 'мастер-класс'),
            'выставк': ('Выставка современного искусства',
                        'Смело и необычно — есть о чём поговорить вечером', 'выставка'),
            'прогулк': ('Вечерняя прогулка по Патриаршим',
                        'Уютный маршрут с кофейней в конце пути', 'прогулка'),
            'джаз': ('Джаз-вечер в подвале на Китай-городе',
                     'Живой звук, приглушённый свет, столик на двоих', 'концерт'),
        }
        for key, (title, description, category) in catalog.items():
            if key in query:
                now = datetime.now(UTC)
                candidate = CandidateOut(canonical_id=uuid4(),
                    publication_version_id=uuid4(), title=title, description=description,
                    category=category, address='Москва, центр (демо-данные)',
                    source_url='https://example.com/demo', price_total_kopeks=250000,
                    price_is_bounded=True, valid_until=now + timedelta(days=3),
                    reason_codes=['демо-каталог', 'подходит по условиям'])
                return ToolResult.success(candidates=[candidate], count=1, query=query)
        return ToolResult.success(candidates=[], count=0, query=query)


def build_agent() -> DateIdeasAgent:
    registry = ToolRegistry()
    registry.register(DemoCatalogTool())
    registry.register(RelaxConditionTool())
    return DateIdeasAgent(registry, guardrails=Guardrails(max_clarifications=2))


# --- Хранилище сессий (в памяти процесса) ---

@router.get('/health')
def chat_health():
    return {'status': 'ok', 'agent': 'date-ideas', 'version': '0.29.0'}


class ChatService:
    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._transports: dict[str, InMemoryTransport] = {}
        self._agents: dict[str, DateIdeasAgent] = {}

    def _get(self, chat_id: str) -> tuple[AgentSession, InMemoryTransport, DateIdeasAgent]:
        if chat_id not in self._sessions:
            self._sessions[chat_id] = AgentSession(user_ref=f'web:{chat_id}')
            self._transports[chat_id] = InMemoryTransport()
            self._agents[chat_id] = build_agent()
        return self._sessions[chat_id], self._transports[chat_id], self._agents[chat_id]

    def deliver(self, chat_id: str, reply) -> list[dict]:
        session, transport, _ = self._get(chat_id)
        receipts = transport.send_all(session.user_ref, reply.messages)
        return [_message_to_json(m, r) for m, r in zip(reply.messages, receipts)]

    def history(self, chat_id: str) -> list[dict]:
        session, transport, _ = self._get(chat_id)
        return [_message_to_json(m, r) for ref, m, r in transport.sent
                if ref == session.user_ref]

    def reset(self, chat_id: str) -> None:
        self._sessions.pop(chat_id, None)
        self._transports.pop(chat_id, None)
        self._agents.pop(chat_id, None)


def _message_to_json(message: OutboundMessage, receipt) -> dict:
    return {'text': message.text, 'parse_mode': message.parse_mode,
            'buttons': [[{'label': b.label, 'action': b.action} for b in row]
                        for row in message.buttons],
            'delivered': receipt.ok}


service = ChatService()


class MessageIn(BaseModel):
    text: str

class ActionIn(BaseModel):
    action: str


@router.post('/{chat_id}/messages')
def send_message(chat_id: str, payload: MessageIn):
    text = payload.text.strip()
    if not text or len(text) > 500:
        raise HTTPException(422, 'Сообщение пустое или слишком длинное')
    session, _transport, agent = service._get(chat_id)
    try:
        if text.casefold() in ('другие варианты', '/more'):
            reply = agent.handle_more(session)
        else:
            reply = agent.handle_text(session, text)
    except Exception:  # noqa: BLE001 — гард: сбой агента не крешит HTTP
        raise HTTPException(503, 'Агент временно недоступен') from None
    return {'messages': service.deliver(chat_id, reply), 'state': session.state,
            'generation': session.generation}


@router.post('/{chat_id}/actions')
def run_action(chat_id: str, payload: ActionIn):
    session, _transport, agent = service._get(chat_id)
    action = payload.action.strip()
    try:
        if action == 'more':
            reply = agent.handle_more(session)
        elif action.startswith('relax:'):
            reply = agent.handle_relax(session, action[len('relax:'):])
        elif action.startswith(('like:', 'exclude:')):
            # Реакции в web-MVP фиксируются как событие; постоянные запреты
            # остаются в Telegram-адаптере, где есть токены доставки.
            session.emit('recommendation_feedback_' +
                         ('like' if action.startswith('like:') else 'exclude'))
            reply = None
        else:
            raise HTTPException(422, 'Неизвестное действие')
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 — гард: сбой агента не крешит HTTP
        raise HTTPException(503, 'Агент временно недоступен') from None
    messages = service.deliver(chat_id, reply) if reply else []
    return {'messages': messages, 'state': session.state}


@router.get('/{chat_id}/history')
def chat_history(chat_id: str):
    return {'messages': service.history(chat_id)}


@router.post('/{chat_id}/reset')
def chat_reset(chat_id: str):
    service.reset(chat_id)
    new_id = uuid.uuid4().hex[:12]
    return {'ok': True, 'chat_id': new_id}
