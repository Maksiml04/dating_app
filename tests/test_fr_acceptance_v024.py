"""Contract-level acceptance checks for the FR gaps closed in v0.24.

These tests are NOT a substitute for PostgreSQL/Telegram/SQLAdmin end-to-end runs.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.dialogue import clarification_question, extract_constraints
from app.dialogue_worker import render_candidates
from app.models import Canonical, FeedbackAction, TelegramSource, UserExclusion
from app.schemas import PublishIn
from app.user_controls import canonical_root, is_excluded
from app.weather import assess_outdoors

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def payload(**kwargs):
    common = dict(kind='place', title='Парк', address='Москва, улица Ленина, 1',
                  source_url='https://example.org/park', last_verified_at=NOW,
                  valid_until=NOW + timedelta(days=10))
    return PublishIn(**{**common, **kwargs})


def test_fr02_per_person_budget_preserves_original_unit_and_source():
    value = extract_constraints('Гончарный мастер-класс до 2 000 рублей на человека', {})
    assert value['budget_total_kopeks'] == 400_000
    assert value['budget_original_kopeks'] == 200_000
    assert value['budget_original_scope'] == 'person'
    assert value['_provenance']['budget_total_kopeks'] == 'current_message'
    assert value['query'].lower() == 'гончарный мастер-класс'


def test_fr02_budgets_do_not_alter_persistent_preference():
    out = extract_constraints('до 5000 руб', {'query': 'театр', 'budget_total_kopeks': 300000})
    assert out['query'] == 'театр'
    assert out['budget_total_kopeks'] == 500000


def test_fr03_ask_only_one_meaningful_question():
    assert clarification_question({'query': 'музей'}) is None
    assert 'пятниц' in clarification_question({'query': 'музей', '_ambiguous_friday': True})
    assert 'помещени' in clarification_question({'query': 'музей', '_ambiguous_indoor': True})


def test_fr05_ambiguous_friday_needs_confirmation():
    out = extract_constraints('хочу на выставку в пятницу', {}, now=NOW)
    assert out['_ambiguous_friday'] is True
    assert not out.get('starts_at')
    confirmed = extract_constraints('в эту пятницу', out, now=NOW)
    assert confirmed['_ambiguous_friday'] is None if '_ambiguous_friday' in confirmed else True
    assert datetime.fromisoformat(confirmed['starts_at']).tzinfo is not None


def test_fr07_adverse_weather_from_transport_only():
    timestamp = (NOW + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
    def reply(request):
        assert request.url.path == '/v1/forecast'
        return httpx.Response(200, json={'hourly':{
            'time':[timestamp], 'precipitation_probability':[90], 'wind_speed_10m':[10]}})
    out = assess_outdoors(NOW+timedelta(days=1), NOW+timedelta(days=1,hours=1),
                          transport=httpx.MockTransport(reply), now=NOW)
    assert out.status == 'adverse'


def test_fr07_forecast_failure_never_invented():
    def reply(request): return httpx.Response(503)
    out = assess_outdoors(NOW+timedelta(days=1), NOW+timedelta(days=1,hours=1),
                          transport=httpx.MockTransport(reply), now=NOW)
    assert out.status == 'unknown'


def test_fr08_absence_causes_are_distinct():
    texts = [render_candidates([], no_results_reason=x) for x in
             ('NO_MATCH','UNKNOWN_REQUIRED_DATA','EXHAUSTED','DEPENDENCY_FAILURE')]
    assert len(set(texts)) == 4
    assert 'не ослаблены' in texts[1]


def test_fr09_10_output_keeps_verified_fields_without_fake_booking():
    c = SimpleNamespace(title='Музей', description='Экскурсия', category='культура',
                        address='Москва, Тверская, 1', source_url='https://example.org/view',
                        price_total_kopeks=0, starts_at=NOW, booking_url=None,
                        reason_codes=['проверенная публикация'])
    text = render_candidates([c])
    assert 'Москва, Тверская, 1' in text
    assert 'https://example.org/view' in text
    assert '0 ₽' in text
    assert 'Бронирование:' not in text


def test_fr11_reject_unsafe_or_missing_url():
    for url in ('http://example.org', 'javascript:alert(1)', 'https://u:p@example.com/x', ''):
        with pytest.raises(ValidationError): payload(source_url=url)
    assert payload(booking_url='https://example.org/book').booking_url.endswith('/book')


def test_fr14_incomplete_address_rejected_at_contract_boundary():
    with pytest.raises(ValidationError): payload(address=None)


def test_fr15_event_needs_linked_place_and_session():
    with pytest.raises(ValidationError): payload(kind='event', starts_at=NOW+timedelta(days=1),
                                                   ends_at=NOW+timedelta(days=1,hours=2))
    event = payload(kind='event', parent_place_id=uuid4(),
                    starts_at=NOW+timedelta(days=1),
                    ends_at=NOW+timedelta(days=1,hours=2))
    assert event.parent_place_id


def test_fr18_19_models_have_ownership_and_unique_guards():
    assert set(UserExclusion.__table__.columns.keys()) >= {'chat_id', 'canonical_id'}
    assert any(x.name == 'uq_exclusion_chat_canonical' for x in UserExclusion.__table__.constraints)
    assert 'outbox_id' in FeedbackAction.__table__.columns
    assert 'parent_place_id' in Canonical.__table__.columns


class RootsDB:
    def __init__(self, root, alias, parent):
        self.root = root
        self.alias = alias
        self.parent = parent
    def scalar(self, statement):
        sql = str(statement.compile(dialect=postgresql.dialect(),
                                    compile_kwargs={'literal_binds': True}))
        if str(self.alias) in sql:
            return self.root
        return None
    def scalars(self, stmt):
        return [self.root]
    def get(self, model, pk):
        if model is Canonical and pk == self.parent: return SimpleNamespace(parent_place_id=self.root)
        return None


def test_fr19_21_exclude_place_covers_related_event_and_alias():
    root, alias, event = uuid4(), uuid4(), uuid4()
    db = RootsDB(root, alias, event)
    assert canonical_root(db, alias) == root
    assert is_excluded(db, 123, event) is True


def test_fr12_source_has_diagnostics():
    cols = TelegramSource.__table__.columns
    assert 'last_success_at' in cols and 'last_error' in cols


def test_fr02_date_time_district_are_separate_slots():
    from app.dialogue import extract_constraints
    data = extract_constraints('завтра вечером в центре до 3000 на двоих, мастер-класс', {}, now=NOW)
    assert data['budget_total_kopeks'] == 300000
    assert data['time_period'] == 'evening'
    assert data['district'] == 'центр'
    assert data['query'] == 'мастер-класс'
    assert data['starts_at'].startswith('2026-10-30T21:00:00')
    assert data['_provenance']['time_period'] == 'current_message'


def test_fr02_new_budget_does_not_remove_date_or_location():
    previous = extract_constraints('завтра в центре театр', {}, now=NOW)
    result = extract_constraints('до 4500 на двоих', previous, now=NOW)
    assert result['query'] == 'театр'
    assert result['district'] == 'центр'
    assert result['starts_at'] == previous['starts_at']
    assert result['budget_total_kopeks'] == 450000


def test_fr04_contradictory_indoor_constraints_are_not_a_new_topic():
    result = extract_constraints('в помещении и на улице', {'query':'музей'})
    assert result['_ambiguous_indoor'] is True
    assert result['query'] == 'музей'
    assert 'помещени' in clarification_question(result)


def test_fr04_explicit_profile_notes_are_only_soft_ranking():
    from app.feedback_ranking import reorder_by_feedback
    a = SimpleNamespace(title='Гончарный мастер-класс', description='',
                        category='творчество', publication_version_id=uuid4())
    b = SimpleNamespace(title='Театр', description='',
                        category='театр', publication_version_id=uuid4())
    result = reorder_by_feedback([b,a], explicit_preferences='гончарный')
    assert result == [a,b]
    assert len(result) == 2  # not a hard filter


def test_fr05_past_relative_date_requires_clarification():
    result = extract_constraints('вчера', {}, now=NOW)
    assert not result.get('starts_at')
    assert 'прошла' in clarification_question(result)


def test_fr06_centre_is_an_explicit_group_not_llm_geography():
    from app.search import CENTER_DISTRICTS
    assert {'арбат', 'пресненский', 'замоскворечье'} <= CENTER_DISTRICTS


def test_fr07_favorable_weather_and_no_data_are_distinct():
    timestamp = (NOW+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={'hourly':{
        'time':[timestamp], 'precipitation_probability':[0], 'wind_speed_10m':[3]}}))
    favorable = assess_outdoors(NOW+timedelta(days=1), NOW+timedelta(days=1,hours=1),
                                now=NOW, transport=transport)
    assert favorable.status == 'favorable'
    too_far = assess_outdoors(NOW+timedelta(days=30), NOW+timedelta(days=30,hours=1), now=NOW)
    assert too_far.status == 'unknown'


def test_fr11_booking_is_displayed_only_when_known():
    item = SimpleNamespace(title='Музей', description='', category='музей',
        address='Москва, 1-й переулок, 5', source_url='https://example.org',
        booking_url='https://example.org/tickets', starts_at=None,
        price_total_kopeks=120000, reason_codes=[])
    text = render_candidates([item])
    assert 'https://example.org/tickets' in text
    assert 'Забронировано' not in text


def test_fr06_opening_hours_respect_time_and_full_duration():
    from app.opening_hours import place_available
    # 2026-11-02 is Monday; local 18:00 to 21:00.
    start = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
    end = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
    week = {'0': [['10:00', '20:00']]}
    assert place_available(week, {}, starts_at=start, ends_at=end,
                           time_period='evening', duration_minutes=60, now=NOW)
    assert not place_available(week, {}, starts_at=start, ends_at=end,
                               time_period='evening', duration_minutes=180, now=NOW)


def test_fr06_closures_override_weekly_schedule():
    from app.opening_hours import place_available
    start = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    end = start+timedelta(hours=3)
    week = {'0': [['09:00','21:00']]}
    assert not place_available(week, {'2026-11-02':None},
        starts_at=start,ends_at=end,now=NOW)


def test_fr06_overnight_schedule_belongs_to_previous_day():
    from app.opening_hours import place_available
    # Tuesday 01:00–02:00 Moscow is Monday 22:00–23:00 UTC.
    start = datetime(2026, 11, 2, 22, 0, tzinfo=timezone.utc)
    end = start+timedelta(hours=1)
    week = {'0': [['20:00','02:00']]}
    assert place_available(week, {}, starts_at=start,ends_at=end,
                           time_period='night',duration_minutes=45,now=NOW)


def test_fr06_malformed_opening_hours_rejected_before_publication():
    with pytest.raises(ValidationError):
        payload(opening_hours={'0':[['40:00','22:00']]})
    with pytest.raises(ValidationError):
        payload(opening_hours={'9':[['10:00','20:00']]})


def test_fr02_duration_is_normalized_with_provenance():
    r = extract_constraints('мастер-класс на 2 часа', {})
    assert r['duration_minutes'] == 120
    assert r['_provenance']['duration_minutes'] == 'current_message'


def test_fr02_interest_and_format_slots_are_separate_from_query():
    data = extract_constraints('интересы: танцы, кулинария; формат: спокойный; до 3000 рублей', {})
    assert data['interests'] == ['танцы', 'кулинария']
    assert data['format'] == 'спокойный'
    assert not data.get('query')
    assert data['_provenance']['interests'] == 'current_message'
    assert data['_provenance']['format'] == 'current_message'


def test_fr04_indoors_is_hard_even_in_full_free_text():
    data = extract_constraints('Хочу выставку в помещении до 3000 рублей', {})
    assert data['query'].casefold() == 'хочу выставку'
    assert data['indoors'] is True
    assert data['budget_total_kopeks'] == 300000


def test_fr06_event_duration_is_a_database_eligibility_filter():
    from app.search import occurrence_query
    from app.schemas import SearchRequest
    compiled = str(occurrence_query(uuid4(), SearchRequest(duration_minutes=120), NOW).compile(
        dialect=postgresql.dialect(), compile_kwargs={'literal_binds':False}))
    assert 'occurrence.ends_at >=' in compiled
    assert 'occurrence.starts_at +' in compiled


def test_fr04_explicit_ban_matches_named_place_only(monkeypatch):
    from app import user_controls
    root = uuid4()
    class DummySession:
        def scalar(self, query): return 'Музей современной керамики'
    monkeypatch.setattr(user_controls, 'excluded_roots', lambda db, chat_id: {root})
    assert user_controls.explicitly_requested_exclusion(DummySession(), 42, 'Музей современной керамики') == root
    assert user_controls.explicitly_requested_exclusion(DummySession(), 42, 'музей') is None


def test_fr10_price_starting_at_is_not_presented_as_guaranteed_fixed_price():
    c = SimpleNamespace(title='Место', description='', category='кафе',
        address='Москва, Пушкина, 1', source_url='https://example.org',
        price_total_kopeks=150000, price_is_bounded=False,
        starts_at=None, booking_url=None, reason_codes=[])
    assert 'от 1500 ₽' in render_candidates([c])


def test_fr20_delete_requires_prior_confirmation_code():
    from inspect import getsource
    from app.dialogue import process_update
    source = getsource(process_update)
    assert "text == '/delete_data confirm' and" in source
    assert "'_delete_confirmation'" in source


def test_fr02_metro_in_a_complete_request_retains_topic_and_budget():
    value = extract_constraints('выставка у метро Арбатская до 3000 рублей', {})
    assert value['metro'] == 'Арбатская'
    assert value['query'] == 'выставка'
    assert value['budget_total_kopeks'] == 300000


def test_fr02_two_word_metro_station_is_not_search_topic():
    value = extract_constraints('мастер-класс у метро Площадь Революции вечером', {})
    assert value['metro'] == 'Площадь Революции'
    assert value['query'] == 'мастер-класс'
    assert value['time_period'] == 'evening'


def test_fr14_sqladmin_requires_expose_support_in_modelview():
    from pathlib import Path
    import tomllib
    cfg = tomllib.loads((Path(__file__).parents[1]/'pyproject.toml').read_text())
    assert 'sqladmin>=0.21,<1' in cfg['project']['dependencies']
