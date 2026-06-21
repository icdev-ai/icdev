# CUI // SP-CTI
# Feature: Diplomatic Activity Tracker (DAT) with Diplomatic Tension Index (DTI)

## Metadata
issue_number: `18`
run_id: `23a1687c`

## Feature Description
The MCIP (Multi-source Collection & Intelligence Processing) system requires a Diplomatic
Activity Tracker (DAT) capability. DAT ingests three signal streams — State Department cable
traffic, UNSC meeting schedules, and back-channel communication metadata — and synthesizes
them into a single, quantified **Diplomatic Tension Index (DTI)** score. The DTI is refreshed
every 6 hours via an autonomous background reflex.

## User Story
As an intelligence analyst on the MCIP platform  
I want a Diplomatic Tension Index that automatically aggregates diplomatic signal streams  
So that I can monitor geopolitical tension in near-real-time without manual data integration

## Solution Statement
Build a new `mcip` canvas with:
1. **Ingestion layer** — three typed event sources stored in `mcip_dat_events` table
2. **Scoring engine** — weighted DTI algorithm in `tools/mcip/analytics.py`, range 0–100
3. **6-hour reflex** — `tools/genesis/reflexes/mcip_dti_scorer.py` populates `mcip_dti_scores`
4. **Dashboard page** — `/dat` route with live DTI gauge, trend chart, event feed
5. **REST API** — `GET /api/dat/dti` (current + history), `POST /api/dat/events` (ingest)
6. **IQE integration** — natural-language queries against DAT collections

## COA Context
- **COA Name**: None
- **Architecture Pattern**: Canvas module (monolith, matches existing ICDEV™ pattern)
- **Scope**: Full DAT capability (ingestion + scoring + UI + API + reflex)
- **Timeline**: Single PI (1 sprint)
- **Boundary Tier**: GREEN
- **Key Risks**: Simulated ingestion only (no live State Dept API) — synthetic data used

## ATO Impact Assessment
- **Boundary Impact**: GREEN — no new external connections, no classification change
- **New NIST Controls**: SI-4 (system monitoring), AU-2 (auditable events), AC-3 (access enforcement)
- **SSP Impact**: Addendum note for new MCIP canvas data flows
- **Data Classification Change**: No — stays within CUI // SP-CTI boundary

## Relevant Files

### New Files
- `tools/mcip/__init__.py`
- `tools/mcip/blueprint.py` — Flask routes for `/dat` page + `/api/dat/*` endpoints
- `tools/mcip/analytics.py` — DTI scoring engine + ingestion + history queries
- `tools/mcip/constants.py` — source types, DTI thresholds, signal weights
- `tools/genesis/reflexes/mcip_dti_scorer.py` — 6-hour background reflex
- `tools/iqe/adapters/mcip.py` — IQE adapter for DAT collections
- `tools/dashboard/templates/mcip/page.html` — canvas page
- `icdev/tools/dashboard/templates/mcip/page.html` — icdev/ mirror
- `tests/test_mcip_dat.py` — unit tests
- `features/mcip_dat.feature` — BDD scenarios

### Modified Files
- `tools/dashboard/app.py` — register blueprint + IQE dispatch entry
- `tools/dashboard/templates/base.html` — nav link + PATH_CANVAS entry
- `tools/genesis/reflex_registry.py` — register mcip_dti_scorer reflex
- `tests/conftest.py` — add MCIP table schemas to MINIMAL_ICDEV_SCHEMA
- `.claude/hooks/pre_tool_use.py` — add `mcip_dti_scores` to APPEND_ONLY_TABLES
- `args/projects.yaml` — register MCIP project

## Implementation Plan

### Phase 1: Foundation (DB schema + constants)
Define the data model and constants that everything else depends on.

### Phase 2: Core Analytics
Implement the DTI scoring algorithm and CRUD helpers.

### Phase 3: Blueprint + Template + Reflex
Wire up the API, render the dashboard page, and register the 6-hour scoring reflex.

### Phase 4: IQE + Nav + Registrations
Complete the 8-component gate: IQE adapter, nav link, reflex registry, etc.

### Phase 5: Tests
Unit tests (pytest) + BDD scenarios (behave).

## Step by Step Tasks

### Step 1: constants.py
- Define `DAT_SOURCE_TYPES = ["cable_traffic", "unsc_schedule", "backchannel_metadata"]`
- Define signal weights: `DTI_WEIGHTS = {"cable_traffic": 0.45, "unsc_schedule": 0.30, "backchannel_metadata": 0.25}`
- Define tension bands: `DTI_BANDS = {"low": (0,33), "moderate": (34,66), "high": (67,100)}`
- Define `DTI_UPDATE_INTERVAL_HOURS = 6`

### Step 2: DB schema in tests/conftest.py
- Add `mcip_dat_events` table (id, source_type, content_hash, sender, recipient, classification, tension_signal REAL, ingested_at)
- Add `mcip_dti_scores` table (id, score REAL, cable_sub, unsc_sub, backchannel_sub, event_count, computed_at) — append-only

### Step 3: analytics.py
- `ingest_event(source_type, content_hash, sender, recipient, classification, tension_signal)` → INSERT into mcip_dat_events
- `compute_dti(window_hours=6)` → weighted average of tension_signals per source, returns 0–100 float
- `record_dti_score(score, sub_scores, event_count)` → INSERT into mcip_dti_scores
- `get_current_dti()` → most recent mcip_dti_scores row
- `get_dti_history(hours=48)` → list of score rows
- `get_recent_events(source_type=None, limit=50)` → recent dat_events

### Step 4: blueprint.py
- `@bp.route("/dat")` → render template with current DTI + stats
- `GET /api/dat/dti` → current DTI JSON
- `GET /api/dat/dti/history` → DTI trend (last 48 h)
- `GET /api/dat/events` → paginated event feed
- `POST /api/dat/events` → ingest a new event
- `POST /api/dat/compute` → trigger manual DTI recompute
- `POST /api/iqe-query` → IQE integration

### Step 5: Reflex (mcip_dti_scorer.py)
- On `run(ctx, session)`: call `compute_dti()`, call `record_dti_score()`, return JSON result
- Register in reflex_registry.py: DOMAIN tier, 6.0 h interval

### Step 6: Template (page.html)
- CUI banner, hero with DTI gauge (large number, color-coded by band)
- Stat cards: current DTI, event count, last updated, dominant source
- DTI trend chart (SVG line chart, 48-h window)
- Event feed table (source type, sender→recipient, tension signal, time)
- IQE query widget

### Step 7: IQE adapter
- Register collections: `dat.events`, `dat.dti_history`

### Step 8: app.py updates
- Register blueprint: `from tools.mcip.blueprint import bp as _dat_bp; app.register_blueprint(_dat_bp)`
- Add `"dat": ("tools.iqe.adapters.mcip", ["dat.events", "dat.dti_history"])` to `_CANVAS_MAP`

### Step 9: base.html updates
- Add `/dat` nav link under Strategos menu (or Intelligence section)
- Add `[/^\/dat/, 'dat']` to PATH_CANVAS

### Step 10: Register in APPEND_ONLY_TABLES
- Add `"mcip_dti_scores"` to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`

### Step 11: Unit tests
- Test `compute_dti()` with mock events
- Test `ingest_event()` roundtrip
- Test DTI band classification
- Test API endpoints via Flask test client

### Step 12: BDD scenarios
- Scenario: DAT ingests a cable traffic event and DTI updates
- Scenario: DTI score reflects weighted combination of all three sources
- Scenario: History endpoint returns last 48h of scores

## Testing Strategy
### Unit Tests
- Fixture-based: seed mcip_dat_events, assert compute_dti() range [0,100]
- Edge case: no events → DTI = 0; single source type → partial sub-score
- API test: POST /api/dat/events returns 200 + event_id

### BDD Tests
```gherkin
Feature: Diplomatic Activity Tracker
  Scenario: Ingest cable traffic and compute DTI
    Given the system is operational and the user is authenticated
    When I ingest a cable traffic event with tension_signal 0.8
    Then the DTI history contains at least one score above 0
```

### Edge Cases
- Empty window → DTI = 0.0 (not null)
- tension_signal clamped to [0.0, 1.0]
- compute_dti with only one source type

## Acceptance Criteria
1. `POST /api/dat/events` ingests events and returns `{"status": "ok", "event_id": "..."}`
2. `GET /api/dat/dti` returns `{"score": <float 0-100>, "band": "low|moderate|high", "computed_at": "..."}`
3. `GET /api/dat/dti/history` returns list of DTI records spanning up to 48 h
4. Dashboard page `/dat` renders without errors (HTTP 200)
5. Reflex registered at 6.0 h interval in REGISTRY
6. All pytest tests pass; BDD scenarios pass
7. SAST: 0 critical, 0 high

## Validation Commands
- `python -m py_compile tools/mcip/blueprint.py tools/mcip/analytics.py tools/mcip/constants.py` — Syntax
- `ruff check tools/mcip/ tests/test_mcip_dat.py --fix` — Lint
- `python -m pytest tests/test_mcip_dat.py -v --tb=short` — Unit tests
- `python -m behave features/mcip_dat.feature` — BDD
- `python tools/security/sast_runner.py --project-path . --json` — SAST
- `python tools/security/secret_detector.py --project-path . --json` — Secrets
- `python tools/security/dependency_auditor.py --project-path . --json` — Deps

## NIST 800-53 Controls
- **SI-4** — System monitoring (DTI continuous monitoring)
- **AU-2** — Auditable events (ingestion + scoring audit trail)
- **AC-3** — Access enforcement (authenticated endpoints)
- **SI-2** — Flaw remediation (dependency audit gate)
- **PM-16** — Threat awareness program (DTI feeds threat picture)

## Notes
- DAT uses synthetic/simulated signals — no live State Dept API integration in this sprint
- tension_signal is a normalized float [0.0, 1.0] provided by upstream ingestion agents
- DTI = sum(weight_i × avg_tension_i) × 100 per active source type, clamped to [0,100]
- mcip_dti_scores is append-only (immutable audit trail per NIST AU-9)

# CUI // SP-CTI
