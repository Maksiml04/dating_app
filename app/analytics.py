"""Продуктовая аналитика по разделу 9 спеки.

Событие записывается в той же транзакции, что и породившее его действие:
аналитика согласована с фактом доставки/реакции и откатывается вместе с ним.
Запрещены PII: пользователь псевдонимизирован HMAC-хешем (соль в ANALYTICS_SALT,
без соли user_hash не пишется); сырые тексты и полные URL не включаются.
"""
import hashlib
import hmac
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.config import settings
from app.models import AnalyticsEvent

log = logging.getLogger(__name__)
SCHEMA_VERSION = 1

def user_hash(chat_id) -> str | None:
    salt = settings.analytics_salt
    if not salt or chat_id is None:
        return None
    return hmac.new(salt.encode(), str(chat_id).encode(), hashlib.sha256).hexdigest()[:32]

def record(db, name: str, *, chat_id=None, request_id=None, **payload) -> None:
    """Добавляет событие в текущую транзакцию; commit выполняет вызывающий код."""
    payload = {k: v for k, v in payload.items() if v is not None}
    db.add(AnalyticsEvent(name=name[:60], user_hash=user_hash(chat_id),
        request_id=str(request_id)[:64] if request_id else None,
        environment=settings.environment[:20], is_test=settings.is_test,
        schema_version=SCHEMA_VERSION, payload=payload))

def compute_summary(db, *, days: int = 14, now=None) -> dict:
    """Метрики B1 (успешный выбор) и B5 (пустая выдача) по событиям."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=max(1, min(days, 365)))
    rows = db.execute(select(AnalyticsEvent.name, func.count(AnalyticsEvent.id))
        .where(AnalyticsEvent.occurred_at >= since)
        .group_by(AnalyticsEvent.name)).all()
    counts = {name: count for name, count in rows}
    requests = counts.get('request_started', 0)
    delivered = counts.get('recommendation_delivered', 0)
    no_results = counts.get('no_results', 0)
    likes = counts.get('recommendation_feedback_like', 0)
    return {
        'window_days': max(1, min(days, 365)),
        'requests': requests,
        'deliveries': delivered,
        'feedback_likes': likes,
        'feedback_excludes': counts.get('recommendation_feedback_exclude', 0),
        'no_results': no_results,
        'alternatives_requested': counts.get('alternatives_requested', 0),
        'b1_success_rate': round(likes / delivered, 4) if delivered else None,
        'b5_empty_rate': round(no_results / requests, 4) if requests else None,
    }
