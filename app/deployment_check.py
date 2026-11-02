"""Offline deployment configuration checks; never print secret values."""
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, unquote

REQUIRED = ("POSTGRES_PASSWORD", "DATABASE_URL", "MINIO_ROOT_PASSWORD", "ADMIN_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET")
PLACEHOLDERS = ("replace", "change-this", "your_", "example", "changeme")

def parse_env(path: Path) -> dict[str, str]:
    result = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid .env line {number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid .env key at line {number}")
        result[key] = value.strip().strip('"').strip("'")
    return result

def validate(config: dict[str, str], *, require_telegram: bool = False) -> list[str]:
    errors = []
    for key in REQUIRED:
        value = config.get(key, "").strip()
        if not value or any(marker in value.lower() for marker in PLACEHOLDERS):
            errors.append(f"{key}: missing or placeholder")
    dsn = config.get("DATABASE_URL", "")
    try:
        parts = urlsplit(dsn)
        if parts.scheme != "postgresql+psycopg" or not parts.hostname or not parts.username:
            errors.append("DATABASE_URL: expected postgresql+psycopg URL")
        if parts.hostname != "db":
            errors.append("DATABASE_URL: Docker Compose hostname must be db")
        if unquote(parts.password or "") != config.get("POSTGRES_PASSWORD", ""):
            errors.append("DATABASE_URL: password does not match POSTGRES_PASSWORD")
    except ValueError:
        errors.append("DATABASE_URL: invalid URL")
    if config.get("ADMIN_PASSWORD") or config.get("ADMIN_PASSWORD_HASH"):
        if len(config.get("ADMIN_SESSION_SECRET", "")) < 32:
            errors.append("ADMIN_SESSION_SECRET: must be >= 32 characters when admin UI enabled")
    if require_telegram:
        for key in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH"):
            if not config.get(key):
                errors.append(f"{key}: required for Telethon")
        try:
            if int(config.get("TELEGRAM_API_ID", "0")) <= 0:
                errors.append("TELEGRAM_API_ID: must be positive integer")
        except ValueError:
            errors.append("TELEGRAM_API_ID: must be integer")
    return errors

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--ingest", action="store_true")
    args = parser.parse_args()
    path = Path(args.env)
    if not path.is_file():
        print("FAIL: .env not found (copy .env.example first)")
        return 2
    try:
        errors = validate(parse_env(path), require_telegram=args.ingest)
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 2
    for error in errors:
        print(f"FAIL: {error}")
    if not errors:
        print("OK: deployment environment checks passed (no connection attempts)")
    return int(bool(errors))

if __name__ == "__main__":
    sys.exit(main())
