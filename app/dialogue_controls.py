"""Пользовательские сводки поверх истории реакций."""

from sqlalchemy import func, select

from app.models import Canonical, Feedback, PublicationVersion


def liked_summary(db, chat_id: int, *, limit: int = 10) -> str:
    """Список последних мест, отмеченных 👍. Только проверенные заголовки."""
    latest = (select(PublicationVersion.canonical_id,
                     func.max(PublicationVersion.version).label('v'))
              .group_by(PublicationVersion.canonical_id).subquery())
    rows = db.execute(
        select(PublicationVersion.title, Canonical.kind, Feedback.created_at)
        .select_from(Feedback)
        .join(PublicationVersion, PublicationVersion.id == Feedback.publication_id)
        .join(Canonical, Canonical.id == PublicationVersion.canonical_id)
        .join(latest, (latest.c.canonical_id == PublicationVersion.canonical_id) &
              (latest.c.v == PublicationVersion.version))
        .where(Feedback.chat_id == chat_id, Feedback.reaction == 'like')
        .order_by(Feedback.created_at.desc()).limit(limit)).all()
    if not rows:
        return 'Пока нет отметок 👍. Нажми «Подходит» под карточкой — и место сохранится здесь.'
    kind_label = {'place': 'место', 'event': 'событие'}
    lines = [f'{i}. {title} ({kind_label.get(kind, "место/событие")})'
             for i, (title, kind, _created) in enumerate(rows, 1)]
    return 'Твои отметки 👍 (последние):\n' + '\n'.join(lines)
