"""Приём сообщений: идемпотентно, без автоматической публикации."""
from datetime import datetime, timezone
from hashlib import sha256
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import TelegramSource, SourceMessage, AuditLog

def normalize_text(value: str) -> str:
    return ' '.join(value.split())

def preserve_message_lines(value: str) -> str:
    """Сохраняет границы строк для выделения заголовка и контекста."""
    return '\n'.join(line for line in (' '.join(chunk.split()) for chunk in value.splitlines()) if line)

def fingerprint(value: str) -> str:
    return sha256(normalize_text(value).casefold().encode('utf-8')).hexdigest()

def ingest_message(db: Session, source: TelegramSource, message_id: int, text: str, message_date: datetime, source_url: str | None = None, *, allow_empty: bool = False) -> tuple[SourceMessage | None, bool]:
    if message_id <= 0 or message_date.tzinfo is None:
        raise ValueError('Некорректные ID сообщения или дата')
    existing = db.scalar(select(SourceMessage).where(SourceMessage.source_id == source.id, SourceMessage.message_id == message_id))
    if existing:
        return existing, False
    clean = preserve_message_lines(text)
    if not clean and not allow_empty:
        return None, False
    item = SourceMessage(source_id=source.id, message_id=message_id, text=clean, message_date=message_date, source_url=source_url, content_hash=fingerprint(clean), status='pending_review')
    db.add(item)
    source.last_message_id = max(source.last_message_id, message_id)
    db.flush()
    return item, True

def review_message(db: Session, item: SourceMessage, decision: str, actor: str):
    if decision not in ('approved', 'rejected'):
        raise ValueError('Недопустимое решение')
    if item.status != 'pending_review':
        raise ValueError('Сообщение уже обработано')
    item.status = decision
    item.reviewed_at = datetime.now(timezone.utc)
    item.reviewed_by = actor
    db.add(AuditLog(actor=actor,action='source_review',subject_id=str(item.id),details={'decision':decision}))
    # approved означает проверку исходника, НЕ публикацию в каталог.
