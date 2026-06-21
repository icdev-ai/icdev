# NSA ZIG Full Implementation — Zero Trust Implementation Guide

**Phase:** zig (core)  
**Classification:** CUI // SP-CTI  
**Shipped:** 2026-06-01  
**Tasks:** zig-db-01..07, zig-engine-01..05, zig-api-01..10, zig-ui-01..07, zig-iqe-01..03, zig-sc-01..04, zig-infra-01..06

---

## What Was Built

All 7 NSA ZIG pillars (published January 2026), 42 capabilities, and 91 activities incorporated into `/security/zig/`.

### Engine Modules (`icdev/tools/security_canvas/`)

| File | Purpose |
|------|---------|
| `zig_assessor.py` | Main entry point — scores all 7 pillars, identifies gaps, persists results; bridges to ZTA backend |
| `zig_pillar_scorer.py` | Per-pillar maturity score (0.0–1.0); 60% activity rate + 40% capability rate weighted formula |
| `zig_activity_tracker.py` | CRUD for `zig_activity_completions`; statuses: not_started / in_progress / complete |
| `zig_phase_tracker.py` | Discovery / Phase 1 / Phase 2 completion tracking per pillar |
| `zig_roadmap_generator.py` | FY2027 / FY2032 roadmap with NIST 800-207 crosswalk |
| `zig_artifact_generator.py` | Markdown gap assessment report download |

### DB Schema (`icdev/tools/security_canvas/db/init_db.py`)

| Table | Rows (seeded) |
|-------|--------------|
| `zig_pillars` | 7 (User, Device, Network, Application, Data, Visibility, Automation) |
| `zig_capabilities` | 42 |
| `zig_activities` | 91 |
| `zig_activity_completions` | 0 (runtime, per-target) |
| `zig_maturity_scores` | 0 (runtime, per-assessment-run) |
| `zig_targets` | 0 (runtime; see ZIG-EXT) |

### Dashboard Templates (`icdev/tools/dashboard/templates/security_canvas/zig/`)

| Template | Route | Description |
|----------|-------|-------------|
| `index.html` | `/security/zig/` | 7-pillar radar chart, aggregate score, FY2027 readiness |
| `pillar.html` | `/security/zig/pillar/<slug>` | Per-pillar capability checklist + activity completion |
| `phase.html` | `/security/zig/phase` | Discovery / Phase 1 / Phase 2 tracker |
| `assessment.html` | `/security/zig/assessment` | Run full assessment, gap table |
| `roadmap.html` | `/security/zig/roadmap` | FY2027/FY2032 roadmap with milestones |
| `portfolio.html` | `/security/zig/portfolio` | Multi-target portfolio view (ZIG-EXT) |
| `external_canvas.html` | `/security/zig/external` | External targets management (ZIG-EXT) |

### API Routes (17 core + 9 ext = 26 total in `blueprint.py`)

Core: pillars, capabilities (list + PATCH), activities (list + complete), maturity, assess, phases, roadmap, artifact download.  
Ext (added ZIG-EXT): target CRUD, per-target assess/activity/ingest, portfolio health/compare.

### IQE Integration

- 5 collections registered in `icdev/tools/iqe/adapters/security.py`: `zig.pillars`, `zig.capabilities`, `zig.activities`, `zig.maturity`, `zig.gaps`
- Seed queries: `context/iqe/queries/security/zig_queries.iqe` (8 queries)
- IQE widget in `index.html`, `portfolio.html`, `external_canvas.html` using `iqe_canvas = "security_zig"`
- `_CANVAS_MAP` entry: `"security_zig"` → `tools.iqe.adapters.security` (added 2026-06-14)

### Security Canvas Integration

- ZIG nav dropdown in `security_canvas/base.html`
- ZIG card in `security_canvas/index.html` (posture overview section)
- Gap badges wired to assessment results

---

## ZIG-EXT Extension (2026-06-14)

See [`phase-zig-ext-data-foundation.md`](phase-zig-ext-data-foundation.md) for the external targets and ingest adapters.
