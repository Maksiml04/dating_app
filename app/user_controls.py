"""FR-19–21: user-owned permanent exclusions and explicit preference controls."""
from uuid import UUID
from sqlalchemy import select
from app.models import CanonicalAlias, UserExclusion, PublicationVersion, Canonical


def canonical_root(db, canonical_id):
    """Resolve aliases with a cycle guard; never lift an exclusion implicitly."""
    current = canonical_id
    visited = set()
    for _ in range(32):
        if current in visited:
            raise ValueError('Цикл в таблице объединённых объектов')
        visited.add(current)
        target = db.scalar(select(CanonicalAlias.target_id).where(CanonicalAlias.alias_id == current))
        if target is None:
            return current
        current = target
    raise ValueError('Слишком длинная цепочка алиасов')


def excluded_roots(db, chat_id):
    return {canonical_root(db, x) for x in db.scalars(
        select(UserExclusion.canonical_id).where(UserExclusion.chat_id == chat_id)
    )}


def is_excluded(db, chat_id, canonical_id):
    roots = excluded_roots(db, chat_id)
    if canonical_root(db, canonical_id) in roots:
        return True
    canonical = db.get(Canonical, canonical_id)
    return bool(canonical is not None and canonical.parent_place_id and
                canonical_root(db, canonical.parent_place_id) in roots)


def exclude_publication(db, chat_id, publication_id):
    pub = db.get(PublicationVersion, publication_id)
    if pub is None:
        return False
    canonical_id = canonical_root(db, pub.canonical_id)
    exists = db.scalar(select(UserExclusion.id).where(
        UserExclusion.chat_id == chat_id, UserExclusion.canonical_id == canonical_id))
    if exists is None:
        db.add(UserExclusion(chat_id=chat_id, canonical_id=canonical_id))
    return True


def remove_exclusion(db, chat_id, canonical_id: UUID):
    root = canonical_root(db, canonical_id)
    rows = list(db.scalars(select(UserExclusion).where(UserExclusion.chat_id == chat_id)))
    matching = [x for x in rows if canonical_root(db, x.canonical_id) == root]
    for row in matching:
        db.delete(row)
    return bool(matching)


def explicitly_requested_exclusion(db, chat_id, requested_title):
    """FR-04: acknowledge a specifically requested banned object without lifting its ban.

    Generic terms like 'выставка' are intentionally not treated as a named object.
    """
    from sqlalchemy import func
    title = ' '.join((requested_title or '').casefold().split()).strip(' ,.!?')
    if len(title) < 8:
        return None
    roots = excluded_roots(db, chat_id)
    if not roots:
        return None
    # Bounded lookup over the local user's own bans; no global user data returned.
    for canonical_id in roots:
        row = db.scalar(select(PublicationVersion.title).where(
            PublicationVersion.canonical_id == canonical_id
        ).order_by(PublicationVersion.version.desc()).limit(1))
        if row:
            known = ' '.join(row.casefold().split()).strip(' ,.!?')
            if title == known or (len(title) >= 12 and len(title) >= .8 * len(known) and title in known):
                return canonical_id
    return None
