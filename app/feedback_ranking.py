"""Мягкая персонализация: реакции никогда не ослабляют жёсткие ограничения поиска."""
from collections import Counter


def reorder_by_feedback(candidates, positive_categories=(), negative_categories=(),
                        seen_publication_ids=(), limit=3, explicit_preferences=''):
    likes=Counter(positive_categories)
    dislikes=Counter(negative_categories)
    seen={str(x) for x in seen_publication_ids}
    # Stored notes are soft evidence, not a hidden hard condition.
    preference_words = {w.casefold() for w in str(explicit_preferences).split()
                        if len(w) >= 4} 
    indexed=list(enumerate(candidates))
    def score(item):
        i, c=item
        # Небольшой бонус/штраф; новые категории не исчезают из выдачи.
        relevance=min(likes[c.category], 3)*2-min(dislikes[c.category], 3)*2
        repeat_penalty=3 if str(c.publication_version_id) in seen else 0
        content = f"{getattr(c,'category','')} {getattr(c,'title','')} {getattr(c,'description','')}".casefold()
        note_bonus = min(2, sum(word in content for word in preference_words))
        return (-relevance-note_bonus+repeat_penalty, i)
    return [candidate for _,candidate in sorted(indexed,key=score)[:limit]]


def get_behavior_signals(db, chat_id):
    from sqlalchemy import select
    from app.models import Feedback, Impression, PublicationVersion
    # Одна реакция на карточку: последняя по времени не гарантирована моделью;
    # агрегируем все реакции как мягкие сигналы, не как жёсткий запрет.
    positive=[];negative=[]
    rows=db.execute(select(Feedback.reaction,PublicationVersion.category)
        .join(PublicationVersion,PublicationVersion.id==Feedback.publication_id)
        .where(Feedback.chat_id==chat_id)).all()
    for reaction,category in rows:
        if reaction=='like': positive.append(category)
        elif reaction=='dislike': negative.append(category)
    seen=list(db.scalars(select(Impression.publication_id).where(Impression.chat_id==chat_id)
        .order_by(Impression.sent_at.desc()).limit(100)))
    return positive,negative,seen
