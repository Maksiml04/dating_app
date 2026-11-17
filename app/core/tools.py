"""Инструменты агента (Tool pattern): единственный источник фактов для карточек.

- ToolSpec: имя, схема аргументов, пометка destructive (меняет память пользователя).
- ToolRegistry: ядро может вызывать только зарегистрированные инструменты;
  попытка вызова неизвестного — ошибка домена.
- ToolResult: факты возвращаются только инструментами; ядро и LLM не имеют права
  придумывать цены, адреса, ссылки (FR-09).
Инструменты зависят от домена (app.search, app.weather), но не от транспорта.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.core.session import AgentSession


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments: dict[str, type] = field(default_factory=dict)
    # destructive: инструмент изменяет память/профиль пользователя, а не только читает.
    destructive: bool = False


@dataclass
class ToolResult:
    ok: bool
    data: dict = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def success(cls, **data) -> ToolResult:
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, error: str) -> ToolResult:
        return cls(ok=False, error=error)


class Tool(Protocol):
    spec: ToolSpec
    def execute(self, session: AgentSession, **kwargs) -> ToolResult: ...


class UnknownTool(RuntimeError):
    pass


class ToolExecutionError(RuntimeError):
    pass


class ToolRegistry:
    """Реестр инструментов: имя -> Tool. Гард-рейл доступа вызывается здесь."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f'Инструмент {tool.spec.name!r} уже зарегистрирован')
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise UnknownTool(f'Инструмент {name!r} не зарегистрирован')
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def invoke(self, session: AgentSession, name: str, **arguments) -> ToolResult:
        tool = self.get(name)
        self._validate_arguments(tool, arguments)
        result = tool.execute(session, **arguments)
        session.record_tool(name, arguments, result.ok,
                            result.error or f"keys={sorted(result.data)}")
        return result

    @staticmethod
    def _validate_arguments(tool: Tool, arguments: dict) -> None:
        for arg_name, arg_type in tool.spec.arguments.items():
            if arg_name in arguments and arguments[arg_name] is not None \
                    and not isinstance(arguments[arg_name], arg_type):
                raise ToolExecutionError(
                    f'{tool.spec.name}.{arg_name}: ожидался {arg_type.__name__}')


# --- Инструменты домена ------------------------------------------------------

class SearchCatalogTool:
    """Поиск по проверенному каталогу: жёсткие фильтры + ранжирование (FR-06)."""
    spec = ToolSpec(name='search_catalog',
                    description='Поиск опубликованных проверенных вариантов по условиям',
                    arguments={'limit': int})

    def __init__(self, db, *, exclude_ids: list | None = None) -> None:
        self._db = db
        self._exclude_ids = exclude_ids or []

    def execute(self, session: AgentSession, **kwargs) -> ToolResult:
        from app.schemas import SearchRequest
        from app.search import search
        limit = kwargs.get('limit') or 3
        constraints = dict(session.memory.constraints)
        constraints.pop('_relax_offers', None)
        request = SearchRequest(**{**constraints,
            'exclude_canonical_ids': self._exclude_ids, 'limit': max(1, min(int(limit), 20))})
        try:
            candidates = search(self._db, request)
        except Exception as exc:  # noqa: BLE001 — гард: сбой поиска не крешит ядро
            return ToolResult.failure(f'search_failed: {type(exc).__name__}')
        return ToolResult.success(candidates=[c for c in candidates], count=len(candidates),
                                  query=request.query)


class WeatherAssessmentTool:
    """FR-07: прогноз для уличных вариантов; неопределённость не выдумывается."""
    spec = ToolSpec(name='assess_weather',
                    description='Прогноз непогоды для уличных вариантов на интервал')

    def execute(self, session: AgentSession, **kwargs) -> ToolResult:
        from app.weather import assess_outdoors
        constraints = session.memory.constraints
        try:
            forecast = assess_outdoors(constraints.get('starts_at'), constraints.get('ends_at'))
        except Exception as exc:  # noqa: BLE001 — гард: сбой погоды не крешит ядро
            return ToolResult.failure(f'weather_failed: {type(exc).__name__}')
        return ToolResult.success(status=forecast.status)


class RelaxConditionTool:
    """FR-08: ослабление ОДНОГО условия, только предложенного пользователю."""
    spec = ToolSpec(name='relax_condition',
                    description='Снять одно условие с явного согласия пользователя',
                    arguments={'field': str})

    def execute(self, session: AgentSession, **kwargs) -> ToolResult:
        from app.dialogue import apply_relax
        offered = session.memory.relax_offers
        field = str(kwargs.get('field') or '')
        if field not in offered:
            return ToolResult.failure('field_not_offered')
        constraints = dict(session.memory.constraints)
        if not apply_relax(constraints, field):
            return ToolResult.failure('field_not_relaxable')
        session.memory.constraints = constraints
        session.memory.relax_offers = []
        session.memory.shown_canonical_ids = []
        session.memory.clarifications = 0
        return ToolResult.success(relaxed=field)


class RememberPreferenceTool:
    """FR-20: явное /remember — мягкое предпочтение профиля."""
    spec = ToolSpec(name='remember_preference',
                    description='Сохранить явное предпочтение пользователя',
                    arguments={'note': str}, destructive=True)

    def execute(self, session: AgentSession, **kwargs) -> ToolResult:
        note = (kwargs.get('note') or '').strip()[:300]
        if not note:
            return ToolResult.failure('empty_note')
        session.memory.constraints['_explicit_note'] = note
        return ToolResult.success(remembered=True)
