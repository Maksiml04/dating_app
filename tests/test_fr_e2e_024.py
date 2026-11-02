"""Real isolated PostgreSQL: opaque feedback -> permanent exclusion -> /new.

Only runs from docker-compose.e2e.yml with a fake Telegram transport.
No real user token or production DB can be used.
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
from app.models import (Canonical, DialogueSession, FeedbackAction,
                        RequestOperation, Outbox, UserExclusion)
from app import dialogue_worker, worker

pytestmark = pytest.mark.e2e


def test_opaque_ban_and_selection_reset(monkeypatch):
    expected = 'postgresql+psycopg://dateideas_test:isolated-test-password@db:5432/dateideas_test'
    if os.environ.get('E2E_DATABASE_URL') != expected:
        pytest.skip('Only isolated PostgreSQL E2E is permitted')
    assert settings.database_url == expected
    assert settings.telegram_bot_token == 'fake-e2e-bot-token'
    configure_engine()
    monkeypatch.setattr(dialogue_worker, 'extract_with_llm', lambda _: None)
    from app.main import app
    client = TestClient(app)
    chat_id = 852410031
    now = datetime.now(timezone.utc)
    headers = {'X-Admin-Key': settings.admin_api_key}
    response = client.post('/api/admin/publications', json={
        'kind':'place','title':'Купольная гончарная студия', 'category':'мастер-класс',
        'address':'Москва, Тестовый переулок, 27',
        'description':'Гончарная мастерская для пары',
        'source_url':'https://example.org/ceramic-test',
        'last_verified_at':(now-timedelta(minutes=2)).isoformat(),
        'valid_until':(now+timedelta(days=5)).isoformat(),
        'price_kopeks':220000,'price_is_bounded':True,
    }, headers=headers)
    assert response.status_code == 200, response.text
    canonical_id = UUID(response.json()['canonical_id'])
    publication_id = UUID(response.json()['publication_version_id'])

    def webhook(num, text):
        result = client.post('/api/telegram/webhook', json={
            'update_id':num, 'message': {'message_id':num, 'chat':{'id':chat_id,'type':'private'},
                                        'from':{'id':chat_id},'text':text}},
            headers={'X-Telegram-Bot-Api-Secret-Token':settings.telegram_webhook_secret})
        assert result.status_code == 200, result.text
        return result.json()

    webhook(99211, 'купольная гончарная студия до 3000 рублей')
    with SessionLocal() as db:
        op = dialogue_worker.claim(db)
        assert op
        assert dialogue_worker.process_one(db, op)
        message = db.scalar(select(Outbox).where(Outbox.chat_id == chat_id))
        assert message and str(publication_id) in message.publication_ids
        claimed = worker.claim_batch(db)
        assert claimed[0][0] == message.id
        delivered_requests=[]
        def fake_api(request):
            delivered_requests.append(request)
            return httpx.Response(200,json={'ok':True,'result':{'message_id':891}})
        real_client = httpx.Client
        monkeypatch.setattr(worker.httpx, 'Client',
            lambda **kwargs:real_client(transport=httpx.MockTransport(fake_api), **kwargs))
        assert worker.deliver_one(db, message, reservation_token=claimed[0][1])
        body = __import__('json').loads(delivered_requests[0].content)
        controls = body['reply_markup']['inline_keyboard']
        exclude_data = controls[0][1]['callback_data']
        assert exclude_data.startswith('fb:exclude:')
        assert len(exclude_data.split(':')[-1]) == 32
        assert str(publication_id) not in exclude_data
        assert any(x['text']=='🔄 Другие варианты' for row in controls for x in row)
        binding = db.get(FeedbackAction, exclude_data.split(':')[-1])
        assert binding.chat_id == chat_id and binding.publication_id == publication_id

    callback = {'update_id':99212,'callback_query':{
        'id':'fr024-ban-callback','data':exclude_data,'from':{'id':chat_id},
        'message':{'chat':{'id':chat_id,'type':'private'},'message_id':891}}}
    response = client.post('/api/telegram/webhook',json=callback,
        headers={'X-Telegram-Bot-Api-Secret-Token':settings.telegram_webhook_secret})
    assert response.json()['accepted'] is True
    with SessionLocal() as db:
        ban = db.scalar(select(UserExclusion).where(UserExclusion.chat_id==chat_id))
        assert ban and ban.canonical_id == canonical_id

    assert webhook(99213, '/new')['accepted']
    assert webhook(99214, 'купольная гончарная студия до 3000')['accepted']
    with SessionLocal() as db:
        op = dialogue_worker.claim(db)
        # /new creates an outbox, not a search operation.
        assert op
        assert dialogue_worker.process_one(db, op)
        latest = db.scalar(select(Outbox).where(Outbox.idempotency_key==f'operation:{op.id}'))
        assert latest.publication_ids == []

    assert webhook(99215, f'/unexclude {canonical_id}')['accepted']
    with SessionLocal() as db:
        assert db.scalar(select(UserExclusion.id).where(UserExclusion.chat_id==chat_id)) is None
