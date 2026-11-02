from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.models import AuditLog, Canonical, Occurrence, Offer, PublicationVersion
from app.schemas import PublishIn

def publish(db: Session, payload: PublishIn, actor: str):
    now = datetime.now(timezone.utc)
    if payload.valid_until <= now: raise ValueError('Нельзя опубликовать истёкшую карточку')
    if 'москва' not in (payload.address or '').casefold():
        raise ValueError('В каталоге MVP допустим только подтверждённый адрес Москвы')
    if payload.kind == 'event':
        parent = db.get(Canonical, payload.parent_place_id)
        if parent is None or parent.kind != 'place' or parent.status != 'published':
            raise ValueError('Событие должно ссылаться на опубликованное место')
    if payload.canonical_id:
        canonical = db.get(Canonical,payload.canonical_id,with_for_update=True)
        if canonical is None: raise ValueError('Объект не найден')
        if canonical.kind != payload.kind: raise ValueError('Тип объекта не совпадает')
        if canonical.kind == 'event' and canonical.parent_place_id != payload.parent_place_id:
            raise ValueError('Изменение места события требует отдельной модерации')
    else:
        canonical = Canonical(kind=payload.kind, parent_place_id=payload.parent_place_id)
        db.add(canonical)
        db.flush()
    last = db.scalar(select(PublicationVersion)
                     .where(PublicationVersion.canonical_id == canonical.id)
                     .order_by(PublicationVersion.version.desc()).limit(1))
    version = (last.version if last else 0) + 1
    before = ({'version':last.version, 'publication_id':str(last.id),
               'title': last.title,'address':last.address,'source_url':last.source_url}
              if last else None)
    pub = PublicationVersion(canonical_id=canonical.id,version=version,title=payload.title,
        description=payload.description,category=payload.category,district=payload.district,metro=payload.metro,
        address=payload.address,indoors=payload.indoors,source_url=payload.source_url,booking_url=payload.booking_url,
        opening_hours=payload.opening_hours,calendar_exceptions=payload.calendar_exceptions,
        last_verified_at=payload.last_verified_at,next_verification_at=payload.next_verification_at,
        valid_until=payload.valid_until,index_status='pending')
    db.add(pub)
    db.flush()
    if payload.starts_at:
        occ = Occurrence(publication_id=pub.id, starts_at=payload.starts_at,ends_at=payload.ends_at)
        db.add(occ)
        db.flush()
    else: occ = None
    if payload.price_kopeks is not None:
        db.add(Offer(publication_id=pub.id,occurrence_id=occ.id if occ else None,
            price_kopeks=payload.price_kopeks,price_scope=payload.price_scope,price_is_bounded=payload.price_is_bounded))
    canonical.status='published'
    db.add(AuditLog(actor=actor, action='publish', subject_id=str(canonical.id),
        details={'before':before, 'after':{'version':version,'publication_id':str(pub.id),
                'title':pub.title,'address':pub.address,'source_url':pub.source_url},
                'reason':'manual_verification'}))
    # Фиксация принадлежит вызывающему API: статус черновика и версия
    # публикации должны сохраняться в одной транзакции.
    db.flush()
    return {'canonical_id':canonical.id,'publication_version_id':pub.id,'version':version}
