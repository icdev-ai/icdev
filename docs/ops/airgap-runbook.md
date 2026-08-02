# ICDEV™ Air-Gap Runbook

Operating ICDEV™ inside an air-gapped / IL5+ / SECRET enclave. Covers install,
network config, mTLS, and validation. Referenced from `CLAUDE.md`.

> **New to a pip-only air-gap install?** Start with
> [airgap-pip-install.md](airgap-pip-install.md) — the step-by-step offline
> wheelhouse install, the mandatory `icdev init`, `icdev setup` for toggling
> features, and a troubleshooting section (leading with "a page/canvas is
> missing from the menu → it's default-OFF"). This runbook covers the broader
> enclave operations below.

---

## 1. Install from a private PyPI mirror

`pip install icdev` works unchanged against a private mirror as long as pip is
pointed at it. The common setups:

### Option A — per-invocation
```bash
pip install icdev \
  --index-url https://pypi.internal.gov/simple \
  --trusted-host pypi.internal.gov
```

### Option B — user/site config (preferred for engineers)
```ini
# ~/.pip/pip.conf  (Linux)  or  %APPDATA%\pip\pip.ini  (Windows)
[global]
index-url = https://pypi.internal.gov/simple
extra-index-url = https://pypi.external-mirror.gov/simple
trusted-host = pypi.internal.gov

[install]
trusted-host = pypi.internal.gov
```

### Option C — mTLS-protected mirror
Private Artifactory / Nexus / devpi deployments often require a client
certificate. pip honours these env vars (also accepted as CLI flags):

| Env | pip flag | Purpose |
|---|---|---|
| `PIP_CLIENT_CERT` | `--client-cert` | PEM with both cert and key concatenated (pip reads both from this single file) |
| `PIP_CERT` | `--cert` | CA bundle used to verify the mirror's server cert |
| `PIP_INDEX_URL` | `--index-url` | Mirror URL |

```bash
export PIP_CLIENT_CERT=/etc/pki/icdev/pypi-client.pem   # cert + key, concatenated
export PIP_CERT=/etc/pki/icdev/enterprise-ca.pem
export PIP_INDEX_URL=https://pypi.internal.gov/simple
pip install icdev
```

For CI/CD pipelines that must not write env vars to disk, pass flags directly:
```bash
pip install icdev \
  --index-url https://pypi.internal.gov/simple \
  --client-cert /run/secrets/pypi-client.pem \
  --cert /etc/ssl/certs/enterprise-ca.pem
```

### Option D — fully disconnected (no mirror)
Stage wheels on removable media, then install from the local directory:
```bash
pip download icdev -d /mnt/transfer/wheels/ --platform manylinux2014_x86_64 \
  --python-version 3.12 --only-binary=:all:
# ship the wheelhouse into the enclave
pip install --no-index --find-links /mnt/wheels/ icdev
```

---

## 2. Mode detection

```bash
python -m tools.airgap --detect --json     # reports cloud vs air-gap
python -m tools.airgap --activate          # patches llm_config.yaml routing
```

When `ICDEV_AIRGAP=true` (or the detector flips it), the router refuses any
provider that requires internet egress.

---

## 3. Outbound mTLS (post-install)

The central HTTP client at `tools/http/client.py` reads these env vars and
applies them to every outbound call made via `get_session()` / `request()`:

| Env | Meaning |
|---|---|
| `ICDEV_MTLS_CLIENT_CERT` | Path to client certificate (PEM) |
| `ICDEV_MTLS_CLIENT_KEY` | Path to client private key (PEM) |
| `ICDEV_MTLS_CA_BUNDLE` | CA bundle for server verification |
| `ICDEV_MTLS_VERIFY` | `false` disables TLS verification (testing only) |
| `ICDEV_HTTP_TIMEOUT` | Default timeout in seconds (default 30) |
| `ICDEV_HTTP_PROXY` / `ICDEV_HTTPS_PROXY` | Proxy URLs (HTTP_PROXY / HTTPS_PROXY also honoured) |

Precedence: `ICDEV_MTLS_*` env > per-client config (a2a/eMASS/Xacta accept
both but defer to env when set).

---

## 4. PostgreSQL mTLS

`tools/db/storage.py` builds the libpq SSL connection from these env vars:

| Env | libpq param | Typical value |
|---|---|---|
| `ICDEV_PG_SSLMODE` | `sslmode` | `verify-full` |
| `ICDEV_PG_SSLCERT` | `sslcert` | `/etc/pki/icdev/pg-client.crt` |
| `ICDEV_PG_SSLKEY` | `sslkey` | `/etc/pki/icdev/pg-client.key` |
| `ICDEV_PG_SSLROOTCERT` | `sslrootcert` | `/etc/pki/icdev/pg-ca.crt` |
| `ICDEV_PG_SSLCRL` | `sslcrl` | `/etc/pki/icdev/pg.crl` |

Unset vars fall through to libpq defaults.

---

## 5. Inbound dashboard TLS

To serve the dashboard on HTTPS with optional client-cert verification:

```bash
export ICDEV_DASHBOARD_TLS_CERT=/etc/pki/icdev/dashboard.crt
export ICDEV_DASHBOARD_TLS_KEY=/etc/pki/icdev/dashboard.key
# optional — triggers mTLS (CERT_REQUIRED):
export ICDEV_DASHBOARD_TLS_CA_BUNDLE=/etc/pki/icdev/enterprise-ca.pem
icdev-dashboard --port 5050
```

For production: terminate TLS at nginx/haproxy in front of the dashboard and
leave the app bound to `127.0.0.1:5050` without TLS.

---

## 6. Cron / scheduled tasks

```bash
# /etc/cron.d/icdev-audit — nightly compliance scan
0 2 * * * icdev-user cd /opt/icdev && \
  python -c "
from tools.airgap.hook_compat import get_session_id, run_auto_commit
get_session_id()   # sets CLAUDE_SESSION_ID + ICDEV_SESSION_ID for audit trail
# ... invoke tools here (health_check, bandit, etc.) ...
run_auto_commit('chore: nightly audit auto-commit')
" >> /var/log/icdev/cron.log 2>&1
```

---

## 7. Validation

```bash
python tools/testing/health_check.py --json
python tools/testing/e2e_runner.py --run-all --mode native --json
python -m bandit -r tools/ --severity-level medium
python tools/workflow/coherence_checker.py --all --gate
```

All four should exit 0. The coherence gate is authoritative for pre-merge
readiness; the E2E suite is the sign-off gate.

---

## 8. Air-gap LLM routing

```bash
# .env — forces all routing through local Ollama, no cloud fallback
OLLAMA_BASE_URL=http://localhost:11434
ICDEV_LLM_PROVIDER=ollama
# Also set in args/llm_config.yaml: two_tier.enabled: false
```

Programmatic:
```python
from tools.airgap import is_airgap, activate_airgap
if is_airgap():
    activate_airgap()
```

---

## 9. GitLab CI gate (example)

```yaml
security-scan:
  stage: validate
  script:
    - export ICDEV_AUTO_COMMIT=true
    - python tools/testing/health_check.py --json
    - python -m bandit -r tools/ --severity-level medium
    - python -c "from tools.airgap.hook_compat import run_pre_tool_check; \
        r = run_pre_tool_check('Bash', {'command': 'git push'}); \
        exit(0 if r['allowed'] else 1)"
    - python -c "from tools.airgap.hook_compat import run_auto_commit; \
        run_auto_commit('ci: post-scan auto-commit')"
```

---

## 10. Strategos OSINT pre-staging (air-gap OSINT ingestion)

When `ICDEV_AIRGAP=true`, the OSINT harvester cannot reach internet RSS feeds
directly. Use the three-tier fallback chain to feed signals into an air-gapped
enclave:

### Tier resolution order (first reachable wins)

| Tier | Condition | Data source |
|---|---|---|
| `TIER_INTERNET` | `ICDEV_AIRGAP` unset | Live RSS/Atom feeds |
| `TIER_GITLAB` | `ICDEV_AIRGAP=true` + `GITLAB_URL` reachable + `GITLAB_OSINT_PROJECT_ID` set | GitLab CI artifact |
| `TIER_FILE_INBOX` | `ICDEV_AIRGAP=true` + JSON files in `data/osint_inbox/` | Pre-staged JSON batches |
| `TIER_NONE` | All sources unavailable | Audit row written; 0 signals; exit 0 |

### Option A — Pre-stage on the internet-connected side, transfer via removable media

```bash
# On the internet-connected machine:
python tools/strategos/osint_prestage.py --output-dir /mnt/transfer/osint_inbox/

# Copy the generated osint_prestage_<timestamp>.json files to removable media,
# then copy into the enclave's data/osint_inbox/ directory.
# The harvester will pick them up automatically on the next Genesis reflex cycle.
```

Dry-run (count without writing):
```bash
python tools/strategos/osint_prestage.py --dry-run --json
```

### Option B — GitLab CI artifact (TIER_GITLAB)

Configure a GitLab pipeline job (`osint_collect`) in the OSINT project that
runs `tools/strategos/gitlab_osint_collector.py` and uploads `osint_signals.json`
as a CI artifact. The harvester downloads and processes it automatically:

```bash
# Required env vars on the air-gapped side:
export ICDEV_AIRGAP=true
export GITLAB_URL=https://gitlab.internal.gov
export GITLAB_TOKEN=<service-account-token>
export GITLAB_OSINT_PROJECT_ID=<project-id>   # numeric project ID
export GITLAB_OSINT_REF=main                   # branch/tag (default: main)
```

### Option C — File inbox (TIER_FILE_INBOX, fallback)

Drop pre-staged JSON files into `data/osint_inbox/` before the reflex fires.
The harvester accepts any file matching `osint_prestage_*.json` or `*.json`:

```bash
# Format expected by osint_harvester:
# {"signals": [{"title": str, "body": str, "source": str, "date": str, "url": str, "geo_hint": null|str}, ...], "count": N, "prestaged_at": "..."}
ls data/osint_inbox/          # pending files
ls data/osint_inbox/processed/ # already ingested files (moved here after processing)
```

### Verifying tier resolution

```bash
python tools/strategos/tier_resolver.py --json
# Returns: {"osint_tier": "TIER_FILE_INBOX", "exec_tier": "EXEC_OLLAMA_LOCAL", ...}
```

### TIER_NONE behaviour

If all tiers are unavailable (inbox empty, GitLab unreachable, internet disabled):
- The harvester exits 0 (no failure).
- A diagnostic audit row is written to `sg_raw_signals_audit` with `reason="TIER_NONE: no source available"`.
- The Kanban task is NOT marked failed — it remains in `in_progress` and retries on the next cycle.

---

## 11. Escape hatches

| Variable | Effect |
|---|---|
| `ICDEV_MTLS_VERIFY=false` | Disables all outbound TLS verification (DEV ONLY) |
| `ICDEV_KANBAN_VERIFY_GATE=false` | Dashboard `/move` skips the done-gate (bulk migrations) |
| `ICDEV_KANBAN_VERIFY_BUDGET_SEC` | Overrides the 300s per-task verification budget |
| `ICDEV_PG_NO_FALLBACK=true` | Crash instead of falling back to SQLite when PG is unreachable |

Never set the first three in production.
