"""NFR-08: очистка сырых данных по срокам хранения.

Сырые диалоги (telegram_inbox.payload) — 30 дней, аналитика — 90 дней,
терминальные записи очереди отправки — 30 дней. Исключения, предпочтения и
история реакций хранятся до /delete_data (профиль, а не сырой диалог).
Неоднозначные отправки (ambiguous) не удаляются: они требуют ручной проверки.
"""
import argparse
import logging
import os
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal, configure_engine
from app.models import AnalyticsEvent, CallbackAnswer, FeedbackAction, Inbox, Outbox

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def run_once(db, *, now=None) -> dict:
    now = now or datetime.now(UTC)
    dialogue_cutoff = now - timedelta(days=settings.retention_dialogue_days)
    analytics_cutoff = now - timedelta(days=settings.retention_analytics_days)
    stats = {}
    stats['inbox'] = db.execute(delete(Inbox).where(Inbox.received_at < dialogue_cutoff)).rowcount
    stats['analytics_event'] = db.execute(
        delete(AnalyticsEvent).where(AnalyticsEvent.occurred_at < analytics_cutoff)).rowcount
    # Outbox удаляем только в терминальном состоянии без открытых вопросов.
    terminal = ['delivered', 'cancelled', 'failed']
    old_ids = list(db.scalars(select(Outbox.id).where(
        Outbox.status.in_(terminal), Outbox.expires_at < dialogue_cutoff).limit(1000)))
    if old_ids:
        db.execute(delete(FeedbackAction).where(FeedbackAction.outbox_id.in_(old_ids)))
        stats['outbox'] = db.execute(delete(Outbox).where(Outbox.id.in_(old_ids))).rowcount
    stats['callback_answer'] = db.execute(
        delete(CallbackAnswer).where(
            CallbackAnswer.status.in_(['delivered', 'failed', 'expired']),
            CallbackAnswer.created_at < dialogue_cutoff)).rowcount
    db.commit()
    return stats


def main(argv=None):
    parser = argparse.ArgumentParser(description='Retention worker (NFR-08)')
    parser.add_argument('--loop', action='store_true', help='непрерывный режим worker')
    parser.add_argument('--once', action='store_true', help='один проход')
    args = parser.parse_args(argv)
    configure_engine()
    if not args.loop:
        with SessionLocal() as db:
            log.info('Удалено: %s', run_once(db))
        return
    interval = max(60, int(os.environ.get('RETENTION_SCAN_SECONDS', '86400')))
    while True:
        try:
            with SessionLocal() as db:
                stats = run_once(db)
            if any(stats.values()):
                log.info('Retention: удалено %s', stats)
        except Exception:
            log.exception('Ошибка retention worker; следующий проход повторится')
        time.sleep(interval)


if __name__ == '__main__':
    main()
