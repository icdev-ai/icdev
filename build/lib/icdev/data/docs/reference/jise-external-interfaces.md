# CUI // SP-CTI
# Joint Intelligence Support Element (JISE) Portal — Required External Interfaces

> **Classification:** CUI // SP-CTI  
> **Generated:** 2026-05-16  
> **Scope:** All operational interfaces required by or provided to the JISE API  
> **Sources:** `tools/intelligence/jise_portal.py`, `tools/dashboard/api/jise.py`, `tools/saas/openapi_spec.py`, `tools/saas/auth/middleware.py`, `tools/dashboard/api/__init__.py`, `k8s/configmap.yaml`

---

## 1. Outward-Facing JISE API Endpoints (External Consumers Connect Here)

These are the REST endpoints the JISE portal exposes for downstream intelligence consumers.

| Method | Path | Description | Auth Required | Data Source |
|--------|------|-------------|---------------|-------------|
| `GET` | `/api/v1/jise/portal-data` | Structured intelligence records (SIGINT, HUMINT, OSINT, GEOINT, IMINT, FININT) with classification/source filtering. | Yes | `_SEED_RECORDS` (production: DB query) |
| `GET` | `/api/v1/jise/status` | Connectivity health check — probes DB liveness. | Yes | `icdev.db` |
| `GET` | `/api/v1/jise/requirements` | Requirements feed (id, title, description, status, priority, classification). | Yes | `requirements` table |
| `GET` | `/api/v1/jise/intelligence` | Security findings from scan results (severity, scan_type, title, description). | Yes | `security_scan_results` table |
| `GET` | `/api/v1/jise/compliance` | Compliance posture summary: open POAM count, control status summary, project count. | Yes | `poam_items`, `compliance_controls`, `projects` tables |

**Base URL:** `https://<host>/api/v1/jise` (also aliased at `/api/jise` for backward compatibility)

---

## 2. Authentication Methods

All JISE endpoints require authentication. The following methods are supported:

| Scheme | Header / Parameter | Description |
|--------|--------------------|-------------|
| **API Key (Bearer)** | `Authorization: Bearer icdev_<key>` | SHA-256 hashed lookup in `api_keys` table. Default scheme. |
| **OAuth 2.0 / OIDC (Bearer)** | `Authorization: Bearer <JWT>` | JWT decoded against tenant JWKS endpoint. Tenant resolved from `tenant_id` claim. |
| **CAC / PIV Certificate** | `X-Client-Cert-CN: <CN>`<br>`X-Client-Cert-Serial: <serial>` | TLS proxy extracts client cert CN. Looked up in `users` table. |
| **Portal Session** | `Authorization: Bearer psess_<token>` | Opaque session token validated against platform session store. |
| **Query Param (SSE)** | `?api_key=icdev_<key>` | Fallback for SSE connections that cannot set headers easily. |

**CUI Response Headers:** Every response includes `X-Classification: CUI` and security headers (HSTS, nosniff, DENY frame-options, no-store cache-control).

---

## 3. External Dependencies (Systems JISE API Consumes)

| Dependency | Purpose | Config / Env Var |
|------------|---------|------------------|
| **SQLite Database** | Default storage backend for all JISE feeds (requirements, scans, POAM, controls, projects). | `ICDEV_STORAGE_BACKEND=sqlite` (default); path: `data/icdev.db` |
| **PostgreSQL Database** | Production storage backend override. | `ICDEV_STORAGE_BACKEND=postgresql`; `DATABASE_URL=postgresql://...` |
| **Platform Database** | API key and user validation lookups. | `PLATFORM_DB_PATH=data/platform.db` |
| **AWS Bedrock** | Optional LLM provider for intelligence analysis / enrichment. | `BEDROCK_REGION=us-gov-west-1`, `BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0` |
| **ELK Stack** | Centralized logging / SIEM integration. | `ELK_URL=https://elk.internal:9200` |
| **Splunk** | Audit and security event ingestion. | `SPLUNK_URL=https://splunk.internal:8089` |
| **Prometheus** | Metrics scraping endpoint (`/metrics` on gateway). | `PROMETHEUS_URL=http://prometheus.internal:9090` |
| **Grafana** | Operational dashboards. | `GRAFANA_URL=http://grafana.internal:3000` |

---

## 4. Data Classifications Supported

The JISE portal handles records at the following classification levels:

- `UNCLASSIFIED`
- `FOUO`
- `CUI` (default)
- `SECRET`

> **Note:** IL6 / SECRET deployments require SIPR-only connectivity, NSA Type 1 encryption, and air-gapped CI/CD per ICDEV compliance framework.

---

## 5. Intelligence Collection Sources (Seed / Expected Production Feeds)

| Source | Description |
|--------|-------------|
| **SIGINT** | Signals intelligence intercepts |
| **HUMINT** | Human-source intelligence reports |
| **OSINT** | Open-source indicators |
| **GEOINT** | Geospatial / satellite imagery analysis |
| **IMINT** | Imagery intelligence |
| **FININT** | Financial intelligence |

---

## 6. NIST 800-53 Controls Mapping

| Control | Application |
|---------|-------------|
| AC-3 | Access Enforcement — all endpoints require auth |
| AU-2 | Audit Events — request logger captures all authenticated calls |
| AU-6 | Audit Review — Splunk / ELK ingestion of audit trail |
| CM-3 | Configuration Change Control — code under version control, BDD tests |
| SA-11 | Developer Security Testing — pytest + behave coverage |
| SI-12 | Information Management and Retention — classification headers on every response |

---

## 7. Operational Network Context

- **Impact Levels Supported:** IL2, IL4, IL5, IL6
- **Default Deployment Classification:** CUI // SP-CTI
- **TLS:** Required (Strict-Transport-Security enabled)
- **CORS:** Configurable origins; allowed headers include `Authorization`, `X-Client-Cert-CN`, `X-Client-Cert-Serial`
- **Rate Limiting:** Enforced per subscription tier (Starter: 60/min, Professional: 300/min, Enterprise: unlimited)

---

*End of document — generated by ICDEV research agent.*
