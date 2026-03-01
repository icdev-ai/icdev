# Phase 56 — Compliance Evidence & Artifact Lineage

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 56 |
| Title | Compliance Evidence & Artifact Lineage |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 46 (Observability & XAI), Phase 18 (MBSE Integration), Phase 23 (Universal Compliance Platform), Phase 4 (NIST Compliance) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-26 |

---

## 1. Problem Statement

ICDEV supports 14 compliance frameworks (NIST 800-53, FedRAMP, CMMC, HIPAA, CJIS, PCI DSS, ISO 27001, SOC 2, NIST 800-207, MITRE ATLAS, AI Transparency, SBOM, audit trail, and more), each generating evidence across different DB tables and file artifacts. Before Phase 56, there was no unified mechanism to:

- Collect evidence across all frameworks in a single operation
- Check whether collected evidence is still fresh enough for an upcoming ATO assessment
- Integrate evidence freshness into the heartbeat monitoring daemon for continuous compliance
- Visualize the relationships between artifacts produced at every stage of the SDLC — from MBSE model elements through provenance activities to audit events and SBOM components

Assessors had to query each framework's DB tables individually. Compliance officers had no cross-framework inventory view. The digital thread, provenance graph, audit trail, and SBOM were four separate data silos with no unified visualization.

Phase 56 closes these gaps with two capabilities: universal evidence auto-collection with freshness monitoring (D347), and an artifact lineage DAG that joins all four data sources into a single interactive visualization (D348).

---

## 2. Goals

1. Provide a universal evidence collector that spans all 14 compliance frameworks in a single CLI invocation
2. Map each framework to its backing DB tables and file artifact patterns via a declarative registry
3. Compute per-framework evidence counts, freshness timestamps, and staleness alerts
4. Support configurable max-age thresholds for freshness checking (default 168 hours / 7 days)
5. Integrate evidence freshness into the heartbeat daemon for continuous compliance monitoring
6. Build a unified artifact lineage DAG joining 4 data sources: digital thread, W3C PROV, audit trail, and SBOM
7. Render the lineage DAG as an SVG visualization on the `/lineage` dashboard page
8. Provide a `/evidence` dashboard page with framework inventory, collection trigger, and freshness checking
9. Expose REST API endpoints for both evidence and lineage operations

---

## 3. Architecture

```
         Universal Compliance Evidence & Artifact Lineage
  ┌─────────────────────────────────────────────────────────────┐
  │                evidence_collector.py (D347)                 │
  │   FRAMEWORK_EVIDENCE_MAP: 14 frameworks → tables + globs   │
  └──────────────┬──────────────────────────────┬───────────────┘
                 │                              │
     ┌───────────┴───────────┐       ┌──────────┴──────────┐
     │  collect_evidence()   │       │  check_freshness()  │
     │  per-table counts     │       │  max_age_hours      │
     │  per-file hashing     │       │  staleness alerts    │
     └───────────┬───────────┘       └──────────┬──────────┘
                 │                              │
      ┌──────────┴──────────────────────────────┴──────────┐
      │              Dashboard: /evidence                   │
      │  stat grid · framework table · collect · freshness  │
      └─────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────┐
  │              lineage_api.py (D348)                          │
  │   4 data sources → unified DAG (nodes + edges)             │
  └──────────────┬──────────────────────────────────────────────┘
                 │
     ┌───────────┼───────────────┬──────────────┐
     ↓           ↓               ↓              ↓
  Digital     W3C PROV       Audit Trail     SBOM
  Thread      Entities       Events          Components
  (MBSE)      + Relations    (append-only)   (sbom_records)
     │           │               │              │
     └───────────┼───────────────┘              │
                 ↓                              ↓
          Nodes + Edges ────────────────────────┘
                 │
      ┌──────────┴──────────────────────────────────────────┐
      │              Dashboard: /lineage                     │
      │  stat grid · SVG DAG · artifact inventory table     │
      └─────────────────────────────────────────────────────┘

  Heartbeat Integration:
  ┌──────────────────────────────────────────────────────────┐
  │  heartbeat_daemon.py  →  check_evidence_freshness()     │
  │  Periodic probe → stale evidence → audit + SSE alert    │
  └──────────────────────────────────────────────────────────┘
```

### Key Design Principles

- **Extends existing patterns** — Evidence collector follows the `cssp_evidence_collector.py` pattern, not a new architecture (D347)
- **Declarative registry** — `FRAMEWORK_EVIDENCE_MAP` maps each framework to DB tables and file globs; add new frameworks without code changes (D26 pattern)
- **Read-only DAG** — Lineage visualization joins existing tables without creating new data or modifying sources (D348)
- **Air-gap safe** — All operations use stdlib `sqlite3`, `hashlib`, `pathlib`, and `xml.etree.ElementTree`; zero external dependencies
- **Append-only audit** — Evidence collection events recorded in the audit trail (D6 pattern)

---

## 4. Implementation

### Component 1: Universal Evidence Collector (`tools/compliance/evidence_collector.py`)

**Declarative Framework Registry** — `FRAMEWORK_EVIDENCE_MAP` defines 14 frameworks, each with:

| Field | Purpose |
|-------|---------|
| `description` | Human-readable framework name |
| `tables` | List of DB tables containing evidence for this framework |
| `file_patterns` | Glob patterns for file-based artifacts (e.g., `**/ssp_*.json`) |
| `required` | Whether this framework is mandatory for ATO readiness |

**14 Supported Frameworks:**

| Framework | Tables | Required |
|-----------|--------|----------|
| `nist_800_53` | control_implementations, audit_trail, stig_results | Yes |
| `fedramp` | fedramp_assessments, control_implementations, oscal_validation_log | Yes |
| `cmmc` | cmmc_assessments, control_implementations | No |
| `hipaa` | hipaa_assessments | No |
| `cjis` | cjis_assessments | No |
| `pci_dss` | pci_dss_assessments | No |
| `iso27001` | iso27001_assessments | No |
| `soc2` | soc2_assessments | No |
| `nist_800_207` | nist_800_207_assessments, zta_maturity_scores | No |
| `atlas` | atlas_assessments, atlas_red_team_results | No |
| `ai_transparency` | omb_m25_21_assessments, omb_m26_04_assessments, nist_ai_600_1_assessments, gao_ai_assessments, model_cards, system_cards, ai_use_case_inventory | No |
| `sbom` | sbom_records | Yes |
| `audit_trail` | audit_trail | Yes |
| `hitrust` | hitrust_assessments | No |

**Core Functions:**

- `collect_evidence(project_id, project_dir, framework)` — Scans all or one framework, counts DB records per table, hashes file artifacts, returns structured summary
- `check_freshness(project_id, max_age_hours)` — Computes evidence age for each framework, flags stale items beyond threshold
- `list_frameworks()` — Returns all registered frameworks with metadata

**Helper Utilities:**

- `_count_project_records()` — Counts records for a project in a table, auto-detects timestamp columns (`created_at`, `collected_at`, `assessed_at`, `timestamp`) for freshness
- `_hash_file()` — SHA-256 file hashing for artifact integrity verification
- `_compute_age_hours()` — Parses multiple timestamp formats (ISO, SQLite datetime) and computes age in hours
- `_table_exists()` — Safe table existence check via `sqlite_master`

### Component 2: Artifact Lineage API (`tools/dashboard/api/lineage.py`)

**Blueprint `lineage_api`** with routes:

- `GET /api/lineage/graph` — Builds the unified DAG for a project by querying 4 data sources
- `GET /api/lineage/stats` — Returns node/edge counts per data source

**4 Data Sources Joined into DAG:**

| Source | Table(s) | Node Type | Edge Type |
|--------|----------|-----------|-----------|
| Digital Thread | `digital_thread_links` | `source_type:source_id` | `link_type` (traces_to, implements, etc.) |
| W3C Provenance | `prov_entities`, `prov_relations` | `entity_type` | `relation_type` (wasGeneratedBy, used, wasDerivedFrom) |
| Audit Trail | `audit_trail` | `audit_event` | Temporal ordering (last 50 events) |
| SBOM | `sbom_records` | `sbom_component` | Component dependency (up to 100 components) |

Each node carries: `id`, `type`, `label`, `source`. Each edge carries: `source`, `target`, `relation`, `origin`.

### Component 3: Evidence Dashboard API (`tools/dashboard/api/evidence.py`)

**Blueprint `evidence_api`** with routes:

- `GET /api/evidence/stats` — Overall evidence statistics (framework count, required count, per-framework record totals)
- `POST /api/evidence/collect` — Trigger evidence collection for a project (accepts `project_id`, `framework`, `project_dir`)
- `GET /api/evidence/freshness` — Check evidence freshness for a project (accepts `project_id`, `max_age_hours`)

### Component 4: Heartbeat Integration

The heartbeat daemon (`tools/monitor/heartbeat_daemon.py`) includes an evidence freshness check that periodically probes stale evidence across all required frameworks. When evidence exceeds the configured max-age threshold, the daemon:

1. Records the staleness event in the audit trail
2. Pushes an SSE notification to the dashboard
3. Sends an alert to configured gateway channels (if enabled)

---

## 5. Database

Phase 56 does not create new database tables. It reads from existing tables across multiple subsystems:

### Evidence Collection — Tables Read

| Table | Framework | Purpose |
|-------|-----------|---------|
| `control_implementations` | NIST 800-53, FedRAMP, CMMC | Control implementation evidence |
| `audit_trail` | Audit Trail | Append-only event log |
| `stig_results` | NIST 800-53 | STIG scan results |
| `fedramp_assessments` | FedRAMP | FedRAMP assessment records |
| `oscal_validation_log` | FedRAMP | OSCAL validation attempts |
| `cmmc_assessments` | CMMC | CMMC practice assessments |
| `hipaa_assessments` | HIPAA | HIPAA safeguard assessments |
| `cjis_assessments` | CJIS | CJIS policy area assessments |
| `pci_dss_assessments` | PCI DSS | PCI DSS requirement assessments |
| `iso27001_assessments` | ISO 27001 | ISO 27001 control assessments |
| `soc2_assessments` | SOC 2 | SOC 2 trust criteria assessments |
| `nist_800_207_assessments` | NIST 800-207 | ZTA assessment records |
| `zta_maturity_scores` | NIST 800-207 | ZTA pillar maturity scores |
| `atlas_assessments` | ATLAS | MITRE ATLAS assessments |
| `atlas_red_team_results` | ATLAS | Red team test results |
| `omb_m25_21_assessments` | AI Transparency | OMB M-25-21 assessments |
| `omb_m26_04_assessments` | AI Transparency | OMB M-26-04 assessments |
| `nist_ai_600_1_assessments` | AI Transparency | NIST AI 600-1 assessments |
| `gao_ai_assessments` | AI Transparency | GAO AI assessments |
| `model_cards` | AI Transparency | AI model cards |
| `system_cards` | AI Transparency | AI system cards |
| `ai_use_case_inventory` | AI Transparency | AI use case registry |
| `sbom_records` | SBOM | Software bill of materials |
| `hitrust_assessments` | HITRUST | HITRUST CSF assessments |

### Lineage DAG — Tables Read

| Table | Source | Node Type |
|-------|--------|-----------|
| `digital_thread_links` | MBSE Digital Thread | Requirements, model elements, code modules |
| `prov_entities` | W3C Provenance | Provenance entities (artifacts, agents) |
| `prov_relations` | W3C Provenance | Provenance relationships |
| `audit_trail` | Audit Trail | Audit events (actions by actors) |
| `sbom_records` | SBOM | Software components with versions |

---

## 6. Configuration

Evidence freshness thresholds are configurable via CLI flags:

```bash
# Default: 168 hours (7 days)
python tools/compliance/evidence_collector.py --project-id "proj-123" --freshness --max-age-hours 168 --json

# Stricter: 72 hours (3 days) for cATO environments
python tools/compliance/evidence_collector.py --project-id "proj-123" --freshness --max-age-hours 72 --json
```

Heartbeat daemon configuration in `args/monitoring_config.yaml` includes the evidence freshness check interval and max-age threshold.

The framework registry (`FRAMEWORK_EVIDENCE_MAP`) is defined as a Python dict constant in `evidence_collector.py`. To add a new framework, add a new entry with `description`, `tables`, `file_patterns`, and `required` fields.

---

## 7. Dashboard

### `/evidence` — Compliance Evidence Inventory

- **Stat grid** (4 cards): total frameworks, required frameworks, frameworks with evidence, coverage percentage
- **Controls**: project ID input, "Collect Evidence" button (POST), "Check Freshness" button (GET)
- **Framework table**: framework ID, description, required flag, record count, status badge (green/yellow/red)
- **Freshness results**: per-framework age display with stale/fresh indicators

### `/lineage` — Artifact Lineage DAG

- **Stat grid** (3 cards): total nodes, total edges, data sources contributing
- **Controls**: project ID input, "Load Lineage" button
- **SVG DAG visualization**: client-side rendered DAG with color-coded nodes by source (digital thread, provenance, audit trail, SBOM); WCAG accessible (`role="img"`, `aria-label`)
- **Artifact inventory table**: all nodes listed with ID, type, label, source; auto-enhanced by `tables.js` (search, sort, filter, CSV export)

Both pages follow existing dashboard patterns: `base.html` extension, stat-grid layout, `table-container` wrapper, `charts.js` SVG rendering, CUI banner integration.

---

## 8. Security Gates

Phase 56 does not introduce a new named security gate. Evidence freshness is enforced through the existing gate infrastructure:

- **cATO Gate** — `0 expired evidence on critical controls, readiness >= 50%` already blocks on stale evidence. Phase 56's freshness checker provides the data that feeds this gate evaluation.
- **Multi-Regime Gate** — `All applicable frameworks must pass individual gates` depends on evidence being current across all detected frameworks.

The evidence collector's `--freshness` flag and heartbeat integration ensure that staleness is detected proactively before gate evaluation occurs, rather than discovering it at deployment time.

---

## 9. Verification

```bash
# CLI — Collect evidence for all frameworks
python tools/compliance/evidence_collector.py --project-id "proj-123" --json

# CLI — Collect evidence for a single framework
python tools/compliance/evidence_collector.py --project-id "proj-123" --framework fedramp --json

# CLI — Check evidence freshness (default 168-hour threshold)
python tools/compliance/evidence_collector.py --project-id "proj-123" --freshness --max-age-hours 168 --json

# CLI — List supported frameworks
python tools/compliance/evidence_collector.py --list-frameworks --json

# Dashboard — Evidence page
# Navigate to /evidence, enter project ID, click "Collect Evidence" or "Check Freshness"

# Dashboard — Lineage page
# Navigate to /lineage, enter project ID, click "Load Lineage" to render SVG DAG

# API — Evidence endpoints
curl http://localhost:5000/api/evidence/stats
curl -X POST http://localhost:5000/api/evidence/collect -d '{"project_id":"proj-123"}'
curl "http://localhost:5000/api/evidence/freshness?project_id=proj-123&max_age_hours=168"

# API — Lineage endpoints
curl "http://localhost:5000/api/lineage/graph?project_id=proj-123"
curl "http://localhost:5000/api/lineage/stats"
```

---

## 10. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D347 | Evidence collector extends `cssp_evidence_collector.py` pattern to all 14 frameworks | Proven pattern from Phase 14; declarative framework-to-table mapping enables adding new frameworks without code changes (D26 pattern). Uses crosswalk engine for multi-framework evidence mapping. |
| D348 | Lineage dashboard joins digital thread + provenance + audit trail + SBOM into unified DAG visualization | Read-only SVG rendering from existing DB tables. No new data storage, no data duplication. Four previously siloed data sources become a single navigable graph for compliance officers and assessors. |

### Related Decisions

| ID | Relevance |
|----|-----------|
| D6 | Audit trail is append-only/immutable — lineage reads but never modifies |
| D7 | stdlib `xml.etree.ElementTree` for file parsing — air-gap safe |
| D26 | Declarative JSON/dict registries — add frameworks without code changes |
| D94 | SVG chart library (zero dependencies) — lineage DAG uses same rendering approach |
| D287 | W3C PROV-AGENT provenance in 3 append-only SQLite tables — lineage reads prov_entities and prov_relations |
| D163 | Heartbeat notifications fan out to audit trail, SSE, gateway — evidence staleness alerts use same channels |

---

## 11. Files

### New Files (5)

| File | Purpose |
|------|---------|
| `tools/compliance/evidence_collector.py` | Universal compliance evidence auto-collector (14 frameworks) |
| `tools/dashboard/api/evidence.py` | Dashboard API Blueprint for evidence collection |
| `tools/dashboard/api/lineage.py` | Dashboard API Blueprint for artifact lineage DAG |
| `tools/dashboard/templates/evidence.html` | Evidence collection dashboard page |
| `tools/dashboard/templates/lineage.html` | Artifact lineage DAG dashboard page |

### Modified Files

| File | Change |
|------|--------|
| `tools/dashboard/app.py` | +/evidence and /lineage routes, +Blueprint registrations |
| `tools/monitor/heartbeat_daemon.py` | +evidence_freshness check integration |
| `tools/mcp/tool_registry.py` | +evidence and lineage tool entries in unified gateway |
| `CLAUDE.md` | +D347-D348, +CLI commands, +dashboard pages, +evidence collection section |
| `tools/manifest.md` | +Evidence Collection and Artifact Lineage entries |
| `goals/manifest.md` | +Evidence Collection entry |
