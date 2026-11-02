"""Acknowledgements for inline Telegram buttons, decoupled from webhook DB commit.

Unlike sending messages, retrying answerCallbackQuery has no business side effect.
We retry short-lived acks and stop when Telegram's callback lifetime expires.
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.config import settings
from app.models import CallbackAnswer

log = logging.getLogger(__name__)
MAX_AGE_SECONDS = 75
MAX_ATTEMPTS = 3


def queue_answer(db, callback_id: str, text: str):
    if not callback_id or len(callback_id) > 120:
        return
    if db.get(CallbackAnswer, callback_id) is None:
        db.add(CallbackAnswer(callback_id=callback_id, text=text[:200]))


def take_answers(db, limit: int = 20, *, now=None):
    now = now or datetime.now(timezone.utc)
    rows = db.scalars(select(CallbackAnswer).where(
        (CallbackAnswer.status == 'pending') |
        ((CallbackAnswer.status == 'sending') & (CallbackAnswer.claimed_at < now - timedelta(seconds=12)))
    ).order_by(CallbackAnswer.created_at).limit(limit)
      .with_for_update(skip_locked=True)).all()
    ids = []
    for row in rows:
        age = (now - row.created_at).total_seconds()
        if age > MAX_AGE_SECONDS or row.attempts >= MAX_ATTEMPTS:
            row.status = 'expired'
        else:
            row.status = 'sending'
            row.claimed_at = now
            row.attempts += 1
            ids.append(row.callback_id)
    db.commit()
    return ids


def deliver_answer(db, callback_id: str, *, transport=None):
    row = db.get(CallbackAnswer, callback_id)
    if row is None or row.status != 'sending':
        return False
    if not settings.telegram_bot_token:
        row.status = 'failed'
        row.last_error = 'TELEGRAM_BOT_TOKEN missing'
        db.commit()
        return False
    url = f'https://api.telegram.org/bot{settings.telegram_bot_token}/answerCallbackQuery'
    try:
        if transport is None:
            with httpx.Client(timeout=5.0) as client:
                response = client.post(url, json={'callback_query_id': callback_id,
                                                   'text': row.text, 'show_alert': False})
        else:
            response = transport(url, {'callback_query_id': callback_id, 'text': row.text,
                                       'show_alert': False})
        result = response.json()
        if response.is_success and result.get('ok') is True:
            row.status = 'delivered'
        elif response.status_code == 400:
            row.status = 'expired'  # Invalid/too-old query; do not retry.
            row.last_error = 'Telegram rejected callback (HTTP 400)'
        else:
            row.status = 'pending'
            row.last_error = f'Telegram HTTP {response.status_code}'
    except (httpx.RequestError, ValueError, TypeError) as exc:
        row.status = 'pending'
        row.last_error = f'{type(exc).__name__} while acknowledging callback'
    db.commit()
    return row.status == 'delivered'


def run_callback_answers(db):
    for callback_id in take_answers(db):
        try:
            deliver_answer(db, callback_id)
        except Exception:
            db.rollback()
            log.exception('Callback ACK failed, will retry')


def main():
    import time
    from app.db import SessionLocal, configure_engine
    configure_engine()
    while True:
        try:
            with SessionLocal() as db:
                run_callback_answers(db)
        except Exception:
            log.exception('Failed to process Telegram callbacks')
        time.sleep(0.5)


if __name__ == '__main__':
    main()
