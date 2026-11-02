"""Периодический worker перепроверки: только создаёт задачи для модератора."""
import logging
import os
import time
from app.db import SessionLocal, configure_engine
from app.verification import enqueue_due_verifications

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

def main():
    interval = max(60, int(os.environ.get('VERIFICATION_SCAN_SECONDS', '3600')))
    configure_engine()
    while True:
        try:
            with SessionLocal() as db:
                count = enqueue_due_verifications(db)
                db.commit()
            log.info('Создано задач перепроверки: %s', count)
        except Exception:
            log.exception('Ошибка планирования перепроверки: повторная попытка после паузы')
        time.sleep(interval)

if __name__ == '__main__':
    main()
