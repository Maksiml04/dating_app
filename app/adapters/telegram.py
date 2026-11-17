"""Telegram-адаптер ядра: единственное место, знающее про chat_id и outbox.

Соответствие идентичностей: user_ref = 'telegram:<chat_id>'. Сырой chat_id не
проникает в ядро, а ядро никогда не формирует Telegram-специфичные payload'ы.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.agent import OutboundMessage
from app.core.transports import DeliveryReceipt, Transport

USER_REF_PREFIX = 'telegram:'


def user_ref_of(chat_id: int) -> str:
    return f'{USER_REF_PREFIX}{chat_id}'


def chat_id_of(user_ref: str) -> int | None:
    if not user_ref.startswith(USER_REF_PREFIX):
        return None
    try:
        return int(user_ref[len(USER_REF_PREFIX):])
    except ValueError:
        return None


# Действия кнопок, которые адаптер умеет переводить в callback-data Telegram.
# like/exclude требуют токенов, привязанных к доставке, — их генерирует
# существующий sender worker (FeedbackAction); адаптер их не подменяет.
_PASS_THROUGH_ACTIONS = ('relax:', 'fb:more:')


class TelegramOutboxTransport(Transport):
    """Доставка через существующую очередь telegram_outbox (worker отправляет)."""

    def __init__(self, db, *, expires_minutes: int = 10) -> None:
        self._db = db
        self._expires_minutes = expires_minutes

    def send(self, user_ref: str, message: OutboundMessage) -> DeliveryReceipt:
        from uuid import uuid4

        from app.models import Outbox
        chat_id = chat_id_of(user_ref)
        if chat_id is None:
            return DeliveryReceipt(ok=False, delivered_at=_now_iso(),
                                   error='user_ref не является telegram-идентификатором')
        row = Outbox(idempotency_key=f'agent:{uuid4()}', chat_id=chat_id, text=message.text,
                     publication_ids=message.facts_ref.get('publication_ids') or [],
                     reply_markup=_to_reply_markup(message), parse_mode=message.parse_mode,
                     expires_at=datetime.now(UTC) + timedelta(minutes=self._expires_minutes))
        self._db.add(row)
        # Receipt подтверждает постановку в очередь; финальную доставку
        # подтверждает sender worker (статус delivered).
        return DeliveryReceipt(ok=True, delivered_at=_now_iso(), ref=str(row.id))


def _to_reply_markup(message: OutboundMessage) -> dict | None:
    rows = [[{'text': button.label, 'callback_data': button.action}
             for button in row if button.action.startswith(_PASS_THROUGH_ACTIONS)]
            for row in message.buttons]
    rows = [row for row in rows if row]
    return {'inline_keyboard': rows} if rows else None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def load_agent_session(dialogue_row: Any, *, prefix: str = USER_REF_PREFIX) -> object:
    """DialogueSession -> AgentSession (без смены схемы БД)."""
    from app.core.session import AgentSession
    return AgentSession.from_storage(f'{prefix}{dialogue_row.chat_id}',
                                     dialogue_row.constraints,
                                     generation=int(dialogue_row.generation_id or 0))


def save_agent_session(dialogue_row: Any, session) -> None:
    """AgentSession -> DialogueSession: состояние и память в JSONB constraints."""
    dialogue_row.constraints = session.to_storage()
    dialogue_row.state = session.state
    dialogue_row.generation_id = session.generation
