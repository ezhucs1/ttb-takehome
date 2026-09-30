FROM python:3.11-slim

# tesseract enables the local OCR fallback; fonts are only needed to regenerate samples.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY labelverify ./labelverify
RUN pip install --no-cache-dir .

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn labelverify.web.app:app --host 0.0.0.0 --port ${PORT}"]
