"""Unit-level integration contracts. Network PostgreSQL/MTProto/S3 E2E still required."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from app import source_updates, media_storage, callback_answers, telethon_ingest
from app.models import (AuditLog, Canonical, CatalogDraft, CallbackAnswer,
                        SourceMedia, SourceMessage, SourceMessageRevision)
from app.webhook_setup import webhook_url, request

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


class ChangesDB:
    def __init__(self, message, draft=None, canonical=None):
        self.message, self.draft, self.canonical = message, draft, canonical
        self.calls = 0
        self.added = []
    def scalar(self, query):
        self.calls += 1
        return self.message if self.calls % 2 else self.draft
    def get(self, model, key, **kwargs):
        return self.canonical if model is Canonical else None
    def add(self, row):
        self.added.append(row)
    def flush(self):
        pass


def make_message(text='Фестиваль: вход 500 ₽'):
    return SourceMessage(id=uuid4(), source_id=uuid4(), message_id=22,
                         text=text, message_date=NOW, status='approved',
                         content_hash=__import__('app.ingestion', fromlist=['fingerprint']).fingerprint(text),
                         revision=1)


def test_edited_source_hides_published_catalog_and_invalidates_approval():
    row = make_message()
    canonical = Canonical(id=uuid4(), status='published', kind='event')
    draft = CatalogDraft(source_message_id=row.id, canonical_id=canonical.id,
                         status='published', extracted={'title': 'Фестиваль'}, duplicate_candidates=[])
    db = ChangesDB(row, draft, canonical)
    result, changed = source_updates.reconcile_edit(
        db, row.source_id, row.message_id, 'Фестиваль: вход 900 ₽', NOW)
    assert changed and result is row
    assert row.revision == 2 and row.status == 'pending_review'
    assert row.reviewed_at is None and row.content_hash != ''
    assert canonical.status == 'review_required' and draft.status == 'source_changed'
    assert [x.revision for x in db.added if isinstance(x, SourceMessageRevision)] == [1, 2]
    assert any(isinstance(x, AuditLog) for x in db.added)


def test_repeated_same_edit_does_not_create_another_revision():
    row = make_message()
    row.edited_at = NOW
    db = ChangesDB(row)
    _, changed = source_updates.reconcile_edit(db, row.source_id, 22, row.text, NOW)
    assert not changed and row.revision == 1 and db.added == []


def test_media_only_revision_forces_moderation():
    row = make_message('Без изменений')
    db = ChangesDB(row)
    _, changed = source_updates.reconcile_edit(db, row.source_id, row.message_id,
                                               row.text, NOW, force=True)
    assert changed and row.revision == 2 and row.status == 'pending_review'


class S3:
    def __init__(self):
        self.upload = None
    def head_bucket(self, **kwargs):
        return {'ok': True}
    def put_object(self, **kwargs):
        self.upload = kwargs


class MediaDB:
    def __init__(self):
        self.current = None
    def scalar(self, query):
        return self.current
    def add(self, value):
        self.current = value
    def flush(self):
        pass


def test_image_saved_privately_with_content_hash_and_metadata():
    row = make_message()
    db, s3 = MediaDB(), S3()
    data = b'\xff\xd8\xff' + b'photo bytes'
    stored = media_storage.store_photo(db, row, data, s3=s3, telegram_photo_id=123)
    assert stored.content_type == 'image/jpeg' and stored.telegram_photo_id == 123
    assert stored.sha256 in stored.object_key
    assert s3.upload['Body'].read() == data
    assert s3.upload['Bucket'] == 'dateideas-media'
    assert not stored.object_key.startswith('http')


def test_unsupported_media_and_oversized_photo_rejected(monkeypatch):
    with pytest.raises(ValueError):
        media_storage.image_type(b'not a jpeg')
    monkeypatch.setattr(media_storage.settings, 'telegram_media_max_bytes', 5)
    with pytest.raises(ValueError):
        media_storage.store_photo(MediaDB(), make_message(), b'\xff\xd8\xffxxx', s3=S3())


class AckDB:
    def __init__(self):
        self.item = None
        self.commits = 0
    def get(self, model, key):
        return self.item
    def add(self, item):
        self.item = item
    def commit(self):
        self.commits += 1


def test_callback_is_queued_then_acknowledged_by_worker(monkeypatch):
    db = AckDB()
    callback_answers.queue_answer(db, 'callback_1', 'Спасибо')
    assert isinstance(db.item, CallbackAnswer)
    callback_answers.queue_answer(db, 'callback_1', 'Дубликат')
    assert db.item.text == 'Спасибо'
    db.item.status = 'sending'
    monkeypatch.setattr(callback_answers.settings, 'telegram_bot_token', '123:TEST')
    seen = {}
    def fake_transport(url, body):
        seen.update(body)
        return NS(is_success=True, status_code=200, json=lambda: {'ok': True})
    assert callback_answers.deliver_answer(db, 'callback_1', transport=fake_transport)
    assert db.item.status == 'delivered' and seen['callback_query_id'] == 'callback_1'


def test_callback_retries_network_errors(monkeypatch):
    db = AckDB()
    callback_answers.queue_answer(db, 'callback_2', '')
    db.item.status = 'sending'
    monkeypatch.setattr(callback_answers.settings, 'telegram_bot_token', '123:TEST')
    assert not callback_answers.deliver_answer(db, 'callback_2',
                 transport=lambda *_: (_ for _ in ()).throw(ValueError('broken json')))
    assert db.item.status == 'pending'


@pytest.mark.parametrize('base', ['http://example.com', 'https://localhost',
                                  'https://example.com/private', 'https://a.com/?key=1'])
def test_webhook_rejects_wrong_base(base):
    with pytest.raises(ValueError):
        webhook_url(base)


def test_webhook_provisioning_does_not_expose_bot_token(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'telegram_bot_token', '123:SECRET')
    monkeypatch.setattr(settings, 'telegram_webhook_secret', 'a' * 32)
    monkeypatch.setattr(settings, 'public_base_url', 'https://date.example')
    result = request('setWebhook', transport=lambda url, body: NS(
        status_code=200, raise_for_status=lambda: None, json=lambda: {'ok': True}))
    assert result['url'] == 'https://date.example/api/telegram/webhook'
    assert 'SECRET' not in str(result)


def test_edit_scan_runs_with_no_new_posts(monkeypatch):
    source = NS(id=uuid4(), channel_id=100, username='public_channel', last_message_id=10)
    message = NS(id=9, message='Изменённая цена', date=NOW, edit_date=NOW, photo=None)
    class Client:
        async def get_entity(self, *_): return NS(id=100)
        def iter_messages(self, entity, **kwargs):
            async def gen():
                if 'min_id' not in kwargs:
                    yield message
            return gen()
    class DB:
        def __init__(self): self.commits = 0
        def commit(self): self.commits += 1
        def rollback(self): pass
        def scalar(self, _): return None
    db = DB()
    monkeypatch.setattr(telethon_ingest, 'reconcile_edit', lambda *args: (None, False))
    stat = asyncio.run(telethon_ingest.import_source(db, Client(), source, edit_scan_limit=5))
    assert not stat.error and stat.checkpoint == 10 and stat.fetched == 0


def test_changed_draft_is_reextracted_after_source_reapproval(monkeypatch):
    from app import drafts
    item = make_message('Новая цена: 200 ₽')
    item.status = 'approved'
    existing = CatalogDraft(source_message_id=item.id, status='source_changed',
                            extracted={'title': 'Устарело'}, duplicate_candidates=[])
    class DB:
        def __init__(self): self.flushed = 0
        def scalar(self, _): return existing
        def flush(self): self.flushed += 1
    monkeypatch.setattr(drafts, 'combine_extraction',
                        lambda text, extractor: {'title': 'Новая версия'})
    monkeypatch.setattr(drafts, 'candidate_duplicates', lambda db, value: [])
    db = DB()
    result = drafts.prepare_draft(db, item)
    assert result is existing and existing.status == 'needs_review'
    assert existing.extracted['title'] == 'Новая версия' and db.flushed == 1


def test_edited_post_lookback_invokes_reconciliation_even_without_new_ids(monkeypatch):
    seen = []
    source = NS(id=uuid4(), channel_id=111, username='channel_public', last_message_id=100)
    old = NS(id=99, message='Скидка изменилась', date=NOW, edit_date=NOW, photo=None)
    class Client:
        async def get_entity(self, handle): return NS(id=111)
        def iter_messages(self, ent, **kwargs):
            async def generate():
                if kwargs.get('limit') == 3:
                    yield old
            return generate()
    class DB:
        def commit(self): pass
        def rollback(self): pass
    def fake_reconcile(db, sid, mid, text, edit_date):
        seen.append((sid, mid, text))
        return None, False
    monkeypatch.setattr(telethon_ingest, 'reconcile_edit', fake_reconcile)
    stats = asyncio.run(telethon_ingest.import_source(DB(), Client(), source,
                                                       limit=20, edit_scan_limit=3))
    assert stats.error is None and stats.checkpoint == 100
    assert seen == [(source.id, 99, 'Скидка изменилась')]
