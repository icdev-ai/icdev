# Phase 1 — PDC Pipeline Twin: Pre-Merge What-If Simulation

**Date:** 2026-04-17
**Canvas:** PDC (Pipeline Design Canvas)
**Status:** Shipped — Phase 1
**Classification:** CUI // SP-CTI

---

## Summary

Implements the world's first pipeline digital twin for DevSecOps CI/CD pipelines. No commercial vendor offers twin semantics for pipeline graphs — this is whitespace product capability identified in the [digital twin market scan](../briefs/digital-twin-market-canvas-implementation-plan.md).

A developer snapshots their current pipeline DAG, then pastes a proposed delta graph. The twin runs the delta through PDC's existing antipattern detector, SLSA assessor, and compliance engine to produce a **PASS / WARN / FAIL verdict before any change is merged**.

---

## What Was Built

### New Files
| File | Purpose |
|------|---------|
| `tools/pipeline/twin.py` | Core twin engine: snapshot, simulate, diff |
| `tools/dashboard/templates/pipeline/twin.html` | Pipeline Twin simulation UI |

### Modified Files
| File | Change |
|------|--------|
| `tools/pipeline/db/init_db.py` | Added `pdc_snapshots` and `pdc_simulations` tables |
| `tools/pipeline/blueprint.py` | Extracted `assess_slsa`/`run_compliance_check` to module level; added 5 new routes |
| `tools/manifest/design-canvases.md` | Registered Pipeline Twin in manifest |

---

## API Surface

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/devops/twin/<pipe_id>` | Pipeline Twin UI |
| `POST` | `/devops/api/pipelines/<id>/twin/snapshot` | Freeze current DAG as a snapshot |
| `GET` | `/devops/api/pipelines/<id>/twin/snapshots` | List all snapshots |
| `POST` | `/devops/api/pipelines/<id>/twin/simulate` | Run pre-merge simulation on delta graph |
| `GET` | `/devops/api/twin/simulations/<sim_id>` | Retrieve stored simulation result |

### Simulation Request Body
```json
{
  "delta_graph": { "nodes": [...], "edges": [...] },
  "baseline_snap_id": "abc123"  // optional — auto-takes snapshot if omitted
}
```

### Simulation Response
```json
{
  "id": "sim-abc123",
  "verdict": "fail",
  "antipatterns": [...],
  "slsa": { "achieved_level": 1, "evidence": {...} },
  "compliance": { "passed": 18, "failed": 5, "findings": [...] },
  "diff": { "added_nodes": [...], "removed_nodes": [...], "node_delta": 2 },
  "critical_count": 1,
  "high_count": 2,
  "medium_count": 1
}
```

---

## Verdict Logic

| Verdict | Condition |
|---------|-----------|
| `fail` | Any critical antipattern OR ≥3 failed compliance rules |
| `warn` | Any high antipattern OR ≥1 failed compliance rule |
| `pass` | No antipatterns + all compliance rules pass |

---

## Database Tables

### `pdc_snapshots`
Stores frozen DAG snapshots with node/edge counts and authorship.

### `pdc_simulations`
Stores simulation results: verdict, antipattern JSON, SLSA JSON, compliance JSON, diff JSON, severity counts.

Both tables reference `pipelines(id)` and use append-only audit semantics (no UPDATE/DELETE).

---

## Phase Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| **1** | ✅ Done | DAG snapshot + delta simulate → antipattern + SLSA + compliance verdict |
| 2 | Backlog | Cost + duration prediction (train on `pipeline_runs` table) |
| 3 | Backlog | Blast-radius analysis — which downstream dependent pipelines are impacted |

---

## GovCon Differentiator

No CI/CD vendor (Harness, Spacelift, Argo, GitLab) offers pipeline twin semantics — they provide policy-as-code but not pre-merge simulation with SLSA + NIST SSDF + DoD DevSecOps verdicts. This is a credible **novel product** differentiator for GovCon pursuits requiring supply chain assurance (EO 14028, SLSA, SSDF).
