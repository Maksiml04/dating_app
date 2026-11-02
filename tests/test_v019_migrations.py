"""Проверки цепочки миграций без Docker/PostgreSQL.

Offline SQL нужен, чтобы ловить ошибки идентификаторов ревизий и создание
повторных таблиц при развёртывании из пустой базы, ещё до запуска Compose.
"""
import re
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.initial_schema_v0001 import Base as InitialBase
from app.db import Base as CurrentBase
import app.models  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def test_migration_graph_has_one_head_and_all_revisions():
    config = Config(str(ROOT / 'alembic.ini'))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ['0015_opening_hours']
    revisions = list(script.walk_revisions())
    assert len(revisions) == 15
    assert revisions[-1].revision == '0001'
    assert revisions[-1].down_revision is None
    assert revisions[-2].revision == '0002'
    assert revisions[-5].revision == '0005_operation_leases'


def test_first_migration_is_frozen_historical_schema():
    tables = set(InitialBase.metadata.tables)
    assert 'canonical' in tables
    assert 'request_operation' in tables
    assert 'catalog_draft' not in tables
    assert 'source_message' not in tables
    assert 'verification_task' not in tables
    assert 'callback_id' not in InitialBase.metadata.tables['feedback'].columns
    assert 'claimed_at' not in InitialBase.metadata.tables['request_operation'].columns
    assert 'claim_token' not in InitialBase.metadata.tables['request_operation'].columns
    assert 'reserved_at' not in InitialBase.metadata.tables['telegram_outbox'].columns
    assert set(InitialBase.metadata.tables).issubset(CurrentBase.metadata.tables)


def test_offline_upgrade_generates_consistent_schema():
    result = subprocess.run(
        [sys.executable, '-m', 'alembic', 'upgrade', 'head', '--sql'],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    sql = result.stdout
    created = re.findall(r'^CREATE TABLE ([a-z_]+) \(', sql, re.MULTILINE)
    assert len(created) == len(set(created)), 'A migration creates the same table twice'
    expected = set(CurrentBase.metadata.tables) | {'publication_embedding', 'alembic_version'}
    assert set(created) == expected
    additions = re.findall(r'^ALTER TABLE ([a-z_]+) ADD COLUMN ([a-z_]+)', sql, re.MULTILINE)
    assert len(additions) == len(set(additions)), 'A migration adds a column twice'
    assert ('feedback', 'callback_id') in additions
    assert ('request_operation', 'claim_token') in additions
    assert ('telegram_outbox', 'reservation_token') in additions
    assert 'CREATE EXTENSION IF NOT EXISTS vector' in sql
    assert 'ix_pub_embedding_hnsw' in sql


def test_migrated_columns_match_current_models():
    # Сравниваем историческую схему плюс явно мигрирующие поля с актуальной ORM.
    additions = {
        'feedback': {'callback_id'},
        'publication_version': {'booking_url', 'opening_hours', 'calendar_exceptions'},
        'canonical': {'parent_place_id'},
        'telegram_source': {'last_success_at', 'last_error'},
        'request_operation': {'claimed_at', 'attempts', 'last_error', 'claim_token'},
        'telegram_outbox': {'reserved_at', 'reservation_token'},
        'source_message': {'edited_at', 'revision'},
    }
    for table_name, baseline in InitialBase.metadata.tables.items():
        current = CurrentBase.metadata.tables[table_name]
        assert set(current.columns.keys()) - set(baseline.columns.keys()) == additions.get(table_name, set())
