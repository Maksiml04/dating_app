"""Regression tests for defects found by the v0.21 integration audit."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import yaml

from app import catalog, indexer
from app.models import AuditLog, Canonical, PublicationVersion
from app.schemas import PublishIn
from app.webhook_probe import assess_webhook, read_webhook_info


class FakeDB:
    def __init__(self):
        self.objects = []
        self.commit_calls = 0
        self.flush_calls = 0
        self.rolled_back = False

    def add(self, obj):
        self.objects.append(obj)

    def flush(self):
        self.flush_calls += 1
        for obj in self.objects:
            if getattr(obj, 'id', None) is None:
                obj.id = uuid4()

    def scalar(self, statement):
        return None

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rolled_back = True


def payload():
    now = datetime.now(timezone.utc)
    return PublishIn(kind='place', title='Гончарный мастер-класс', address='Москва, Арбат, 1', source_url='https://example.org/event',
                     last_verified_at=now-timedelta(hours=1),
                     valid_until=now+timedelta(days=10))


def test_catalog_publish_does_not_commit_inside_nested_transaction():
    db = FakeDB()
    created = catalog.publish(db, payload(), actor='test')
    assert created['version'] == 1
    assert created['canonical_id'] is not None
    assert created['publication_version_id'] is not None
    assert db.commit_calls == 0, 'Premature commit breaks atomic draft publication'
    assert any(isinstance(item, Canonical) for item in db.objects)
    assert any(isinstance(item, PublicationVersion) for item in db.objects)
    assert any(isinstance(item, AuditLog) for item in db.objects)


def test_direct_admin_publication_has_transaction_boundary():
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).parents[1] / 'app/main.py').read_text())
    route = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'api_publish')
    calls = [n for n in ast.walk(route) if isinstance(n, ast.Call) and
             isinstance(n.func, ast.Attribute) and n.func.attr == 'commit']
    assert len(calls) == 1, 'Direct publication needs exactly one commit at the API boundary'


def test_indexer_cli_initializes_engine_before_first_query(monkeypatch):
    calls = []
    monkeypatch.setattr(indexer, 'configure_engine', lambda: calls.append('configure'))
    monkeypatch.setattr(indexer, 'run', lambda batch_size: calls.append(('run', batch_size)) or 2)
    indexer.main(['--batch-size', '7'])
    assert calls == ['configure', ('run', 7)]


def test_compose_starts_periodic_indexer():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / 'docker-compose.yml').read_text())
    service = compose['services']['indexer_worker']
    assert 'python -m app.indexer --loop' == service['command']
    assert service['depends_on']['migrate']['condition'] == 'service_completed_successfully'
    assert service['restart'] == 'unless-stopped'


def test_webhook_probe_exposes_queue_and_errors_without_token():
    secret = '123456:SECRET-BOT-TOKEN'
    def handler(request):
        assert request.url.path == f'/bot{secret}/getWebhookInfo'
        return httpx.Response(200, json={'ok': True, 'result': {
            'url': 'https://example.test/api/telegram/webhook',
            'pending_update_count': 17, 'last_error_message': 'Wrong response',
            'allowed_updates': ['message'],
        }})
    result = read_webhook_info(secret, 'https://example.test/api/telegram/webhook',
                              transport=httpx.MockTransport(handler))
    assert result['ok'] is False
    assert result['pending_update_count'] == 17
    assert any('callback_query' in x for x in result['problems'])
    assert secret not in str(result)


def test_webhook_probe_detects_missing_or_wrong_url():
    result = assess_webhook({'url': '', 'pending_update_count': 0},
                            expected_url='https://example.test/api/telegram/webhook')
    assert not result['ok']
    assert any('не установлен' in item for item in result['problems'])
    result = assess_webhook({'url': 'https://other.test/hook', 'pending_update_count': 0},
                            expected_url='https://example.test/api/telegram/webhook')
    assert any('отличается' in item for item in result['problems'])
