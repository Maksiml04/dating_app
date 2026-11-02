"""Reconcile edited Telegram messages without silently mutating published data.

This path only touches known source messages.  A detected content change
invalidates prior moderator approval and any draft generated from that text.
Published snapshots remain immutable and are hidden pending review.
"""
from datetime import datetime, timezone
from sqlalchemy import select
from app.ingestion import fingerprint, preserve_message_lines
from app.models import (AuditLog, Canonical, CatalogDraft, SourceMessage,
                        SourceMessageRevision)


def reconcile_edit(db, source_id, message_id: int, text: str,
                   edit_date: datetime | None, *, force: bool = False) -> tuple[SourceMessage | None, bool]:
    row = db.scalar(select(SourceMessage).where(
        SourceMessage.source_id == source_id,
        SourceMessage.message_id == message_id,
    ).with_for_update())
    if row is None:
        return None, False  # Never introduce old unknown posts during edit scan.
    cleaned = preserve_message_lines(text)
    new_hash = fingerprint(cleaned)
    if not force and row.edited_at and edit_date and edit_date <= row.edited_at:
        return row, False  # Stale copy must not roll back a newer edit.
    if new_hash == row.content_hash and not force:
        if edit_date and (row.edited_at is None or edit_date > row.edited_at):
            row.edited_at = edit_date
        return row, False

    old_revision = row.revision
    if old_revision == 1:
        # Store original for a fully traceable first edit.
        db.add(SourceMessageRevision(source_message_id=row.id, revision=1,
                                     text=row.text, content_hash=row.content_hash,
                                     edit_date=None))
    row.revision = old_revision + 1
    row.text = cleaned
    row.content_hash = new_hash
    row.edited_at = edit_date or datetime.now(timezone.utc)
    row.status = 'pending_review'
    row.reviewed_at = None
    row.reviewed_by = None
    db.add(SourceMessageRevision(source_message_id=row.id, revision=row.revision,
                                 text=cleaned, content_hash=new_hash,
                                 edit_date=row.edited_at))
    draft = db.scalar(select(CatalogDraft).where(CatalogDraft.source_message_id == row.id)
                      .with_for_update())
    if draft:
        draft.status = 'source_changed'
        if draft.canonical_id:
            canonical = db.get(Canonical, draft.canonical_id, with_for_update=True)
            if canonical and canonical.status == 'published':
                # Search and sender only expose status='published'.
                canonical.status = 'review_required'
    db.add(AuditLog(actor='telegram-sync', action='source_message_edited',
                    subject_id=str(row.id), details={
                        'revision': row.revision, 'source_id': str(source_id),
                        'message_id': message_id, 'requires_review': True,
                    }))
    db.flush()
    return row, True
