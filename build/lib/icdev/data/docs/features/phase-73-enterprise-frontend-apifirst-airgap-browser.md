# CUI // SP-CTI
# Phase 73 — Enterprise Frontend: API-First, Air-Gap Browser Automation & SharePoint Fallback

**Date:** 2026-04-17
**Specification:** N/A (Kanban task efa-I1-feature-doc)

---

## Overview

This phase delivers four tightly-coupled capabilities that together enable ICDEV™ to serve
enterprise, GovCloud, and air-gapped DoD environments: (a) an incremental Flask → Next.js
migration via the strangler fig pattern, (b) a typed `/api/v1/*` contract backed by an
OpenAPI 3.1 spec, (c) zero-network browser automation using vendored msedgedriver, and (d)
a SharePoint Server REST client with an automatic Selenium fallback for classic/SPFx pages.
A two-tier E2E harness (Playwright native + Selenium canvas) validates the full stack.

---

## Why We Moved Off Flask-Only to the Next.js Strangler

The legacy ICDEV™ dashboard is a monolithic Flask application that renders HTML server-side.
Three pressures forced a change:

1. **Air-gap static export** — DoD IL4/IL5 environments cannot reach npm CDNs at runtime.
   `next build && next export -o out/` produces a fully static bundle that ships inside the
   container image. Flask Jinja2 templates cannot replicate this without a separate bundler
   pipeline with identical guarantees.

2. **Type-safe API surface** — The OpenAPI → TypeScript codegen workflow (`npm run codegen`)
   generates `frontend/lib/api-types.ts` directly from the live spec. Any breaking change to a
   `/api/v1/*` endpoint is immediately surfaced as a TypeScript compile error. Flask-rendered
   templates had no equivalent compile-time contract.

3. **Incremental, zero-downtime cutover** — The strangler fig manager
   (`tools/modernization/strangler_fig_manager.py`) tracks each dashboard component through
   four states: `legacy → parallel → modern → decommissioned`. Traffic is routed through an
   API gateway facade; only validated components are promoted to `modern`. The legacy Flask
   layer remains live until all components pass coexistence health checks, eliminating big-bang
   migration risk.

The strangler fig approach was chosen over a rewrite specifically because ICDEV™ must remain
deployable on day N+1 while migration proceeds — a hard requirement for active DoD programs.

---

## What Was Built

- **`tools/modernization/strangler_fig_manager.py`** — 1,695-line orchestrator: per-component
  cutover tracking, API gateway routing config generation, anti-corruption layer (ACL) adapter
  codegen (Python / Java / C#), and coexistence health validation.
- **`frontend/package.json`** — Next.js project config with `build:airgap` (static export) and
  `codegen` (OpenAPI → TypeScript type generation) scripts.
- **`frontend/lib/api-types.ts`** — Machine-generated TypeScript types derived from the
  `/api/v1/openapi.json` spec (via `openapi-typescript ^7.8.0`).
- **`tools/saas/openapi_spec.py`** — Programmatic OpenAPI 3.1 spec: 23 endpoints across 8
  tags (Tenants, Users, API Keys, Projects, Compliance, Security, Audit, Usage, Health).
- **`tools/airgap/driver_vendor.py`** — Admin tool to download and SHA256-verify msedgedriver
  and chromedriver binaries into `vendor/drivers/` — a one-time operation that makes all
  subsequent E2E runs network-free.
- **`tools/browser/driver_manager.py`** — Runtime driver resolver with four-level fallback:
  vendored msedgedriver → PATH msedgedriver → vendored chromedriver → PATH chromedriver.
- **`tools/sharepoint/client.py`** — 413-line minimal REST client for on-prem SharePoint Server
  2016 / 2019 / Subscription Edition: NTLM, Kerberos, Basic auth; exponential backoff; hard
  fail on 401.
- **`tools/sharepoint/browser_fallback.py`** — Phase F (P4.2) Selenium fallback for classic
  web parts and SPFx pages unreachable via REST; gated by `sharepoint.fallback_enabled`.
- **`tools/sharepoint/ingest.py`** — Decision-tree orchestrator: try REST → on `FallbackRequired`
  exception, invoke `browser_fallback.fetch_classic_page()`.
- **`args/sharepoint.yaml`** — Declarative config for endpoint, auth, ingest schedule, fallback
  flag, TLS verify.
- **`tools/testing/e2e_runner.py`** — Two-tier E2E orchestrator: `native` (Playwright
  `.spec.ts`) and `mcp` (markdown specs via Claude + Playwright MCP); `--driver selenium` flag
  (G6) routes canvas tests through Selenium instead.

---

## Technical Implementation

### Files Modified / Added

| File | Change |
|------|--------|
| `tools/modernization/strangler_fig_manager.py` | New — strangler fig orchestrator |
| `frontend/package.json` | New — Next.js + openapi-typescript config |
| `frontend/lib/api-types.ts` | Generated — TypeScript types from OpenAPI spec |
| `tools/saas/openapi_spec.py` | Extended — 23-endpoint OpenAPI 3.1 spec |
| `tools/airgap/driver_vendor.py` | New — admin driver download + SHA256 verify |
| `tools/browser/driver_manager.py` | New — runtime driver resolver |
| `tools/sharepoint/client.py` | New — on-prem SharePoint REST client |
| `tools/sharepoint/browser_fallback.py` | New — Selenium classic-page fallback |
| `tools/sharepoint/ingest.py` | New — REST → fallback decision tree |
| `args/sharepoint.yaml` | New — SharePoint config schema |
| `tools/testing/e2e_runner.py` | Extended — `--driver selenium` mode (G6) |

### Key Changes

- **Strangler fig cutover lifecycle** enforced as a state machine:
  `legacy → parallel → modern → decommissioned`. No component can skip `parallel`; the
  coexistence health check must pass before promotion.

- **OpenAPI → TypeScript pipeline** is one command (`npm run codegen`) that hits the live
  `/api/v1/openapi.json` endpoint and writes `lib/api-types.ts`. The spec is generated
  programmatically in Python so spec and implementation are always in sync (enforced by the
  `check_openapi_parity()` coherence gate, commit `5baa44fd`).

- **Vendored driver layout** isolates major versions:
  ```
  vendor/drivers/
    msedgedriver/{major}/msedgedriver[.exe]  + SHA256SUM
    chromedriver/{major}/chromedriver[.exe]  + SHA256SUM
  ```
  The runtime resolver matches the installed Edge major version to the vendored binary — no
  CDN, no `webdriver-manager`, no network.

- **SharePoint REST endpoints used:**
  `/_api/search/query`, `/_api/web/webs`, `/_api/web/lists`, `/_api/web/lists(guid'...')/items`,
  `/_api/web/GetFileByServerRelativeUrl('...')/$value`

- **Fallback gating** — `browser_fallback.fetch_classic_page()` raises `FallbackDisabledError`
  when `sharepoint.fallback_enabled: false` (the default), so environments without a display
  server are never silently broken.

---

## /api/v1 + OpenAPI Contract

The spec is served at `GET /api/v1/openapi.json` (also `/api/v1/openapi.yaml`).

All endpoints require `ApiKeyAuth` (header `X-API-Key`). Base path: `/api/v1`.

**Tag summary:**

| Tag | Endpoints |
|-----|-----------|
| Tenants | CRUD `/tenants`, `/tenants/{id}` |
| Users | CRUD `/users`, `/users/{id}` |
| API Keys | `/api-keys`, `/api-keys/{id}` |
| Projects | CRUD `/projects`, `/projects/{id}` |
| Compliance | `/compliance/controls`, `/compliance/controls/{id}` |
| Security | `/security/scans`, `/security/scans/{id}` |
| Audit | Read-only `/audit/events`, `/audit/events/{id}` |
| Usage | `/usage/metrics`, `/usage/metrics/{id}` |
| Health | `GET /health` (no auth required) |

OpenAPI 3.1 schema components include: `Tenant`, `User`, `ApiKey`, `Project`,
`ComplianceControl`, `SecurityScan`, `AuditEvent`, `UsageMetric`, `Error`.

---

## Air-Gap Browser Automation

### Why No CDN

DoD air-gapped networks block all outbound internet. The standard `webdriver-manager` package
downloads drivers at test runtime from Google/Microsoft CDNs — this fails silently on IL4/IL5
networks and has caused blocked CI pipelines on prior programs.

### One-Time Admin Vendoring

```bash
# Vendor msedgedriver matching the installed Edge version
python tools/airgap/driver_vendor.py --fetch-edge

# Vendor a specific version
python tools/airgap/driver_vendor.py --fetch-edge --version 134.0.3124.57

# Vendor chromedriver by major
python tools/airgap/driver_vendor.py --fetch-chrome --major 134

# Verify SHA256 of all vendored binaries
python tools/airgap/driver_vendor.py --verify

# List vendored drivers
python tools/airgap/driver_vendor.py --list --json
```

This step requires internet access and is run once by an admin. The `vendor/drivers/` directory
is committed to the repo or pre-loaded into the container image before air-gap deployment.

### Runtime Resolution (No Network)

`tools/browser/driver_manager.py` `get_driver()` tries in order:

1. Vendored `msedgedriver` matching installed Edge major version
2. `msedgedriver` on PATH (system-installed)
3. Vendored `chromedriver`
4. `chromedriver` on PATH

The driver is launched headless with `--no-sandbox`, `--disable-gpu`,
`--window-size=1920,1080`.

---

## SharePoint REST Primary + Selenium Fallback Architecture

```
ingest.py
  │
  ├─► SharePointClient.list_items()  ← REST API (/_api/web/lists/.../items)
  │     ├── success → store items in DB (sharepoint_items table)
  │     └── FallbackRequired raised by classic-page detection
  │
  └─► browser_fallback.fetch_classic_page(url, selectors)
        ├── guard: raise FallbackDisabledError if fallback_enabled=false
        ├── get_driver() → vendored msedgedriver / chromedriver
        ├── driver.get(url)  [NTLM SSO via Windows Credential Manager]
        └── extract rows by CSS selectors → return List[Dict[str, str]]
```

**Auth modes** (configured in `args/sharepoint.yaml`):

| Mode | Library | Notes |
|------|---------|-------|
| `ntlm` | `requests-ntlm` (optional import) | On-prem AD domains |
| `kerberos` | `requests-kerberos` (optional import) | Kerberos SSO |
| `basic` | stdlib `HTTPBasicAuth` | Always available |

The NTLM and Kerberos libraries are **lazy-imported** so `auth_mode='basic'` works on
Python 3.14 environments that cannot build the C extensions (aligns with
`feedback_airgap_compat.md`).

**SharePoint Online / M365** is explicitly out of scope — that path requires Microsoft Graph +
MSAL and is deferred to a Phase C follow-up.

---

## Two-Tier E2E

| Tier | Technology | Spec Location | Triggered By |
|------|-----------|---------------|-------------|
| **Native** | Playwright TypeScript | `tests/e2e/*.spec.ts` | `npm run test:e2e` or `e2e_runner.py --mode native` |
| **Canvas / Selenium** | Python + Selenium WebDriver | `tests/e2e_*.py`, `tests/dashboard/e2e_*.py` | `e2e_runner.py --mode native --driver selenium` (G6) |

`e2e_runner.py` auto-selects `native` when Playwright is installed and `.spec.ts` files
exist; otherwise falls back to MCP (markdown specs via Claude + Playwright MCP server).

The `--driver selenium` flag (added in commit `b7ec5fd0`, G6) routes all canvas tests through
the Selenium `driver_manager` path, enabling full air-gap E2E without Playwright's CDN
dependency.

**Dashboard tests ported to Selenium (G4, commit `f9fbb273`):**
- `dashboard_health`
- `activity_usage`
- `agents_monitoring`

---

## How to Use

### 1. Vendor drivers (admin, one-time, requires internet)

```bash
python tools/airgap/driver_vendor.py --fetch-edge
python tools/airgap/driver_vendor.py --verify
```

### 2. Generate TypeScript API types

```bash
cd frontend
npm install
npm run codegen          # writes lib/api-types.ts from live /api/v1/openapi.json
```

### 3. Build air-gap static frontend

```bash
cd frontend
npm run build:airgap     # writes static bundle to frontend/out/
```

### 4. Run SharePoint ingest

```bash
# Configure first
cp args/sharepoint.yaml.example args/sharepoint.yaml
# Edit: endpoint_url, auth_mode, credentials

python tools/sharepoint/ingest.py --run-once --json
```

### 5. Run two-tier E2E

```bash
# Playwright (native)
python tools/testing/e2e_runner.py --mode native --run-all

# Selenium (air-gap)
python tools/testing/e2e_runner.py --mode native --driver selenium --run-all
```

### 6. Strangler fig status

```bash
python tools/modernization/strangler_fig_manager.py --plan-id <id> --status
```

---

## Configuration

### `args/sharepoint.yaml`

| Key | Default | Description |
|-----|---------|-------------|
| `endpoint_url` | — | SharePoint root (e.g. `https://sp.internal.mil`) |
| `auth_mode` | `ntlm` | `ntlm` \| `kerberos` \| `basic` |
| `credential_source` | `env` | `env` \| `windows_sso` \| `vault` |
| `fallback_enabled` | `false` | Enable Selenium fallback for classic pages |
| `ingest_interval_sec` | `3600` | Ingest polling interval |
| `verify_tls` | `true` | TLS certificate verification |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `ICDEV_SHAREPOINT_PASS` | Password for NTLM/Basic auth |
| `ICDEV_SHAREPOINT_USER` | Username (overrides yaml) |

### `frontend/.env.local`

```bash
NEXT_PUBLIC_API_BASE=http://localhost:5050/api/v1
```

---

## Testing

```bash
# Unit tests
pytest tests/test_sharepoint_client.py -v
pytest tests/test_driver_manager.py -v
pytest tests/test_strangler_fig.py -v

# Integration (requires running ICDEV server)
python tools/sharepoint/ingest.py --dry-run --json

# E2E — Playwright
python tools/testing/e2e_runner.py --mode native --run-all

# E2E — Selenium (air-gap)
python tools/testing/e2e_runner.py --mode native --driver selenium --run-all

# OpenAPI parity gate
python tools/workflow/coherence_checker.py --check openapi_parity --gate
```

Screenshots are written to `playwright/screenshots/<name>.png` per CLAUDE.md policy.

---

## NIST 800-53 Controls

| Control | How Addressed |
|---------|--------------|
| **AU-2 / AU-12** (Audit Events) | All SharePoint ingest and fallback invocations logged to append-only `audit_events` table |
| **AC-3** (Access Enforcement) | SharePoint auth modes enforce identity before any data access; fallback gated by config flag |
| **SC-8** (Transmission Confidentiality) | TLS enforced on SharePoint REST calls (`verify_tls: true`); NTLM/Kerberos provide message-level integrity |
| **SI-7** (Software Integrity) | SHA256SUM files in `vendor/drivers/` verify binary integrity before execution |
| **SA-11** (Developer Testing) | Two-tier E2E (Playwright + Selenium) provides automated acceptance evidence at each deployment |
| **CM-6** (Configuration Settings) | All behavior driven by `args/sharepoint.yaml` and `.env`; no hardcoded secrets or model IDs |
| **SI-2** (Flaw Remediation) | Strangler fig parallel state enables rolling rollback without service interruption |

---

## Notes

- **SharePoint Online is out of scope.** The REST client targets on-prem Server 2016 / 2019 /
  Subscription Edition only. M365 Graph + MSAL is deferred (Phase C follow-up).
- **`requests-ntlm` and `requests-kerberos` are optional** — `basic` auth always works. On
  Python 3.14 or air-gap environments where C extensions cannot be compiled, set
  `auth_mode: basic` and supply credentials via `ICDEV_SHAREPOINT_PASS`.
- **Driver vendoring is admin-only** — the download URLs hit Microsoft/Google CDNs. Run once
  before air-gap deployment; commit `vendor/drivers/` or bake into the container image.
- **Strangler fig ACL codegen** supports Python, Java, and C# adapter patterns. The `parallel`
  state is mandatory — no component may jump directly from `legacy` to `modern`.
- **Next.js version** is not pinned in `package.json` because the `build:airgap` script
  requires Next.js ≥ 13 (App Router + static export). Pin when container base image is locked.

---

## I3 Validation — CodeLens + Coherence Scan (2026-04-17)

### CodeLens Output (`tools/code_intelligence/codelens.py --all --json`)

```json
{
  "gate": "codelens",
  "status": "pass",
  "reason": "code intelligence scan completed",
  "target": "tools/",
  "analysis": {
    "scan_id": "scan-c328753a5936",
    "timestamp": "2026-04-17T06:25:44.544690+00:00",
    "files_analyzed": 1413,
    "total_functions": 13410,
    "avg_cyclomatic_complexity": 5.72,
    "total_smells": 13722,
    "avg_maintainability_score": 0.9278,
    "metric_count": 14823
  }
}
```

### Coherence Checker Output (`tools/workflow/coherence_checker.py --all --fix --gate`)

```json
{
  "overall_pass": true,
  "timestamp": "2026-04-17T06:26:08Z",
  "total_checks": 17,
  "passed_checks": 17,
  "failed_checks": 0,
  "warned_checks": 0,
  "total_fixes": 0,
  "checks_summary": [
    {"check_id": "schema_code", "status": "pass", "message": "All INSERT columns match schema definitions"},
    {"check_id": "config_code", "status": "pass", "message": "Checked 20 configs"},
    {"check_id": "signature_call", "status": "pass", "message": "No positional-arg risks detected"},
    {"check_id": "fixture_schema", "status": "pass", "message": "All 346 fixture tables match schema"},
    {"check_id": "manifest", "status": "pass", "message": "Manifest coverage adequate (974 tools checked)"},
    {"check_id": "append_only", "status": "pass", "message": "All append-only tables are protected"},
    {"check_id": "ruff_lint", "status": "pass", "message": "No blocking ruff lint issues"},
    {"check_id": "api_wiring", "status": "pass", "message": "All API handlers have DB/storage calls"},
    {"check_id": "route_uniqueness", "status": "pass", "message": "No duplicate Flask view functions"},
    {"check_id": "attribution_claims", "status": "pass", "message": "All 11 attribution claims match the registry"},
    {"check_id": "llm_injection_patterns", "status": "pass", "message": "No untrusted input reaching LLMRequest in 68 scanned tools"},
    {"check_id": "skill_standard", "status": "pass", "message": "All 21 SKILL.md files conform to the standard"},
    {"check_id": "sandbox_coverage", "status": "pass", "message": "All 4 gap references present in sandbox-coverage.md"},
    {"check_id": "direct_anthropic_import", "status": "pass", "message": "No disallowed direct anthropic imports detected"},
    {"check_id": "karpathy_sync", "status": "pass", "message": "All 10 AI platform configs contain all 5 canonical Karpathy principle headings"},
    {"check_id": "openapi_parity", "status": "pass", "message": "All 435 /api/v1/* routes present in OpenAPI spec — no api-contract-drift"}
  ]
}
```

# CUI // SP-CTI
