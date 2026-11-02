from datetime import datetime, timezone, timedelta
import pytest
from pydantic import ValidationError
from app.schemas import PublishIn, SearchRequest
from app.dialogue import extract_constraints

def test_budget_from_text():
    r=extract_constraints('мастер-класс до 3 000 руб',{})
    assert r['budget_total_kopeks']==300000

def test_constraint_preserved():
    r=extract_constraints('что-то необычное',{'budget_total_kopeks':500000})
    assert r['budget_total_kopeks']==500000

def test_reject_timezone_naive():
    with pytest.raises(ValidationError): SearchRequest(starts_at=datetime(2026,11,1))

def test_reject_inverted_interval():
    now=datetime.now(timezone.utc)
    with pytest.raises(ValidationError): SearchRequest(starts_at=now,ends_at=now-timedelta(hours=1))

def test_event_requires_occurrence():
    now=datetime.now(timezone.utc)
    with pytest.raises(ValidationError): PublishIn(kind='event',title='Концерт',last_verified_at=now,valid_until=now+timedelta(days=1))

def test_budget_cannot_be_negative():
    with pytest.raises(ValidationError): SearchRequest(budget_total_kopeks=-1)
