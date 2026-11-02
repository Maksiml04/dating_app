"""v0.12: сбои и fencing tokens без реальной БД/сетевого доступа.

Эти тесты изолированные; интеграция с PostgreSQL требует отдельного стенда.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.dialogue_worker import _finish_owned, recover_stale, claim
from app.security import hash_password, verify_password, verify_secret, verify_admin_credentials
from app.worker import claim_batch, deliver_one, recover_abandoned


def test_secret_fails_closed():
    assert verify_secret('correct', 'correct')
    assert not verify_secret('', '')
    assert not verify_secret(None, 'anything')
    assert not verify_secret('wrong', 'correct')


def test_password_hash_and_credentials():
    password_hash = hash_password('длинный-секрет', iterations=100_000, salt=b'fixed-test-salt')
    assert verify_password('длинный-секрет', password_hash)
    assert not verify_password('wrong', password_hash)
    assert not verify_password('anything', 'pbkdf2_sha256$hello$broken$broken')
    cfg = SimpleNamespace(admin_username='admin', admin_password='', admin_password_hash=password_hash)
    assert verify_admin_credentials('admin', 'длинный-секрет', cfg)
    assert not verify_admin_credentials('other', 'длинный-секрет', cfg)


def test_claim_issues_new_fencing_token():
    op = SimpleNamespace(status='queued', claimed_at=None, claim_token=None, attempts=0)
    db = MagicMock()
    db.scalar.return_value = op
    assert claim(db) is op
    assert op.status == 'processing' and op.attempts == 1
    assert len(op.claim_token) == 36
    db.commit.assert_called_once()


def test_stale_dialogue_task_clears_old_ownership():
    now = datetime.now(timezone.utc)
    op = SimpleNamespace(status='processing', claimed_at=now-timedelta(seconds=300),
                         claim_token='old-token', attempts=1, chat_id=123, generation_id=5)
    db = MagicMock()
    db.scalars.return_value = [op]
    db.get.return_value = SimpleNamespace(generation_id=5)
    assert recover_stale(db, now) == 1
    assert op.status == 'queued' and op.claim_token is None and op.claimed_at is None


def test_stale_dialogue_owner_cannot_finish_reclaimed_task():
    op = SimpleNamespace(status='processing', claim_token='new-token')
    db = MagicMock()
    db.scalar.return_value = op
    assert not _finish_owned(db, uuid4(), 'old-token', 'completed')
    assert op.status == 'processing' and op.claim_token == 'new-token'
    db.rollback.assert_called_once()


def test_sender_recovers_only_safe_reservations():
    old = datetime.now(timezone.utc)-timedelta(seconds=300)
    reserved = SimpleNamespace(status='reserved', reserved_at=old, reservation_token='old')
    sending = SimpleNamespace(status='sending', reserved_at=old, reservation_token=None, last_error=None)
    db = MagicMock()
    db.scalars.side_effect = [[reserved], [sending]]
    assert recover_abandoned(db) == (1, 1)
    assert reserved.status == 'pending' and reserved.reservation_token is None
    assert sending.status == 'ambiguous' and 'ручная проверка' in sending.last_error


def test_sender_claims_unique_token_before_commit():
    row = SimpleNamespace(id=uuid4(), status='pending', reserved_at=None, reservation_token=None)
    db = MagicMock()
    db.scalars.return_value = [row]
    result = claim_batch(db)
    assert row.status == 'reserved'
    assert row.reserved_at is not None
    assert result == [(row.id, row.reservation_token)]
    assert len(row.reservation_token) == 36
    db.commit.assert_called_once()


def test_sender_rejects_stale_token_before_network():
    row = SimpleNamespace(id=uuid4(), status='reserved', reservation_token='new')
    db = MagicMock()
    db.scalar.return_value = row
    assert deliver_one(db, row, reservation_token='old') is False
    assert row.status == 'reserved'
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
