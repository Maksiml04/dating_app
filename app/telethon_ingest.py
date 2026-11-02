"""Telegram MTProto ingestion and *live* read-only channel probe.

Usage (Telethon session already authorized)::

  python -m app.telethon_ingest probe --channel @kuda_v_moskva --limit 5
  python -m app.telethon_ingest sync --limit 200

First-time authorization (interactive, never paste codes into chat)::

  python -m app.telethon_ingest login

`probe` performs live Telegram API calls but never touches PostgreSQL or
advances an ingestion checkpoint. `sync` is a bounded, one-shot importer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal, configure_engine
from app.ingestion import ingest_message
from app.source_updates import reconcile_edit
from app.config import settings
from app.models import TelegramSource


@dataclass
class SourceStats:
    source: str
    fetched: int = 0
    saved: int = 0
    empty: int = 0
    duplicates: int = 0
    edited: int = 0
    photos: int = 0
    checkpoint: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(vars(self))


def clean_channel(value: str) -> str:
    """Accept public @usernames or t.me links; reject malformed handles."""
    value = (value or '').strip().rstrip('/')
    for prefix in ('https://t.me/', 'http://t.me/', 't.me/'):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
            break
    value = value.removeprefix('@')
    # Do not silently turn a message URL or an invite link into a channel.
    if not (5 <= len(value) <= 32 and value.replace('_', '').isalnum() and value.isascii()):
        raise ValueError('Укажите публичный @username канала (например, @kuda_v_moskva)')
    return '@' + value


def _source_identifier(source: TelegramSource) -> str | int:
    if source.username:
        return clean_channel(source.username)
    # Bare numeric IDs need an entity previously seen by the logged-in account.
    return source.channel_id


def _id_matches(stored: int, real: int) -> bool:
    # UI may supply raw MTProto IDs or Bot API-style -100-prefixed channel IDs.
    return stored == real or stored == int(f'-100{real}')


def _message_date(msg: Any) -> datetime:
    date = msg.date
    if not isinstance(date, datetime) or date.tzinfo is None:
        raise ValueError('Telegram message has no timezone-aware date')
    return date.astimezone(timezone.utc)


def _link(username: str | None, message_id: int) -> str | None:
    if not username:
        return None
    try:
        return f'https://t.me/{clean_channel(username)[1:]}/{message_id}'
    except ValueError:
        return None


def _preview(msg: Any, handle: str | None) -> dict[str, Any]:
    text = (getattr(msg, 'message', None) or '').strip()
    return {
        'id': msg.id,
        'date': _message_date(msg).isoformat(),
        'has_text': bool(text),
        'text_preview': ' '.join(text.split())[:180],
        'url': _link(handle, msg.id),
    }


async def probe_channel(client: Any, handle: str, *, limit: int = 5) -> dict[str, Any]:
    """Read posts from the *live* Telegram connection, with zero DB writes."""
    if not 1 <= limit <= 50:
        raise ValueError('Для диагностики допустим limit от 1 до 50')
    username = clean_channel(handle)
    entity = await client.get_entity(username)
    if not getattr(entity, 'broadcast', False) and not getattr(entity, 'megagroup', False):
        raise ValueError('Указанный Telegram-источник не является каналом или супергруппой')
    posts = []
    async for msg in client.iter_messages(entity, limit=limit):
        posts.append(_preview(msg, username))
    if not posts:
        raise RuntimeError('Telegram не вернул ни одного сообщения из канала')
    return {
        'connected': True,
        'channel': username,
        'channel_id': entity.id,
        'posts_received': len(posts),
        'text_posts': sum(bool(p['has_text']) for p in posts),
        'messages': posts,
        'database_unchanged': True,
    }


async def import_source(db: Any, client: Any, source: TelegramSource, *, limit: int = 200,
                        edit_scan_limit: int = 0) -> SourceStats:
    """Fetch at most `limit` posts; only checkpoint after DB commit.

    New channels bootstrap from the newest N messages. Established channels
    continue from the oldest not-yet-processed message (ascending ID order).
    """
    if not 1 <= limit <= 500 or not 0 <= edit_scan_limit <= 500:
        raise ValueError('Размер пачки и окна правок должны быть в пределах 0..500')
    label = source.username or str(source.channel_id)
    result = SourceStats(source=label, checkpoint=int(source.last_message_id or 0))
    try:
        entity = await client.get_entity(_source_identifier(source))
        if not _id_matches(int(source.channel_id), int(entity.id)):
            raise ValueError('ID источника не соответствует Telegram-каналу; проверьте настройки')
        checkpoint = int(source.last_message_id or 0)
        if checkpoint:
            iterator = client.iter_messages(entity, min_id=checkpoint, reverse=True, limit=limit)
            async for msg in iterator:
                await _store(db, source, msg, result, client=client)
        else:
            # Important: reverse=True with min_id=0 gives the OLDEST history.
            # Bootstrap from the latest posts instead of thousands of stale ones.
            latest = [msg async for msg in client.iter_messages(entity, limit=limit)]
            for msg in sorted(latest, key=lambda m: m.id):
                await _store(db, source, msg, result, client=client)
        # A bounded look-back finds edits of *known* old posts even when no
        # new message IDs were added. It never imports unfamiliar historical posts.
        if edit_scan_limit:
            async for msg in client.iter_messages(entity, limit=edit_scan_limit):
                row, changed = reconcile_edit(db, source.id, msg.id,
                    getattr(msg, 'message', None) or '', getattr(msg, 'edit_date', None))
                if row:
                    from app.models import SourceMedia
                    old_media = db.scalar(select(SourceMedia).where(
                        SourceMedia.source_message_id == row.id))
                    photo = getattr(msg, 'photo', None)
                    photo_id = getattr(photo, 'id', None)
                    media_changed = (old_media is not None and
                        (photo is None or (photo_id is not None and
                         old_media.telegram_photo_id != photo_id)))
                    if media_changed and not changed:
                        row, changed = reconcile_edit(db, source.id, msg.id,
                            getattr(msg, 'message', None) or '',
                            getattr(msg, 'edit_date', None), force=True)
                    if changed:
                        result.edited += 1
                    if photo is not None:
                        if old_media is None or media_changed:
                            await _sync_photo(db, client, msg, row, result)
                    elif old_media is not None:
                        db.delete(old_media)
                    db.commit()
        source.last_success_at = datetime.now(timezone.utc)
        source.last_error = None
        db.commit()
        result.checkpoint = int(source.last_message_id or 0)
    except Exception as exc:
        db.rollback()
        result.error = f'{type(exc).__name__}: {str(exc)[:250]}'
        source.last_error = result.error
        db.commit()
        result.checkpoint = int(source.last_message_id or 0)
    return result


async def _store(db: Any, source: TelegramSource, msg: Any, result: SourceStats, *, client=None) -> None:
    result.fetched += 1
    if msg.id <= int(source.last_message_id or 0):
        result.duplicates += 1
        return
    text = (getattr(msg, 'message', None) or '').strip()
    if text:
        _item, created = ingest_message(
            db, source, msg.id, text, _message_date(msg), _link(source.username, msg.id)
        )
        if _item and getattr(msg, 'edit_date', None):
            _item.edited_at = msg.edit_date
        if _item and getattr(msg, 'photo', None) is not None:
            await _sync_photo(db, client, msg, _item, result)
        result.saved += int(created)
        result.duplicates += int(not created)
    elif getattr(msg, 'photo', None) is not None:
        # Keep photo-only source records for moderation (without fake captions).
        item, created = ingest_message(db, source, msg.id, '', _message_date(msg),
                                      _link(source.username, msg.id), allow_empty=True)
        if item:
            await _sync_photo(db, client, msg, item, result)
        result.saved += int(created)
    else:
        # Service messages still advance the cursor.
        result.empty += 1
    source.last_message_id = max(int(source.last_message_id or 0), msg.id)
    db.commit()  # The database commit is the durable checkpoint boundary.


async def _sync_photo(db, client, msg, source_message, stats):
    if client is None:
        return
    size = getattr(getattr(msg, 'file', None), 'size', None)
    if size is not None and size > settings.telegram_media_max_bytes:
        return  # oversized photos are not downloaded
    from app.media_storage import store_photo
    data = await client.download_media(msg, file=bytes)
    if data:
        store_photo(db, source_message, bytes(data),
                    telegram_photo_id=getattr(msg.photo, 'id', None))
        stats.photos += 1


async def sync_sources(db: Any, client: Any, *, limit: int = 200,
                       edit_scan_limit: int = 100) -> dict[str, Any]:
    sources = db.scalars(select(TelegramSource).where(TelegramSource.enabled.is_(True))).all()
    stats = [await import_source(db, client, source, limit=limit, edit_scan_limit=edit_scan_limit) for source in sources]
    return {
        'sources': len(stats),
        'fetched': sum(item.fetched for item in stats),
        'saved': sum(item.saved for item in stats),
        'edited': sum(item.edited for item in stats),
        'photos': sum(item.photos for item in stats),
        'errors': sum(item.error is not None for item in stats),
        'details': [item.as_dict() for item in stats],
    }


def _credentials() -> tuple[int, str, str]:
    raw_id = os.environ.get('TELEGRAM_API_ID', '')
    api_hash = os.environ.get('TELEGRAM_API_HASH', '')
    try:
        api_id = int(raw_id)
    except ValueError as exc:
        raise RuntimeError('Укажите корректный TELEGRAM_API_ID в .env') from exc
    if api_id <= 0 or not api_hash or 'replace' in api_hash.lower():
        raise RuntimeError('Задайте TELEGRAM_API_ID и TELEGRAM_API_HASH в .env')
    session = os.environ.get('TELEGRAM_SESSION', '/data/telegram.session')
    return api_id, api_hash, session


async def main_async(args: argparse.Namespace) -> int:
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError('Для чтения Telegram требуется pip install ".[telegram]"') from exc
    api_id, api_hash, session = _credentials()
    client = TelegramClient(session, api_id, api_hash)
    if args.command == 'login':
        # Deliberately interactive. Never put phone/code/2FA in env or logs.
        await client.start()
        try:
            print('OK: Telegram-сессия авторизована')
        finally:
            await client.disconnect()
        return 0

    # Probe and sync never start a phone/OTP login prompt implicitly.
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError('Telegram-сессия не авторизована. Сначала запустите команду login.')
        if args.command == 'probe':
            data = await probe_channel(client, args.channel, limit=args.limit)
            print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
            return 0
        configure_engine()
        if args.command == 'watch':
            while True:
                with SessionLocal() as db:
                    data = await sync_sources(db, client, limit=args.limit,
                                              edit_scan_limit=args.edit_scan_limit)
                print(json.dumps(data, ensure_ascii=False, default=str), flush=True)
                await asyncio.sleep(max(15, args.interval))
        with SessionLocal() as db:
            data = await sync_sources(db, client, limit=args.limit,
                                      edit_scan_limit=args.edit_scan_limit)
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return 1 if data.get('errors') else 0
    finally:
        await client.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('login', help='Однократная интерактивная авторизация Telegram')
    probe = sub.add_parser('probe', help='Прочитать реальные сообщения, не меняя базу')
    probe.add_argument('--channel', required=True, help='Публичный @username канала')
    probe.add_argument('--limit', type=int, default=5)
    sync = sub.add_parser('sync', help='Синхронизация включённых источников в PostgreSQL')
    sync.add_argument('--limit', type=int, default=200)
    sync.add_argument('--edit-scan-limit', type=int, default=settings.telegram_edit_scan_limit)
    watch = sub.add_parser('watch', help='Периодически читать новые сообщения и правки старых')
    watch.add_argument('--limit', type=int, default=200)
    watch.add_argument('--edit-scan-limit', type=int, default=settings.telegram_edit_scan_limit)
    watch.add_argument('--interval', type=int, default=90)
    args = parser.parse_args(argv)
    if args.command is None:
        args.command, args.limit, args.edit_scan_limit = 'sync', 200, settings.telegram_edit_scan_limit
    try:
        return asyncio.run(main_async(args))
    except (RuntimeError, ValueError, OSError) as exc:
        print(f'ERROR: {exc}', file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
