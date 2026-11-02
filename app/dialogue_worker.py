"""Фоновый обработчик поиска. Сетевые вызовы не блокируют webhook."""
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.db import SessionLocal, configure_engine
from app.models import DialogueSession, RequestOperation, Outbox
from app.schemas import SearchRequest
from app.search import search
from app.llm import extract_with_llm
from app.dialogue import extract_constraints
from app.feedback_ranking import get_behavior_signals, reorder_by_feedback
from app.user_controls import excluded_roots
from app.models import UserPreference
from app.weather import assess_outdoors

log=logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def render_candidates(candidates, *, no_results_reason='NO_MATCH', weather=None):
    """Verified facts only: no fake price, booking status, address, or URL."""
    if not candidates:
        cases = {
            'NO_MATCH': 'По заданным условиям проверенных вариантов нет. Можно изменить район, дату или бюджет — только с твоего согласия.',
            'EXHAUSTED': 'Непоказанные варианты закончились. Можно начать новый подбор или явно изменить условия.',
            'UNKNOWN_REQUIRED_DATA': 'У вариантов не подтверждены обязательные цена или время. Условия не ослаблены.',
            'DEPENDENCY_FAILURE': 'Поиск временно недоступен. Попробуй ещё раз позже; условия сохранены.',
        }
        return cases.get(no_results_reason, cases['NO_MATCH'])
    from zoneinfo import ZoneInfo
    local_tz = ZoneInfo('Europe/Moscow')
    lines = []
    for i, c in enumerate(candidates, 1):
        prefix = '' if getattr(c,'price_is_bounded',False) else 'от '
        price = f'{prefix}{c.price_total_kopeks // 100} ₽ за двоих' if c.price_total_kopeks is not None else 'стоимость не подтверждена'
        date = (f"\nСеанс: {c.starts_at.astimezone(local_tz):%d.%m.%Y %H:%M} МСК" if getattr(c, 'starts_at', None) else '')
        reason = ', '.join(getattr(c, 'reason_codes', [])) if getattr(c, 'reason_codes', None) else 'по условиям подбора'
        detail = (getattr(c, 'description', '') or '').strip()[:250]
        link = c.source_url or ''
        book = f'\nБилеты/бронирование: {c.booking_url}' if getattr(c,'booking_url',None) else ''
        category = getattr(c,'category','место/событие')
        lines.append(f'{i}. {c.title} ({category})\n{detail}\n{c.address or "Адрес не подтверждён"}\n{price}{date}\nПочему: {reason}\nПодробнее: {link}{book}')
    advisory = ''
    if weather:
        if weather.status == 'adverse':
            advisory = '\n\n⚠️ Для прогулок в Москве прогнозируется непогода; проверь условия перед выходом.'
        elif weather.status == 'unknown':
            advisory = '\n\nПрогноз для уличных вариантов недоступен; погода не подтверждена.'
    return 'Вот проверенные варианты:\n\n' + '\n\n'.join(lines) + advisory

LEASE_SECONDS=120
MAX_ATTEMPTS=3

def recover_stale(db, now=None):
    """Возвращаем операции после падения worker; поколения проверяются повторно."""
    now=now or datetime.now(timezone.utc)
    stale=list(db.scalars(select(RequestOperation).where(
        RequestOperation.status=='processing',
        RequestOperation.claimed_at < now-timedelta(seconds=LEASE_SECONDS)
    ).with_for_update(skip_locked=True)))
    for op in stale:
        session=db.get(DialogueSession,op.chat_id)
        if session is None or session.generation_id!=op.generation_id:
            op.status='cancelled'
        elif op.attempts >= MAX_ATTEMPTS:
            op.status='failed';op.last_error='Превышено число попыток обработки'
        else:
            op.status='queued';op.claimed_at=None
        op.claim_token=None
    db.commit()
    return len(stale)

def merge_constraints(previous, message, extracted):
    """Сохраняем смысл между репликами, не доверяя слоту query от LLM без текста."""
    result = extract_constraints(message, previous, resolve_dates=False)
    # Изолированно извлекаем тему из самой реплики. Отдельные слова о дате,
    # бюджете и месте не разрешают LLM придумывать новую тему.
    message_topic = extract_constraints(message, {}, resolve_dates=False).get('query')
    if extracted:
        for k, v in extracted.items():
            if v is None or v == '':
                continue
            # Детерминированно распознанные изменения приоритетнее LLM.
            if k == 'budget_total_kopeks' and result.get(k) != previous.get(k):
                continue
            if k == 'query':
                if not message_topic:
                    continue
                normalized = extract_constraints(str(v), {}, resolve_dates=False).get('query')
                if not normalized:
                    continue
                v = normalized
            if k in ('metro', 'indoors') and result.get(k) != previous.get(k):
                continue
            result[k] = v
    return result

def claim(db):
    op=db.scalar(select(RequestOperation).where(RequestOperation.status=='queued')
                 .order_by(RequestOperation.created_at,RequestOperation.id)
                 .limit(1).with_for_update(skip_locked=True))
    if op:
        op.status='processing'
        op.claimed_at=datetime.now(timezone.utc)
        op.claim_token=str(uuid.uuid4())
        op.attempts+=1
        db.commit()
    return op

def process_one(db, op):
    """Только текущий владелец lease может сохранить результат поиска.

    LLM/поиск выполняются без блокировки строк; перед commit перечитываем запись
    с SELECT FOR UPDATE и сравниваем fencing token. Поздний результат старого
    воркера не перезапишет ответ нового поколения или повторного исполнителя.
    """
    expected_token = op.claim_token
    operation_id = op.id
    if not expected_token:
        return False
    session = db.get(DialogueSession, op.chat_id)
    if session is None or session.generation_id != op.generation_id:
        return _finish_owned(db, operation_id, expected_token, 'cancelled')
    message = op.payload.get('message', '')
    try:
        constraints = merge_constraints(dict(op.payload.get('constraints') or {}),
                                        message, extract_with_llm(message))
        # Exclusions are enforced anew on every request, including /more.
        banned = excluded_roots(db, op.chat_id)
        seen_in_selection = constraints.get('_shown_current', [])
        for raw_id in seen_in_selection:
            try: banned.add(uuid.UUID(raw_id))
            except (ValueError, TypeError): pass
        req = SearchRequest(**{**constraints, 'exclude_canonical_ids': list(banned), 'limit': 3})
        candidates = search(db, req)
        positive, negative, seen = get_behavior_signals(db, op.chat_id)
        profile = db.get(UserPreference, op.chat_id)
        note = (profile.preferences or {}).get('explicit_note','') if profile else ''
        # Interests and format from the current request are soft ranking features.
        note = ' '.join([str(note), *constraints.get('interests', []),
                         str(constraints.get('format') or '')])
        candidates = reorder_by_feedback(candidates, positive, negative, seen,
                                         limit=3, explicit_preferences=note)
        reason = 'EXHAUSTED' if not candidates and seen_in_selection else 'NO_MATCH'
        # If everything is seen, distinguish a fully exhausted selection from no match.
        if not candidates and seen_in_selection:
            remaining = search(db, SearchRequest(**{**constraints,
                'exclude_canonical_ids': list(excluded_roots(db, op.chat_id)), 'limit': 1}))
            if not remaining: reason = 'NO_MATCH'
        elif not candidates and req.budget_total_kopeks is not None:
            # Diagnostic query never delivers items that violate the budget.
            unpriced = search(db, SearchRequest(**{**constraints,
                'budget_total_kopeks': None,
                'exclude_canonical_ids': list(excluded_roots(db, op.chat_id)),
                'limit': 3}))
            if unpriced and all(c.price_total_kopeks is None for c in unpriced):
                reason = 'UNKNOWN_REQUIRED_DATA'
        weather = None
        if candidates and any(getattr(c, 'indoors', None) is False for c in candidates):
            weather = assess_outdoors(req.starts_at, req.ends_at)
        answer = render_candidates(candidates, no_results_reason=reason, weather=weather)
        if not candidates and constraints.get('query'):
            from app.user_controls import explicitly_requested_exclusion
            banned_named = explicitly_requested_exclusion(db, op.chat_id, constraints['query'])
            if banned_named:
                answer = ('Этот объект находится в твоих постоянных исключениях. '
                          f'Если хочешь снова его видеть, отправь /unexclude {banned_named}. '
                          'Сам запрет не снят.')
        ids = [str(c.publication_version_id) for c in candidates]
    except Exception as exc:
        db.rollback()
        log.exception('Ошибка поиска для операции %s', operation_id)
        # Failed requests are not silently discarded; produce a safe fallback response.
        # Keep the failure status for monitoring and do not fabricate recommendations.
        return _finish_owned(db, operation_id, expected_token, 'failed', type(exc).__name__)

    # Один порядок захвата блокировок: операция -> диалог. Обновление generation
    # Telegram webhook сериализуется строкой диалога.
    locked = db.scalar(select(RequestOperation).where(RequestOperation.id == operation_id)
                       .with_for_update().execution_options(populate_existing=True))
    if locked is None or locked.status != 'processing' or locked.claim_token != expected_token:
        db.rollback()
        return False
    session = db.scalar(select(DialogueSession).where(DialogueSession.chat_id == locked.chat_id)
                        .with_for_update().execution_options(populate_existing=True))
    if session is None or session.generation_id != locked.generation_id:
        locked.status = 'cancelled'
        locked.claim_token = None
        locked.claimed_at = None
        db.commit()
        return False
    session.constraints = constraints
    session.state = 'awaiting_feedback'
    locked.status = 'completed'
    locked.claim_token = None
    locked.claimed_at = None
    db.add(Outbox(idempotency_key=f'operation:{locked.id}', chat_id=locked.chat_id,
        generation_id=locked.generation_id, text=answer, publication_ids=ids,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    db.commit()
    return True


def _finish_owned(db, operation_id, token, status, error=None):
    locked = db.scalar(select(RequestOperation).where(RequestOperation.id == operation_id)
                       .with_for_update().execution_options(populate_existing=True))
    if locked is None or locked.status != 'processing' or locked.claim_token != token:
        db.rollback()
        return False
    locked.status = status
    locked.claimed_at = None
    locked.claim_token = None
    locked.last_error = error
    if status == 'failed':
        session = db.get(DialogueSession, locked.chat_id)
        if session is not None and session.generation_id == locked.generation_id:
            session.state = 'failed'
            db.add(Outbox(idempotency_key=f'failure:{locked.id}',
                chat_id=locked.chat_id, generation_id=locked.generation_id,
                text=render_candidates([], no_results_reason='DEPENDENCY_FAILURE'),
                publication_ids=[], expires_at=datetime.now(timezone.utc)+timedelta(minutes=10)))
    db.commit()
    return True

def run_once():
    with SessionLocal() as db:
        recover_stale(db)
        op=claim(db)
        if op: process_one(db,op)

def main():
    configure_engine()
    while True:
        try: run_once()
        except Exception: log.exception('Ошибка dialogue worker')
        time.sleep(1)

if __name__=='__main__':main()
