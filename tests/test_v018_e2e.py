"""Реальный сквозной тест через PostgreSQL и FastAPI TestClient.

Запускается исключительно в отдельной базе dateideas_test через docker-compose.e2e.yml.
Внешний Telegram API подменён HTTP-моком, реальные секреты не требуются.
"""
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, configure_engine
from app.models import DialogueSession, Feedback, Impression, Inbox, Outbox, RequestOperation
from app import dialogue_worker, worker

pytestmark = pytest.mark.e2e


def _require_isolated_database():
    url = os.environ.get('E2E_DATABASE_URL', '')
    # Fail closed: никогда не применять тест к пользовательской/prod БД.
    expected = 'postgresql+psycopg://dateideas_test:isolated-test-password@db:5432/dateideas_test'
    if url != expected:
        pytest.skip('E2E разрешён только для отдельной БД dateideas_test')
    assert settings.database_url == url, 'Тестовая и рабочая DATABASE_URL не совпадают'
    assert settings.telegram_bot_token == 'fake-e2e-bot-token', 'В E2E нужен фиктивный Telegram токен'
    assert settings.telegram_webhook_secret == 'local-e2e-webhook-secret'
    assert settings.admin_api_key == 'local-e2e-admin-api-key'
    assert not settings.llm_base_url and not settings.embedding_base_url


def _telegram_update(update_id, chat_id, text):
    return {'update_id': update_id, 'message': {'message_id': update_id, 'chat': {'id': chat_id}, 'text': text}}


def _webhook(client, update):
    response = client.post('/api/telegram/webhook', json=update,
        headers={'X-Telegram-Bot-Api-Secret-Token': settings.telegram_webhook_secret})
    assert response.status_code == 200, response.text
    return response.json()


def test_webhook_to_recommendation_to_feedback(monkeypatch):
    _require_isolated_database()
    engine = configure_engine()
    # Таблицы создаёт alembic upgrade head, не Base.metadata.create_all.
    monkeypatch.setattr(dialogue_worker, 'extract_with_llm', lambda message: None)

    now = datetime.now(timezone.utc)
    admin = {'X-Admin-Key': settings.admin_api_key}
    client = TestClient(__import__('app.main', fromlist=['app']).app)
    chat_id = 8310210084

    with client:
        assert client.get('/health').status_code == 200
        assert client.get('/ready').status_code == 200
        assert client.post('/api/admin/publications', json={}).status_code == 403
        assert client.post('/api/telegram/webhook', json=_telegram_update(98001, chat_id, 'гончарный мастер-класс')).status_code == 403

        def publication(title, price, category):
            payload = dict(kind='place', title=title, category=category,
                description='Романтический гончарный мастер-класс для пары',
                address='Москва, Тестовая улица, 1', source_url='https://example.org/ceramics',
                last_verified_at=(now-timedelta(minutes=5)).isoformat(),
                valid_until=(now+timedelta(days=5)).isoformat(),
                price_kopeks=price, price_scope='pair', price_is_bounded=True)
            response = client.post('/api/admin/publications', json=payload, headers=admin)
            assert response.status_code == 200, response.text
            return UUID(response.json()['publication_version_id'])

        affordable = publication('Гончарный мастер-класс для двоих', 250000, 'творчество')
        expensive = publication('Гончарный мастер-класс премиум', 900000, 'творчество')

        first = _telegram_update(98001, chat_id, 'гончарный мастер-класс')
        assert _webhook(client, first) == {'ok': True, 'accepted': True}
        assert _webhook(client, first) == {'ok': True, 'accepted': False}
        assert _webhook(client, _telegram_update(98002, chat_id, 'до 3000 руб'))['accepted']

        with SessionLocal() as db:
            session = db.get(DialogueSession, chat_id)
            assert session.constraints['query'] == 'гончарный мастер-класс'
            assert session.constraints['budget_total_kopeks'] == 300000
            assert session.generation_id == 2
            assert db.scalar(select(Inbox).where(Inbox.update_id == 98001)) is not None
            ops = list(db.scalars(select(RequestOperation).where(RequestOperation.chat_id == chat_id)
                                  .order_by(RequestOperation.generation_id)))
            assert len(ops) == 2
            # Первое поколение должно отмениться, второе — попасть в Outbox.
            assert dialogue_worker.claim(db).id == ops[0].id
            assert dialogue_worker.process_one(db, ops[0]) is False
            assert dialogue_worker.claim(db).id == ops[1].id
            assert dialogue_worker.process_one(db, ops[1]) is True
            messages = list(db.scalars(select(Outbox).where(Outbox.chat_id == chat_id)))
            assert len(messages) == 1
            msg = messages[0]
            assert msg.publication_ids == [str(affordable)]
            assert '2500 ₽' in msg.text
            assert '9000 ₽' not in msg.text
            claimed = worker.claim_batch(db, limit=10)
            assert len(claimed) == 1 and claimed[0][0] == msg.id

            sent = []
            def fake_telegram(request):
                assert request.url.host == 'api.telegram.org'
                assert request.url.path.endswith('/sendMessage')
                sent.append(request)
                return httpx.Response(200, json={'ok': True, 'result': {'message_id': 450}})

            real_client = httpx.Client
            def mocked_client(**kwargs):
                return real_client(transport=httpx.MockTransport(fake_telegram), **kwargs)

            # Только сетевой вызов Telegram заменён, остальные компоненты — реальные.
            monkeypatch.setattr(worker.httpx, 'Client', mocked_client)
            assert worker.deliver_one(db, msg, reservation_token=claimed[0][1]) is True
            assert len(sent) == 1
            assert db.scalar(select(Impression).where(Impression.chat_id == chat_id,
                         Impression.publication_id == affordable)) is not None
            assert db.get(Outbox, msg.id).status == 'delivered'

        callback = {'update_id': 98003, 'callback_query': {
            'id': 'e2e-test-callback', 'data': f'fb:like:{affordable}',
            'message': {'chat': {'id': chat_id}, 'message_id': 450}}}
        assert _webhook(client, callback)['accepted'] is True
        assert _webhook(client, callback)['accepted'] is False
        with SessionLocal() as db:
            feedback = list(db.scalars(select(Feedback).where(Feedback.chat_id == chat_id)))
            assert len(feedback) == 1 and feedback[0].reaction == 'like'
            assert feedback[0].publication_id == affordable
            assert db.get(DialogueSession, chat_id).state == 'awaiting_feedback'
    engine.dispose()
