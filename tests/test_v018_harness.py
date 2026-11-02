"""Локальные проверки безопасности E2E стенда — без Docker/PostgreSQL."""
from pathlib import Path


def test_isolated_e2e_compose_is_not_production_compose():
    project = Path(__file__).resolve().parents[1]
    contents = (project / 'docker-compose.e2e.yml').read_text()
    assert 'name: dateideas-e2e' in contents
    assert 'dateideas_test' in contents
    assert 'tmpfs:' in contents
    assert 'ports:' not in contents
    assert 'telegram_ingest:' not in contents
    assert 'env_file:' not in contents


def test_e2e_fails_closed_without_test_database():
    path = Path(__file__).resolve().parent / 'test_v018_e2e.py'
    contents = path.read_text()
    assert "pytest.skip('E2E разрешён только для отдельной БД dateideas_test')" in contents
    assert "settings.telegram_bot_token == 'fake-e2e-bot-token'" in contents
    assert 'httpx.MockTransport(fake_telegram)' in contents


def test_e2e_runner_cleans_up_test_db():
    script = (Path(__file__).resolve().parents[1] / 'scripts' / 'e2e-compose.sh').read_text()
    assert 'trap cleanup EXIT' in script
    assert 'down --volumes --remove-orphans' in script
    assert '--exit-code-from e2e' in script


def test_dockerignore_excludes_secrets():
    rules = (Path(__file__).resolve().parents[1] / '.dockerignore').read_text().splitlines()
    assert '.env' in rules
    assert '.env.*' in rules
    assert '*.session' in rules
