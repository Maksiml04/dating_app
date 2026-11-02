# v0.15 — Deployment preflight and Telegram ingestion wiring

- Added opt-in `telegram_ingest` Compose profile and persistent Telethon session volume.
- Docker image now installs `.[telegram]` so ingestion can import Telethon.
- Added offline `python -m app.deployment_check --env .env` preflight that checks required configuration and database password consistency without displaying secrets.
- Added `--ingest` to require Telethon credentials and six regression tests.

## Usage

1. `cp .env.example .env` and replace placeholders locally. Never commit `.env`.
2. `python -m app.deployment_check --env .env`
3. `docker compose config` (requires Docker Compose installed).
4. `docker compose up --build -d` then check `/ready`.
5. After Telethon authorization/session setup: `docker compose --profile ingest run --rm telegram_ingest`.

**Limitations:** First Telethon login may require interactive phone/code/2FA; do not assume unattended first run. The importer is a one-shot job, not a scheduler. This environment has no Docker/PostgreSQL, so live integration and migrations are not verified. Preflight checks configuration only, not external connectivity or security completeness.
