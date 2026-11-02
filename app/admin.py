"""Защищённая административная панель. Публикации и аудит только для чтения."""
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from html import escape
import secrets
import hmac
from uuid import UUID
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqladmin import Admin, ModelView, action, expose
from sqladmin.authentication import AuthenticationBackend
from app.config import settings
from app.security import verify_admin_credentials
from app.db import configure_engine, SessionLocal
from app.models import (TelegramSource, SourceMessage, CatalogDraft, Canonical,
                        PublicationVersion, Occurrence, Offer, AuditLog, VerificationTask)


class AdminAuthentication(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        password = str(form.get('password', ''))
        username = str(form.get('username', ''))
        if not settings.admin_session_secret:
            return False
        valid = verify_admin_credentials(username, password, settings)
        if valid:
            request.session.update({'admin_authenticated': True, 'admin_username': settings.admin_username})
        return valid

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return bool((settings.admin_password or settings.admin_password_hash) and request.session.get('admin_authenticated') is True)



def _one_pk(request: Request):
    ids = [item.strip() for item in request.query_params.get('pks', '').split(',') if item.strip()]
    if len(ids) != 1:
        raise HTTPException(422, 'Выберите одну запись для проверки')
    try:
        return UUID(ids[0])
    except ValueError as exc:
        raise HTTPException(422, 'Неверный ID') from exc


def _form_page(request: Request, heading: str, target: str, record_id, fields: str):
    """GET action renders safe form; all changes are POST with session-bound CSRF."""
    token = secrets.token_urlsafe(32)
    request.session['moderation_csrf'] = token
    return HTMLResponse('''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Модерация</title>
    <body style="font:16px system-ui;max-width:780px;margin:40px auto;padding:24px">
    <h1>''' + escape(heading) + '''</h1><form method="post" action="''' + escape(target,quote=True) + '''">
    <input type="hidden" name="id" value="''' + str(record_id) + '''">
    <input type="hidden" name="csrf" value="''' + token + '''">
    ''' + fields + '''
    <p><button type="submit">Сохранить решение</button></p></form>
    <p><a href="/admin">Вернуться в админку</a></p></body></html>''')


def _require_csrf(request: Request, form):
    expected = request.session.pop('moderation_csrf', None)
    supplied = str(form.get('csrf',''))
    if not expected or not hmac.compare_digest(expected, supplied):
        raise HTTPException(403, 'Истёк или недействителен токен формы')
    try:
        return UUID(str(form.get('id','')))
    except ValueError as exc:
        raise HTTPException(422, 'Неверный ID') from exc


def _field(label, name, value='', *, required=False, kind='text'):
    return (f'<p><label>{escape(label)}<br>'
            f'<input style="width:90%;padding:8px" type="{escape(kind)}" '
            f'name="{escape(name)}" value="{escape(str(value or ""),quote=True)}" '
            f'{"required" if required else ""}></label></p>')


def _redirect(request, identity):
    return RedirectResponse(str(request.url_for('admin:list', identity=identity)), status_code=303)


def _session():
    if SessionLocal.kw.get('bind') is None:
        configure_engine()
    return SessionLocal()


class SourceAdmin(ModelView, model=TelegramSource):
    name = 'Источник'
    name_plural = 'Источники Telegram'
    column_list = [TelegramSource.id, TelegramSource.channel_id, TelegramSource.username,
                   TelegramSource.enabled, TelegramSource.last_message_id, TelegramSource.last_success_at, TelegramSource.last_error]
    form_columns = [TelegramSource.channel_id, TelegramSource.username, TelegramSource.enabled]
    can_create = True
    can_edit = True
    can_delete = False

    @action(name='review_source', label='Решение по источнику', add_in_detail=True, add_in_list=True)
    async def review_source(self, request: Request):
        pk = _one_pk(request)
        with _session() as db:
            source = db.get(TelegramSource, pk)
            if not source:
                raise HTTPException(404, 'Источник не найден')
        return _form_page(request, 'Источник Telegram',
            f'/admin/{self.identity}/toggle-submit', pk,
            f'<p><label><input type="checkbox" name="enabled" {"checked" if source.enabled else ""}> Источник активен</label></p>'
            '<p>Снятие флажка останавливает импорт, не удаляя данные.</p>')

    @expose('/toggle-submit', methods=['POST'])
    async def toggle_submit(self, request: Request):
        form = await request.form()
        pk = _require_csrf(request, form)
        with _session() as db:
            row = db.get(TelegramSource, pk, with_for_update=True)
            if not row: raise HTTPException(404, 'Источник не найден')
            previous = row.enabled
            row.enabled = 'enabled' in form
            db.add(AuditLog(actor=request.session.get('admin_username','sqladmin'),
                action='source_enabled', subject_id=str(pk),
                details={'before': previous, 'after': row.enabled}))
            db.commit()
        return _redirect(request, self.identity)


class MessageAdmin(ModelView, model=SourceMessage):
    name = 'Сообщение'
    name_plural = 'Входящие сообщения'
    column_list = [SourceMessage.id, SourceMessage.source_id, SourceMessage.message_id,
                   SourceMessage.status, SourceMessage.message_date, SourceMessage.source_url]
    column_searchable_list = [SourceMessage.text]
    can_create = False
    can_edit = False
    can_delete = False

    @action(name='review_message', label='Проверить сообщение', add_in_detail=True, add_in_list=True)
    async def review_action(self, request: Request):
        pk = _one_pk(request)
        with _session() as db:
            message = db.get(SourceMessage, pk)
            if not message: raise HTTPException(404, 'Сообщение не найдено')
            content = escape((message.text or '')[:5000])
            source = escape(message.source_url or '')
        return _form_page(request, 'Проверка исходного сообщения',
            f'/admin/{self.identity}/review-submit', pk,
            f'<h3>Оригинал</h3><pre style="white-space:pre-wrap">{content}</pre><p>{source}</p>'
            '<p><label>Решение <select name="decision"><option value="approved">Одобрить источник</option>'
            '<option value="rejected">Отклонить источник</option></select></label></p>')

    @expose('/review-submit', methods=['POST'])
    async def review_submit(self, request: Request):
        from app.ingestion import review_message
        form = await request.form()
        pk = _require_csrf(request, form)
        with _session() as db:
            item = db.get(SourceMessage, pk, with_for_update=True)
            if not item: raise HTTPException(404, 'Сообщение не найдено')
            try:
                review_message(db, item, str(form.get('decision','')),
                               actor=request.session.get('admin_username','sqladmin'))
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            db.commit()
        return _redirect(request, self.identity)


class DraftAdmin(ModelView, model=CatalogDraft):
    name = 'Черновик'
    name_plural = 'Черновики каталога'
    column_list = [CatalogDraft.id, CatalogDraft.source_message_id, CatalogDraft.status,
                   CatalogDraft.created_at, CatalogDraft.canonical_id]
    form_columns = [CatalogDraft.extracted]

    @action(name='reopen_draft', label='Вернуть отклонённый в работу', add_in_detail=True, add_in_list=True)
    async def reopen_action(self, request: Request):
        pk = _one_pk(request)
        return _form_page(request, 'Вернуть черновик на повторную модерацию',
                          f'/admin/{self.identity}/reopen-submit', pk,
                          '<p>Ранее отклонённый черновик будет снова доступен модератору.</p>')

    @expose('/reopen-submit', methods=['POST'])
    async def reopen_submit(self, request: Request):
        form = await request.form()
        pk = _require_csrf(request, form)
        with _session() as db:
            draft = db.get(CatalogDraft, pk, with_for_update=True)
            if not draft: raise HTTPException(404, 'Черновик не найден')
            if draft.status != 'rejected': raise HTTPException(409, 'Черновик не отклонён')
            source = db.get(SourceMessage, draft.source_message_id, with_for_update=True)
            if not source or source.status != 'approved':
                raise HTTPException(409,'Источник требует проверки')
            draft.status = 'needs_review'
            db.add(AuditLog(actor=request.session.get('admin_username','sqladmin'),
                action='draft_reopened', subject_id=str(pk),
                details={'before':'rejected','after':'needs_review'}))
            db.commit()
        return _redirect(request,self.identity)

    @action(name='moderate_draft', label='Опубликовать / отклонить', add_in_detail=True, add_in_list=True)
    async def moderate_draft(self, request: Request):
        pk = _one_pk(request)
        with _session() as db:
            draft = db.get(CatalogDraft, pk)
            if not draft: raise HTTPException(404, 'Черновик не найден')
            message = db.get(SourceMessage, draft.source_message_id)
            extracted = draft.extracted or {}
            raw = escape((message.text if message else '')[:5000])
            duplicate = escape(str(draft.duplicate_candidates or [])[:1000])
            url = (message.source_url if message else '') or ''
            kind = extracted.get('kind') or 'place'
            title = extracted.get('title') or ''
            address = extracted.get('address') or ''
            price = extracted.get('price_kopeks')
        start = datetime.now(timezone.utc)
        fields = (f'<pre style="white-space:pre-wrap">{raw}</pre><h3>Возможные дубли</h3><pre>{duplicate}</pre>'
            '<p><label>Действие <select name="decision"><option value="publish">Опубликовать</option>'
            '<option value="reject">Отклонить</option></select></label></p>'
            + _field('Название','title',title,required=True)
            + _field('Тип (place/event)','kind',kind,required=True)
            + _field('Категория','category',extracted.get('category') or 'другое',required=True)
            + _field('Адрес в Москве','address',address,required=True)
            + _field('ID подтверждённого места для события','parent_place_id')
            + _field('Основная HTTPS-ссылка','source_url',url,required=True)
            + _field('Срок проверки (ISO 8601 с timezone)','valid_until',
                     (start + timedelta(days=1 if kind=='event' else 30)).isoformat(),required=True)
            + _field('Начало сеанса (ISO 8601 для event)','starts_at')
            + _field('Конец сеанса (ISO 8601 для event)','ends_at')
            + _field('Цена за пару в копейках','price_kopeks',str(price) if price is not None else '')
            + '<p><label><input type="checkbox" name="price_is_bounded" value="yes"> '
              'Проверил: цена фиксированная или является верхней границей (не просто «от»)</label></p>'
            + _field('Ссылка на бронирование (если проверена)','booking_url')
            + _field('Подтверждённые часы (JSON, {"0":[["10:00","22:00"]]})','opening_hours')
            + _field('Исключения календаря (JSON)','calendar_exceptions')
            + _field('Причина отказа (для reject)','reason')
            + '<p><label><input type="checkbox" name="verified" value="yes"> '
              'Я лично проверил обязательные факты и ссылку</label></p>')
        return _form_page(request, 'Проверка черновика',
            f'/admin/{self.identity}/moderate-submit', pk, fields)

    @expose('/moderate-submit', methods=['POST'])
    async def moderate_submit(self, request: Request):
        from app.catalog import publish
        from app.draft_moderation import reject_draft
        from app.schemas import PublishIn
        from pydantic import ValidationError
        form = await request.form()
        pk = _require_csrf(request, form)
        actor = request.session.get('admin_username','sqladmin')
        with _session() as db:
            draft = db.get(CatalogDraft, pk, with_for_update=True)
            if draft is None: raise HTTPException(404,'Черновик не найден')
            if draft.status != 'needs_review': raise HTTPException(409,'Уже обработан')
            if form.get('decision') == 'reject':
                try: reject_draft(db, draft, actor=actor, reason=str(form.get('reason','')))
                except ValueError as exc: raise HTTPException(422,str(exc)) from exc
            elif form.get('decision') == 'publish':
                message = db.get(SourceMessage, draft.source_message_id)
                if not message or message.status != 'approved':
                    raise HTTPException(409,'Источник не одобрен')
                if form.get('verified') != 'yes':
                    raise HTTPException(422,'Требуется подтверждение ручной проверки')
                def optional_time(name):
                    value = str(form.get(name,'')).strip()
                    return datetime.fromisoformat(value) if value else None
                try:
                    payload = PublishIn(
                        canonical_id=draft.canonical_id, kind=str(form.get('kind','')),
                        parent_place_id=UUID(str(form.get('parent_place_id'))) if str(form.get('parent_place_id','')).strip() else None,
                        title=str(form.get('title','')), description=(draft.extracted or {}).get('description',''),
                        category=str(form.get('category','другое')),
                        address=str(form.get('address','')), source_url=str(form.get('source_url','')),
                        booking_url=str(form.get('booking_url','')).strip() or None,
                        opening_hours=__import__('json').loads(str(form.get('opening_hours'))) if str(form.get('opening_hours','')).strip() else None,
                        calendar_exceptions=__import__('json').loads(str(form.get('calendar_exceptions'))) if str(form.get('calendar_exceptions','')).strip() else None,
                        last_verified_at=datetime.now(timezone.utc),
                        valid_until=optional_time('valid_until'),
                        starts_at=optional_time('starts_at'), ends_at=optional_time('ends_at'),
                        price_kopeks=int(form.get('price_kopeks')) if str(form.get('price_kopeks','')).strip() else None,
                        price_is_bounded=form.get('price_is_bounded') == 'yes',
                    )
                    result = publish(db, payload, actor=actor)
                except (ValidationError, ValueError, TypeError) as exc:
                    raise HTTPException(422,str(exc)) from exc
                draft.status = 'published'
                draft.canonical_id = result['canonical_id']
            else:
                raise HTTPException(422,'Неизвестное действие')
            db.commit()
        return _redirect(request, self.identity)

    can_create = False
    can_edit = True
    can_delete = False


class CanonicalAdmin(ModelView, model=Canonical):
    name = 'Объект'
    name_plural = 'Объекты каталога'
    column_list = [Canonical.id, Canonical.kind, Canonical.status, Canonical.created_at]
    can_create = False
    can_edit = False
    can_delete = False

    @action(name='archive', label='Снять с публикации', add_in_detail=True, add_in_list=True)
    async def archive_action(self, request: Request):
        pk = _one_pk(request)
        return _form_page(request, 'Снять объект с публикации',
                          f'/admin/{self.identity}/archive-submit', pk,
                          '<p>Объект немедленно исчезнет из новых рекомендаций.</p>')

    @expose('/archive-submit', methods=['POST'])
    async def archive_submit(self, request: Request):
        form = await request.form()
        pk = _require_csrf(request, form)
        with _session() as db:
            obj = db.get(Canonical, pk, with_for_update=True)
            if not obj: raise HTTPException(404, 'Объект не найден')
            before = obj.status
            obj.status = 'archived'
            db.add(AuditLog(actor=request.session.get('admin_username','sqladmin'),
                action='archive',subject_id=str(pk),details={'before':before,'after':'archived'}))
            db.commit()
        return _redirect(request,self.identity)


class PublicationAdmin(ModelView, model=PublicationVersion):
    name = 'Публикация'
    name_plural = 'Версии публикаций'
    column_list = [PublicationVersion.id, PublicationVersion.canonical_id,
                   PublicationVersion.version, PublicationVersion.title, PublicationVersion.valid_until]
    can_create = False
    can_edit = False
    can_delete = False


class OccurrenceAdmin(ModelView, model=Occurrence):
    name = 'Сеанс'
    name_plural = 'Сеансы'
    column_list = [Occurrence.id, Occurrence.publication_id, Occurrence.starts_at,
                   Occurrence.ends_at, Occurrence.status]
    can_create = False
    can_edit = False
    can_delete = False


class OfferAdmin(ModelView, model=Offer):
    name = 'Предложение'
    name_plural = 'Цены и предложения'
    column_list = [Offer.id, Offer.publication_id, Offer.occurrence_id, Offer.price_kopeks]
    can_create = False
    can_edit = False
    can_delete = False


class AuditAdmin(ModelView, model=AuditLog):
    name = 'Запись аудита'
    name_plural = 'Журнал аудита'
    column_list = [AuditLog.created_at, AuditLog.actor, AuditLog.action,
                   AuditLog.subject_id, AuditLog.details]
    can_create = False
    can_edit = False
    can_delete = False


class VerificationAdmin(ModelView, model=VerificationTask):
    name = 'Перепроверка'
    name_plural = 'Задачи перепроверки'
    column_list = [VerificationTask.id, VerificationTask.publication_id,
                   VerificationTask.reason, VerificationTask.status, VerificationTask.created_at]
    can_create = False
    can_edit = False
    can_delete = False


def install_admin(app):
    # Без секретов панель не создаётся вообще: не допускаем случайного открытого доступа.
    if not (settings.admin_password or settings.admin_password_hash) or len(settings.admin_session_secret) < 32:
        return False
    auth = AdminAuthentication(secret_key=settings.admin_session_secret)
    admin = Admin(app, configure_engine(), authentication_backend=auth,
                  title='Свидания — управление каталогом', base_url='/admin')
    for view in (SourceAdmin, MessageAdmin, DraftAdmin, CanonicalAdmin,
                 PublicationAdmin, OccurrenceAdmin, OfferAdmin, AuditAdmin, VerificationAdmin):
        admin.add_view(view)
    return True
