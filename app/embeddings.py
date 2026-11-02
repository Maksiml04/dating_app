"""Опциональные эмбеддинги через OpenAI-совместимый API (384 измерения)."""
import logging
import httpx
from app.config import settings

log = logging.getLogger(__name__)

def embed(text: str) -> list[float] | None:
    if not settings.embedding_base_url or not settings.embedding_model or not text.strip():
        return None
    url = settings.embedding_base_url.rstrip('/') + '/embeddings'
    headers = {'Authorization': f'Bearer {settings.embedding_api_key}'} if settings.embedding_api_key else {}
    try:
        with httpx.Client(timeout=settings.embedding_timeout_seconds) as client:
            response = client.post(url, headers=headers, json={'model': settings.embedding_model, 'input': text[:4000]})
            response.raise_for_status()
            values = response.json()['data'][0]['embedding']
        if len(values) != 384 or not all(isinstance(x, (float, int)) and not isinstance(x, bool) for x in values):
            log.warning('Неверная размерность эмбеддинга (ожидалось 384)')
            return None
        return [float(x) for x in values]
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        log.warning('Эмбеддинг недоступен: %s', type(exc).__name__)
        return None
