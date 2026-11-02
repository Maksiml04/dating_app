# Проверки сборки v0.24

Среда: локальный Python, без Docker, PostgreSQL, SQLAdmin и авторизованного Telegram.

- `pytest -q --disable-warnings`: **169 passed, 2 skipped**. Пропущенные: PostgreSQL E2E, преднамеренно защищены переменной изолированной БД.
- `alembic upgrade head --sql`: **успешно**, SQL для 15 версий. Не равно выполнению миграций на PostgreSQL.
- `python -m compileall -q app`: **успешно**.
- `pip wheel . --no-deps --no-build-isolation`: **успешно**, wheel `date_ideas_mvp-0.24.0-py3-none-any.whl`.
- SQLAdmin ModelView custom expose: минимальная версия поднята до 0.21; фактическая проверка панели в браузере не проведена.
- Сеть Telegram/Telethon/Open-Meteo/MinIO: **не проверена**; unit-проверки используют mock transport.

**Итог:** код подготовлен к интеграционной приёмке, но FR-01–FR-21 нельзя официально признать принятыми без реального запуска и ручных сценариев из `FR_ACCEPTANCE_MATRIX.md`.
