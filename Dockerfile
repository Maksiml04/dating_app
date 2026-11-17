FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml ./
COPY README.md ./
COPY app ./app
RUN pip install --no-cache-dir ".[telegram]"
COPY alembic ./alembic
COPY alembic.ini ./
# Собранный web-чат (npm run build) раздаётся API с корня.
COPY web/dist ./web/dist
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
