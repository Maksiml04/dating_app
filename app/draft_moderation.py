"""Действия модератора над черновиком: только допустимые переходы, аудит."""
from sqlalchemy.orm import Session
from app.models import CatalogDraft, AuditLog


def reject_draft(db: Session, draft: CatalogDraft, *, actor: str, reason: str) -> None:
    reason = reason.strip()
    if draft.status != 'needs_review':
        raise ValueError('Черновик уже обработан')
    if not 3 <= len(reason) <= 1000:
        raise ValueError('Укажите причину отклонения (3–1000 символов)')
    draft.status = 'rejected'
    db.add(AuditLog(actor=actor, action='draft_rejected', subject_id=str(draft.id),
                    details={'reason': reason, 'source_message_id': str(draft.source_message_id)}))
