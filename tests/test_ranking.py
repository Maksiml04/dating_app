from app.ranking import reciprocal_rank_fusion

def test_fusion_boosts_shared_results():
    result = reciprocal_rank_fusion({'fts':['a','b'],'vector':['b','c']})
    assert result[0] == 'b'
    assert set(result) == {'a','b','c'}

def test_duplicates_do_not_double_count():
    assert reciprocal_rank_fusion({'fts':['a','a','b']}) == ['a','b']

def test_empty_rankings():
    assert reciprocal_rank_fusion({'fts':[],'vector':[]}) == []
