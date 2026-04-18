# Phase BDC — cATO Twin Phase 1 (MVP)

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | BDC Phase 1 |
| Title | Continuous ATO Twin (cATO) — MVP |
| Status | In Development |
| Priority | P0 |
| Dependencies | Phase 16 (ATO Acceleration), Compliance Engine (crosswalk_engine, cato_monitor, cato_scheduler) |
| Author | ICDEV™ Architect Agent |
| Date | 2026-04-18 |

---

## 1. Problem Statement

Obtaining and sustaining an Authorization to Operate is the single largest operational risk for government and DoD programs. Static, point-in-time ATOs expire or lose relevance the moment a control implementation changes, yet most programs lack a machine-readable record of control state at each assessment boundary. Evidence goes stale undetected, POA&M items slip past review cycles, and compliance drift across multiple frameworks (NIST 800-53, FedRAMP, CMMC, IL4/IL5) is invisible until an assessor surfaces it months later.

The BDC cATO Twin addresses this gap with a single canonical data primitive — a compliance snapshot — that freezes the full cross-framework control state at every assessment run. From that append-only ledger, a continuous reflex monitor re-queries the same rule set every six hours, a readiness scorer surfaces ATO posture in real time, and a drift detector catches cross-framework inconsistencies before they become findings. Phased OSCAL export and a cATO dashboard complete the twin loop from raw evidence through machine-readable artifact delivery.

---

## 2. Goals

1. Establish a canonical, append-only `compliance_snapshots` table as the atomic unit of cATO state: `control × project × framework × timestamp → implementation_status + evidence_ref`
2. Ship a snapshot writer that freezes full cross-framework state at assessor-run end, integrated with the existing `boundary_engine.py` assessment flow
3. Register 20 seed IQE rule queries (FedRAMP Moderate + CMMC Level 2) against the `boundary.snapshots` and `boundary.violations` collections
4. Implement a Genesis reflex job (6h cadence) that re-runs the seed rule set, diffs violations vs the previous snapshot, and auto-creates POA&M entries for net-new findings
5. Compute a real-time cATO readiness score (0–100) from snapshot + evidence freshness dimensions for use in the security gate
6. Detect and surface cross-framework drift (e.g., AC-2 satisfied in NIST but not_satisfied in CMMC) via the crosswalk drift detector
7. Generate machine-readable OSCAL SSP/POA&M/AR artifacts from snapshot state (Phase 2 readiness)
8. Render a single-pane cATO dashboard showing live ATO posture per project with drill-through to control-level remediation (Phase 4 readiness)

---

## 3. Architecture

```
+-------------------------------+
| boundary_engine.py            |
| (assessment run end hook)     |
+---------------+---------------+
                |
                v snapshot_compliance_state()
+-------------------------------+     +----------------------------+
| compliance_snapshots          |     | cato_iqe_rules             |
| (append-only, Phase 1 grain)  |<----| (20 seed rules, FedRAMP +  |
|                               |     |  CMMC)                     |
| snapshot_id | project_id      |     +----------------------------+
| framework_id | control_id     |
| status | evidence_ref         |
| taken_at                      |
+---------------+---------------+
                |
        +-------+-------+
        |               |
        v               v
+---------------+  +-------------------+
| cato_          |  | cato_readiness.py |
| continuous_    |  | (score 0-100:     |
| monitor.py    |  |  ctrl% + evidence |
| (6h reflex)   |  |  % + POA&M age)   |
+-------+-------+  +--------+----------+
        |                   |
        v                   v
+---------------+  +-------------------+
| cato_          |  | dashboard/api/    |
| violations     |  | cato.py           |
| (POA&M refs)  |  | GET /boundary/    |
+---------------+  | cato/projects     |
        |           +-------------------+
        v
+-------------------------------+
| oscal_cato_exporter.py        |
| (Phase 2: SSP/POAM/AR/CD)     |
+-------------------------------+
        |
        v
+-------------------------------+
| crosswalk_drift.py            |
| (Phase 3: AC-2 NIST ≠ CMMC   |
|  drift detection)             |
+-------------------------------+
```

The cATO twin is built in four phases. Phase 1 (this doc) delivers the canonical snapshot table, snapshot writer, seed IQE queries, and continuous reflex monitor. Phase 2 adds OSCAL export from snapshot state. Phase 3 adds cross-framework drift detection. Phase 4 adds the single-pane dashboard and compliance velocity tracking.

---

## 4. Requirements

### 4.1 Canonical Snapshot Schema

#### REQ-BDC-001: Append-Only Snapshot Table
The system SHALL create a `compliance_snapshots` table with grain `(project_id, framework_id, control_id, taken_at)` and implementation status enum `satisfied | not_satisfied | partially_satisfied | not_applicable | planned`. The table is registered in `APPEND_ONLY_TABLES`; no UPDATE or DELETE operations are permitted.

#### REQ-BDC-002: Snapshot Writer Integration
The `cato_snapshot.py` module SHALL expose `snapshot_compliance_state(project_id, framework_id, timestamp=None)` callable from `boundary_engine.py` assessment run end hooks. It SHALL write one row per control per framework per assessment run and return a summary dict with `{snapshot_id, control_count, evidence_count}`.

#### REQ-BDC-003: Evidence Reference Linkage
Each snapshot row SHALL include an `evidence_ref` field linking to the corresponding `cato_evidence` row. If no evidence exists for a control, `evidence_ref` SHALL be null and the control readiness contribution SHALL be zero.

### 4.2 IQE Query Surface

#### REQ-BDC-004: IQE Collection Registration
The `tools/iqe/adapters/boundary.py` module SHALL register three collections on import: `boundary.snapshots` (SELECT from `compliance_snapshots`), `boundary.controls` (JOIN of `project_controls` and `compliance_controls`), and `boundary.violations` (SELECT from `poam_items` where source = 'cato').

#### REQ-BDC-005: Seed Query Set
The system SHALL seed 20 IQE rules into `cato_iqe_rules`: minimum 10 FedRAMP Moderate rules (critical and high severity controls not satisfied) and 10 CMMC Level 2 practice rules. Each rule SHALL include `rule_id`, `framework_id`, `dsl_text`, `severity`, and `enabled` flag.

### 4.3 Continuous Reflex Monitoring

#### REQ-BDC-006: Genesis Reflex Job
The `cato_continuous_monitor.py` module SHALL register as a Genesis reflex with 6-hour cadence. Each run SHALL: (a) identify the latest snapshot per project/framework pair, (b) execute all enabled IQE rules against that snapshot, (c) diff violations vs the previous reflex run, (d) create POA&M entries for net-new violations, (e) mark resolved entries with `resolved_at` timestamp.

#### REQ-BDC-007: Readiness Scoring
The `cato_readiness.py` module SHALL compute a 0–100 readiness score from four weighted dimensions: control satisfaction rate (40%), evidence freshness rate (30%), POA&M age distribution (20%), and critical-control gap count (10%). Scores SHALL map to rating: `green` (≥80), `amber` (50–79), `red` (<50).

### 4.4 Security Gate Integration

#### REQ-BDC-008: Security Gate Block Condition
The cATO security gate SHALL block deployment when: (a) readiness score < 50% (red), (b) any critical-severity control has `not_satisfied` status with evidence older than 90 days, or (c) more than 5 open critical POA&M items exist without a scheduled remediation date.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `compliance_snapshots` | Core cATO twin primitive. Append-only. One row per control assessment event. |
| `cato_iqe_rules` | Seed IQE rule set. 20 rules across FedRAMP Moderate and CMMC Level 2. |
| `cato_violations` | Violation ledger from continuous monitor reflex. Linked to POA&M entries. |

### compliance_snapshots DDL

```sql
CREATE TABLE IF NOT EXISTS compliance_snapshots (
    snapshot_id     TEXT PRIMARY KEY,          -- UUID v4
    project_id      TEXT NOT NULL,
    framework_id    TEXT NOT NULL,             -- e.g. 'fedramp-moderate'
    control_id      TEXT NOT NULL,             -- e.g. 'AC-2'
    implementation_status TEXT NOT NULL
        CHECK (implementation_status IN (
            'satisfied', 'not_satisfied',
            'partially_satisfied', 'not_applicable', 'planned'
        )),
    evidence_ref    TEXT,                      -- FK to cato_evidence.evidence_id (nullable)
    notes           TEXT,
    taken_at        TEXT NOT NULL,             -- ISO 8601 UTC
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);
CREATE INDEX IF NOT EXISTS idx_cs_proj_fw
    ON compliance_snapshots (project_id, framework_id);
CREATE INDEX IF NOT EXISTS idx_cs_ctrl_status
    ON compliance_snapshots (control_id, implementation_status);
CREATE INDEX IF NOT EXISTS idx_cs_taken_at
    ON compliance_snapshots (taken_at);
```

### cato_iqe_rules DDL

```sql
CREATE TABLE IF NOT EXISTS cato_iqe_rules (
    rule_id         TEXT PRIMARY KEY,
    framework_id    TEXT NOT NULL,
    dsl_text        TEXT NOT NULL,
    severity        TEXT NOT NULL
        CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);
```

### cato_violations DDL

```sql
CREATE TABLE IF NOT EXISTS cato_violations (
    violation_id    TEXT PRIMARY KEY,          -- UUID v4
    snapshot_id     TEXT NOT NULL,
    rule_id         TEXT NOT NULL,
    control_id      TEXT NOT NULL,
    project_id      TEXT NOT NULL,
    detected_at     TEXT NOT NULL,
    resolved_at     TEXT,                      -- NULL = open
    poam_ref        TEXT,                      -- FK to poam_items
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);
CREATE INDEX IF NOT EXISTS idx_cv_project
    ON cato_violations (project_id, detected_at);
CREATE INDEX IF NOT EXISTS idx_cv_open
    ON cato_violations (project_id) WHERE resolved_at IS NULL;
```

---

## 6. Tools

| Tool | File | Purpose |
|------|---------|---------|
| Snapshot Writer | `tools/boundary_canvas/cato_snapshot.py` | Freeze cross-framework control state at assessment run end |
| IQE Adapter | `tools/iqe/adapters/boundary.py` | Register boundary.snapshots / boundary.controls / boundary.violations collections |
| Continuous Monitor | `tools/boundary_canvas/cato_continuous_monitor.py` | Genesis reflex: re-query rules, diff violations, create POA&M entries |
| Readiness Scorer | `tools/boundary_canvas/cato_readiness.py` | Real-time cATO readiness score (0–100) with green/amber/red rating |
| OSCAL Exporter | `tools/boundary_canvas/oscal_cato_exporter.py` | Generate OSCAL SSP/POA&M/AR/CD from snapshot state (Phase 2) |
| Drift Detector | `tools/boundary_canvas/crosswalk_drift.py` | Cross-framework control drift detection (Phase 3) |
| Dashboard API | `tools/dashboard/api/cato.py` | Flask Blueprint: ATO posture per project, drill-down, what-if simulate (Phase 4) |
| cATO Monitor (existing) | `tools/compliance/cato_monitor.py` | Evidence freshness monitoring — integrated by `cato_continuous_monitor.py` |
| cATO Scheduler (existing) | `tools/compliance/cato_scheduler.py` | Evidence collection scheduling — feeds `evidence_ref` into snapshots |
| Boundary Engine (existing) | `tools/boundary_canvas/boundary_engine.py` | Assessment run end hook calls `snapshot_compliance_state()` |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D-BDC-1 | Canonical grain: control × project × framework × timestamp | Finest grain needed to answer "when did AC-2 change in FedRAMP for project X?" without re-running an assessment |
| D-BDC-2 | Append-only `compliance_snapshots`; no UPDATE/DELETE | NIST AU audit trail requirement; snapshot history is the twin's memory — mutable history breaks drift detection |
| D-BDC-3 | IQE DSL over raw SQL for violation queries | IQE queries survive schema migration; raw SQL hardcodes table structure and breaks across DB backends (SQLite ↔ PostgreSQL via `get_connection()`) |
| D-BDC-4 | 6-hour reflex cadence (not real-time CDC) | Assessment evidence is collection-event-driven, not row-change-driven; 6h matches cATO frequency guidance from NIST SP 800-137 and keeps reflex quota within Genesis limits |
| D-BDC-5 | OSCAL export deferred to Phase 2 | Phase 1 value is the snapshot primitive and continuous monitoring loop; OSCAL schema alignment with FedRAMP 20x requires separate validation gate — don't block Phase 1 on Phase 2 dependency |

---

## 8. Security Gate

**cATO Gate (Phase 1):**
- 0 critical-severity controls with `not_satisfied` status and evidence older than 90 days
- Readiness score ≥ 50% (amber or green)
- No more than 5 open critical POA&M items without scheduled remediation date

**FedRAMP 20x Readiness (Phase 2 gate):**
- All OSCAL artifacts validate against NIST OSCAL 1.1.2 schema
- SSP `implemented-requirement` entries cover 100% of FedRAMP Moderate baseline controls

**Continuous Monitoring Gate:**
- Genesis reflex last run ≤ 8 hours ago (evidence of live monitoring)
- `compliance_snapshots` has at least one row per project per target framework within last 30 days

---

## 9. Commands

```bash
# Take a compliance snapshot (Phase 1)
python -c "
from tools.boundary_canvas.cato_snapshot import snapshot_compliance_state
result = snapshot_compliance_state('proj-123', 'fedramp-moderate')
print(result)
"

# Check cATO readiness score
python tools/boundary_canvas/cato_readiness.py --project-id proj-123 --framework fedramp-moderate --json

# Run continuous monitor reflex manually
python tools/boundary_canvas/cato_continuous_monitor.py --project-id proj-123 --json

# List open violations
python -c "
from tools.db.storage import get_connection
with get_connection() as conn:
    rows = conn.execute(
        'SELECT violation_id, control_id, detected_at FROM cato_violations'
        ' WHERE project_id=? AND resolved_at IS NULL ORDER BY detected_at DESC',
        ('proj-123',)
    ).fetchall()
    for r in rows: print(dict(r))
"

# Generate OSCAL SSP from latest snapshot (Phase 2)
python tools/boundary_canvas/oscal_cato_exporter.py \
  --snapshot-id <snapshot-id> --artifact-type ssp --format json --output-dir /tmp/oscal

# Detect cross-framework drift (Phase 3)
python tools/boundary_canvas/crosswalk_drift.py \
  --snapshot-id <snapshot-id> --framework-pairs nist-fedramp nist-cmmc --json

# Existing cATO monitoring (integrated)
python tools/compliance/cato_monitor.py --project-id proj-123 --readiness
python tools/compliance/cato_monitor.py --project-id proj-123 --check-freshness
python tools/compliance/cato_scheduler.py --project-id proj-123 --run-due
```

---

**CUI // SP-CTI**
