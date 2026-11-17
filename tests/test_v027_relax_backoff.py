"""v0.27: FR-08 кнопки ослабления условия; FR-12 backoff источников."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.dialogue import apply_relax, relax_markup, relax_offers

# --- FR-08: предложения ослабления ---

def _constraints(**overrides):
    base = {'query': 'мастер-класс', 'district': 'центр',
            'budget_total_kopeks': 500000, 'budget_original_kopeks': 500000,
            'budget_original_scope': 'pair', '_provenance': {'query': 'current_message'}}
    base.update(overrides)
    return base


def test_relax_offers_lists_only_explicit_conditions():
    offers = relax_offers(_constraints())
    assert offers == ['district', 'budget_total_kopeks']
    assert relax_offers({'query': 'выставка'}) == []


def test_relax_removes_budget_with_original_unit():
    constraints = _constraints()
    assert apply_relax(constraints, 'budget_total_kopeks') is True
    assert 'budget_total_kopeks' not in constraints
    assert 'budget_original_kopeks' not in constraints
    assert 'budget_original_scope' not in constraints
    assert constraints['query'] == 'мастер-класс'  # замысел сохранён


def test_relax_never_removes_query_or_unknown_fields():
    constraints = _constraints()
    assert apply_relax(constraints, 'query') is False
    assert apply_relax(constraints, 'starts_at') is False
    assert apply_relax(constraints, 'no_such_field') is False
    assert constraints['query'] == 'мастер-класс'


def test_relax_clears_shown_and_provenance():
    constraints = _constraints(_shown_current=['x'], _relax_offers=['district'])
    assert apply_relax(constraints, 'district') is True
    assert '_shown_current' not in constraints and '_relax_offers' not in constraints
    assert 'district' not in constraints.get('_provenance', {})


def test_relax_markup_binds_field_to_callback():
    markup = relax_markup(['district'])
    button = markup['inline_keyboard'][0][0]
    assert button['callback_data'] == 'relax:district'
    assert 'район' in button['text'].lower()


# --- FR-08: callback обрабатывается только в текущем поколении ---

def test_relax_callback_valid_only_for_offered_field():
    from app.dialogue import process_update
    from app.models import DialogueSession
    session = DialogueSession(chat_id=1, generation_id=3, state='awaiting_feedback',
                              constraints={**_constraints(), '_relax_offers': ['district']})
    db = MagicMock()
    db.get.return_value = None
    db.scalar.return_value = session  # FOR UPDATE возвращает нашу сессию

    def make_update(field):
        return {'update_id': 100, 'callback_query': {'id': f'cb-{field}', 'data': f'relax:{field}',
                'message': {'chat': {'id': 1}}}}

    assert process_update(db, make_update('district')) is True
    assert 'district' not in session.constraints
    assert '_relax_offers' not in session.constraints
    assert session.generation_id == 4  # новое поколение поиска

    # Поле, которого не было в предложениях, не принимается: constraints без изменений.
    session2 = DialogueSession(chat_id=1, generation_id=5, state='awaiting_feedback',
                               constraints={**_constraints(), '_relax_offers': ['district']})
    db.scalar.return_value = session2
    before = dict(session2.constraints)
    assert process_update(db, make_update('metro')) is False
    assert session2.constraints == before
    assert session2.generation_id == 5  # поколение не меняется у невалидного callback


# --- FR-12: backoff источников ---

def test_backoff_is_exponential_with_hour_cap():
    from app.telethon_ingest import backoff_seconds
    assert backoff_seconds(1) == 60
    assert backoff_seconds(2) == 120
    assert backoff_seconds(3) == 240
    assert backoff_seconds(10) == 3600
    assert backoff_seconds(50) == 3600
    assert backoff_seconds(0) == 60


def test_failed_source_gets_next_attempt():
    import asyncio

    from app.telethon_ingest import import_source

    class FakeSource:
        channel_id = 100
        username = '@test'
        last_message_id = 0
        last_error = None
        last_success_at = None
        consecutive_errors = 0
        next_attempt_at = None

    class FakeClient:
        async def get_entity(self, ident):
            raise RuntimeError('network down')

    source = FakeSource()
    result = asyncio.run(import_source(MagicMock(), FakeClient(), source, limit=10, edit_scan_limit=0))
    assert result.error is not None
    assert source.consecutive_errors == 1
    assert source.next_attempt_at is not None
    assert source.next_attempt_at > datetime.now(UTC)


def test_successful_source_resets_backoff():
    import asyncio

    from app.telethon_ingest import import_source

    class FakeSource:
        channel_id = 100
        username = '@testsrc'
        last_message_id = 0
        last_error = 'old'
        last_success_at = None
        consecutive_errors = 3
        next_attempt_at = datetime.now(UTC) + timedelta(minutes=30)

    class FakeClient:
        async def get_entity(self, ident):
            return SimpleNamespace(id=100)
        def iter_messages(self, *args, **kwargs):
            return _aiter([])

    async def _aiter(items):
        for item in items:
            yield item

    source = FakeSource()
    result = asyncio.run(import_source(MagicMock(), FakeClient(), source, limit=10, edit_scan_limit=0))
    assert result.error is None
    assert source.consecutive_errors == 0
    assert source.next_attempt_at is None
    assert source.last_error is None


def test_sync_skips_sources_on_backoff():
    import asyncio

    from app.telethon_ingest import sync_sources

    class FakeSource:
        def __init__(self, backoff):
            self.channel_id = 1
            self.username = None
            self.next_attempt_at = backoff
            self.enabled = True

    db = MagicMock()
    db.scalars.return_value.all.return_value = [
        FakeSource(None), FakeSource(datetime.now(UTC) + timedelta(hours=1))]
    calls = []
    async def fake_import(db, client, source, **kw):
        calls.append(source)
        return MagicMock(fetched=0, saved=0, edited=0, photos=0, error=None,
                         checkpoint=0, as_dict=dict)
    import app.telethon_ingest as ti
    original = ti.import_source
    ti.import_source = fake_import
    try:
        data = asyncio.run(sync_sources(db, MagicMock(), limit=10, edit_scan_limit=0))
    finally:
        ti.import_source = original
    assert data['skipped_backoff'] == 1
    assert data['sources'] == 1
    assert len(calls) == 1


# --- Воркер отправляет разметку предложений ---

def test_worker_includes_relax_markup_for_empty_result(monkeypatch):
    from app import worker
    from app.config import settings
    monkeypatch.setattr(settings, 'telegram_bot_token', 'test-token')
    captured = {}

    class FakeResponse:
        status_code = 200
        is_success = True
        def json(self):
            return {'ok': True}

    def fake_post(url, json=None):
        captured.update(json or {})
        return FakeResponse()

    class FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def post(self, *args, **kwargs):
            return fake_post(*args, **kwargs)

    monkeypatch.setattr(worker.httpx, 'Client', lambda timeout: FakeClient())
    locked = SimpleNamespace(id=1, chat_id=5, generation_id=1, expires_at=datetime.now(UTC) + timedelta(minutes=5),
                             publication_ids=[], text='Вариантов нет', reply_markup=relax_markup(['district']),
                             parse_mode=None, attempts=0, status='reserved', reservation_token='tok')
    db = MagicMock()
    db.get.return_value = SimpleNamespace(generation_id=1, constraints={})  # DialogueSession
    db.scalar.return_value = locked
    db.scalars.return_value = []
    assert worker.deliver_one(db, locked, reservation_token='tok') is True
    assert captured.get('reply_markup') == relax_markup(['district'])
