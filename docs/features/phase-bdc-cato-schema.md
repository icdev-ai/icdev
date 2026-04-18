# BDC cATO Twin — Canonical Schema Design

**Phase:** BDC Phase 1 (MVP)
**Date:** 2026-04-18
**Author:** Sovanna Chuon
**Status:** Draft — no code yet

---

## Overview

The `compliance_snapshots` table is the foundational primitive of the BDC cATO Twin. It
captures the implementation status of a single compliance control, for a single project,
under a specific framework, at a point in time. Every downstream query, dashboard, POA&M
auto-generation, and continuous-monitoring reflex reads from this table.

---

## Table Definition

```sql
CREATE TABLE compliance_snapshots (
    snapshot_id   TEXT        NOT NULL,  -- UUID; PK; immutable once written
    project_id    TEXT        NOT NULL,  -- FK → projects.project_id
    framework_id  TEXT        NOT NULL,  -- e.g. 'fedramp-moderate', 'cmmc-l2', 'nist-800-53'
    control_id    TEXT        NOT NULL,  -- e.g. 'AC-2', 'IA-5(1)', 'CM-6'
    status        TEXT        NOT NULL   CHECK (status IN (
                                             'satisfied',
                                             'not_satisfied',
                                             'partially_satisfied',
                                             'not_applicable',
                                             'planned'
                                         )),
    evidence_ref  TEXT,                  -- URI or DB path to artifact; nullable if no evidence yet
    taken_at      TEXT        NOT NULL,  -- ISO-8601 UTC timestamp; set by snapshot writer

    PRIMARY KEY (snapshot_id)
);
```

### Indexes

| Index | Columns | Purpose |
|-------|---------|---------|
| `idx_cs_project_framework` | `(project_id, framework_id)` | Per-project framework rollup (most common query pattern) |
| `idx_cs_control_status` | `(control_id, status)` | Cross-project control-status lookups (IQE queries) |
| `idx_cs_taken_at` | `(taken_at)` | Time-range slicing for continuous-monitoring trend analysis |

### Constraints

- `snapshot_id` is a UUID generated at snapshot-write time; rows are **append-only** (cATO
  audit trail; never UPDATE or DELETE).
- `taken_at` stored as ISO-8601 string (`datetime.now(timezone.utc).isoformat()`) — consistent
  with ICDEV cross-platform policy (no DB-side `CURRENT_TIMESTAMP` that differs between
  SQLite and PostgreSQL).
- `evidence_ref` is nullable; the snapshot writer sets it when an evidence artifact is
  available. A `NULL` value with `status = 'planned'` is a valid and expected state.

---

## OSCAL Field Mapping

OSCAL defines four machine-readable authorization document types (SSP, SAP, SAR, POA&M).
The table maps directly onto all four:

| `compliance_snapshots` column | OSCAL document | OSCAL element | Notes |
|-------------------------------|---------------|---------------|-------|
| `snapshot_id` | SSP / SAR | `uuid` on `<assessment-result>` / `<finding>` | Becomes the finding or result UUID |
| `project_id` | SSP | `<system-characteristics> / system-id` | Each project = one OSCAL system |
| `framework_id` | SSP / Profile | `<import> / href` (OSCAL Profile URL) | e.g. `https://csrc.nist.gov/projects/oscal/fedramp-moderate` |
| `control_id` | SSP / SAR | `<implemented-requirement> / control-id` | NIST SP 800-53 / FedRAMP control identifiers |
| `status` | SAR / POA&M | `<finding> / target / status` (`satisfied` / `not-satisfied`) | `partially_satisfied` and `planned` are ICDEV extensions; export maps them to `not-satisfied` with a remark |
| `evidence_ref` | SAR | `<finding> / related-observations / observation-uuid` → links to `<observation> / relevant-evidence / href` | URI to artifact stored in ICDEV evidence store |
| `taken_at` | SAR | `<assessment-results> / metadata / last-modified` and `<result> / start` | UTC timestamp; formatted as `dateTime` in OSCAL XML/JSON |

### OSCAL shape summary

```
SSP  →  system-implementation / implemented-requirement
         └── control-id      ← control_id
         └── by-component    ← status (implementation-status/@state)

SAP  →  assessment-plan / reviewed-controls / control-selection
         └── control-id      ← control_id  (controls in scope)

SAR  →  assessment-results / result / finding
         └── target/control-id  ← control_id
         └── status             ← status
         └── related-observations / href  ← evidence_ref
         └── start              ← taken_at

POA&M →  poam-item (generated for every row where status != 'satisfied' and != 'not_applicable')
         └── related-findings / finding-uuid  ← snapshot_id
         └── subject / subject-id  ← project_id
```

---

## Why this schema, not an alternative

**Alternative 1 — store the full OSCAL XML blob per snapshot.**
Rejected: queryable only via XPath/XSLT; no SQL joins for IQE; OSCAL blobs are MBs per
assessment; export is a projection of this table, not the source.

**Alternative 2 — denormalize framework + control into one `control_urn` column.**
Rejected: cross-framework drift queries (Phase 3) require framework as a first-class
dimension. Splitting is cheap now; merging later is expensive.

**Alternative 3 — use the existing `compliance_assessments` table.**
Rejected (after review): that table records assessor runs, not individual control states. It
is the parent; `compliance_snapshots` is the immutable child grain.

---

## Phase sequencing note

This schema is the only output of Phase 1 design. Code (snapshot writer, IQE queries,
continuous-monitoring job) follows in Phase 1 implementation. OSCAL export (Phase 2) emits
SSP/SAR/POA&M XML/JSON as projections of this table — no schema change required at Phase 2.
