from uuid import UUID
from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import select, text as sql_text, func
from sqlalchemy.orm import Session
from app.config import settings
from app.security import verify_secret
from app.db import get_db
from app.catalog import publish
from app.dialogue import process_update
from app.models import Canonical, PublicationVersion, UserPreference
from app.schemas import PublishIn, SearchRequest
from app.search import search

app=FastAPI(title='Куда сходить на свидание — API',version='0.24.0')

def admin_auth(x_admin_key: str | None = Header(None)):
    if not verify_secret(x_admin_key, settings.admin_api_key):
        raise HTTPException(403,'Администратор не авторизован')

@app.get('/health')
def health():
    # Liveness не зависит от PostgreSQL: не рестартуем API из-за временной недоступности БД.
    return {'status': 'ok'}


@app.get('/ready')
def readiness(db: Session = Depends(get_db)):
    # Readiness сообщает об отсутствии БД, но не раскрывает параметры подключения.
    try:
        db.execute(sql_text('SELECT 1'))
    except Exception as exc:
        raise HTTPException(503, 'База данных недоступна') from exc
    return {'status': 'ready'}


@app.post('/api/search')
def api_search(payload: SearchRequest,db:Session=Depends(get_db)):
    return search(db,payload)

@app.post('/api/admin/publications',dependencies=[Depends(admin_auth)])
def api_publish(payload:PublishIn,db:Session=Depends(get_db)):
    try:
        result = publish(db,payload,actor='api-admin')
        db.commit()
        return result
    except ValueError as exc:
        db.rollback();raise HTTPException(422,str(exc)) from exc

@app.post('/api/admin/canonical/{canonical_id}/archive',dependencies=[Depends(admin_auth)])
def archive(canonical_id: UUID,db:Session=Depends(get_db)):
    c=db.get(Canonical,canonical_id,with_for_update=True)
    if not c: raise HTTPException(404,'Не найдено')
    before = c.status
    c.status='archived'
    from app.models import AuditLog
    db.add(AuditLog(actor='api-admin', action='archive', subject_id=str(c.id),
                    details={'before': before, 'after': 'archived'}))
    db.commit()
    return {'status':'archived'}

@app.post('/api/telegram/webhook')
def webhook(payload:dict,x_telegram_bot_api_secret_token:str|None=Header(None),db:Session=Depends(get_db)):
    if not verify_secret(x_telegram_bot_api_secret_token, settings.telegram_webhook_secret):
        raise HTTPException(403,'Некорректный секрет webhook')
    if not isinstance(payload.get('update_id'),int): raise HTTPException(422,'Отсутствует update_id')
    try: accepted=process_update(db,payload)
    except Exception:
        db.rollback();raise
    return {'ok':True,'accepted':accepted}

@app.put('/api/admin/preferences/{chat_id}',dependencies=[Depends(admin_auth)])
def set_preferences(chat_id:int,preferences:dict,db:Session=Depends(get_db)):
    p=db.get(UserPreference,chat_id)
    if p is None: p=UserPreference(chat_id=chat_id,preferences=preferences);db.add(p)
    else: p.preferences=preferences
    db.commit()
    return {'ok':True}

# Модерация исходных Telegram-сообщений. Публикация в каталог — отдельное действие.
from pydantic import BaseModel, Field
from app.models import TelegramSource, SourceMessage
from app.ingestion import review_message

class SourceCreate(BaseModel):
    channel_id: int
    username: str | None = None

class ReviewDecision(BaseModel):
    decision: str

@app.post('/api/admin/sources', dependencies=[Depends(admin_auth)])
def add_source(payload: SourceCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(TelegramSource).where(TelegramSource.channel_id == payload.channel_id))
    if existing:
        raise HTTPException(409, 'Источник уже зарегистрирован')
    source = TelegramSource(channel_id=payload.channel_id, username=payload.username)
    db.add(source)
    db.commit()
    return {'id': source.id, 'channel_id': source.channel_id}

@app.get('/api/admin/source-messages', dependencies=[Depends(admin_auth)])
def list_source_messages(status: str = 'pending_review', limit: int = 50, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 100))
    rows = db.scalars(select(SourceMessage).where(SourceMessage.status == status).order_by(SourceMessage.received_at).limit(limit)).all()
    return [{'id': str(r.id), 'text': r.text, 'source_url': r.source_url, 'status': r.status, 'message_date': r.message_date} for r in rows]

@app.post('/api/admin/source-messages/{message_id}/review', dependencies=[Depends(admin_auth)])
def review_source_message(message_id: UUID, payload: ReviewDecision, db: Session = Depends(get_db)):
    item = db.get(SourceMessage, message_id, with_for_update=True)
    if not item:
        raise HTTPException(404, 'Сообщение не найдено')
    try:
        review_message(db, item, payload.decision, actor='api-admin')
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return {'id': item.id, 'status': item.status}


# Черновики создаются только после проверки исходного сообщения.
from app.models import CatalogDraft
from app.drafts import prepare_draft

@app.post('/api/admin/source-messages/{message_id}/draft', dependencies=[Depends(admin_auth)])
def create_catalog_draft(message_id: UUID, db: Session = Depends(get_db)):
    item = db.get(SourceMessage, message_id)
    if not item:
        raise HTTPException(404, 'Сообщение не найдено')
    if item.status != 'approved':
        raise HTTPException(409, 'Сначала проверьте исходное сообщение')
    draft = prepare_draft(db, item)
    db.commit()
    return {'id': draft.id, 'status': draft.status, 'extracted': draft.extracted, 'duplicate_candidates': draft.duplicate_candidates}

@app.get('/api/admin/drafts', dependencies=[Depends(admin_auth)])
def list_catalog_drafts(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.scalars(select(CatalogDraft).where(CatalogDraft.status == 'needs_review').limit(max(1,min(limit,100)))).all()
    return [{'id': d.id, 'source_message_id': d.source_message_id, 'extracted': d.extracted, 'duplicate_candidates': d.duplicate_candidates} for d in rows]

class DraftPublish(BaseModel):
    publication: PublishIn

@app.post('/api/admin/drafts/{draft_id}/publish', dependencies=[Depends(admin_auth)])
def publish_catalog_draft(draft_id: UUID, payload: DraftPublish, db: Session = Depends(get_db)):
    draft = db.get(CatalogDraft, draft_id, with_for_update=True)
    if not draft:
        raise HTTPException(404, 'Черновик не найден')
    if draft.status != 'needs_review':
        raise HTTPException(409, 'Черновик уже обработан')
    # При повторной модерации изменённого источника необходимо выпускать
    # новую версию ТОГО ЖЕ объекта, иначе устаревшая карточка останется скрытой.
    if draft.canonical_id and payload.publication.canonical_id != draft.canonical_id:
        raise HTTPException(409, 'Для изменённого источника укажите исходный canonical_id')
    # Source could have been revised after the draft was prepared.
    source = db.get(SourceMessage, draft.source_message_id, with_for_update=True)
    if source is None or source.status != 'approved':
        raise HTTPException(409, 'Исходная публикация требует повторного одобрения')
    # Не доверяем извлечённым данным: PublishIn требует явно проверенные дату, срок и цену.
    try:
        result = publish(db, payload.publication, actor='api-admin')
        draft.status = 'published'
        draft.canonical_id = result['canonical_id']
        from app.models import AuditLog
        db.add(AuditLog(actor='api-admin',action='draft_published',subject_id=str(draft.id),
            details={'source_message_id': str(draft.source_message_id),
                     'publication_version_id':str(result['publication_version_id']),
                     'evidence': (draft.extracted or {}).get('evidence', {})}))
        db.commit()
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc

# Reopen a rejected draft only on explicit administrator action.
@app.post('/api/admin/drafts/{draft_id}/reopen', dependencies=[Depends(admin_auth)])
def reopen_catalog_draft(draft_id: UUID, db: Session = Depends(get_db)):
    from app.models import AuditLog
    draft = db.get(CatalogDraft, draft_id, with_for_update=True)
    if draft is None:
        raise HTTPException(404, 'Черновик не найден')
    if draft.status != 'rejected':
        raise HTTPException(409, 'Только отклонённый черновик можно вернуть на проверку')
    source = db.get(SourceMessage, draft.source_message_id, with_for_update=True)
    if source is None or source.status != 'approved':
        raise HTTPException(409, 'Необходимо повторно проверить исходную публикацию')
    draft.status = 'needs_review'
    db.add(AuditLog(actor='api-admin', action='draft_reopened', subject_id=str(draft.id),
                    details={'before':'rejected','after':'needs_review'}))
    db.commit()
    return {'id': draft.id, 'status': draft.status}

# Отклонение черновика — отдельный проверяемый переход с записью аудита.
from app.draft_moderation import reject_draft

class DraftReject(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)

@app.post('/api/admin/drafts/{draft_id}/reject', dependencies=[Depends(admin_auth)])
def reject_catalog_draft(draft_id: UUID, payload: DraftReject, db: Session = Depends(get_db)):
    draft = db.get(CatalogDraft, draft_id, with_for_update=True)
    if draft is None:
        raise HTTPException(404, 'Черновик не найден')
    try:
        reject_draft(db, draft, actor='api-admin', reason=payload.reason)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return {'id': draft.id, 'status': draft.status}

# SQLAdmin — только при настроенной авторизации.
from app.admin import install_admin
install_admin(app)

# Перепроверка публикаций. Решение модератора не продлевает исходный snapshot.
from app.models import VerificationTask
from app.verification import enqueue_due_verifications, resolve_verification

class VerificationDecision(BaseModel):
    decision: str
    notes: str = ''

@app.post('/api/admin/verifications/schedule', dependencies=[Depends(admin_auth)])
def schedule_verifications(db: Session = Depends(get_db)):
    try:
        count = enqueue_due_verifications(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {'created': count}

@app.get('/api/admin/verifications', dependencies=[Depends(admin_auth)])
def list_verifications(status: str = 'pending', limit: int = 50, db: Session = Depends(get_db)):
    if status not in ('pending','verified','needs_update','unavailable'):
        raise HTTPException(422, 'Недопустимый статус')
    tasks = db.scalars(select(VerificationTask).where(VerificationTask.status == status)
                       .order_by(VerificationTask.created_at).limit(max(1, min(limit, 100)))).all()
    return [{'id': t.id, 'publication_id': t.publication_id, 'reason': t.reason,
             'status': t.status, 'created_at': t.created_at} for t in tasks]

@app.post('/api/admin/verifications/{task_id}/resolve', dependencies=[Depends(admin_auth)])
def review_verification(task_id: UUID, payload: VerificationDecision, db: Session = Depends(get_db)):
    task = db.get(VerificationTask, task_id, with_for_update=True)
    if not task:
        raise HTTPException(404, 'Задача не найдена')
    try:
        resolve_verification(db, task, payload.decision, 'api-admin', payload.notes)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return {'id': task.id, 'status': task.status}


# Диагностика очередей доступна только администратору, без payload, ключей и PII.
from app.models import RequestOperation, Outbox

@app.get('/api/admin/diagnostics/queues', dependencies=[Depends(admin_auth)])
def diagnostics_queues(db: Session = Depends(get_db)):
    operation_rows = db.execute(select(RequestOperation.status, func.count(RequestOperation.id)).group_by(RequestOperation.status)).all()
    outbox_rows = db.execute(select(Outbox.status, func.count(Outbox.id)).group_by(Outbox.status)).all()
    return {'operations': {status: count for status, count in operation_rows},
            'outbox': {status: count for status, count in outbox_rows}}

# Private media preview for moderator: S3 server is not exposed to the internet.
from fastapi import Response
from app.models import SourceMedia
from app.media_storage import client as minio_client

@app.get('/api/admin/source-messages/{message_id}/photo', dependencies=[Depends(admin_auth)])
def source_message_photo(message_id: UUID, db: Session = Depends(get_db)):
    media = db.scalar(select(SourceMedia).where(SourceMedia.source_message_id == message_id))
    if not media:
        raise HTTPException(404, 'Фотография не найдена')
    try:
        obj = minio_client().get_object(Bucket=settings.minio_bucket, Key=media.object_key)
        try:
            data = obj['Body'].read(settings.telegram_media_max_bytes + 1)
        finally:
            obj['Body'].close()
    except Exception as exc:
        raise HTTPException(503, 'Медиа-хранилище недоступно') from exc
    if len(data) > settings.telegram_media_max_bytes:
        raise HTTPException(422, 'Слишком большое изображение')
    return Response(content=data, media_type=media.content_type,
                    headers={'Cache-Control': 'private, no-store',
                             'X-Content-Type-Options': 'nosniff'})
