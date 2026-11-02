from types import SimpleNamespace
from uuid import uuid4
from app.dialogue import extract_constraints, clarification_question
from app.dialogue_worker import merge_constraints
from app.feedback_ranking import reorder_by_feedback


def test_multiturn_budget_does_not_erase_topic():
    first=extract_constraints('гончарный мастер-класс',{})
    second=extract_constraints('до 3000 рублей',first)
    assert second['query']=='гончарный мастер-класс'
    assert second['budget_total_kopeks']==300000
    assert clarification_question(second) is None


def test_explicit_budget_change():
    result=extract_constraints('нет, до 5000 руб',{'query':'выставка','budget_total_kopeks':300000})
    assert result['query']=='выставка'
    assert result['budget_total_kopeks']==500000


def test_explicit_budget_clear():
    result=extract_constraints('без ограничений по бюджету',{'query':'театр','budget_total_kopeks':200000})
    assert result=={'query':'театр'}


def test_merge_llm_nullable_fields():
    out=merge_constraints({'query':'квест','budget_total_kopeks':300000},'до 4000 руб',{'query':None,'budget_total_kopeks':300000})
    assert out['budget_total_kopeks']==400000 and out['query']=='квест'


def test_feedback_soft_rank_and_repeat():
    a=SimpleNamespace(category='театр', publication_version_id=uuid4())
    b=SimpleNamespace(category='мастер-класс', publication_version_id=uuid4())
    c=SimpleNamespace(category='выставка', publication_version_id=uuid4())
    result=reorder_by_feedback([a,b,c],positive_categories=['мастер-класс'],seen_publication_ids=[a.publication_version_id])
    assert result[0] is b
    assert set(map(id,result))==set(map(id,[a,b,c]))


def test_feedback_no_hard_exclusion():
    a=SimpleNamespace(category='театр',publication_version_id=uuid4())
    assert reorder_by_feedback([a],negative_categories=['театр'])==[a]
