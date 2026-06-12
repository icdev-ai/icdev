# Phase ODC — MITRE ATT&CK Coverage Dashboard (Observability Design Canvas)

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | ODC-MITRE |
| Title | MITRE ATT&CK Enterprise Matrix Coverage Dashboard |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 39 (Observability Hooks), ODC Digital Twin Canvas |
| Author | ICDEV™ Architect Agent |
| Date | 2026-04-18 |

---

## 1. Problem Statement

ICDEV™ operates in IL4/IL5 environments where Cyber Threat Intelligence (CTI) coverage gaps
directly affect ATO continuity. Security engineering teams needed a structured way to visualize
which MITRE ATT&CK Enterprise techniques were covered by existing detection rules, which were
cataloged but undeployed, and which remained entirely uncovered. Without a purpose-built matrix
view, this assessment required manual cross-referencing across Sigma rule libraries and
spreadsheets — a process that did not scale to 500+ techniques.

---

## 2. Solution Overview

The ODC MITRE ATT&CK Coverage Dashboard is a fully integrated page within the Observability
Design Canvas (ODC) that renders the MITRE Enterprise ATT&CK matrix color-coded by detection
coverage state. It supports drill-through to individual technique pages and includes a
deterministic Sigma rule generator that produces detection stubs without any LLM dependency.

Key capabilities:

- **Matrix view** — 16 tactic columns, technique cards colored by coverage: covered (green),
  cataloged (yellow/amber), gap (red/gray). Coverage state derives from the ODC's existing
  detection rule inventory in `observability_canvas.db`.
- **Drill-through** — Each technique card links to a detail page with the official ATT&CK
  description, sub-technique list, a direct link to attack.mitre.org, and the Sigma generator.
- **Sigma generator** — POST endpoint at `/observability/mitre/sigma` accepts a technique ID
  and name and returns a YAML Sigma rule stub. Deterministic: no LLM call, no external service.
  Safe for air-gap and IL6 environments.
- **Tactic filter** — The matrix URL accepts `?tactic=<short_name>` to scope the view to a
  single ATT&CK tactic (e.g., `?tactic=initial-access`).

---

## 3. Architecture

### 3.1 Data Flow

```
context/mitre/enterprise.json
        │
        ▼
tools/observability_canvas/mitre_loader.py
  load_techniques(catalog_path, tactic_filter)
        │
        ▼
tools/observability_canvas/blueprint.py
  GET /observability/mitre
  GET /observability/mitre/<tid>
  POST /observability/mitre/sigma
        │
        ▼
tools/dashboard/templates/observability_canvas/
  mitre.html          ← matrix page
  mitre_detail.html   ← technique detail page
```

### 3.2 Catalog

The MITRE Enterprise ATT&CK v5.4.0 catalog is stored at `context/mitre/enterprise.json`.
It contains 16 tactics, 84+ top-level techniques, and a further ~200 sub-techniques. The
loader parses this file once per request (no persistent cache required at this scale) and
returns a sorted `list[MitreTechnique]` ordered by technique ID.

### 3.3 Coverage State Logic

Coverage state per technique is resolved at render time by querying the ODC database:

| State | Color | Meaning |
|-------|-------|---------|
| `covered` | Green | At least one active deployed detection rule exists |
| `catalog` | Amber | Technique is in the detection backlog / Sigma draft exists |
| `gap` | Gray/Red | No detection coverage — ATT&CK technique is blind spot |

### 3.4 Sigma Generator

The POST handler at `/observability/mitre/sigma` accepts `technique_id` and `technique_name`
from a form submission and returns a YAML Sigma rule stub. The rule template includes:

- Rule metadata: title, id (UUID), status, description, author, date, tags (ATT&CK references)
- Detection block: placeholder `selection` with `EventID` field
- Condition: `selection`
- Falsepositives: `Unknown`
- Level: `medium`

No external API call is made. The rule is deterministic given the same inputs.

---

## 4. File Inventory

| File | Role |
|------|------|
| `tools/observability_canvas/mitre_loader.py` | Catalog parser — `MitreTechnique` dataclass + `load_techniques()` |
| `tools/observability_canvas/blueprint.py` | Flask Blueprint — matrix, detail, Sigma routes |
| `tools/dashboard/templates/observability_canvas/mitre.html` | Matrix Jinja2 template |
| `tools/dashboard/templates/observability_canvas/mitre_detail.html` | Detail Jinja2 template |
| `context/mitre/enterprise.json` | MITRE ATT&CK v5.4.0 Enterprise catalog (static data) |
| `tests/test_observability_mitre_route.py` | 4 unit tests for routes and loader |
| `tests/e2e_observability_mitre.py` | E2E test suite for matrix + detail pages |

---

## 5. Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/observability/mitre` | Matrix view (all tactics); accepts `?tactic=<short_name>` |
| GET | `/observability/mitre/<tid>` | Technique detail; `<tid>` = ATT&CK ID (e.g., T1059) |
| POST | `/observability/mitre/sigma` | Generate Sigma rule stub; form params: `technique_id`, `technique_name` |

---

## 6. Tests

| Test | File | Coverage |
|------|------|----------|
| Matrix page loads (200) | `tests/test_observability_mitre_route.py` | GET /observability/mitre |
| Technique detail loads (200) | `tests/test_observability_mitre_route.py` | GET /observability/mitre/<tid> |
| Sigma generation returns YAML | `tests/test_observability_mitre_route.py` | POST /observability/mitre/sigma |
| Loader returns sorted list | `tests/test_observability_mitre_route.py` | `load_techniques()` |
| E2E matrix render | `tests/e2e_observability_mitre.py` | Full browser lifecycle |

All 4 unit tests pass. E2E suite covers page load, tactic filter, and drill-through navigation.

---

## 7. Configuration

No new YAML config file is required. The MITRE feature is gated by the existing
`ICDEV_OBSERVABILITY_ENABLED` environment variable (default: `true`). The catalog path is
resolved relative to the repo root; it can be overridden via the `catalog_path` argument to
`load_techniques()` for testing.

---

## 8. Security & Compliance Considerations

- **Air-gap safe** — catalog is a static JSON file bundled in the repo; no external HTTP calls.
- **No LLM dependency** — Sigma generator uses a string template. Safe at IL6/SIPR.
- **No PII** — Technique IDs and names are all public MITRE data.
- **CUI marking** — All source files include `# CUI // SP-CTI` header per classification policy.
- **Read-only data** — `load_techniques()` opens `enterprise.json` read-only; no DB writes on
  the matrix or detail pages.
- **Sigma output** — Returned as plain text in a `<pre>` block; no eval or shell execution.

---

## 9. Acceptance Criteria (Verified)

- [x] Matrix page renders all 16 ATT&CK tactic columns
- [x] Technique cards colored by coverage state (covered / catalog / gap)
- [x] Drill-through to technique detail page works for any valid `tid`
- [x] Sub-techniques listed on detail page
- [x] Sigma generator returns valid YAML stub on POST
- [x] Tactic filter (`?tactic=`) scopes the matrix correctly
- [x] All 4 unit tests pass (`pytest tests/test_observability_mitre_route.py`)
- [x] Manifest entry added to `tools/manifest/observability-traceability-explainable-ai.md`
- [x] Companion sync written to all AI platforms
- [x] Feature doc present at `docs/features/phase-odc-mitre-coverage.md`

---

## 10. Related Phases

| Phase | Title | Relationship |
|-------|-------|--------------|
| Phase 39 | Observability Hooks | Parent ODC system; MITRE page mounts within the ODC Blueprint |
| Phase 37 | MITRE ATLAS Integration | ATLAS (AI security) — parallel coverage effort for AI-specific threats |
| ODC Digital Twin | Observability Design Canvas | Host canvas; Sigma rules feed back into ODC detection inventory |
