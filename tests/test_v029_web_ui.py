"""v0.29: красивая UI-обертка — HTML-карточки и web-чат на агентном ядре."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.schemas import CandidateOut


def _candidate(title='Гончарная студия <script>', description='описание <b>тут</b>'):
    now = datetime.now(UTC)
    return CandidateOut(canonical_id=uuid4(), publication_version_id=uuid4(),
        title=title, description=description, category='мастер-класс',
        address='Москва, Пятницкая, 1 & 2', source_url='https://example.com/p',
        price_total_kopeks=300000, price_is_bounded=True,
        valid_until=now + timedelta(days=3), reason_codes=['проверенная публикация'],
        starts_at=now + timedelta(days=1))


# --- HTML-рендер карточек ---

def test_cards_html_structure_and_emoji():
    from app.core.rendering import render_cards_html
    text = render_cards_html([_candidate()])
    assert '<b>1. Гончарная студия' in text
    assert '📍 Москва, Пятницкая, 1 &amp; 2' in text
    assert '💰 3000 ₽ за двоих' in text
    assert '✅ Почему: проверенная публикация' in text
    assert '🕒 Сеанс:' in text


def test_cards_html_escapes_source_text():
    from app.core.rendering import render_cards_html
    text = render_cards_html([_candidate()])
    assert '<script>' not in text and '&lt;script&gt;' in text
    assert '<b>тут</b>' not in text[100:]  # описание не внедряет разметку
    assert '<i>описание &lt;b&gt;тут&lt;/b&gt;</i>' in text


def test_unbounded_price_gets_ot_prefix():
    from app.core.rendering import render_cards_html
    c = _candidate()
    c = c.model_copy(update={'price_is_bounded': False})
    assert '💰 от 3000 ₽' in render_cards_html([c])


def test_unknown_price_is_stated_not_fabricated():
    from app.core.rendering import render_cards_html
    c = _candidate().model_copy(update={'price_total_kopeks': None})
    assert 'Стоимость не подтверждена' in render_cards_html([c])


def test_agent_renderer_sets_html_parse_mode():
    from app.core.agent import default_renderer
    messages = default_renderer([_candidate(title='Чисто')])
    assert messages[0].parse_mode == 'HTML'
    assert '<b>1. Чисто</b>' in messages[0].text


def test_sender_worker_passes_parse_mode(monkeypatch):
    from app import worker
    from app.config import settings
    monkeypatch.setattr(settings, 'telegram_bot_token', 'tok')
    captured = {}

    class Resp:
        status_code = 200
        is_success = True
        def json(self):
            return {'ok': True}

    class Client:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def post(self, url, json=None):
            captured.update(json or {})
            return Resp()

    monkeypatch.setattr(worker.httpx, 'Client', lambda timeout: Client())
    locked = worker.Outbox(idempotency_key='x', chat_id=1, text='<b>Привет</b>',
                           publication_ids=[], parse_mode='HTML', attempts=0,
                           next_attempt_at=datetime.now(UTC),
                           expires_at=datetime.now(UTC) + timedelta(minutes=5))
    locked.status = 'reserved'
    locked.reservation_token = 't'
    db = MagicMock()
    db.scalar.return_value = locked  # SELECT FOR UPDATE возвращает ту же строку
    db.get.return_value = None  # DialogueSession отсутствует — просто доставка
    assert worker.deliver_one(db, locked, reservation_token='t') is True
    assert captured.get('parse_mode') == 'HTML'


from unittest.mock import MagicMock

# --- Web-чат: HTTP-адаптер ядра ---

def test_chat_health():
    from fastapi.testclient import TestClient

    from app.web_chat import router
    client = TestClient(_app_with(router))
    data = client.get('/api/chat/health').json()
    assert data['status'] == 'ok'


def _app_with(router):
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return app


def test_chat_full_conversation():
    from fastapi.testclient import TestClient

    from app.web_chat import router
    client = TestClient(_app_with(router))
    chat = 'test123'
    data = client.post(f'/api/chat/{chat}/messages', json={'text': 'гончарный мастер-класс'}).json()
    assert data['state'] == 'awaiting_feedback'
    text = data['messages'][0]['text']
    assert 'Гончарная студия' in text and '<b>' in text
    actions = [b['action'] for row in data['messages'][0]['buttons'] for b in row]
    assert 'like:0' in actions and 'more' in actions


def test_chat_empty_result_offers_relax():
    from fastapi.testclient import TestClient

    from app.web_chat import router
    client = TestClient(_app_with(router))
    chat = 'empty1'
    # Явное условие (бюджет) даёт кнопку ослабления при пустой выдаче.
    client.post(f'/api/chat/{chat}/messages', json={'text': 'полёт на луну до 1000 руб'})
    data = client.post(f'/api/chat/{chat}/actions', json={'action': 'more'}).json()
    actions = [b['action'] for m in data['messages'] for row in m['buttons'] for b in row]
    assert 'relax:budget_total_kopeks' in actions


def test_chat_relax_action_changes_search():
    from fastapi.testclient import TestClient

    from app.web_chat import router
    client = TestClient(_app_with(router))
    chat = 'relax1'
    client.post(f'/api/chat/{chat}/messages', json={'text': 'полёт на луну'})
    client.post(f'/api/chat/{chat}/actions', json={'action': 'more'})
    data = client.post(f'/api/chat/{chat}/actions', json={'action': 'relax:budget_total_kopeks'}).json()
    assert data['state'] in ('processing', 'awaiting_feedback')


def test_chat_history_and_reset():
    from fastapi.testclient import TestClient

    from app.web_chat import router
    client = TestClient(_app_with(router))
    chat = 'hist1'
    client.post(f'/api/chat/{chat}/messages', json={'text': 'джаз'})
    history = client.get(f'/api/chat/{chat}/history').json()['messages']
    assert len(history) >= 1
    reset = client.post(f'/api/chat/{chat}/reset').json()
    assert reset['ok'] is True


def test_chat_rejects_empty_message():
    from fastapi.testclient import TestClient

    from app.web_chat import router
    client = TestClient(_app_with(router))
    assert client.post('/api/chat/x/messages', json={'text': '  '}).status_code == 422
