"""v0.25: фото в карточках, метрики, rate limiting, /liked."""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.schemas import PublishIn


# --- FR-22: привязка фото к публикации ---

def _publish_payload(**overrides):
    now = datetime.now(timezone.utc)
    base = dict(kind='place', title='Гончарная студия', description='',
                address='Москва, ул. Пятницкая, 1', source_url='https://example.com/post',
                last_verified_at=now, valid_until=now + timedelta(days=7))
    base.update(overrides)
    return PublishIn(**base)


def test_publish_in_accepts_source_message_reference():
    payload = _publish_payload(source_message_id=uuid.uuid4())
    assert payload.source_message_id is not None


def test_publish_in_photo_reference_is_optional():
    assert _publish_payload().source_message_id is None


def test_catalog_publish_rejects_unapproved_source():
    from app.catalog import publish
    db = MagicMock()
    db.get.return_value = SimpleNamespace(status='pending_review')
    try:
        publish(db, _publish_payload(source_message_id=uuid.uuid4()), actor='test')
        assert False, 'должна быть ошибка валидации'
    except ValueError as exc:
        assert 'проверенному исходному' in str(exc)


def test_catalog_publish_sets_source_message_on_version():
    from app.catalog import publish
    from app.models import PublicationVersion, SourceMessage
    canonical = SimpleNamespace(id=uuid.uuid4(), kind='place', status='draft',
                                parent_place_id=None)
    db = MagicMock()
    db.get.side_effect = lambda model, pk, **kw: (
        SimpleNamespace(status='approved') if model is SourceMessage else canonical)
    db.scalar.return_value = None
    added = []
    def capture(obj):
        if isinstance(obj, PublicationVersion):
            added.append(obj)
    db.add.side_effect = capture
    publish(db, _publish_payload(source_message_id=uuid.uuid4()), actor='test')
    assert added and added[0].source_message_id is not None


# --- Воркер: best-effort отправка фото ---

def test_publication_photo_requires_source_message():
    from app.worker import publication_photo
    from app.models import PublicationVersion
    db = MagicMock()
    db.get.side_effect = lambda model, pk, **kw: (
        PublicationVersion(source_message_id=None, title='x') if model is PublicationVersion else None)
    assert publication_photo(db, uuid.uuid4()) is None


def test_publication_photo_reads_private_object():
    from app.worker import publication_photo
    from app.models import PublicationVersion
    pub = PublicationVersion(source_message_id=uuid.uuid4(), title='Студия')
    media = SimpleNamespace(object_key='telegram/s/1/abc.jpg', content_type='image/jpeg')
    db = MagicMock()
    db.get.return_value = pub
    db.scalar.return_value = media
    body = MagicMock()
    body.read.return_value = b'\xff\xd8\xff' + b'x' * 10
    body.close = lambda: None
    import app.media_storage as storage
    original = storage.client
    storage.client = lambda: SimpleNamespace(get_object=lambda **kw: {'Body': body})
    try:
        result = publication_photo(db, uuid.uuid4())
    finally:
        storage.client = original
    assert result is not None
    assert result['title'] == 'Студия'
    assert result['filename'] == 'abc.jpg'


def test_deliver_photos_never_raises():
    from app import worker
    db = MagicMock()
    db.get.side_effect = RuntimeError('storage down')
    outbox = SimpleNamespace(chat_id=1, publication_ids=[str(uuid.uuid4())])
    worker.deliver_photos(db, outbox)  # не должно падать


def test_deliver_photos_skips_without_token(monkeypatch):
    from app import worker
    from app.config import settings
    monkeypatch.setattr(settings, 'telegram_bot_token', '')
    called = []
    monkeypatch.setattr(worker, 'publication_photo', lambda db, pid: called.append(pid))
    worker.deliver_photos(MagicMock(), SimpleNamespace(chat_id=1, publication_ids=[str(uuid.uuid4())]))
    assert not called


# --- Метрики ---

def test_metrics_render_contains_registered_counter():
    from app import metrics as registry
    registry.increment('test_metric_total', 3)
    text = registry.render()
    assert '# TYPE test_metric_total counter' in text
    assert 'test_metric_total 3' in text


def test_metrics_do_not_contain_chat_ids():
    from app import metrics as registry
    registry.increment('webhook_updates_total')
    assert 'chat_id' not in registry.render()


# --- Rate limiting ---

def test_rate_limit_blocks_after_limit():
    from app.rate_limit import allow
    chat = uuid.uuid4().int % 10**9
    for _ in range(5):
        assert allow(chat, limit=5, now=100.0)
    assert not allow(chat, limit=5, now=100.0)
    assert allow(chat, limit=5, now=100.0 + 61)  # окно истекло


def test_rate_limit_is_independent_per_chat():
    from app.rate_limit import allow
    a, b = 10**9 + 1, 10**9 + 2
    for _ in range(3):
        assert allow(a, limit=3, now=200.0)
    assert allow(b, limit=3, now=200.0)
    assert not allow(a, limit=3, now=200.0)


# --- /liked ---

def test_liked_summary_empty_history():
    from app.dialogue_controls import liked_summary
    db = MagicMock()
    db.execute.return_value.all.return_value = []
    assert 'Пока нет отметок' in liked_summary(db, 1)


def test_liked_summary_lists_recent_likes():
    from app.dialogue_controls import liked_summary
    db = MagicMock()
    db.execute.return_value.all.return_value = [
        ('Гончарная студия', 'place', datetime(2026, 11, 17, tzinfo=timezone.utc)),
        ('Джаз-вечер', 'event', datetime(2026, 11, 16, tzinfo=timezone.utc)),
    ]
    answer = liked_summary(db, 1)
    assert 'Гончарная студия (место)' in answer
    assert 'Джаз-вечер (событие)' in answer
