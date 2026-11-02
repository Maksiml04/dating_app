"""Fake MTProto client tests: NOT a real Telegram connectivity test."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from app import telethon_ingest as tg


class Client:
    def __init__(self, messages, *, channel_id=123, broadcast=True):
        self.messages = messages
        self.entity = NS(id=channel_id, broadcast=broadcast, megagroup=False)
        self.seen = []

    async def get_entity(self, identifier):
        self.seen.append(('entity', identifier))
        return self.entity

    def iter_messages(self, entity, **kwargs):
        self.seen.append(('iter', kwargs))
        async def stream():
            msgs = [m for m in self.messages if not kwargs.get('min_id') or m.id > kwargs['min_id']]
            if not kwargs.get('reverse'):
                msgs = list(reversed(msgs))
            for m in msgs[:kwargs['limit']]:
                yield m
        return stream()


def msg(mid, text='Выставка и экскурсия'):
    return NS(id=mid, message=text, date=datetime(2026, 10, 30, 15, tzinfo=timezone.utc))


def source(checkpoint=0, username='kuda_v_moskva'):
    return NS(id='source-1', channel_id=123, username=username,
              last_message_id=checkpoint, enabled=True)


class DB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
    def commit(self):
        self.commits += 1
    def rollback(self):
        self.rollbacks += 1


def run(coro):
    return asyncio.run(coro)


def test_probe_reads_messages_without_database():
    client = Client([msg(10), msg(11, ''), msg(12, 'Фестиваль')])
    result = run(tg.probe_channel(client, 'https://t.me/kuda_v_moskva', limit=3))
    assert result['connected'] and result['database_unchanged']
    assert result['posts_received'] == 3
    assert result['text_posts'] == 2
    assert result['messages'][0]['id'] == 12
    assert result['messages'][0]['url'] == 'https://t.me/kuda_v_moskva/12'
    assert client.seen[0] == ('entity', '@kuda_v_moskva')


def test_probe_requires_actual_channel():
    with pytest.raises(ValueError, match='каналом'):
        run(tg.probe_channel(Client([msg(1)], broadcast=False), '@kuda_v_moskva'))


def test_probe_fails_if_nothing_read():
    with pytest.raises(RuntimeError, match='ни одного'):
        run(tg.probe_channel(Client([]), '@kuda_v_moskva'))


@pytest.mark.parametrize('handle', ['', 't.me/abc/123', 'https://t.me/+private', '@ab', 'not a channel'])
def test_reject_bad_source_names(handle):
    with pytest.raises(ValueError):
        tg.clean_channel(handle)


def test_first_import_bootstrap_fetches_newest_first_and_saves_ascending(monkeypatch):
    created = []
    def fake_ingest(db, src, mid, text, dt, url):
        created.append((mid, text, url))
        return object(), True
    monkeypatch.setattr(tg, 'ingest_message', fake_ingest)
    s, db, client = source(), DB(), Client([msg(1), msg(2), msg(3)])
    result = run(tg.import_source(db, client, s, limit=2))
    assert [x[0] for x in created] == [2, 3]
    assert result.saved == 2 and result.checkpoint == 3
    assert db.commits == 3
    assert client.seen[-1] == ('iter', {'limit': 2})


def test_media_only_post_advances_checkpoint(monkeypatch):
    def fake_ingest(*args):
        return object(), True
    monkeypatch.setattr(tg, 'ingest_message', fake_ingest)
    s, db = source(checkpoint=10), DB()
    client = Client([msg(11, None), msg(12, 'Лекция')])
    result = run(tg.import_source(db, client, s, limit=20))
    assert (result.empty, result.saved, result.checkpoint) == (1, 1, 12)
    assert db.commits == 3
    assert client.seen[-1] == ('iter', {'min_id': 10, 'reverse': True, 'limit': 20})


def test_mismatched_channel_id_is_not_imported(monkeypatch):
    monkeypatch.setattr(tg, 'ingest_message', lambda *args: pytest.fail('must not ingest wrong source'))
    s, db = source(), DB()
    result = run(tg.import_source(db, Client([msg(11)], channel_id=777), s))
    assert result.error and 'ID' in result.error
    assert result.fetched == 0 and db.commits == 1


def test_bot_api_signed_channel_id_accepted(monkeypatch):
    monkeypatch.setattr(tg, 'ingest_message', lambda *args: (object(), True))
    s = source()
    s.channel_id = -100123
    result = run(tg.import_source(DB(), Client([msg(3)]), s))
    assert result.saved == 1 and not result.error


def test_one_broken_channel_does_not_prevent_next(monkeypatch):
    monkeypatch.setattr(tg, 'ingest_message', lambda *args: (object(), True))
    broken, healthy, db = source(username='channel_bad'), source(username='channel_ok'), DB()
    broken.channel_id = 999
    class Multi(Client):
        async def get_entity(self, identifier):
            return NS(id=999 if identifier == '@channel_bad' else 123, broadcast=True)
        def iter_messages(self, entity, **kwargs):
            if entity.id == 999:
                raise RuntimeError('no access')
            return super().iter_messages(entity, **kwargs)
    async def check():
        stats1 = await tg.import_source(db, Multi([msg(15)]), broken)
        stats2 = await tg.import_source(db, Multi([msg(15)]), healthy)
        return stats1, stats2
    stats1, stats2 = run(check())
    assert stats1.error and stats2.saved == 1
    assert db.rollbacks == 1 and db.commits == 3


def test_probe_command_uses_authorized_client_no_postgres(monkeypatch, capsys):
    import sys
    client = Client([msg(42)])
    async def connect():
        client.seen.append(('connect', None))
    async def authorized():
        return True
    async def disconnect():
        client.seen.append(('disconnect', None))
    client.connect, client.is_user_authorized, client.disconnect = connect, authorized, disconnect
    monkeypatch.setitem(sys.modules, 'telethon', NS(TelegramClient=lambda *a: client))
    monkeypatch.setattr(tg, '_credentials', lambda: (111, 'fake', '/fake/session'))
    monkeypatch.setattr(tg, 'configure_engine', lambda: pytest.fail('probe accessed DB'))
    assert tg.main(['probe', '--channel', '@kuda_v_moskva', '--limit', '1']) == 0
    result = __import__('json').loads(capsys.readouterr().out)
    assert result['posts_received'] == 1
    assert ('disconnect', None) in client.seen


def test_probe_command_does_not_prompt_for_authentication(monkeypatch, capsys):
    import sys
    client = NS()
    async def connect(): pass
    async def authorized(): return False
    async def disconnect(): pass
    client.connect, client.is_user_authorized, client.disconnect = connect, authorized, disconnect
    monkeypatch.setitem(sys.modules, 'telethon', NS(TelegramClient=lambda *a: client))
    monkeypatch.setattr(tg, '_credentials', lambda: (111, 'fake', '/fake/session'))
    assert tg.main(['probe', '--channel', '@kuda_v_moskva']) == 1
    assert 'не авторизована' in capsys.readouterr().err
