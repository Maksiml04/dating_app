"""Ограниченные, детерминированные интервалы для русских относительных дат.

Все значения храним как абсолютные ISO-строки с UTC offset: иначе JSONB
не может сериализовать datetime, а «завтра» сменит смысл после полуночи.
"""
from datetime import datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY_RE = re.compile(r'(?<!\w)(послезавтра|завтра|сегодня|на выходных|в выходные)(?!\w)', re.I)
NEGATION_RE = re.compile(r'(?:^|[\s,])не\s*$', re.I)


def relative_interval(message: str, *, now: datetime | None = None,
                      timezone_name: str = 'Europe/Moscow') -> tuple[str, str] | None:
    """Открытый интервал [start, end) в timezone_name, сериализованный в UTC.

    При неоднозначных нескольких датах не строим интервал; исключение —
    явное исправление «не завтра, а послезавтра».
    """
    matches = list(DAY_RE.finditer(message))
    if not matches:
        return None
    if len(matches) > 1:
        if len(matches) != 2 or not re.search(r'\b(?:а|лучше|тогда)\b', message[matches[0].end():matches[1].start()], re.I):
            return None
        match = matches[-1]
    else:
        match = matches[0]
    # Отрицание без альтернативы — не положительное ограничение.
    if NEGATION_RE.search(message[:match.start()]):
        return None
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f'Неизвестный часовой пояс: {timezone_name}') from exc
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError('now должен содержать часовой пояс')
    local = current.astimezone(tz)
    label = match.group(1).casefold()
    today = local.date()
    if label == 'сегодня':
        day = today
        days = 1
    elif label == 'завтра':
        day = today + timedelta(days=1)
        days = 1
    elif label == 'послезавтра':
        day = today + timedelta(days=2)
        days = 1
    else:
        # Сб/Вс относятся к текущим выходным; с понедельника по пятницу — к следующим.
        day = (today - timedelta(days=today.weekday() - 5) if today.weekday() >= 5
               else today + timedelta(days=5 - today.weekday()))
        days = 2
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = datetime.combine(day + timedelta(days=days), time.min, tzinfo=tz)
    # Не предлагать уже прошедшие сеансы в течение сегодняшнего дня/выходных.
    start = max(start_local, local)
    if start >= end_local:
        return None
    return (start.astimezone(timezone.utc).isoformat(),
            end_local.astimezone(timezone.utc).isoformat())
