from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from app.ingestion import fingerprint, normalize_text, review_message

def test_fingerprint_whitespace_and_case():
    assert fingerprint('  Кино   вечером ') == fingerprint('кино вечером')

def test_normalize_text():
    assert normalize_text('Привет\n  мир') == 'Привет мир'

def test_review_rejects_unknown_decision():
    with pytest.raises(ValueError):
        review_message(None, SimpleNamespace(status='pending_review'), 'published', 'admin')

def test_review_rejects_repeated_action():
    with pytest.raises(ValueError):
        review_message(None, SimpleNamespace(status='approved'), 'approved', 'admin')
