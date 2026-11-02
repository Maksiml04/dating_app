from app.dialogue import extract_constraints, clarification_question
from app.dialogue_worker import merge_constraints

def test_short_date_does_not_replace_topic():
    old = {'query': 'гончарный мастер-класс'}
    for message in ('завтра', 'на выходных', 'на двоих', 'в центре'):
        assert extract_constraints(message, old)['query'] == old['query']

def test_metro_and_indoor_slot_updates():
    old = {'query': 'выставка'}
    result = extract_constraints('метро Китай-город', old)
    assert result['query'] == 'выставка'
    assert result['metro'] == 'Китай-город'
    result = extract_constraints('в помещении', result)
    assert result['indoors'] is True
    assert extract_constraints('на улице', result)['indoors'] is False

def test_llm_does_not_erase_topic_on_short_reply():
    previous = {'query': 'квест'}
    result = merge_constraints(previous, 'завтра', {'query': 'завтра'})
    assert result['query'] == 'квест'

def test_query_is_still_replaceable():
    assert extract_constraints('лучше театр', {'query': 'выставка'})['query'] == 'театр'

def test_clarification_still_required_without_topic():
    assert clarification_question(extract_constraints('завтра', {}))
