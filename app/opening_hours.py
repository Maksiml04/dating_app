"""FR-05/06: verified opening-hour eligibility for permanent places.

Data contract: opening_hours is {"0": [["10:00","22:00"]], ...},
0=Monday. calendar_exceptions maps ISO dates to None (closed) or interval lists.
Overnight intervals are allowed (e.g. 20:00–02:00). Moscow local time only.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

MSK = ZoneInfo('Europe/Moscow')
TIME_WINDOWS = {'morning':(6,12),'day':(12,18),'evening':(18,23),'night':(23,6)}


def parse_clock(value: str) -> int:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ':':
        raise ValueError('Часы должны быть HH:MM')
    hh, mm = value.split(':')
    if not (hh.isdigit() and mm.isdigit()):
        raise ValueError('Часы должны быть HH:MM')
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError('Неверное время')
    return h * 60 + m


def validate_intervals(intervals):
    if not isinstance(intervals, list) or len(intervals) > 8:
        raise ValueError('Неверный список интервалов')
    for span in intervals:
        if not isinstance(span, list) or len(span) != 2:
            raise ValueError('Нужна пара времени [начало, конец]')
        first, last = map(parse_clock, span)
        if first == last:
            raise ValueError('Интервал нулевой длины')


def validate_schedule(opening_hours, calendar_exceptions):
    if opening_hours is None:
        if calendar_exceptions:
            raise ValueError('Исключения требуют основного расписания')
        return
    if not isinstance(opening_hours, dict) or len(opening_hours)>7:
        raise ValueError('Неверное расписание')
    for key, spans in opening_hours.items():
        if str(key) not in ('0','1','2','3','4','5','6'):
            raise ValueError('Недопустимый день недели')
        validate_intervals(spans)
    for date_key, spans in (calendar_exceptions or {}).items():
        from datetime import date
        date.fromisoformat(date_key)
        if spans is not None:
            validate_intervals(spans)


def _windows_for_day(opening_hours, calendar_exceptions, day):
    override = (calendar_exceptions or {}).get(day.isoformat(), 'not_overridden')
    if override is None:
        return []
    if override != 'not_overridden':
        return override
    return (opening_hours or {}).get(str(day.weekday()), [])


def place_available(opening_hours, calendar_exceptions, *, starts_at, ends_at,
                    time_period=None, duration_minutes=None, now=None):
    """Fail closed on an unknown schedule/date. At least one valid future slot must fit."""
    if not opening_hours or not starts_at or not ends_at:
        return False
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        raise ValueError('Timezone-aware interval required')
    now = now or datetime.now(timezone.utc)
    if starts_at >= ends_at:
        return False
    first = max(starts_at, now).astimezone(MSK)
    end = ends_at.astimezone(MSK)
    if first >= end:
        return False
    min_duration = timedelta(minutes=duration_minutes or 1)
    # Requested window is reasonably short in the MVP; protect against abuse.
    for offset in range(min(10, (end.date()-first.date()).days + 3)):
        # Include previous day: 20:00–02:00 belongs to the opening weekday.
        day = first.date()-timedelta(days=1)+timedelta(days=offset)
        for open_time, close_time in _windows_for_day(opening_hours, calendar_exceptions, day):
            start_minutes = parse_clock(open_time)
            end_minutes = parse_clock(close_time)
            open_dt = datetime.combine(day, time(0), tzinfo=MSK) + timedelta(minutes=start_minutes)
            close_dt = datetime.combine(day, time(0), tzinfo=MSK) + timedelta(minutes=end_minutes)
            if end_minutes < start_minutes:
                close_dt += timedelta(days=1)
            candidates = [(max(first, open_dt), min(end, close_dt))]
            if time_period:
                lo, hi = TIME_WINDOWS[time_period]
                segment_start = datetime.combine(day, time(0), tzinfo=MSK) + timedelta(hours=lo)
                segment_end = datetime.combine(day, time(0), tzinfo=MSK) + timedelta(hours=hi)
                if hi < lo: segment_end += timedelta(days=1)
                candidates = [(max(first, open_dt, segment_start),
                               min(end, close_dt, segment_end))]
            if any(b-a >= min_duration for a,b in candidates):
                return True
    return False
