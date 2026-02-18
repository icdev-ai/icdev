FROM python:3.11-slim AS base

LABEL maintainer="ICDEV System"
LABEL classification="CUI"

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    apt-get autoremove -y && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .
COPY src/ ./src/

RUN chown -R appuser:appuser /app && chmod -R 750 /app
USER appuser
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "src.test_svc.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "4"]
