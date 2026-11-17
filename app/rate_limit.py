"""Ограничение частоты запросов webhook по чату: скользящее окно в памяти процесса.

Защита от флуда уровня MVP; для кластера из нескольких воркеров заменяется на
общий счётчик в PostgreSQL/Redis. Лимит по умолчанию: 20 сообщений в минуту
на чат, превышение получает HTTP 429 без обработки обновления.
"""
import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
_events: dict[int, deque] = defaultdict(deque)
_MAX_TRACKED_CHATS = 10_000

def allow(chat_id: int, *, limit: int, window_seconds: float = 60.0, now: float | None = None) -> bool:
    now = now if now is not None else time.monotonic()
    with _lock:
        if len(_events) > _MAX_TRACKED_CHATS:
            _events.clear()  # защита от роста памяти на чужих chat_id
        window = _events[chat_id]
        while window and window[0] <= now - window_seconds:
            window.popleft()
        if len(window) >= limit:
            return False
        window.append(now)
        return True
