# ICDEV Builder Agent — STIG-Hardened Container
# Port: 8445 | Tier: Domain
FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim

# STIG: Non-root user
RUN groupadd -g 1000 icdev && useradd -u 1000 -g icdev -s /bin/false icdev

WORKDIR /app
COPY --from=builder /install /usr/local
COPY tools/ tools/
COPY args/ args/
COPY context/ context/
COPY goals/ goals/

# STIG: Read-only root filesystem preparation
RUN mkdir -p /app/data /tmp && chown -R icdev:icdev /app /tmp

USER icdev
EXPOSE 8445

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8445/health')" || exit 1

ENTRYPOINT ["python", "tools/agent/agent_server.py", "--port", "8445"]
