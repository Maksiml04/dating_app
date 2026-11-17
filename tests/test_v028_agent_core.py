"""v0.28: агентное ядро — сессия, инструменты, гарды, транспортный порт.

Ключевой контракт: app.core не зависит от Telegram и HTTP-транспорта;
Telegram — лишь один из адаптеров (app.adapters).
"""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.agent import Button, DateIdeasAgent, Guardrails, OutboundMessage
from app.core.session import AgentSession, IllegalTransition, WorkingMemory
from app.core.tools import (
    RelaxConditionTool,
    RememberPreferenceTool,
    ToolExecutionError,
    ToolRegistry,
    ToolResult,
    UnknownTool,
)
from app.core.transports import InMemoryTransport
from app.schemas import CandidateOut

# --- Контракт гексагональной архитектуры ---

def test_core_does_not_depend_on_transport_layer():
    import pathlib
    core = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'core'
    for path in core.glob('*.py'):
        source = path.read_text()
        assert 'telethon' not in source, path.name
        assert 'api.telegram.org' not in source, path.name
        assert 'app.worker' not in source, path.name


def test_adapters_are_the_only_place_with_chat_id():
    import pathlib
    core = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'core'
    for path in core.glob('*.py'):
        assert 'chat_id' not in path.read_text(), path.name


# --- State machine сессии ---

def test_valid_transitions_follow_state_machine():
    session = AgentSession(user_ref='web:1')
    session.transition('interpret')
    assert session.state == 'clarifying'
    session.transition('process')
    assert session.state == 'processing'
    session.transition('deliver')
    assert session.state == 'awaiting_feedback'
    session.transition('reset')
    assert session.state == 'idle'


def test_illegal_transition_raises():
    session = AgentSession(user_ref='web:1')
    with pytest.raises(IllegalTransition):
        session.transition('deliver')  # из idle доставлять нечего


# --- Working memory и сериализация без смены схемы БД ---

def test_memory_round_trip_through_storage():
    session = AgentSession(user_ref='telegram:42', state='processing', generation=7,
                           memory=WorkingMemory(constraints={'query': 'выставка'},
                                                shown_canonical_ids=['abc'],
                                                clarifications=1))
    session.record_tool('search_catalog', {'limit': 3}, True, 'keys=[candidates]')
    restored = AgentSession.from_storage('telegram:42', session.to_storage(), generation=7)
    assert restored.state == 'processing'
    assert restored.memory.constraints['query'] == 'выставка'
    assert restored.memory.shown_canonical_ids == ['abc']
    assert restored.memory.clarifications == 1
    assert restored.tool_trace[0].tool == 'search_catalog'


def test_storage_payload_is_json_safe():
    session = AgentSession(user_ref='web:1')
    session.record_tool('search_catalog', {}, True, 'ok')
    json.dumps(session.to_storage())  # не должно падать


# --- Реестр инструментов и гарды вызова ---

class _DummyTool:
    spec = SimpleNamespace(name='dummy', description='', arguments={'n': int},
                           destructive=False)
    def __init__(self):
        self.calls = []
    def execute(self, session, **kwargs):
        self.calls.append(kwargs)
        return ToolResult.success(value=kwargs.get('n'))


def test_registry_denies_unknown_tool():
    registry = ToolRegistry()
    with pytest.raises(UnknownTool):
        registry.get('no_such_tool')
    session = AgentSession(user_ref='web:1')
    with pytest.raises(UnknownTool):
        registry.invoke(session, 'no_such_tool')


def test_registry_validates_argument_types_and_traces_calls():
    tool = _DummyTool()
    registry = ToolRegistry()
    registry.register(tool)
    session = AgentSession(user_ref='web:1')
    assert registry.invoke(session, 'dummy', n=3).ok is True
    with pytest.raises(ToolExecutionError):
        registry.invoke(session, 'dummy', n='не число')
    assert len(session.tool_trace) == 1  # неуспешный вызов не попадает в trace
    assert session.tool_trace[0].tool == 'dummy'


def test_tools_are_the_only_facts_source_marker():
    # Деструктивные инструменты помечены: адаптер обязан спрашивать согласие.
    assert RememberPreferenceTool.spec.destructive is True
    assert RelaxConditionTool.spec.destructive is False  # согласие уже в кнопке


# --- Поворот диалога агента ---

def _candidate(title='Гончарная студия'):
    now = datetime.now(UTC)
    return CandidateOut(canonical_id=__import__('uuid').uuid4(),
        publication_version_id=__import__('uuid').uuid4(), title=title,
        description='Гончарный мастер-класс для двоих', category='мастер-класс',
        address='Москва, ул. Пятницкая, 1', source_url='https://example.com/p',
        price_total_kopeks=300000, price_is_bounded=True,
        valid_until=now + timedelta(days=3), reason_codes=['проверенная публикация'])


class _FakeSearchTool:
    spec = SimpleNamespace(name='search_catalog', description='',
                           arguments={'limit': int}, destructive=False)
    def __init__(self, candidates):
        self._candidates = candidates
    def execute(self, session, **kwargs):
        return ToolResult.success(candidates=list(self._candidates),
                                  count=len(self._candidates), query='x')


def _agent(candidates):
    registry = ToolRegistry()
    registry.register(_FakeSearchTool(candidates))
    registry.register(RelaxConditionTool())
    return DateIdeasAgent(registry)


def test_full_turn_delivers_cards_from_tool_output():
    candidate = _candidate()
    session = AgentSession(user_ref='web:1')
    reply = _agent([candidate]).handle_text(session, 'гончарный мастер-класс')
    assert reply.reason == 'delivered'
    assert 'Гончарная студия' in reply.text  # факт из инструмента, не из LLM
    flat_buttons = [b.action for row in reply.messages[0].buttons for b in row]
    assert 'more' in flat_buttons and 'like:0' in flat_buttons
    assert session.state == 'awaiting_feedback'
    assert str(candidate.canonical_id) in session.memory.shown_canonical_ids


def test_empty_turn_offers_relax_buttons_without_auto_relax():
    session = AgentSession(user_ref='web:1')
    agent = _agent([])
    session.memory.constraints = {'query': 'мастер-класс', 'district': 'центр',
                                  'budget_total_kopeks': 300000}
    session.memory.constraints['query'] = 'мастер-класс'
    reply = agent.handle_more(session)
    assert reply.reason == 'no_results'
    actions = [b.action for row in reply.messages[0].buttons for b in row]
    assert 'relax:district' in actions and 'relax:budget_total_kopeks' in actions
    # Гарды: условия НЕ ослаблены автоматически.
    assert session.memory.constraints['district'] == 'центр'
    assert session.memory.relax_offers == ['district', 'budget_total_kopeks']


def test_relax_button_removes_exactly_one_condition():
    session = AgentSession(user_ref='web:1')
    agent = _agent([])
    session.memory.constraints = {'query': 'мастер-класс', 'district': 'центр'}
    session.memory.relax_offers = ['district']
    reply = agent.handle_relax(session, 'district')
    assert reply.reason in ('no_results', 'delivered')
    assert 'district' not in session.memory.constraints
    assert session.memory.constraints['query'] == 'мастер-класс'


def test_relax_rejects_not_offered_field():
    session = AgentSession(user_ref='web:1')
    agent = _agent([])
    session.memory.constraints = {'query': 'x', 'district': 'центр'}
    agent.handle_relax(session, 'district')  # не было предложения
    assert session.memory.constraints.get('district') == 'центр'  # условие на месте


def test_tool_failure_yields_dependency_failure_without_fabrication():
    class _BrokenTool:
        spec = SimpleNamespace(name='search_catalog', description='', arguments={}, destructive=False)
        def execute(self, session, **kwargs):
            return ToolResult.failure('search_failed: Boom')
    registry = ToolRegistry()
    registry.register(_BrokenTool())
    session = AgentSession(user_ref='web:1')
    session.memory.constraints = {'query': 'мастер-класс'}
    reply = DateIdeasAgent(registry).handle_text(session, 'мастер-класс')
    assert reply.reason == 'failed' and reply.reason_code == 'DEPENDENCY_FAILURE'
    assert 'временно недоступен' in reply.text


def test_clarification_limit_is_bounded():
    agent = _agent([])
    agent.guardrails = Guardrails(max_clarifications=2)
    session = AgentSession(user_ref='web:1')
    # 'вечером' не содержит темы -> ядро задаёт уточняющий вопрос.
    reply = agent.handle_text(session, 'вечером')
    assert reply.reason == 'clarification'
    for _ in range(4):
        agent.handle_text(session, 'вечером')
    # После лимита уточнений счётчик не растёт бесконечно (FR-03).
    assert session.memory.clarifications <= 2


# --- Транспортный порт ---

def test_inmemory_transport_records_delivery_receipts():
    transport = InMemoryTransport()
    receipts = transport.send_all('web:1', [OutboundMessage(text='Привет!'),
                                            OutboundMessage(text='Варианты:')])
    assert all(r.ok for r in receipts)
    assert transport.texts('web:1') == ['Привет!', 'Варианты:']
    assert transport.texts('web:2') == []


def test_telegram_adapter_maps_user_ref_and_relax_buttons():
    from app.adapters.telegram import TelegramOutboxTransport, chat_id_of, user_ref_of
    assert user_ref_of(12345) == 'telegram:12345'
    assert chat_id_of('telegram:12345') == 12345
    assert chat_id_of('web:1') is None
    db = MagicMock()
    added = []
    db.add.side_effect = added.append
    receipt = TelegramOutboxTransport(db).send('telegram:42', OutboundMessage(
        text='Вариантов нет',
        buttons=[[Button(label='🏘 Другие районы', action='relax:district')],
                 [Button(label='👍', action='like:0')]]))
    assert receipt.ok is True
    row = added[0]
    assert row.chat_id == 42
    assert row.publication_ids == []
    keyboard = row.reply_markup['inline_keyboard']
    assert keyboard == [[{'text': '🏘 Другие районы', 'callback_data': 'relax:district'}]]


def test_telegram_session_adapter_round_trip():
    from app.adapters.telegram import load_agent_session, save_agent_session
    row = SimpleNamespace(chat_id=7, generation_id=2,
                          constraints={'query': 'выставка', '_state': 'awaiting_feedback'})
    session = load_agent_session(row)
    assert session.user_ref == 'telegram:7'
    assert session.memory.constraints['query'] == 'выставка'
    session.memory.constraints['query'] = 'прогулка'
    save_agent_session(row, session)
    # Новый канонический формат хранения: условия вложены, состояние рядом.
    assert row.constraints['constraints']['query'] == 'прогулка'
    assert row.state == 'awaiting_feedback'
