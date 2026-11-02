"""Строго последовательная обработка updates одного чата через блокировку строки в БД."""
import re
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from app.models import DialogueSession, Inbox, Outbox, RequestOperation, Feedback, Impression, UserPreference, UserExclusion, FeedbackAction, PublicationVersion, Outbox as OutboxModel
from app.schemas import SearchRequest
from app.search import search
from app.llm import extract_with_llm
from app.date_constraints import DAY_RE, relative_interval
from app.config import settings
from app.callback_answers import queue_answer

# Исправления по умолчанию — явно заданное пользователем новое условие имеет приоритет.
BUDGET_RE = re.compile(r'(?:до|максимум|не больше)\s*(\d[\d\s]*)\s*(?:₽|руб(?:лей|ля|ль)?|р\b)?(?=\W|$)', re.I)
PER_PERSON_RE = re.compile(r'(?:(?:по|до)\s*)?(\d[\d\s]*)\s*(?:₽|руб(?:лей|ля|ль)?|р\b)?\s*(?:на человека|с человека|за человека|/\s*чел)\b', re.I)
METRO_RE = re.compile(r'(?<!\w)(?:(?:у|возле|рядом с|около)\s+)?метро\s+([А-ЯЁA-Z][\w\-]{1,32}(?:\s+[А-ЯЁA-Z][\w\-]{1,32}){0,2})', re.U)
DURATION_RE = re.compile(r'(?<!\w)на\s+(\d{1,2})\s*(?:часа?|часов)(?!\w)', re.I)
TIME_PERIOD_RE = re.compile(r'(?<!\w)(утром|днём|днем|вечером|ночью)(?!\w)', re.I)
CENTER_RE = re.compile(r'(?<!\w)(?:в\s+центре|по\s+центру)(?!\w)', re.I)
PAST_RE = re.compile(r'(?<!\w)(?:вчера|позавчера|в\s+прошлую\s+пятницу)(?!\w)', re.I)
WEEKDAY_RE = re.compile(r'(?<!\w)(?:в\s+)?(?:эту|следующую)?\s*пятниц[ауые](?!\w)', re.I)
INTERESTS_RE = re.compile(r'(?<!\w)интересы\s*:\s*([^;.!?]+)', re.I)
FORMAT_RE = re.compile(r'(?<!\w)формат\s*:\s*([^;,.!?]+)', re.I)
CONFLICT_INDOORS_RE = re.compile(r'(?=.*\bв помещении\b)(?=.*\bна улице\b)', re.I)
SHORT_CONTEXT_RE = re.compile(r'^(?:завтра|сегодня|послезавтра|на выходных|в выходные|вечером|утром|днём|на двоих|для двоих|вдвоём|в центре|в помещении|на улице|без разницы|неважно)$', re.I)
CORRECTION_RE = re.compile(r'^(?:нет[,\s]*|не[,\s]*|лучше\s+|давай\s+|а\s+|тогда\s+)', re.I)


def extract_constraints(message: str, previous: dict, *, resolve_dates: bool = True, now: datetime | None = None) -> dict:
    """Детерминированный merge слотов между сообщениями без потери исходной идеи.

    Отдельная реплика о бюджете не заменяет поисковый замысел.
    Для смены идеи принимается явный маркер исправления или новое тематическое сообщение.
    """
    result = dict(previous)
    msg = message.strip()
    lower = msg.lower()
    duration = DURATION_RE.search(msg)
    if duration:
        value = int(duration.group(1)) * 60
        if 15 <= value <= 720:
            result['duration_minutes'] = value
            result['_provenance'] = {**result.get('_provenance',{}), 'duration_minutes':'current_message'}
    interests = INTERESTS_RE.search(msg)
    if interests:
        tags = [v.strip()[:60] for v in interests.group(1).split(',') if v.strip()]
        if tags:
            result['interests'] = tags[:8]
            result['_provenance'] = {**result.get('_provenance', {}), 'interests':'current_message'}
    format_match = FORMAT_RE.search(msg)
    if format_match:
        result['format'] = format_match.group(1).strip()[:100]
        result['_provenance'] = {**result.get('_provenance', {}), 'format':'current_message'}
    if PAST_RE.search(msg):
        result['_past_date'] = True
    time_part = TIME_PERIOD_RE.search(msg)
    if time_part:
        part = time_part.group(1).casefold().replace('днем','днём')
        result['time_period'] = {'утром':'morning','днём':'day','вечером':'evening','ночью':'night'}[part]
        result['_provenance'] = {**result.get('_provenance',{}),'time_period':'current_message'}
    if CENTER_RE.search(msg):
        result['district'] = 'центр'
        result['_provenance'] = {**result.get('_provenance',{}),'district':'current_message'}
    if resolve_dates:
        interval = relative_interval(msg, now=now, timezone_name=settings.moscow_timezone)
        if interval is not None:
            result['starts_at'], result['ends_at'] = interval
            result.pop('_past_date', None)
            result['_provenance'] = {**result.get('_provenance', {}),
                                     'starts_at':'current_message','ends_at':'current_message'}
    match = BUDGET_RE.search(msg)
    person = PER_PERSON_RE.search(msg)
    if person:
        amount = int(person.group(1).replace(' ', ''))
        result['budget_total_kopeks'] = amount * 2 * 100
        result['budget_original_kopeks'] = amount * 100
        result['budget_original_scope'] = 'person'
        result['_provenance'] = {**result.get('_provenance', {}), 'budget_total_kopeks': 'current_message'}
    elif match:
        result['budget_total_kopeks'] = int(match.group(1).replace(' ', '')) * 100
        result['budget_original_kopeks'] = result['budget_total_kopeks']
        result['budget_original_scope'] = 'pair'
        result['_provenance'] = {**result.get('_provenance', {}), 'budget_total_kopeks': 'current_message'}
    if re.search(r'без ограничени[йя] по бюджету', lower):
        result.pop('budget_total_kopeks', None)
        result.pop('budget_original_kopeks', None)
        result.pop('budget_original_scope', None)
        if '_provenance' in result:
            result['_provenance'].pop('budget_total_kopeks', None)
            if not result['_provenance']: result.pop('_provenance')
    metro = METRO_RE.search(msg.strip(' ,.!?'))
    if metro:
        result['metro'] = metro.group(1).strip()
        result['_provenance'] = {**result.get('_provenance', {}), 'metro':'current_message'}
    if CONFLICT_INDOORS_RE.search(lower):
        result['_ambiguous_indoor'] = True
    elif re.search(r'(?<!\w)(?:в помещении|только внутри)(?!\w)', lower):
        result['indoors'] = True
        result['_provenance'] = {**result.get('_provenance', {}), 'indoors':'current_message'}
    elif re.search(r'(?<!\w)(?:на улице|на свежем воздухе)(?!\w)', lower):
        result['indoors'] = False
        result['_provenance'] = {**result.get('_provenance', {}), 'indoors':'current_message'}
    elif lower in ('неважно где', 'без разницы где'):
        result.pop('indoors', None)
    cleaned = FORMAT_RE.sub('', INTERESTS_RE.sub('', DURATION_RE.sub('', CENTER_RE.sub('', TIME_PERIOD_RE.sub('',
              WEEKDAY_RE.sub('', PAST_RE.sub('',
              BUDGET_RE.sub('', PER_PERSON_RE.sub('', msg)))))))))
    cleaned = METRO_RE.sub('', DAY_RE.sub('', cleaned))
    cleaned = re.sub(r'\b(?:на двоих|для двоих|вдвоём)\b', '', cleaned, flags=re.I)
    cleaned = re.sub(r'без ограничени[йя] по бюджету', '', cleaned, flags=re.I)
    cleaned = re.sub(r'(?<!\w)(?:в помещении|только внутри|на улице|на свежем воздухе)(?!\w)', '', cleaned, flags=re.I)
    cleaned = CORRECTION_RE.sub('', cleaned).strip(' ,.!?;:')
    if CONFLICT_INDOORS_RE.search(lower):
        cleaned = ''  # contradictory conditions never become the search subject
    if WEEKDAY_RE.search(msg) and not PAST_RE.search(msg) and not re.search(r'\b(?:эту|следующую)\b', msg, re.I):
        result['_ambiguous_friday'] = True
    # explicit weekday resolution should not stay ambiguous.
    elif WEEKDAY_RE.search(msg) and not PAST_RE.search(msg):
        from zoneinfo import ZoneInfo
        from datetime import time
        local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(settings.moscow_timezone))
        delta = (4 - local.weekday()) % 7
        if delta == 0 and local.hour >= 19:
            delta = 7
        if 'следующую' in lower:
            delta += 7
        day = local.date() + timedelta(days=delta)
        start = datetime.combine(day, time.min, tzinfo=local.tzinfo)
        finish = start + timedelta(days=1)
        result['starts_at'] = start.astimezone(timezone.utc).isoformat()
        result['ends_at'] = finish.astimezone(timezone.utc).isoformat()
        result.pop('_ambiguous_friday', None)
        result['_provenance'] = {**result.get('_provenance', {}), 'starts_at':'confirmed_clarification', 'ends_at':'confirmed_clarification'}
    # Фразы коррекции даты не должны становиться новым поисковым запросом.
    if cleaned.casefold() in {'а', 'не', 'не а', 'нет а', 'лучше', 'тогда', 'или'}:
        cleaned = ''
    # Не превращаем короткие ответы согласия в запрос к каталогу.
    if cleaned and cleaned.lower() not in {'да','нет','ок','хорошо','спасибо','неважно'} and not SHORT_CONTEXT_RE.fullmatch(cleaned) and cleaned.lower() not in {'внутри', 'на свежем воздухе', 'неважно где', 'без разницы где'}:
        # Тема должна быть содержательной; одиночные слова о бюджете не меняют запрос.
        if not re.fullmatch(r'(?:рублей|бюджет|на двоих|на двоих примерно|примерно|дороже|дешевле)', cleaned, re.I):
            result['query'] = cleaned
            result['_provenance'] = {**result.get('_provenance', {}), 'query':'current_message'}
    return result


def clarification_question(constraints: dict) -> str | None:
    """Only one ambiguity at a time, no questionnaire."""
    if constraints.get('_past_date'):
        return 'Эта дата уже прошла. На какую будущую дату искать? Можно написать «завтра» или «на выходных».'
    if constraints.get('_ambiguous_indoor'):
        return 'Выбрать только помещение или только улицу? Можно ответить «неважно».'
    if constraints.get('_ambiguous_friday'):
        return 'В эту пятницу или в следующую? Напиши «в эту пятницу» или «в следующую пятницу».'
    if not (constraints.get('query') or '').strip():
        return 'Что тебе интересно: мастер-класс, выставка, прогулка или что-то необычное?'
    return None

def handle_feedback(db: Session, update: dict) -> bool:
    """Bind the callback to delivered outbox and chat. Legacy UUID callbacks stay
    supported only for already-sent buttons and are not generated anymore.
    """
    from uuid import UUID
    from app.user_controls import exclude_publication
    callback = update.get('callback_query') or {}
    callback_id = callback.get('id')
    chat_id = ((callback.get('message') or {}).get('chat') or {}).get('id')
    sender_id = (callback.get('from') or {}).get('id')
    data = callback.get('data') or ''
    parts = data.split(':')
    if (not isinstance(chat_id, int) or not isinstance(callback_id, str) or
        len(parts) != 3 or parts[0] != 'fb' or parts[1] not in ('like','dislike','exclude')):
        return False
    if sender_id is not None and sender_id != chat_id:
        return False  # MVP is private chats only; group users require separate identity.
    if db.scalar(select(Feedback.id).where(Feedback.callback_id == callback_id)):
        return False
    action = parts[1]
    token = parts[2]
    if len(token) == 32:
        binding = db.get(FeedbackAction, token)
        if binding is None or binding.chat_id != chat_id:
            return False
        outbox = db.get(OutboxModel, binding.outbox_id)
        if outbox is None or outbox.status != 'delivered':
            return False
        current_dialogue = db.get(DialogueSession, chat_id)
        if (current_dialogue is None or outbox.generation_id is None or
            current_dialogue.generation_id != outbox.generation_id):
            return False
        publication_id = binding.publication_id
    else:
        # Compatibility with previously delivered fb:like:<publication UUID> buttons.
        try:
            publication_id = UUID(token)
        except ValueError:
            return False
    shown = db.scalar(select(Impression.id).where(
        Impression.chat_id == chat_id, Impression.publication_id == publication_id).limit(1))
    if not shown:
        return False
    # A stale/archived publication is not eligible for new reactions.
    from app.models import Canonical
    from sqlalchemy import func
    pub = db.get(PublicationVersion, publication_id) if len(token)==32 else None
    if pub is not None:
        obj = db.get(Canonical, pub.canonical_id)
        if obj is None or obj.status != 'published' or pub.valid_until <= datetime.now(timezone.utc):
            return False
        latest = db.scalar(select(func.max(PublicationVersion.version)).where(
            PublicationVersion.canonical_id == pub.canonical_id))
        if pub.version != latest:
            return False
    if action == 'exclude':
        if not exclude_publication(db, chat_id, publication_id):
            return False
    db.add(Feedback(chat_id=chat_id, publication_id=publication_id,
                    reaction=action, callback_id=callback_id))
    return True

def process_update(db: Session, update: dict) -> bool:
    update_id=update['update_id']
    is_callback='callback_query' in update
    message=(update.get('callback_query',{}).get('message') if is_callback else update.get('message')) or {}
    chat=message.get('chat') or {}
    chat_id=chat.get('id')
    if not isinstance(chat_id,int): return False
    if chat.get('type') not in (None, 'private'): return False
    stmt=insert(DialogueSession).values(chat_id=chat_id,generation_id=0,state='idle',constraints={}).on_conflict_do_nothing(index_elements=['chat_id'])
    db.execute(stmt)
    session=db.scalar(select(DialogueSession).where(DialogueSession.chat_id == chat_id).with_for_update())
    existing=db.get(Inbox,update_id)
    if existing: db.commit(); return False
    db.add(Inbox(update_id=update_id,chat_id=chat_id,payload=update,status='processed'))
    if is_callback:
        data = (update.get('callback_query') or {}).get('data','')
        if data.startswith('fb:more:'):
            callback = update['callback_query']
            token = data[len('fb:more:'):]
            binding = db.get(FeedbackAction, token) if len(token)==32 else None
            original = db.get(OutboxModel, binding.outbox_id) if binding else None
            sender_id = (callback.get('from') or {}).get('id')
            valid = bool(binding and binding.chat_id==chat_id and original and
                original.status=='delivered' and
                original.generation_id==session.generation_id and
                (sender_id is None or sender_id==chat_id) and
                session.constraints.get('query'))
            callback_id = callback.get('id')
            if valid:
                session.generation_id += 1
                gen = session.generation_id
                session.state = 'processing'
                db.add(RequestOperation(chat_id=chat_id,generation_id=gen,
                    status='queued',payload={'constraints':dict(session.constraints),
                                             'message':'','more':True}))
            queue_answer(db, callback_id, 'Ищу другие варианты.' if valid
                         else 'Этот подбор устарел. Напиши новый запрос.')
            db.commit()
            return valid
        accepted=handle_feedback(db,update)
        callback_id = (update.get('callback_query') or {}).get('id')
        queue_answer(db, callback_id, 'Принято. Больше не предложу это место.' if accepted and (update.get('callback_query') or {}).get('data','').startswith('fb:exclude:') else ('Спасибо! Учту оценку.' if accepted else 'Оценка не принята или уже учтена.'))
        db.commit()
        return accepted
    text=(message.get('text') or '').strip()
    if not text:
        db.commit();return True
    session.generation_id+=1
    generation=session.generation_id
    if text not in ('/delete_data','/delete_data confirm') and '_delete_confirmation' in session.constraints:
        session.constraints = {k:v for k,v in session.constraints.items() if k!='_delete_confirmation'}
    if text.startswith('/remember '):
        pref=db.get(UserPreference,chat_id)
        if pref is None:
            pref=UserPreference(chat_id=chat_id,preferences={});db.add(pref)
        value=text[len('/remember '):].strip()[:300]
        pref.preferences={**pref.preferences,'explicit_note':value}
        session.state='idle'
        answer='Запомнил предпочтение: '+value
        pubids=[]
    elif text.startswith('/forget'):
        pref=db.get(UserPreference,chat_id)
        if pref: pref.preferences={}
        session.state='idle'
        answer='Сохранённые предпочтения очищены.'
        pubids=[]
    elif text.startswith('/start'):
        session.state='idle'
        session.constraints={}
        answer='Привет! Напиши, какое свидание хочешь: например, «гончарный мастер-класс до 3000 руб». Подбор строится только по проверенному каталогу.'
        pubids=[]
    elif text.startswith('/new'):
        session.state = 'idle'
        session.constraints = {}
        answer = 'Новый подбор. Опиши свидание, бюджет и желаемую дату.'
        pubids = []
    elif text.startswith('/help'):
        answer = ('Напиши запрос свободным текстом. /new — новый подбор; '
                  '/preferences — предпочтения; /excluded — запреты; '
                  '/unexclude <id> — снять запрет; /delete_data — удалить данные.')
        pubids = []
    elif text.startswith('/preferences'):
        pref = db.get(UserPreference, chat_id)
        values = pref.preferences if pref else {}
        answer = ('Предпочтения: ' + ', '.join(f'{k}: {v}' for k, v in values.items())
                  if values else 'Сохранённых предпочтений нет.')
        pubids = []
    elif text.startswith('/excluded'):
        from app.user_controls import excluded_roots
        roots = sorted(str(x) for x in excluded_roots(db, chat_id))
        answer = ('Не показывать: ' + ', '.join(roots) + '. Снять: /unexclude <id>'
                  if roots else 'Постоянных исключений нет.')
        pubids = []
    elif text.startswith('/unexclude '):
        from uuid import UUID
        from app.user_controls import remove_exclusion
        try:
            removed = remove_exclusion(db, chat_id, UUID(text.split(maxsplit=1)[1].strip()))
            answer = ('Исключение снято.' if removed else 'Такого исключения нет.')
        except (ValueError, IndexError):
            answer = 'Укажи ID объекта после /unexclude.'
        pubids = []
    elif text == '/delete_data':
        session.constraints = {**session.constraints, '_delete_confirmation': (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}
        answer = ('Это удалит твои предпочтения, запреты, историю показов и реакций. '
                  'Чтобы подтвердить, отправь /delete_data confirm.')
        pubids = []
    elif text == '/delete_data confirm' and (
        (session.constraints or {}).get('_delete_confirmation') and
        datetime.fromisoformat(session.constraints['_delete_confirmation']) > datetime.now(timezone.utc)
    ):
        from app.models import FeedbackAction
        from sqlalchemy import delete
        # Explicit ownership filters. Drop callback bindings and delivery history as well.
        db.execute(delete(Feedback).where(Feedback.chat_id == chat_id))
        db.execute(delete(Impression).where(Impression.chat_id == chat_id))
        db.execute(delete(UserExclusion).where(UserExclusion.chat_id == chat_id))
        db.execute(delete(FeedbackAction).where(FeedbackAction.chat_id == chat_id))
        db.execute(delete(OutboxModel).where(OutboxModel.chat_id == chat_id))
        db.execute(delete(RequestOperation).where(RequestOperation.chat_id == chat_id))
        db.execute(delete(Inbox).where(Inbox.chat_id == chat_id, Inbox.update_id != update_id))
        pref = db.get(UserPreference, chat_id)
        if pref: db.delete(pref)
        session.constraints = {}
        session.state = 'idle'
        answer = 'Личные данные и история очищены.'
        pubids = []
    elif text == '/delete_data confirm':
        answer = 'Сначала отправь /delete_data, а затем подтверди удаление в течение 5 минут.'
        pubids = []
    elif text.startswith('/reset'):
        session.state='idle'
        session.constraints={}
        answer='Начнём заново. Какое свидание ты хочешь?'
        pubids=[]
    elif text.casefold() in ('другие варианты', 'ещё варианты', 'еще варианты', '/more'):
        if not session.constraints.get('query'):
            answer = 'Сначала напиши, какое свидание ищешь.'
            pubids = []
        else:
            session.state = 'processing'
            db.add(RequestOperation(chat_id=chat_id, generation_id=generation,
                 status='queued', payload={'constraints': dict(session.constraints),
                                           'message': '', 'more': True}))
            db.commit()
            return True
    else:
        # Webhook не выполня сетевые LLM-запросы и тяжёлый поиск.
        # Каждое новое сообщение отменяет предыдущее поколение логически.
        previous_query = (session.constraints or {}).get('query')
        session.constraints=extract_constraints(text,session.constraints)
        if previous_query and session.constraints.get('query') != previous_query:
            # A different idea starts a fresh selection, but keeps explicit budget/date.
            session.constraints.pop('_shown_current',None)
            session.constraints.pop('_clarifications',None)
        if text.casefold() in ('неважно', 'без разницы'):
            session.constraints.pop('_ambiguous_indoor', None)
            session.constraints.pop('_ambiguous_friday', None)
            session.constraints.pop('_past_date', None)
        question=clarification_question(session.constraints)
        if question and session.constraints.get('_clarifications', 0) >= 2:
            question = None
            session.constraints.pop('_ambiguous_friday', None)
            session.constraints.pop('_past_date', None)
            session.constraints.pop('_ambiguous_indoor', None)
        if question:
            session.constraints['_clarifications'] = session.constraints.get('_clarifications', 0) + 1
        if question:
            session.state='clarifying'
            answer=question
            pubids=[]
        else:
            session.state='processing'
            db.add(RequestOperation(chat_id=chat_id,generation_id=generation,
                status='queued',payload={'constraints':dict(session.constraints),'message':text}))
            db.commit()
            return True
    db.add(Outbox(idempotency_key=f'update:{update_id}:g:{generation}',chat_id=chat_id,generation_id=generation,
        text=answer,publication_ids=pubids,expires_at=datetime.now(timezone.utc)+timedelta(minutes=10)))
    db.commit()
    return True
