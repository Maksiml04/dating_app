"""Explicit HTTPS Telegram webhook registration. Never prints BOT_TOKEN.

Run inside Compose after ingress is reachable:
  python -m app.webhook_setup set
  python -m app.webhook_setup info
"""
import argparse
import json
import re
from urllib.parse import urlsplit

import httpx

from app.config import settings


def webhook_url(base: str) -> str:
    parsed = urlsplit((base or '').strip().rstrip('/'))
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError('PUBLIC_BASE_URL должен быть HTTPS-доменом без пути или параметров')
    if parsed.hostname in ('localhost', '127.0.0.1'):
        raise ValueError('Telegram требует публичный HTTPS-домен')
    return base.rstrip('/') + '/api/telegram/webhook'


def request(method: str, *, transport=None):
    token = settings.telegram_bot_token
    if not token or not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
        raise RuntimeError('Настройте корректный TELEGRAM_BOT_TOKEN локально')
    if method == 'setWebhook':
        url = webhook_url(settings.public_base_url)
        secret = settings.telegram_webhook_secret
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', secret or ''):
            raise ValueError('TELEGRAM_WEBHOOK_SECRET должен содержать 1–256 символов A-Z, 0-9, _ или -')
        body = {'url': url, 'secret_token': secret, 'allowed_updates': ['message','callback_query'],
                'drop_pending_updates': False}
    elif method == 'getWebhookInfo':
        body = {}
    else:
        raise ValueError('Недопустимая операция')
    endpoint = f'https://api.telegram.org/bot{token}/{method}'
    if transport is None:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(endpoint, json=body)
    else:
        response = transport(endpoint, body)
    response.raise_for_status()
    result = response.json()
    if not result.get('ok'):
        raise RuntimeError('Telegram отказал в настройке webhook')
    if method == 'getWebhookInfo':
        info = result.get('result') or {}
        return {k: info.get(k) for k in ('url', 'pending_update_count', 'last_error_message',
                                         'last_error_date', 'allowed_updates')}
    return {'registered': True, 'url': url}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['set', 'info'])
    args = parser.parse_args()
    result = request('setWebhook' if args.command == 'set' else 'getWebhookInfo')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
