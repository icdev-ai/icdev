# ZIG Data Foundation — External Targets & Ingest Adapters

**Phase:** zig-ext (multi-target extension)  
**Classification:** CUI // SP-CTI  
**Tasks:** zig-ext-08, zig-ext-14, zig-ext-16, zig-ext-19, zig-ext-20 (+ zig-ext-01/03-06/12/13/15/17/18/22/23 infra)

---

## What Was Built

### Foundation (pre-existing, wired up this phase)
- `icdev/tools/security_canvas/db/init_db.py` — `zig_targets` table + `target_id` column on `zig_maturity_scores`
- `icdev/tools/security_canvas/constants.py` — `ZIG_EVIDENCE_MAP`, `ZIG_EXTERNAL_APP_TYPES`, `ZIG_INGEST_SOURCE_TYPES`
- `icdev/tools/security_canvas/zig_pillar_scorer.py` — `target_id` threaded into `score_pillar()` / `score_all_pillars()`
- `icdev/tools/security_canvas/zig_activity_tracker.py` — `target_id` threaded into `set_activity_status()` / `bulk_activity_status()`, compound key `(activity_id, target_id)` in SQL
- `icdev/tools/security_canvas/zig_assessor.py` — `target_id` threaded into `run_zig_assessment()` / `_persist_scores()`; ZTA bridge conditional on `target_id == "icdev-self"`

### New Modules
| File | Purpose |
|------|---------|
| `icdev/tools/security_canvas/zig_external_adapter.py` | 5 ingest adapters (SBOM, SAST, survey, Nmap, OpenAPI) |
| `icdev/tools/security_canvas/zig_portfolio.py` | Portfolio health, radar comparison, per-target assessment |

### Blueprint Routes (added to `icdev/tools/security_canvas/blueprint.py`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/zig/targets` | List all ZIG targets |
| POST | `/api/zig/targets` | Create new target |
| GET | `/api/zig/targets/<id>` | Get single target |
| POST | `/api/zig/targets/<id>/assess` | Run assessment for target |
| PATCH | `/api/zig/targets/<id>/activities/<act_id>` | Update activity status |
| POST | `/api/zig/targets/<id>/ingest` | Ingest scan results (SBOM/SAST/survey/Nmap/OpenAPI) |
| GET | `/zig/portfolio` | Portfolio dashboard (existing template wired) |
| GET | `/api/zig/portfolio/health` | Portfolio health JSON |
| GET | `/api/zig/portfolio/compare?targets=id1,id2` | Radar comparison JSON |

### Tests
- `tests/test_zig_ingest_adapters.py` — 31 tests, all passing
- `tests/fixtures/zig/` — 5 fixture files (CycloneDX SBOM, Bandit JSON, survey JSON, Nmap XML, OpenAPI YAML)

---

## Ingest Adapter Mappings

| Source | Finding Key | ZIG Activity |
|--------|------------|-------------|
| `sbom` | `cve_critical` | `zig-act-d08` |
| `sbom` | `cve_high` | `zig-act-d08` |
| `sbom` | `outdated_dep` | `zig-act-p1-21` |
| `sast` | `B105`, `B106` | `zig-act-p1-29` (encryption at rest) |
| `sast` | `B502`, `B503` | `zig-act-p1-18` (encrypt in transit) |
| `sast` | `B608` | `zig-act-p1-21` (SAST/SCA in CI/CD) |
| `survey` | `mfa` | `zig-act-p1-01` |
| `survey` | `mfa_admin` | `zig-act-p1-02` |
| `survey` | `rbac` | `zig-act-p1-14` |
| `survey` | `pam` | `zig-act-p1-03` |
| `survey` | `least_priv` | `zig-act-p1-15` |
| `survey` | `lifecycle` | `zig-act-d07` |
| `nmap` | `http_without_https` | `zig-act-p1-18` |
| `nmap` | `admin_ports_open` | `zig-act-p1-16` |
| `nmap` | `no_tls_on_api` | `zig-act-p2-15` |
| `openapi` | `no_security_scheme` | `zig-act-p2-19` |
| `openapi` | `http_only` | `zig-act-p1-18` |

---

## Sandbox Coverage Decision

| Module | Decision |
|--------|---------|
| `zig_external_adapter.py` | **sandboxed** — parses user-supplied SBOM/SAST/Nmap/OpenAPI; uses stdlib `json`, `xml.etree.ElementTree`, optional `yaml.safe_load` only. No eval, no shell exec, no filesystem writes. |
| `zig_portfolio.py` | **trusted-first-party** — reads own DB tables only, no user-supplied content. |
