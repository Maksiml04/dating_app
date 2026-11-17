"""Authorize the Date Ideas Telethon parser via an existing Telegram mobile session.

Run inside the project's telegram_ingest Docker service. Requires Telethon and
`qrcode` installed; uses TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION.
Never share the terminal QR: it grants access to an account during its validity.
"""

import asyncio
import getpass
import os
import sys

from telethon import TelegramClient, errors


def credentials():
    try:
        api_id = int(os.environ["TELEGRAM_API_ID"])
        api_hash = os.environ["TELEGRAM_API_HASH"]
    except (ValueError, KeyError) as exc:
        raise RuntimeError("В .env нужны TELEGRAM_API_ID и TELEGRAM_API_HASH") from exc
    if api_id <= 0 or not api_hash:
        raise RuntimeError("Некорректные TELEGRAM_API_ID / TELEGRAM_API_HASH")
    session = os.environ.get("TELEGRAM_SESSION", "/data/telegram_user.session")
    return api_id, api_hash, session


def display_qr(link):
    try:
        import qrcode
    except ImportError as exc:
        raise RuntimeError("Не установлен qrcode: выполните pip install qrcode") from exc
    qr = qrcode.QRCode(border=3, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(link)
    qr.make(fit=True)
    print("\nОткройте Telegram на телефоне → Настройки → Устройства → Подключить устройство")
    print("Наведите камеру приложения Telegram на QR ниже. Не делитесь этим QR-кодом.\n", flush=True)
    qr.print_ascii(invert=True)
    print(flush=True)


async def main():
    api_id, api_hash, session = credentials()
    client = TelegramClient(session, api_id, api_hash)
    await client.connect()  # Do not call start(): it requests phone/code interactively.
    try:
        if await client.is_user_authorized():
            user = await client.get_me()
            if getattr(user, "bot", False):
                raise RuntimeError("Эта сессия принадлежит боту. Используйте TELEGRAM_SESSION=/data/telegram_user.session")
            print("OK: пользовательская сессия уже авторизована")
            return
        for attempt in range(3):
            qr = await client.qr_login()
            display_qr(qr.url)
            try:
                await qr.wait()
            except asyncio.TimeoutError:
                if attempt < 2:
                    print("QR истёк; создаю новый...", flush=True)
                    continue
                raise RuntimeError("Время QR истекло. Запустите команду ещё раз") from None
            except errors.SessionPasswordNeededError:
                print("На аккаунте включён пароль двухэтапной проверки.")
                await client.sign_in(password=getpass.getpass("Пароль 2FA: "))
            user = await client.get_me()
            if not user or getattr(user, "bot", False):
                raise RuntimeError("Вход не завершён или подключён бот вместо пользователя")
            print("OK: пользователь Telegram авторизован, сессия сохранена")
            return

    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (RuntimeError, errors.RPCError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
