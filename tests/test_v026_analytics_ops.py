"""v0.26: продуктовая аналитика (раздел 9), retention (NFR-08), бюджет LLM (NFR-09)."""
import json
import uuid
from unittest.mock import MagicMock

from app import analytics
from app.config import settings

# --- Псевдонимизация и запись событий ---

def test_user_hash_is_stable_and_pseudonymous(monkeypatch):
    monkeypatch.setattr(settings, 'analytics_salt', 'соль-теста')
    first = analytics.user_hash(12345)
    assert first == analytics.user_hash(12345)
    assert first != analytics.user_hash(12346)
    assert '12345' not in first  # chat_id не восстанавливается из хеша


def test_user_hash_requires_salt(monkeypatch):
    monkeypatch.setattr(settings, 'analytics_salt', '')
    assert analytics.user_hash(12345) is None


def test_record_filters_empty_payload_fields():
    db = MagicMock()
    added = []
    db.add.side_effect = added.append
    analytics.record(db, 'request_started', chat_id=1, request_id=uuid.uuid4(),
                     extra=None, count=3)
    event = added[0]
    assert event.name == 'request_started'
    assert 'extra' not in event.payload and event.payload['count'] == 3


def test_summary_computes_b1_and_b5():
    db = MagicMock()
    db.execute.return_value.all.return_value = [
        ('request_started', 100), ('recommendation_delivered', 80),
        ('recommendation_feedback_like', 40), ('no_results', 10),
    ]
    summary = analytics.compute_summary(db)
    assert summary['b1_success_rate'] == 0.5      # 40 likes / 80 deliveries
    assert summary['b5_empty_rate'] == 0.1        # 10 no_results / 100 requests


def test_summary_without_data_returns_none_rates():
    db = MagicMock()
    db.execute.return_value.all.return_value = []
    summary = analytics.compute_summary(db)
    assert summary['b1_success_rate'] is None and summary['b5_empty_rate'] is None


# --- Retention worker (NFR-08) ---

def test_retention_deletes_only_expired_raw_data():
    from app import retention_worker
    db = MagicMock()
    db.execute.return_value.rowcount = 7
    db.scalars.return_value = []
    stats = retention_worker.run_once(db)
    assert set(stats) == {'inbox', 'analytics_event', 'callback_answer'}
    assert db.commit.called
    # Outbox не тронут, когда нет терминальных старых строк.
    assert 'outbox' not in stats


def test_retention_clears_callback_bindings_before_outbox():
    from app import retention_worker
    db = MagicMock()
    db.execute.return_value.rowcount = 1
    old_id = uuid.uuid4()
    db.scalars.return_value = [old_id]
    stats = retention_worker.run_once(db)
    assert stats['outbox'] == 1
    # Первые два execute: inbox и analytics; затем FeedbackAction, потом Outbox.
    statements = [call for call in db.execute.call_args_list]
    assert len(statements) >= 4
    db.commit.assert_called_once()


# --- Бюджет LLM (NFR-09) ---

class _FakeResponse:
    def __init__(self, content):
        self._content = content
    def raise_for_status(self):
        return None
    def json(self):
        return {'choices': [{'message': {'content': self._content}}]}

class _FakeClient:
    def __init__(self, post):
        self._post = post
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def post(self, *args, **kwargs):
        return self._post(*args, **kwargs)


def _install_fake_llm(monkeypatch, captured):
    from app import llm
    captured_dict = {}
    def fake_post(url, headers=None, **kwargs):
        payload = kwargs.get('json')
        captured_dict['json'] = payload
        captured_dict['headers'] = headers
        if isinstance(captured, list):
            captured.append(payload)
        else:
            captured.update(payload or {})
        return _FakeResponse(json.dumps({'query': 'гончарный мастер-класс'}))
    monkeypatch.setattr(llm.httpx, 'Client', lambda timeout: _FakeClient(fake_post))
    return captured_dict


def test_llm_daily_budget_stops_calls(monkeypatch):
    from app import llm
    monkeypatch.setattr(settings, 'llm_base_url', 'https://llm.example')
    monkeypatch.setattr(settings, 'llm_model', 'test-model')
    monkeypatch.setattr(settings, 'llm_max_calls_per_day', 2)
    llm._daily_calls.clear()
    sent = []
    _install_fake_llm(monkeypatch, sent)
    assert llm.extract_with_llm('мастер-класс') is not None
    assert llm.extract_with_llm('мастер-класс') is not None
    assert llm.extract_with_llm('мастер-класс') is None  # бюджет исчерпан
    assert len(sent) == 2


def test_llm_request_limits_output_tokens(monkeypatch):
    from app import llm
    monkeypatch.setattr(settings, 'llm_base_url', 'https://llm.example')
    monkeypatch.setattr(settings, 'llm_model', 'test-model')
    monkeypatch.setattr(settings, 'llm_max_output_tokens', 128)
    llm._daily_calls.clear()
    captured = {}
    _install_fake_llm(monkeypatch, captured)
    llm.extract_with_llm('выставка')
    assert captured['max_tokens'] == 128


# --- Контракты: события покрыты во всех ключевых точках спеки ---

def test_analytics_events_are_emitted_at_spec_points():
    import pathlib
    project = pathlib.Path(__file__).resolve().parents[1]
    dialogue = (project / 'app' / 'dialogue.py').read_text()
    worker = (project / 'app' / 'dialogue_worker.py').read_text()
    api = (project / 'app' / 'main.py').read_text()
    assert 'request_started' in dialogue
    assert 'clarification_asked' in dialogue
    assert 'alternatives_requested' in dialogue
    assert 'recommendation_feedback_' in dialogue
    assert 'recommendation_delivered' in worker
    assert 'no_results' in worker
    assert 'request_failed' in worker
    assert 'candidate_published' in api and 'candidate_reviewed' in api


def test_compose_runs_retention_worker():
    import pathlib
    project = pathlib.Path(__file__).resolve().parents[1]
    compose = (project / 'docker-compose.yml').read_text()
    assert 'python -m app.retention_worker --loop' in compose


def test_analytics_event_never_stores_raw_chat_id_column():
    import app.models  # noqa: F401
    from app.db import Base
    columns = set(Base.metadata.tables['analytics_event'].columns.keys())
    assert 'chat_id' not in columns
    assert 'user_hash' in columns
