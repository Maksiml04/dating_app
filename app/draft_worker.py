"""Фоновая подготовка черновиков ТОЛЬКО из одобренных сообщений.

После сохранения Telegram-сообщения никакие карточки в каталог не публикуются.
Модератор сначала одобряет источник, затем этот worker создаёт черновик.
"""
import logging
import os
import time
from sqlalchemy import select, or_
from app.db import SessionLocal, configure_engine
from app.drafts import prepare_draft
from app.models import CatalogDraft, SourceMessage

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

def process_approved_messages(limit: int = 20) -> int:
    processed = 0
    with SessionLocal() as db:
        messages = db.scalars(
            select(SourceMessage)
            .outerjoin(CatalogDraft, CatalogDraft.source_message_id == SourceMessage.id)
            .where(SourceMessage.status == 'approved',
                   or_(CatalogDraft.id.is_(None), CatalogDraft.status == 'source_changed'))
            .order_by(SourceMessage.reviewed_at)
            .with_for_update(skip_locked=True)
            .limit(max(1, min(limit, 100)))
        ).all()
        for item in messages:
            try:
                prepare_draft(db, item)
                db.commit()
                processed += 1
            except Exception:
                db.rollback()
                log.exception('Не удалось подготовить черновик из сообщения %s', item.id)
    return processed

def main():
    configure_engine()
    interval = max(15, int(os.environ.get('DRAFT_SCAN_SECONDS','60')))
    while True:
        try:
            count = process_approved_messages()
            if count:
                log.info('Подготовлено черновиков: %s', count)
        except Exception:
            log.exception('Ошибка прохода подготовки черновиков')
        time.sleep(interval)

if __name__ == '__main__':
    main()
