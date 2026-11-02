"""Проверка секретов и PBKDF2-хешей без сторонних криптопакетов."""
import base64
import binascii
import hashlib
import hmac
import os

DEFAULT_ITERATIONS = 600_000


def verify_secret(actual: str | None, expected: str | None) -> bool:
    """Пустые секреты никогда не разрешают доступ."""
    if not actual or not expected:
        return False
    return hmac.compare_digest(actual.encode('utf-8'), expected.encode('utf-8'))


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS, salt: bytes | None = None) -> str:
    if not password or iterations < 100_000:
        raise ValueError('Требуется непустой пароль и не менее 100000 итераций')
    salt = os.urandom(16) if salt is None else salt
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)
    return f'pbkdf2_sha256${iterations}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(value).decode()}'


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, count, raw_salt, raw_value = password_hash.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        iterations = int(count)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        salt, value = base64.urlsafe_b64decode(raw_salt), base64.urlsafe_b64decode(raw_value)
        if not (8 <= len(salt) <= 64 and len(value) == 32):
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)
        return hmac.compare_digest(actual, value)
    except (ValueError, TypeError, OverflowError, binascii.Error):
        return False


def verify_admin_credentials(username, password, settings) -> bool:
    if not verify_secret(username, settings.admin_username):
        return False
    if settings.admin_password_hash:
        return verify_password(password, settings.admin_password_hash)
    return verify_secret(password, settings.admin_password)


if __name__ == '__main__':
    import getpass
    print(hash_password(getpass.getpass('Пароль администратора: ')))
