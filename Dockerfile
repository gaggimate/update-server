FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UPDATE_STORAGE_DIR=/data/storage

WORKDIR /app

COPY pyproject.toml README.md ./
COPY update_server ./update_server

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "update_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
