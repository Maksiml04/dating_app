"""SQL eligibility -> лёгкий lexical ranking -> diversity. Без выдумывания цен или времени."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, func, or_, select, text, extract
from sqlalchemy.orm import Session
from app.models import Canonical, PublicationVersion, Occurrence, Offer
from app.schemas import CandidateOut, SearchRequest
from app.embeddings import embed
from app.ranking import reciprocal_rank_fusion
from app.config import settings

# Stable, explicit centre boundaries for the Moscow MVP. This is not an LLM guess.
CENTER_DISTRICTS = frozenset({
    'арбат','басманный','замоскворечье','красносельский','мещанский',
    'пресненский','таганский','тверской','хамовники','якиманка', 'цао',
})

TIME_WINDOWS = {'morning': (6,12),'day':(12,18),'evening':(18,23),'night':(23,6)}

def occurrence_query(publication_id, req: SearchRequest, now: datetime):
    """Только будущие сеансы; даты из запроса задают полуоткрытый интервал начала.

    Поздний сеанс, заканчивающийся после полуночи, остаётся подходящим.
    """
    stmt = select(Occurrence).where(
        Occurrence.publication_id == publication_id,
        Occurrence.status == 'available',
        Occurrence.starts_at >= now,
    )
    if req.duration_minutes:
        stmt = stmt.where(Occurrence.ends_at >= Occurrence.starts_at + timedelta(minutes=req.duration_minutes))
    if req.starts_at:
        stmt = stmt.where(Occurrence.starts_at >= req.starts_at)
    if req.ends_at:
        stmt = stmt.where(Occurrence.starts_at < req.ends_at)
    if req.time_period:
        start_hour, end_hour = TIME_WINDOWS[req.time_period]
        local_hour = extract('hour', func.timezone('Europe/Moscow', Occurrence.starts_at))
        if start_hour < end_hour:
            stmt = stmt.where(local_hour >= start_hour, local_hour < end_hour)
        else:
            stmt = stmt.where(or_(local_hour >= start_hour, local_hour < end_hour))
    return stmt

def search(db: Session, req: SearchRequest) -> list[CandidateOut]:
    now = datetime.now(timezone.utc)
    latest = (select(PublicationVersion.canonical_id, func.max(PublicationVersion.version).label('v'))
              .group_by(PublicationVersion.canonical_id).subquery())
    stmt = (select(PublicationVersion, Canonical)
        .join(latest, and_(latest.c.canonical_id == PublicationVersion.canonical_id, latest.c.v == PublicationVersion.version))
        .join(Canonical, Canonical.id == PublicationVersion.canonical_id)
        .where(Canonical.status == 'published', PublicationVersion.valid_until > now,
               PublicationVersion.source_url.is_not(None),
               PublicationVersion.address.is_not(None),
               or_(and_(Canonical.kind=='event', PublicationVersion.last_verified_at > now-timedelta(hours=24)),
                   and_(Canonical.kind=='place', PublicationVersion.last_verified_at > now-timedelta(days=30)))))
    if req.exclude_canonical_ids: stmt = stmt.where(~Canonical.id.in_(req.exclude_canonical_ids))
    if req.district:
        if req.district.casefold() in ('центр','цао'):
            stmt = stmt.where(func.lower(PublicationVersion.district).in_(CENTER_DISTRICTS))
        else:
            stmt = stmt.where(func.lower(PublicationVersion.district)==req.district.lower())
    if req.metro: stmt = stmt.where(func.lower(PublicationVersion.metro)==req.metro.lower())
    if req.category: stmt = stmt.where(func.lower(PublicationVersion.category)==req.category.lower())
    if req.indoors is not None: stmt = stmt.where(PublicationVersion.indoors == req.indoors)
    if req.query.strip():
        # PostgreSQL FTS: индексируем заголовок, описание и категорию; запрос экранируется функцией PostgreSQL.
        document = func.to_tsvector('russian', (func.coalesce(PublicationVersion.title, '') + ' ' +
            func.coalesce(PublicationVersion.description, '') + ' ' +
            func.coalesce(PublicationVersion.category, '')))
        query = func.plainto_tsquery('russian', req.query[:500])
        # Не отсекаем семантически релевантные варианты до vector retrieval.
        lexical_score = func.ts_rank_cd(document, query)
    else:
        lexical_score = None
    # Гибридная выборка: lexical rank и vector rank объединяются RRF.
    # SQL eligibility применяется до обеих ветвей; без эмбеддингов используется FTS.
    base = list(db.execute(stmt.order_by(PublicationVersion.published_at.desc()).limit(1000)))
    ordered = base
    if req.query.strip() and base:
        # ts_rank_cd вычисляем в PostgreSQL, не в Python.
        ids = [p.id for p, _ in base]
        lex_rows = db.execute(select(PublicationVersion.id, lexical_score.label('score'))
            .where(PublicationVersion.id.in_(ids)).order_by(lexical_score.desc())).all()
        lexical_ids = [str(pid) for pid, score in lex_rows if score > 0]
        vector_ids = []
        vector = embed(req.query)
        if vector is not None:
            literal = '[' + ','.join(str(v) for v in vector) + ']'
            vector_rows = db.execute(text("""SELECT publication_id FROM publication_embedding
                WHERE model_name=:model AND publication_id = ANY(:ids)
                ORDER BY embedding <=> CAST(:vec AS vector) LIMIT 200"""),
                {'model': settings.embedding_model, 'ids':ids, 'vec':literal}).all()
            vector_ids = [str(row[0]) for row in vector_rows]
        ranked_ids = reciprocal_rank_fusion({'fts':lexical_ids,'vector':vector_ids})
        lookup = {str(p.id):(p,c) for p,c in base}
        ordered = [lookup[x] for x in ranked_ids if x in lookup]
    out = []
    from app.user_controls import canonical_root
    excluded_roots_set = {canonical_root(db, x) for x in req.exclude_canonical_ids}
    for publication, canonical in ordered:
        # Aliases and events hosted at an excluded place must be filtered too.
        if canonical_root(db, canonical.id) in excluded_roots_set: continue
        if canonical.parent_place_id and canonical_root(db, canonical.parent_place_id) in excluded_roots_set: continue
        occ_q = occurrence_query(publication.id, req, now)
        occurrences = list(db.scalars(occ_q.order_by(Occurrence.starts_at).limit(30)))
        if canonical.kind == 'event' and not occurrences: continue
        if canonical.kind == 'place' and (req.starts_at or req.ends_at or req.time_period):
            from app.opening_hours import place_available
            if not place_available(publication.opening_hours, publication.calendar_exceptions,
                starts_at=req.starts_at, ends_at=req.ends_at,
                time_period=req.time_period,duration_minutes=req.duration_minutes,now=now):
                continue
        variants = occurrences if canonical.kind == 'event' else [None]
        accepted = None
        for occ in variants:
            offer_stmt = select(Offer).where(Offer.publication_id == publication.id)
            if occ: offer_stmt = offer_stmt.where(or_(Offer.occurrence_id == occ.id,Offer.occurrence_id.is_(None)))
            else: offer_stmt = offer_stmt.where(Offer.occurrence_id.is_(None))
            offers = list(db.scalars(offer_stmt))
            if not offers: offers = [None]
            for offer in offers:
                price = None if offer is None or offer.price_kopeks is None else offer.price_kopeks * (2 if offer.price_scope == 'person' else 1)
                if req.budget_total_kopeks is not None:
                    if offer is None or not offer.price_is_bounded or price is None or price > req.budget_total_kopeks: continue
                reason = ['проверенная публикация']
                if occ: reason.append('подходящий сеанс')
                elif req.starts_at: reason.append('проверенные часы работы')
                if req.budget_total_kopeks is not None: reason.append('в пределах бюджета')
                accepted = CandidateOut(canonical_id=canonical.id,publication_version_id=publication.id,
                    occurrence_id=occ.id if occ else None, offer_id=offer.id if offer else None,
                    title=publication.title, description=publication.description, category=publication.category,
                    address=publication.address, source_url=publication.source_url,
                    price_total_kopeks=price, price_is_bounded=offer.price_is_bounded if offer else None,
                    valid_until=publication.valid_until,reason_codes=reason,
                    starts_at=occ.starts_at if occ else None,
                    ends_at=occ.ends_at if occ else None,
                    booking_url=publication.booking_url, indoors=publication.indoors)
                break
            if accepted: break
        if accepted: out.append(accepted)
    # Round-robin по категориям против однотипной выдачи
    groups = {}
    for candidate in out: groups.setdefault(candidate.category, []).append(candidate)
    result = []
    while groups and len(result)<req.limit:
        for category in list(groups):
            if groups[category]: result.append(groups[category].pop(0))
            if not groups[category]: groups.pop(category)
            if len(result)>=req.limit: break
    return result
