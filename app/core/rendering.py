"""Красивый рендер карточек (HTML для Telegram и web-чата).

Правила:
- Все динамические значения экранируются html.escape — текст источника не может
  сломать разметку или внедрить теги (NFR-06).
- NFR-10: смысл не зависит от эмодзи — каждый факт продублирован словами.
- Факты только из CandidateOut (FR-09): рендер ничего не добавляет.
"""
from __future__ import annotations

from html import escape
from zoneinfo import ZoneInfo

MOSCOW_TZ = ZoneInfo('Europe/Moscow')


def _price_line(candidate) -> str:
    prefix = '' if getattr(candidate, 'price_is_bounded', False) else 'от '
    if getattr(candidate, 'price_total_kopeks', None) is None:
        return '💰 Стоимость не подтверждена'
    return f'💰 {prefix}{candidate.price_total_kopeks // 100} ₽ за двоих'


def _session_line(candidate) -> str:
    starts = getattr(candidate, 'starts_at', None)
    if not starts:
        return ''
    local = starts.astimezone(MOSCOW_TZ)
    return f'\n🕒 Сеанс: {local:%d.%m.%Y %H:%M} МСК'


def render_cards_html(candidates: list, *, weather=None) -> str:
    """HTML-карточки: жирный заголовок, структурированные факты, эмодзи-навигация."""
    blocks = []
    for index, candidate in enumerate(candidates, 1):
        title = escape(candidate.title)
        category = escape(getattr(candidate, 'category', '') or 'место/событие')
        description = escape((getattr(candidate, 'description', '') or '').strip()[:250])
        address = escape(getattr(candidate, 'address', None) or 'Адрес не подтверждён')
        reasons = ', '.join(getattr(candidate, 'reason_codes', []) or []) or 'по условиям подбора'
        reason = escape(reasons)
        blocks.append(
            f'<b>{index}. {title}</b> · {category}\n'
            f'<i>{description}</i>\n'
            f'📍 {address}\n'
            f'{_price_line(candidate)}{_session_line(candidate)}\n'
            f'✅ Почему: {reason}'
        )
    header = 'Вот проверенные варианты:' if candidates else ''
    advisory = ''
    if weather:
        if getattr(weather, 'status', '') == 'adverse':
            advisory = '\n\n⚠️ Для прогулок в Москве прогнозируется непогода; проверь условия перед выходом.'
        elif getattr(weather, 'status', '') == 'unknown':
            advisory = '\n\nПрогноз для уличных вариантов недоступен; погода не подтверждена.'
    return '\n\n'.join([header, *blocks]).strip() + advisory
