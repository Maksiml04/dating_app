from app.dialogue import clarification_question
from app.dialogue_worker import merge_constraints, MAX_ATTEMPTS, LEASE_SECONDS

def test_ask_for_missing_intent():
    assert clarification_question({'budget_total_kopeks':300000})
    assert clarification_question({'query':'прогулка'}) is None

def test_followup_preserves_budget():
    r=merge_constraints({'budget_total_kopeks':300000},'гончарный мастер-класс',None)
    assert r['query']=='гончарный мастер-класс'
    assert r['budget_total_kopeks']==300000

def test_llm_does_not_erase_existing_budget():
    r=merge_constraints({'budget_total_kopeks':300000},'керамика',{'query':'керамика','budget_total_kopeks':None})
    assert r['budget_total_kopeks']==300000

def test_explicit_budget_change():
    r=merge_constraints({'query':'керамика','budget_total_kopeks':300000},'до 5000 руб',None)
    assert r['budget_total_kopeks']==500000

def test_recovery_limits_are_positive():
    assert MAX_ATTEMPTS>=2 and LEASE_SECONDS>=30
