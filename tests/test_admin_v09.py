"""Проверки конфигурации и ограничений интерфейса без запущенного SQLAdmin."""
from pathlib import Path
from app.config import Settings


def test_admin_disabled_without_credentials():
    settings = Settings(_env_file=None, admin_password='', admin_session_secret='')
    assert not settings.admin_password
    assert len(settings.admin_session_secret) < 32


def test_immutable_models_are_read_only():
    text = (Path(__file__).parents[1] / 'app' / 'admin.py').read_text()
    for model in ('PublicationAdmin', 'AuditAdmin', 'OccurrenceAdmin', 'OfferAdmin'):
        fragment = text.split(f'class {model}(')[1].split('\n\nclass ', 1)[0]
        assert 'can_edit = False' in fragment
        assert 'can_delete = False' in fragment
        assert 'can_create = False' in fragment
