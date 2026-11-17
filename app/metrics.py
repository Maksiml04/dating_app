"""Минимальный реестр метрик в формате exposition Prometheus.

Без внешних зависимостей: счётчики и gauges уровня процесса. Метрики не содержат
PII (chat_id, тексты) — только агрегированные счётчики. Для многопроцессного
развертывания значения per-process; агрегация выполняется сборщиком метрик.
"""
import threading
from datetime import UTC, datetime

_lock = threading.Lock()
_counters: dict[str, float] = {}
_gauges: dict[str, float] = {}
_started_at = datetime.now(UTC)

def increment(name: str, value: float = 1.0) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0.0) + value

def set_gauge(name: str, value: float) -> None:
    with _lock:
        _gauges[name] = value

def snapshot() -> tuple[dict, dict]:
    with _lock:
        return dict(_counters), dict(_gauges)

def render() -> str:
    counters, gauges = snapshot()
    uptime = (datetime.now(UTC) - _started_at).total_seconds()
    lines = ['# HELP process_uptime_seconds Время работы процесса.',
             '# TYPE process_uptime_seconds gauge',
             f'process_uptime_seconds {uptime:.0f}']
    for name, value in sorted(counters.items()):
        lines += [f'# TYPE {name} counter', f'{name} {value}']
    for name, value in sorted(gauges.items()):
        lines += [f'# TYPE {name} gauge', f'{name} {value}']
    return '\n'.join(lines) + '\n'
