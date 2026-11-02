"""Telegram sender: fencing token, TTL, неопределённые отправки без повторов.

Статусы:
  pending -> reserved -> sending -> delivered
  reserved (crash ДО сети) -> pending после истечения резерва;
  sending (crash ВО ВРЕМЯ сети) -> ambiguous, НЕ повторять автоматически.
"""
import logging
import time
import uuid
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, func

from app.config import settings
from app.db import SessionLocal, configure_engine
from app.models import DialogueSession, Impression, Outbox, PublicationVersion, Canonical, FeedbackAction

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
RESERVATION_SECONDS = 45
SENDING_GRACE_SECONDS = 120
MAX_SEND_ATTEMPTS = 4


def recover_abandoned(db, now=None):
    """Разбираем упавшие резервы и неизвестные результаты отправки.

    Перед сетевым запросом уже зафиксирован status=sending. Такой статус
    нельзя перевести обратно в pending без риска двойной доставки.
    """
    now = now or datetime.now(timezone.utc)
    pending = list(db.scalars(select(Outbox).where(
        Outbox.status == 'reserved',
        (Outbox.reserved_at < now-timedelta(seconds=RESERVATION_SECONDS)) |
        (Outbox.reserved_at.is_(None)),
    ).with_for_update(skip_locked=True)))
    uncertain = list(db.scalars(select(Outbox).where(
        Outbox.status == 'sending',
        (Outbox.reserved_at < now-timedelta(seconds=SENDING_GRACE_SECONDS)) |
        (Outbox.reserved_at.is_(None)),
    ).with_for_update(skip_locked=True)))
    for row in pending:
        row.status = 'pending'
        row.reserved_at = None
        row.reservation_token = None
    for row in uncertain:
        row.status = 'ambiguous'
        row.last_error = 'Отправка была начата, результат неизвестен; требуется ручная проверка'
        row.reservation_token = None
    db.commit()
    return len(pending), len(uncertain)


def claim_batch(db, limit=20, now=None):
    now = now or datetime.now(timezone.utc)
    rows = list(db.scalars(select(Outbox).where(
        Outbox.status == 'pending', Outbox.next_attempt_at <= now,
    ).order_by(Outbox.next_attempt_at, Outbox.id).limit(limit).with_for_update(skip_locked=True)))
    claimed = []
    for row in rows:
        row.status = 'reserved'
        row.reserved_at = now
        row.reservation_token = str(uuid.uuid4())
        claimed.append((row.id, row.reservation_token))
    db.commit()
    return claimed


def deliver_one(db, row: Outbox, reservation_token: str | None = None):
    """Проверка ownership выполняется в БД перед переходом в sending."""
    locked = db.scalar(select(Outbox).where(Outbox.id == row.id)
                       .with_for_update().execution_options(populate_existing=True))
    if locked is None or locked.status != 'reserved' or not locked.reservation_token:
        db.rollback()
        return False
    if reservation_token is not None and locked.reservation_token != reservation_token:
        db.rollback()
        return False
    now = datetime.now(timezone.utc)
    dialogue = db.get(DialogueSession, locked.chat_id)
    if locked.expires_at <= now or (locked.generation_id is not None and
       (dialogue is None or locked.generation_id != dialogue.generation_id)):
        locked.status = 'cancelled'
        locked.reservation_token = None
        db.commit()
        return False
    for raw_id in locked.publication_ids:
        try:
            publication_id = uuid.UUID(raw_id)
        except (ValueError, TypeError):
            locked.status = 'failed'
            locked.last_error = 'Некорректный идентификатор публикации'
            db.commit()
            return False
        pub = db.get(PublicationVersion, publication_id)
        canonical = db.get(Canonical, pub.canonical_id) if pub else None
        latest_version = (db.scalar(select(func.max(PublicationVersion.version))
                          .where(PublicationVersion.canonical_id == pub.canonical_id)) if pub else None)
        from app.user_controls import is_excluded
        if (not pub or not canonical or canonical.status != 'published' or
            pub.valid_until <= now or pub.version != latest_version or
            is_excluded(db, locked.chat_id, pub.canonical_id)):
            locked.status = 'cancelled'
            locked.last_error = 'Публикация больше не актуальна'
            db.commit()
            return False
    if not settings.telegram_bot_token:
        locked.status = 'failed'
        locked.last_error = 'TELEGRAM_BOT_TOKEN не задан'
        db.commit()
        return False
    if locked.attempts >= MAX_SEND_ATTEMPTS:
        locked.status = 'failed'
        locked.last_error = 'Лимит отправок'
        db.commit()
        return False

    # Opaque callbacks are created once per outbox/publication, before the network boundary.
    # Reclaim is not allowed after the status becomes sending.
    actions = {}
    for raw_id in locked.publication_ids[:3]:
        pub_id = uuid.UUID(raw_id)
        found = db.scalar(select(FeedbackAction).where(
            FeedbackAction.outbox_id == locked.id,
            FeedbackAction.publication_id == pub_id))
        if found is None:
            found = FeedbackAction(token=uuid.uuid4().hex, outbox_id=locked.id,
                                   chat_id=locked.chat_id, publication_id=pub_id)
            db.add(found)
        actions[raw_id] = found.token
    # Важная граница: после commit мы МОЖЕМ отправить HTTP-запрос.
    # Повтор после сбоя в sending запрещён автоматически.
    locked.status = 'sending'
    locked.attempts += 1
    locked.reserved_at = now
    locked.reservation_token = None
    db.commit()
    url = f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage'
    try:
        payload = {'chat_id': locked.chat_id, 'text': locked.text,
                   'disable_web_page_preview': True}
        if locked.text.startswith('Привет!'):
            payload['reply_markup'] = {'keyboard': [[
                {'text': 'Мастер-класс'}, {'text': 'Выставка'}],
                [{'text': 'Прогулка'}, {'text': 'Что-то необычное'}]],
                'resize_keyboard': True, 'one_time_keyboard': True}
        if locked.publication_ids:
            rows = [[
                {'text': f'👍 Подходит {i+1}', 'callback_data': f'fb:like:{actions[raw_id]}'},
                {'text': f'🚫 Не предлагать {i+1}', 'callback_data': f'fb:exclude:{actions[raw_id]}'},
            ] for i, raw_id in enumerate(locked.publication_ids[:3])]
            last_id = locked.publication_ids[min(2,len(locked.publication_ids)-1)]
            for i, raw_id in enumerate(locked.publication_ids[:3]):
                pub = db.get(PublicationVersion, uuid.UUID(raw_id))
                urls = []
                for key, label in [('source_url', '🔗 Подробнее'),
                                   ('booking_url', '🎟 Бронирование')]:
                    target = getattr(pub, key, None) if pub else None
                    if target:
                        parsed = urlparse(target)
                        if parsed.scheme == 'https' and parsed.hostname and not parsed.username:
                            urls.append({'text':f'{label} {i+1}', 'url':target})
                if urls:
                    rows.append(urls)
            rows.append([{'text':'🔄 Другие варианты',
                         'callback_data':f'fb:more:{actions[last_id]}'}])
            payload['reply_markup'] = {'inline_keyboard': rows}
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload)
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError('Telegram прислал не JSON-object')
        if response.is_success and data.get('ok'):
            locked.status = 'delivered'
            for raw_id in locked.publication_ids:
                db.add(Impression(chat_id=locked.chat_id, publication_id=uuid.UUID(raw_id)))
            # Current selection dedup is based only on CONFIRMED deliveries.
            if dialogue is not None and locked.generation_id == dialogue.generation_id:
                seen = set(dialogue.constraints.get('_shown_current', []))
                for raw_id in locked.publication_ids:
                    pub = db.get(PublicationVersion, uuid.UUID(raw_id))
                    if pub:
                        seen.add(str(pub.canonical_id))
                dialogue.constraints = {**dialogue.constraints, '_shown_current': sorted(seen)}
        elif response.status_code == 429 and locked.attempts < MAX_SEND_ATTEMPTS:
            retry_after = (data.get('parameters') or {}).get('retry_after', 10)
            retry = max(1, min(int(retry_after), 60))
            locked.status = 'pending'
            locked.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=retry)
        elif 400 <= response.status_code < 500:
            locked.status = 'failed'
            locked.last_error = f'Telegram HTTP {response.status_code}'
        else:
            locked.status = 'ambiguous'
            locked.last_error = f'Неоднозначный ответ Telegram: HTTP {response.status_code}'
    except (httpx.RequestError, ValueError, TypeError) as exc:
        locked.status = 'ambiguous'
        locked.last_error = f'Ответ Telegram неизвестен: {type(exc).__name__}'
    db.commit()
    return locked.status == 'delivered'


def run_once():
    with SessionLocal() as db:
        recover_abandoned(db)
        for message_id, token in claim_batch(db):
            try:
                deliver_one(db, Outbox(id=message_id), reservation_token=token)
            except Exception:
                db.rollback()
                log.exception('Ошибка доставки %s; не повторяем неизвестный результат автоматически', message_id)


def main():
    configure_engine()
    while True:
        try:
            run_once()
        except Exception:
            log.exception('Ошибка sender worker')
        time.sleep(1)


if __name__ == '__main__':
    main()
