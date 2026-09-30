FROM python:3.11-slim

# tesseract enables the local OCR fallback.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY labelverify ./labelverify
RUN pip install --no-cache-dir .

# SQLite database and stored label images live here; mount a volume to persist them.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn labelverify.web.app:serve --factory --host 0.0.0.0 --port ${PORT}"]
