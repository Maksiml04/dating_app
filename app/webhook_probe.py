"""Read-only diagnostic of Telegram Bot API getWebhookInfo.

No database access, no webhook registration, and no Telegram messages sent.
Use: python -m app.webhook_probe [--expected-url https://host/api/telegram/webhook]
"""
import argparse
import json
from urllib.parse import urlparse

import httpx
from app.config import settings


def assess_webhook(info: dict, expected_url: str | None = None) -> dict:
    """Return a safe summary without tokens or the Telegram API request URL."""
    url = info.get('url') or ''
    problems = []
    if not url:
        problems.append('Webhook не установлен: Telegram не будет доставлять обновления на API')
    elif urlparse(url).scheme != 'https':
        problems.append('Webhook должен использовать HTTPS')
    if expected_url and url != expected_url:
        problems.append('Установленный адрес webhook отличается от ожидаемого')
    count = info.get('pending_update_count')
    if isinstance(count, int) and count > 0:
        problems.append('Telegram ожидает доставки обновлений: проверьте очередь и доступность API')
    if info.get('last_error_message'):
        problems.append('Telegram сообщает о последней ошибке доставки')
    allowed = info.get('allowed_updates')
    if isinstance(allowed, list) and allowed and 'callback_query' not in allowed:
        problems.append('Webhook не подписан на callback_query: реакции на кнопки могут не приходить')
    if isinstance(allowed, list) and allowed and 'message' not in allowed:
        problems.append('Webhook не подписан на message: текстовые сообщения могут не приходить')
    return {
        'ok': not problems,
        'webhook_url': url,
        'expected_url': expected_url,
        'pending_update_count': count,
        'last_error_date': info.get('last_error_date'),
        'last_error_message': info.get('last_error_message'),
        'allowed_updates': allowed,
        'problems': problems,
    }


def read_webhook_info(token: str, expected_url: str | None = None, *, transport=None) -> dict:
    if not token:
        raise ValueError('TELEGRAM_BOT_TOKEN не задан')
    # URL с секретным токеном нигде не логируем и не возвращаем.
    with httpx.Client(timeout=10, transport=transport) as client:
        response = client.get(f'https://api.telegram.org/bot{token}/getWebhookInfo')
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or payload.get('ok') is not True or not isinstance(payload.get('result'), dict):
        raise ValueError('Telegram вернул неожиданный формат getWebhookInfo')
    return assess_webhook(payload['result'], expected_url)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Проверить webhook Telegram без изменений')
    parser.add_argument('--expected-url', default=None)
    args = parser.parse_args(argv)
    expected = args.expected_url
    if expected is None and settings.public_base_url:
        expected = settings.public_base_url.rstrip('/') + '/api/telegram/webhook'
    try:
        result = read_webhook_info(settings.telegram_bot_token, expected)
    except (ValueError, httpx.HTTPError) as exc:
        # Исключение httpx может содержать URL с токеном — никогда не печатаем exc.
        print(json.dumps({'ok': False, 'error_type': type(exc).__name__,
                          'hint': 'Проверьте токен, сеть и доступ к Telegram Bot API'}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
