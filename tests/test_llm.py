from unittest.mock import patch
from app.llm import extract_with_llm
from app.schemas import SearchRequest

def test_llm_disabled_returns_none():
    with patch('app.llm.settings') as cfg:
        cfg.llm_base_url = ''
        cfg.llm_model = ''
        assert extract_with_llm('мастер-класс') is None

def test_search_rejects_bad_budget():
    try:
        SearchRequest(budget_total_kopeks=-100)
        assert False, 'Отрицательный бюджет должен отклоняться'
    except ValueError:
        pass
