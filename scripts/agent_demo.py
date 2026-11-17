"""Демо агентного ядра: разговор без Telegram, через InMemoryTransport.

Показывает «переезд»: ядро (app.core) + адаптер памяти вместо Telegram.
Запуск: python scripts/agent_demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.agent import DateIdeasAgent, Guardrails  # noqa: E402
from app.core.session import AgentSession  # noqa: E402
from app.core.tools import ToolRegistry, ToolResult  # noqa: E402
from app.core.transports import InMemoryTransport  # noqa: E402
from app.adapters.telegram import save_agent_session  # noqa: E402
from types import SimpleNamespace  # noqa: E402


class DemoSearchTool:
    """Фейковый каталог: без БД, но тот же контракт ToolResult с CandidateOut."""
    spec = SimpleNamespace(name='search_catalog', description='demo',
                           arguments={'limit': int}, destructive=False)
    CATALOG = {
        'гончарный': ('Гончарная студия на Пятницкой', 'мастер-класс, 3000 ₽ на двоих'),
        'выставка': ('Выставка современного искусства', 'билеты от 800 ₽, центр Москвы'),
        'прогулка': ('Вечерняя прогулка по Патриаршим', 'бесплатно, уютный маршрут'),
    }

    def execute(self, session, **kwargs):
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4
        from app.schemas import CandidateOut
        query = str(session.memory.constraints.get('query') or '').lower()
        for key, (title, description) in self.CATALOG.items():
            if key in query:
                now = datetime.now(timezone.utc)
                candidate = CandidateOut(canonical_id=uuid4(),
                    publication_version_id=uuid4(), title=title, description=description,
                    category='демо-каталог', address='Москва (демо)',
                    source_url='https://example.com/demo', price_total_kopeks=None,
                    price_is_bounded=None, valid_until=now + timedelta(days=3),
                    reason_codes=['демо-данные'])
                return ToolResult.success(candidates=[candidate], count=1, query=query)
        return ToolResult.success(candidates=[], count=0, query=query)


def build_agent():
    registry = ToolRegistry()
    registry.register(DemoSearchTool())
    return DateIdeasAgent(registry, guardrails=Guardrails(max_clarifications=2))


def main():
    agent = build_agent()
    transport = InMemoryTransport()
    session = AgentSession(user_ref='demo:1')

    for text in ('гончарный мастер-класс', 'выставка'):
        reply = agent.handle_text(session, text)
        for message in reply.messages:
            transport.send(session.user_ref, message)
        # Ядро сохраняется в любое хранилище через адаптер (здесь — фейковая строка).
        row = SimpleNamespace(chat_id=1, generation_id=session.generation, constraints={})
        save_agent_session(row, session)

    print('=== Диалог через InMemoryTransport ===')
    for i, text in enumerate(transport.texts('demo:1'), 1):
        print(f'[{i}] {text}')
    print('=== Tool trace ===')
    for call in session.tool_trace:
        print(f"{call.at[11:19]} {call.tool} -> {'ok' if call.ok else call.summary}")


if __name__ == '__main__':
    main()
