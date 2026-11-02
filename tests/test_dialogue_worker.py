from app.dialogue_worker import render_candidates
from app.dialogue import extract_constraints
from types import SimpleNamespace

def test_empty_results():
    assert 'нет' in render_candidates([])

def test_render_result():
    c=SimpleNamespace(title='Мастер-класс',address='Москва',price_total_kopeks=300000,source_url='https://example.org')
    assert '3000 ₽' in render_candidates([c])

def test_constraints_kept():
    assert extract_constraints('до 3000 руб',{'query':'керамика'})['query']=='керамика'
