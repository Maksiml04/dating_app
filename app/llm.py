"""Опциональный OpenAI-совместимый экстрактор: модель не формирует SQL и не выдумывает карточки."""
import json
import logging
from datetime import UTC, datetime

import httpx

from app.config import settings
from app.schemas import SearchRequest

log = logging.getLogger(__name__)
SYSTEM = """Извлеки ограничения для поиска свиданий. Верни только JSON-объект с ключами
query (строка), budget_total_kopeks (целое число или null), category (строка или null),
district (строка или null), metro (строка или null), indoors (bool или null).
Не придумывай значения. Бюджет указан в копейках и относится к паре. Не добавляй SQL.
Если запрос содержит неуверенное или неоднозначное ограничение, оставь null.
query — смысловые слова для поиска по каталогу, без суммы бюджета."""
ALLOWED = {'query','budget_total_kopeks','category','district','metro','indoors'}

def _daily_calls_exceeded() -> bool:
    """NFR-09: дневной бюджет вызовов LLM в рамках процесса; 0 — без лимита."""
    limit = settings.llm_max_calls_per_day
    if limit <= 0:
        return False
    today = datetime.now(UTC).date()
    if _daily_calls.get('date') != today:
        _daily_calls.clear()
        _daily_calls['date'] = today
    return _daily_calls.get('count', 0) >= limit

_daily_calls: dict = {}

def extract_with_llm(message: str) -> dict | None:
    if not all((settings.llm_base_url, settings.llm_model)):
        return None
    from app import metrics as registry
    if _daily_calls_exceeded():
        registry.increment('llm_budget_exhausted_total')
        log.warning('Дневной бюджет LLM исчерпан, используется детерминированный разбор')
        return None
    _daily_calls['count'] = _daily_calls.get('count', 0) + 1
    registry.increment('llm_calls_total')
    url = settings.llm_base_url.rstrip('/') + '/chat/completions'
    headers = {'Authorization': f'Bearer {settings.llm_api_key}'} if settings.llm_api_key else {}
    payload = {'model': settings.llm_model, 'temperature': 0,
               'max_tokens': settings.llm_max_output_tokens,
               'messages': [{'role':'system','content':SYSTEM},{'role':'user','content':message}],
               'response_format': {'type':'json_object'}}
    try:
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            parsed = json.loads(response.json()['choices'][0]['message']['content'])
        if not isinstance(parsed, dict): return None
        result = {k:v for k,v in parsed.items() if k in ALLOWED and v is not None}
        SearchRequest.model_validate(result)
        return result
    except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
        log.warning('LLM недоступна, включён детерминированный разбор: %s', type(exc).__name__)
        return None
