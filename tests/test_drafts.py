from app.drafts import extract_fields

def test_price_and_date():
    d=extract_fields('Гончарный мастер-класс\n25.11 в 19:30, 2 500 ₽')
    assert d['price_kopeks']==250000
    assert d['date_hint']=='25.11'
    assert d['time_hint']=='19:30'
    assert d['needs_human_verification']

def test_unknown_fields_are_unknown():
    d=extract_fields('Прогулка по парку')
    assert d['price_kopeks'] is None
    assert d['address'] is None
    assert d['kind'] is None
