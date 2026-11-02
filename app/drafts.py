"""Консервативное извлечение, обоснованный поиск дублей и черновики для модератора."""
import re
from difflib import SequenceMatcher
from typing import Callable
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.models import CatalogDraft, SourceMessage, PublicationVersion, Canonical
from app.catalog_extraction import extract_with_llm

PRICE = re.compile(r"(?<!\d)(\d[\d \u00a0]{0,8})\s*(?:₽|руб(?:лей|ля|\.)?)", re.I)
DATE = re.compile(r"(?<!\d)([0-3]?\d)[./]([01]?\d)(?:[./](\d{4}))?(?!\d)")
TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")

def extract_fields(text: str) -> dict:
    clean = ' '.join(text.split())
    title = next((x.strip(' -—:') for x in text.splitlines() if x.strip()), '')[:300]
    price = PRICE.search(clean)
    date = DATE.search(clean)
    time = TIME.search(clean)
    return {
        'title': title, 'description': text[:6000],
        'price_kopeks': int(price.group(1).replace(' ', '').replace('\u00a0', '')) * 100 if price else None,
        'date_hint': date.group(0) if date else None,
        'time_hint': time.group(0) if time else None,
        'kind': 'event' if date or time else None,
        'category': None, 'address': None,
        'needs_human_verification': True,
        'extraction_method': 'heuristic',
    }

def normalize_name(value: str) -> str:
    value = value.casefold().replace('ё', 'е')
    return ' '.join(re.findall(r'[\w]+', value, flags=re.UNICODE))

def duplicate_similarity(extracted: dict, published: PublicationVersion) -> tuple[float, list[str]]:
    title = normalize_name(extracted.get('title') or '')
    other = normalize_name(published.title)
    if not title or not other:
        return 0., []
    title_score = SequenceMatcher(None, title, other).ratio()
    # Схожий заголовок сам по себе — только вероятный дубль, не автоматический merge.
    reasons = ['сходное название'] if title_score >= .72 else []
    score = title_score
    address = normalize_name(extracted.get('address') or '')
    other_address = normalize_name(published.address or '')
    if address and other_address:
        address_score = SequenceMatcher(None, address, other_address).ratio()
        if address_score >= .85:
            score = min(1., score + .14)
            reasons.append('совпадает адрес')
        elif address_score < .4:
            score = max(0., score - .25)
            reasons.append('адрес отличается')
    return score, reasons

def candidate_duplicates(db: Session, title_or_extracted: str | dict, limit: int = 5) -> list[dict]:
    extracted = {'title': title_or_extracted} if isinstance(title_or_extracted, str) else title_or_extracted
    if not extracted.get('title', '').strip():
        return []
    latest = (select(PublicationVersion.canonical_id, func.max(PublicationVersion.version).label('version'))
              .group_by(PublicationVersion.canonical_id).subquery())
    rows = db.scalars(select(PublicationVersion).join(latest, (latest.c.canonical_id == PublicationVersion.canonical_id) & (latest.c.version == PublicationVersion.version))
                      .join(Canonical, Canonical.id == PublicationVersion.canonical_id)
                      .where(Canonical.status == 'published')
                      .order_by(PublicationVersion.published_at.desc())
                      .limit(1000)).all()
    ranked = []
    for row in rows:
        score, reasons = duplicate_similarity(extracted, row)
        if score >= .72:
            ranked.append({'canonical_id': str(row.canonical_id), 'publication_version_id': str(row.id),
                           'similarity': round(score, 3), 'reasons': reasons})
    return sorted(ranked, key=lambda x: -x['similarity'])[:limit]

def combine_extraction(text: str, extractor: Callable[[str], dict] = extract_with_llm) -> dict:
    heuristic = extract_fields(text)
    claims = extractor(text)
    if not claims:
        return heuristic
    # К каждому полю допускаются только верифицированные цитатой утверждения из catalog_extraction.
    # Здесь не доверяем пользовательскому extractor, повторно проверяем claims.
    from app.catalog_extraction import validate_claims
    verified = validate_claims(text[:12000], claims)
    used = False
    for field in ('title', 'kind', 'category', 'address', 'price_kopeks', 'date_hint', 'time_hint'):
        if field in verified:
            heuristic[field] = verified[field]
            used = True
    if used:
        heuristic['extraction_method'] = 'llm_evidence_checked'
        heuristic['evidence'] = verified['evidence']
    return heuristic

def prepare_draft(db: Session, item: SourceMessage, extractor: Callable[[str], dict] = extract_with_llm) -> CatalogDraft:
    if item.status != 'approved':
        raise ValueError('Сообщение должно быть проверено модератором')
    existing = db.scalar(select(CatalogDraft).where(CatalogDraft.source_message_id == item.id))
    if existing and existing.status != 'source_changed':
        return existing
    extracted = combine_extraction(item.text, extractor)
    if existing:
        existing.extracted = extracted
        existing.duplicate_candidates = candidate_duplicates(db, extracted)
        existing.status = 'needs_review'
        db.flush()
        return existing
    draft = CatalogDraft(source_message_id=item.id, extracted=extracted,
                         duplicate_candidates=candidate_duplicates(db, extracted))
    db.add(draft)
    db.flush()
    return draft
