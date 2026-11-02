from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from app.catalog_extraction import validate_claims, price_in_quote
from app.drafts import combine_extraction, duplicate_similarity, extract_fields
from app.verification import reason_for_verification

MESSAGE = 'Гончарный мастер-класс\n25.11 в 19:30\nСтоимость 2 500 ₽\nАдрес: ул. Ленина, 4'

def test_llm_claims_with_quotes_are_accepted():
    claims = {'title':'Гончарный мастер-класс', 'kind':'event', 'price_kopeks':250000,
              'address':'ул. Ленина, 4', 'time_hint':'19:30',
              'evidence':{'title':'Гончарный мастер-класс', 'kind':'Гончарный мастер-класс',
                          'price_kopeks':'2 500 ₽', 'address':'ул. Ленина, 4', 'time_hint':'19:30'}}
    result=validate_claims(MESSAGE,claims)
    assert result['price_kopeks']==250000
    assert result['kind']=='event'
    assert result['address']=='ул. Ленина, 4'

def test_hallucinated_address_and_price_are_rejected():
    claims = {'address':'Тверская, 7', 'price_kopeks':150000,
              'evidence':{'address':'Тверская, 7', 'price_kopeks':'2 500 ₽'}}
    assert validate_claims(MESSAGE, claims)=={}

def test_price_must_have_explicit_currency_and_exact_amount():
    assert price_in_quote('билет стоит 2 500 ₽',250000)
    assert not price_in_quote('билет стоит 2 500 ₽',150000)
    assert not price_in_quote('цена 2500',250000)
    assert not price_in_quote('2 500 ₽',True)

def test_llm_failure_preserves_heuristic():
    result=combine_extraction(MESSAGE, extractor=lambda _: {})
    assert result['price_kopeks']==250000
    assert result['extraction_method']=='heuristic'
    assert result['needs_human_verification']

def test_unproven_fields_cannot_replace_heuristics():
    fake=lambda _: {'title':'Суперфестиваль', 'price_kopeks':100, 'evidence':{'title':'Суперфестиваль','price_kopeks':'2 500 ₽'}}
    result=combine_extraction(MESSAGE,extractor=fake)
    assert result['title']=='Гончарный мастер-класс'
    assert result['price_kopeks']==250000

def test_duplicate_title_and_address_ranking():
    pub=SimpleNamespace(title='Гончарный мастер-класс', address='ул. Ленина, 4')
    match, reasons=duplicate_similarity({'title':'гончарный мастер класс','address':'ул. Ленина 4'},pub)
    mismatch, _=duplicate_similarity({'title':'гончарный мастер класс','address':'пр. Мира, 99'},pub)
    assert match>mismatch
    assert 'совпадает адрес' in reasons

def test_verification_trigger_and_expiry_priority():
    now=datetime(2026,11,1,tzinfo=timezone.utc)
    fresh=SimpleNamespace(valid_until=now+timedelta(days=9),next_verification_at=now+timedelta(days=2))
    overdue=SimpleNamespace(valid_until=now+timedelta(days=1),next_verification_at=now-timedelta(hours=1))
    expired=SimpleNamespace(valid_until=now-timedelta(hours=1),next_verification_at=now-timedelta(days=1))
    assert reason_for_verification(fresh,now) is None
    assert reason_for_verification(overdue,now)=='due'
    assert reason_for_verification(expired,now)=='expired'


def test_source_text_keeps_title_newline():
    from app.ingestion import preserve_message_lines, fingerprint
    text='  Кино под звёздами  \n   25.11    в 19:30\nЦена 2 000 ₽'
    cleaned=preserve_message_lines(text)
    assert cleaned.startswith('Кино под звёздами\n')
    assert extract_fields(cleaned)['title']=='Кино под звёздами'
    assert fingerprint(cleaned)==fingerprint('Кино под звёздами 25.11 в 19:30 Цена 2 000 ₽')


def test_verification_resolution_keeps_published_snapshot_unchanged():
    import uuid
    from app.verification import resolve_verification
    publication_id=uuid.uuid4()
    task=SimpleNamespace(id=uuid.uuid4(), publication_id=publication_id, status='pending')
    pub=SimpleNamespace(id=publication_id,canonical_id=uuid.uuid4(),version=1,
                        valid_until=datetime(2026,11,10,tzinfo=timezone.utc))
    class DB:
        def __init__(self): self.added=[]
        def get(self,model,key,**kwargs): return pub
        def add(self,obj): self.added.append(obj)
    db=DB()
    old_valid_until=pub.valid_until
    resolve_verification(db,task,'verified','moderator','Проверено')
    assert task.status=='verified'
    assert pub.valid_until==old_valid_until
    assert db.added and db.added[0].action=='verification_review'


def test_verification_requires_reason_for_unavailable():
    import uuid
    import pytest
    from app.verification import resolve_verification
    task=SimpleNamespace(id=uuid.uuid4(), publication_id=uuid.uuid4(), status='pending')
    with pytest.raises(ValueError, match='комментарий'):
        resolve_verification(None,task,'unavailable','moderator','')


def test_verification_schema_compiles_for_postgres():
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import postgresql
    from app.models import VerificationTask
    sql=str(CreateTable(VerificationTask.__table__).compile(dialect=postgresql.dialect()))
    assert 'verification_task' in sql
    assert 'verification_reason' in sql
