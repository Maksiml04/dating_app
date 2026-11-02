"""Read-only smoke checks, run inside the API container after migrations."""
import json
import os
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from sqlalchemy import create_engine, inspect, text
from app.config import settings

BASE = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
EXPECTED = {"canonical", "publication_version", "telegram_outbox", "source_message", "catalog_draft"}


def request(path, *, method="GET", payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urlopen(req, timeout=8) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def run():
    failures = []
    def check(name, condition):
        print(("PASS" if condition else "FAIL") + ": " + name)
        if not condition: failures.append(name)

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    with engine.connect() as conn:
        check("PostgreSQL SELECT 1", conn.execute(text("SELECT 1")).scalar() == 1)
        check("pgvector extension", conn.execute(text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')")).scalar())
        revisions = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        check("single Alembic head 0009", revisions == ["0009_worker_fencing"])
        tables = set(inspect(conn).get_table_names())
        check("core tables exist", EXPECTED <= tables)
    engine.dispose()

    code, body = request("/health")
    check("liveness", code == 200 and json.loads(body).get("status") == "ok")
    code, body = request("/ready")
    check("readiness", code == 200 and json.loads(body).get("status") == "ready")
    code, _ = request("/api/admin/drafts")
    check("admin API denies missing key", code == 403)
    code, _ = request("/api/telegram/webhook", method="POST", payload={"update_id": 123})
    check("webhook denies missing secret", code == 403)
    code, _ = request("/api/telegram/webhook", method="POST", payload={"update_id": 123}, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"})
    check("webhook denies incorrect secret", code == 403)
    print("SMOKE " + ("FAILED" if failures else "PASSED") + f": {len(failures)} failed")
    return int(bool(failures))

if __name__ == "__main__":
    try: sys.exit(run())
    except Exception as exc:
        print(f"SMOKE ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
