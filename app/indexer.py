"""Индексатор опубликованных карточек: разовый запуск или периодический worker.

  python -m app.indexer            # один проход
  python -m app.indexer --loop     # периодически, как Docker service
"""
import argparse
import logging
import os
import time
from sqlalchemy import text
from app.db import SessionLocal, configure_engine
from app.config import settings
from app.embeddings import embed

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def run(batch_size: int = 100) -> int:
    """Один проход. Требует вызова configure_engine() перед run()."""
    if not settings.embedding_model:
        return 0
    count = 0
    with SessionLocal() as db:
        rows = db.execute(text("""SELECT p.id, p.title, p.description, p.category
            FROM publication_version p JOIN canonical c ON c.id=p.canonical_id
            LEFT JOIN publication_embedding e ON e.publication_id=p.id AND e.model_name=:model
            WHERE c.status='published' AND p.valid_until > now() AND e.publication_id IS NULL
            ORDER BY p.published_at DESC LIMIT :limit"""),
            {'model':settings.embedding_model,'limit':max(1,min(batch_size,500))}).all()
        for row in rows:
            vector = embed(f'{row.title} {row.category} {row.description}')
            if vector is None:
                continue
            literal = '[' + ','.join(str(v) for v in vector) + ']'
            db.execute(text("""INSERT INTO publication_embedding(publication_id, model_name, embedding)
                VALUES (:id, :model, CAST(:vec AS vector)) ON CONFLICT (publication_id)
                DO UPDATE SET model_name=EXCLUDED.model_name, embedding=EXCLUDED.embedding, updated_at=now()"""),
                {'id':row.id,'model':settings.embedding_model,'vec':literal})
            db.execute(text("UPDATE publication_version SET index_status='ready' WHERE id=:id"),
                       {'id':row.id})
            count += 1
        db.commit()
    return count


def main(argv=None):
    parser=argparse.ArgumentParser(description='Индексатор публикаций')
    parser.add_argument('--loop', action='store_true', help='непрерывный режим worker')
    parser.add_argument('--batch-size', type=int, default=100)
    args=parser.parse_args(argv)
    # До v0.22 CLI не вызывал configure_engine и падал с UnboundExecutionError.
    configure_engine()
    if not args.loop:
        log.info('Проиндексировано: %s',run(args.batch_size))
        return
    interval = max(15,int(os.environ.get('INDEX_SCAN_SECONDS','120')))
    if not settings.embedding_model:
        log.info('Векторное индексирование отключено (EMBEDDING_MODEL не настроена)')
    while True:
        try:
            count = run(args.batch_size)
            if count:
                log.info('Проиндексировано карточек: %s',count)
        except Exception:
            log.exception('Ошибка индексации; следующий проход будет повторён')
        time.sleep(interval)


if __name__ == '__main__':
    main()
