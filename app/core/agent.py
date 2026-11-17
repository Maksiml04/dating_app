"""Агент подбора свиданий: поворот диалога как управляемый пайплайн.

Паттерны и гарды:
- Turn pipeline: interpret -> plan tools -> execute -> compose reply.
- Facts-only guardrail: текст карточек строится только из результатов
  инструментов (CandidateOut из каталога); ядро не придумывает цены/URL (FR-09).
- Bounded tool use: лимит вызовов инструментов за поворот (NFR-09 по цене).
- No silent relax: условия не ослабляются без явной кнопки пользователя (FR-08).
- Транспорт не важен: результат поворота — список OutboundMessage для Transport.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.session import AgentSession
from app.core.tools import ToolRegistry, ToolResult


@dataclass(frozen=True)
class Guardrails:
    max_clarifications: int = 2
    max_tool_calls_per_turn: int = 4
    allow_auto_relax: bool = False  # False: ослабление только по кнопке пользователя


@dataclass
class Button:
    label: str
    action: str  # транспортно-независимое действие: 'like:1', 'relax:district', 'more'


@dataclass
class OutboundMessage:
    text: str
    buttons: list[list[Button]] = field(default_factory=list)
    # Инструментальный источник фактов для аудита (publication ids, reasons).
    facts_ref: dict = field(default_factory=dict)
    # 'HTML' для карточек; None — унаследованный простой текст.
    parse_mode: str | None = None


@dataclass
class AgentReply:
    messages: list[OutboundMessage]
    new_state: str
    reason: str  # delivered | no_results | clarification | failed
    reason_code: str | None = None

    @property
    def text(self) -> str:
        return '\n\n'.join(m.text for m in self.messages)


ToolRenderer = Callable[[list], list[OutboundMessage]]


def default_renderer(candidates: list) -> list[OutboundMessage]:
    """Карточки только из CandidateOut: проверенные поля, никакого LLM-текста."""
    from app.core.rendering import render_cards_html
    if not candidates:
        return []
    text = render_cards_html(candidates)
    buttons: list[list[Button]] = []
    for index in range(len(candidates[:3])):
        buttons.append([Button(label=f'👍 Подходит {index + 1}', action=f'like:{index}'),
                        Button(label=f'🚫 Не предлагать {index + 1}', action=f'exclude:{index}')])
    buttons.append([Button(label='🔄 Другие варианты', action='more')])
    return [OutboundMessage(text=text, buttons=buttons, parse_mode='HTML',
                            facts_ref={'publication_ids': [str(c.publication_version_id)
                                                           for c in candidates]})]


class DateIdeasAgent:
    """Оркестратор поворота. Транспорт и хранилище подключаются снаружи."""

    def __init__(self, tools: ToolRegistry, *,
                 guardrails: Guardrails | None = None,
                 renderer: ToolRenderer | None = None,
                 behavior_signals: Callable | None = None) -> None:
        self.tools = tools
        self.guardrails = guardrails or Guardrails()
        self.renderer = renderer or default_renderer
        self.behavior_signals = behavior_signals  # (db, user_ref) -> (pos, neg, seen)

    # -- Публичные точки входа ------------------------------------------------

    def handle_text(self, session: AgentSession, text: str, db=None) -> AgentReply:
        """Полный поворот: интерпретация текста -> инструменты -> ответ."""
        from app.dialogue import clarification_question, extract_constraints
        previous_query = session.memory.constraints.get('query')
        session.memory.constraints = extract_constraints(text, session.memory.constraints)
        if previous_query and session.memory.constraints.get('query') != previous_query:
            session.memory.shown_canonical_ids = []
            session.memory.clarifications = 0
        question = clarification_question(session.memory.constraints)
        if question:
            if session.memory.clarifications >= self.guardrails.max_clarifications:
                question = None  # после двух уточнений ищем по известным условиям (FR-03)
            else:
                session.memory.clarifications += 1
        if question:
            session.transition('interpret')
            session.emit('clarification_asked', index=session.memory.clarifications)
            return AgentReply([OutboundMessage(text=question)], session.state,
                              reason='clarification')
        return self._run_search_turn(session, db)

    def handle_relax(self, session: AgentSession, field_name: str) -> AgentReply:
        """Пользователь нажал кнопку ослабления условия: согласие уже дано."""
        result = self.tools.invoke(session, 'relax_condition', field=field_name)
        if not result.ok:
            return AgentReply([OutboundMessage(
                text='Это предложение устарело. Напиши новый запрос.')], session.state,
                reason='clarification')
        session.emit('condition_relaxed', field=field_name)
        return self._run_search_turn(session, None)

    def handle_more(self, session: AgentSession, db=None) -> AgentReply:
        """«Другие варианты»: тот же запрос, исключая уже показанные (FR-21)."""
        if not session.memory.constraints.get('query'):
            return AgentReply([OutboundMessage(
                text='Сначала напиши, какое свидание ищешь.')], session.state,
                reason='clarification')
        return self._run_search_turn(session, db, more=True)

    # -- Пайплайн поиска ------------------------------------------------------

    def _run_search_turn(self, session: AgentSession, db, *, more: bool = False) -> AgentReply:
        if not session.memory.constraints.get('query'):
            return AgentReply([OutboundMessage(text=(
                'Что тебе интересно: мастер-класс, выставка, прогулка или что-то необычное?'))],
                session.state, reason='clarification')
        session.transition('process')
        tool_calls = 0

        search_result = self._invoke_search(session, db, more=more)
        tool_calls += 1
        if not search_result.ok:
            session.transition('fail')
            session.emit('request_failed', dependency='search')
            return AgentReply([OutboundMessage(text=(
                'Поиск временно недоступен. Попробуй ещё раз позже; условия сохранены.'))],
                session.state, reason='failed', reason_code='DEPENDENCY_FAILURE')

        candidates = search_result.data.get('candidates') or []
        candidates = self._apply_behavior_ranking(session, db, candidates)
        if candidates:
            # Гард запрещает авто-ослабление: показываем только точные совпадения.
            session.transition('deliver')
            session.memory.remember_shown([c.canonical_id for c in candidates])
            session.emit('recommendation_delivered', count=len(candidates),
                         publication_ids=[str(c.publication_version_id) for c in candidates])
            messages = self._bounded_render(candidates, tool_calls)
            return AgentReply(messages, session.state, reason='delivered')

        reason_code = self._empty_reason(session, search_result)
        session.transition('no_results')
        session.emit('no_results', reason_code=reason_code)
        return self._empty_reply(session, reason_code)

    def _invoke_search(self, session: AgentSession, db, *, more: bool) -> ToolResult:
        if more:
            session.memory.constraints['_shown_current'] = list(
                session.memory.shown_canonical_ids)
        return self.tools.invoke(session, 'search_catalog', db=db, limit=3)

    def _apply_behavior_ranking(self, session: AgentSession, db, candidates: list) -> list:
        if not candidates or self.behavior_signals is None or db is None:
            return candidates
        positive, negative, seen = self.behavior_signals(db, session.user_ref)
        from app.feedback_ranking import reorder_by_feedback
        note = str(session.memory.constraints.get('_explicit_note') or '')
        note = ' '.join([note, *session.memory.constraints.get('interests', []),
                         str(session.memory.constraints.get('format') or '')])
        return reorder_by_feedback(candidates, positive, negative, seen, limit=3,
                                   explicit_preferences=note)

    def _bounded_render(self, candidates: list, tool_calls: int) -> list[OutboundMessage]:
        messages = self.renderer(candidates)
        # Bounded tool use: суммарные вызовы за поворот ограничены гардом.
        assert tool_calls <= self.guardrails.max_tool_calls_per_turn
        return messages

    def _empty_reason(self, session: AgentSession, search_result: ToolResult) -> str:
        if session.memory.shown_canonical_ids and search_result.data.get('count') == 0:
            return 'EXHAUSTED'
        constraints = session.memory.constraints
        if constraints.get('budget_total_kopeks') is not None:
            return 'UNKNOWN_REQUIRED_DATA'
        return 'NO_MATCH'

    def _empty_reply(self, session: AgentSession, reason_code: str) -> AgentReply:
        from app.dialogue_worker import render_candidates
        text = render_candidates([], no_results_reason=reason_code)
        from app.dialogue import relax_offers
        offered = relax_offers(session.memory.constraints)
        buttons = []
        if offered:
            session.memory.relax_offers = offered
            labels = {'district': '🏘 Другие районы', 'metro': '🚇 Другие станции',
                      'budget_total_kopeks': '💰 Без бюджета',
                      'indoors': '🏠 Помещение и улица', 'time_period': '🕒 Любое время'}
            buttons = [[Button(label=labels[f], action=f'relax:{f}')] for f in offered]
        return AgentReply([OutboundMessage(text=text, buttons=buttons)], session.state,
                          reason='no_results', reason_code=reason_code)
