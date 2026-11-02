from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from app.date_constraints import relative_interval
from app.dialogue import extract_constraints
from app.dialogue_worker import merge_constraints
from app.schemas import SearchRequest


FIXED = datetime(2026, 10, 30, 21, 30, tzinfo=ZoneInfo('Europe/Moscow'))  # Friday


def test_tomorrow_resolves_to_absolute_moscow_day_without_losing_query():
    old = {'query': 'гончарный мастер-класс'}
    result = extract_constraints('завтра', old, now=FIXED)
    assert result['query'] == old['query']
    assert result['starts_at'] == '2026-10-30T21:00:00+00:00'  # Sat 00:00 Moscow
    assert result['ends_at'] == '2026-10-31T21:00:00+00:00'
    SearchRequest(**result)


def test_date_removed_from_fulltext_query():
    c = extract_constraints('выставка завтра до 3000 рублей', {}, now=FIXED)
    assert c['query'] == 'выставка'
    assert c['budget_total_kopeks'] == 300_000
    assert c['starts_at'] and c['ends_at']


def test_weekend_from_friday_is_saturday_sunday():
    start, end = relative_interval('на выходных', now=FIXED)
    assert start == '2026-10-30T21:00:00+00:00'
    assert end == '2026-11-01T21:00:00+00:00'


def test_weekend_on_sunday_does_not_include_past_saturday():
    sunday = datetime(2026, 11, 1, 15, tzinfo=ZoneInfo('Europe/Moscow'))
    start, end = relative_interval('в выходные', now=sunday)
    assert start == sunday.astimezone(timezone.utc).isoformat()
    assert end == '2026-11-01T21:00:00+00:00'


def test_today_filters_past_events_and_handles_local_midnight():
    today = datetime(2026, 10, 31, 0, 30, tzinfo=ZoneInfo('Europe/Moscow'))
    start, end = relative_interval('сегодня', now=today)
    assert start == today.astimezone(timezone.utc).isoformat()
    assert end == '2026-10-31T21:00:00+00:00'


def test_relative_date_correction_replaces_old_slot():
    c = extract_constraints('завтра', {'query':'театр'}, now=FIXED)
    revised = extract_constraints('нет, послезавтра', c, now=FIXED)
    assert revised['query'] == 'театр'
    assert revised['starts_at'] == '2026-10-31T21:00:00+00:00'
    revised2 = extract_constraints('не завтра, а послезавтра', c, now=FIXED)
    assert revised2['starts_at'] == revised['starts_at']
    assert revised2['query'] == 'театр'


def test_negated_day_and_conflicting_dates_do_not_change_interval():
    previous = {'query': 'театр', 'starts_at': '2026-11-02T00:00:00+00:00'}
    assert extract_constraints('не завтра', previous, now=FIXED)['starts_at'] == previous['starts_at']
    assert relative_interval('сегодня или завтра', now=FIXED) is None


def test_worker_does_not_reinterpret_date_after_midnight(monkeypatch):
    previous = extract_constraints('завтра', {'query':'театр'}, now=FIXED)
    result = merge_constraints(previous, 'завтра', {'query':'завтра'})
    assert result['starts_at'] == previous['starts_at']
    assert result['ends_at'] == previous['ends_at']
    assert result['query'] == 'театр'


def test_naive_time_is_rejected():
    with pytest.raises(ValueError):
        relative_interval('завтра', now=datetime(2026, 10, 30))


def test_dst_boundary_in_different_timezone():
    # DST ends in Europe/Berlin on 2026-10-25: local day is 25 hours.
    now = datetime(2026, 10, 24, 18, tzinfo=ZoneInfo('Europe/Berlin'))
    start, end = relative_interval('завтра', now=now, timezone_name='Europe/Berlin')
    assert (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() == 25 * 3600


def test_llm_does_not_override_theme_on_date_only_reply():
    previous = {'query': 'театр'}
    for msg in ('послезавтра', 'нет, послезавтра', 'на выходных', 'сегодня до 3000 рублей'):
        result = merge_constraints(previous, msg, {'query': msg})
        assert result['query'] == 'театр', msg


def test_llm_query_cannot_reintroduce_date_word():
    result = merge_constraints({}, 'выставка завтра', {'query': 'выставка завтра'})
    assert result['query'] == 'выставка'


def test_occurrence_query_uses_future_and_half_open_start_interval():
    from uuid import uuid4
    from sqlalchemy.dialects import postgresql
    from app.search import occurrence_query
    now = datetime(2026, 10, 30, 18, tzinfo=timezone.utc)
    req = SearchRequest(starts_at='2026-10-30T21:00:00+00:00', ends_at='2026-10-31T21:00:00+00:00')
    stmt = occurrence_query(uuid4(), req, now)
    compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
    assert 'occurrence.starts_at >= ' in compiled
    assert 'occurrence.starts_at < ' in compiled
    assert 'occurrence.ends_at <=' not in compiled
    assert '2026-10-30 18:00:00' in compiled


def test_contradictory_days_dont_replace_topic():
    previous = {'query': 'выставка'}
    assert extract_constraints('сегодня или завтра', previous, now=FIXED)['query'] == 'выставка'


def test_serializable_dates_are_accepted_by_jsonb_payload():
    import json
    result = extract_constraints('послезавтра', {'query':'театр'}, now=FIXED)
    assert isinstance(json.loads(json.dumps(result))['starts_at'], str)
