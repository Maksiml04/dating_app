"""Планирование перепроверки и ручное разрешение без мутации опубликованного snapshot."""
from datetime import datetime, timezone
import uuid
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from app.models import AuditLog, Canonical, PublicationVersion, VerificationTask

DECISIONS = {'verified', 'needs_update', 'unavailable'}

def reason_for_verification(pub: PublicationVersion, now: datetime) -> str | None:
    if pub.valid_until <= now:
        return 'expired'
    if pub.next_verification_at is not None and pub.next_verification_at <= now:
        return 'due'
    return None

def enqueue_due_verifications(db: Session, now: datetime | None = None, batch_size: int = 200) -> int:
    now = now or datetime.now(timezone.utc)
    batch_size = max(1, min(batch_size, 1000))
    latest = (select(PublicationVersion.canonical_id, func.max(PublicationVersion.version).label('version'))
              .group_by(PublicationVersion.canonical_id).subquery())
    existing = select(VerificationTask.publication_id)
    rows = db.scalars(select(PublicationVersion)
        .join(latest, and_(PublicationVersion.canonical_id == latest.c.canonical_id,
                           PublicationVersion.version == latest.c.version))
        .join(Canonical, Canonical.id == PublicationVersion.canonical_id)
        .where(Canonical.status == 'published')
        .where(or_(PublicationVersion.valid_until <= now,
                   PublicationVersion.next_verification_at <= now))
        .where(~PublicationVersion.id.in_(existing))
        .order_by(PublicationVersion.valid_until).limit(batch_size)).all()
    created = 0
    for pub in rows:
        # A due/expired verification invalidates visibility immediately,
        # even if the scheduler was delayed (search has its own time filter).
        obj = db.get(Canonical, pub.canonical_id, with_for_update=True)
        if obj is not None and obj.status == 'published':
            obj.status = 'review_required'
            db.add(AuditLog(actor='verification-scheduler', action='verification_due',
                subject_id=str(obj.id), details={'publication_id':str(pub.id),
                'reason':reason_for_verification(pub, now),
                'before':'published','after':'review_required'}))
        # Уникальность на publication_id: безопасно для параллельных scheduler-воркеров.
        statement = insert(VerificationTask).values(
            id=uuid.uuid4(), publication_id=pub.id,
            reason=reason_for_verification(pub, now), status='pending', created_at=now,
        ).on_conflict_do_nothing(index_elements=['publication_id'])
        created += db.execute(statement).rowcount or 0
    return created

def resolve_verification(db: Session, task: VerificationTask, decision: str, actor: str, notes: str) -> None:
    if task.status != 'pending':
        raise ValueError('Задача уже рассмотрена')
    if decision not in DECISIONS:
        raise ValueError('Неизвестное решение')
    if decision != 'verified' and not notes.strip():
        raise ValueError('Для этого решения нужен комментарий')
    pub = db.get(PublicationVersion, task.publication_id)
    if pub is None:
        raise ValueError('Версия публикации не найдена')
    task.status = decision
    task.reviewed_at = datetime.now(timezone.utc)
    task.reviewed_by = actor
    task.notes = notes[:4000]
    if decision == 'unavailable':
        canonical = db.get(Canonical, pub.canonical_id, with_for_update=True)
        # Не архивируем объект из-за устаревшей перепроверки старой версии.
        latest_version = db.scalar(select(func.max(PublicationVersion.version)).where(PublicationVersion.canonical_id == pub.canonical_id))
        if canonical is not None and pub.version == latest_version:
            canonical.status = 'archived'
    db.add(AuditLog(actor=actor, action='verification_review', subject_id=str(task.id),
                    details={'publication_id': str(pub.id), 'decision': decision, 'notes': notes[:4000]}))
    # verified НЕ продлевает valid_until и НЕ меняет immutable-версию.
    # Для изменения актуальности модератор публикует новую версию.
