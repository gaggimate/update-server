# Dependencies are installed from poetry.lock so every image build gets the reviewed, pinned versions.
FROM python:3.12-slim AS builder

ENV POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_NO_INTERACTION=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install "poetry==2.3.2"

WORKDIR /app
COPY pyproject.toml poetry.lock README.md ./
RUN poetry install --only main --no-root


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    UPDATE_STORAGE_DIR=/data/storage

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY update_server ./update_server

RUN mkdir -p /data/storage && chown 1001:1001 /data/storage
USER 1001:1001

EXPOSE 8000

CMD ["uvicorn", "update_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
