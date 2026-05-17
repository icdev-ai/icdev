# CUI // SP-CTI
# Feature: Federated Data Mesh with ETL and ML Conflict Escalation Prediction

## Metadata
issue_number: `19`
run_id: `7640d9b5`

## Feature Description
Build a federated data mesh that orchestrates multiple conflict intelligence
providers (ACLED, GDELT, ReliefWeb), runs an ETL pipeline to normalize
unstructured data into the canonical `sg_conflict_events` schema, and applies
machine learning via LLMRouter to extract escalation signals and produce risk
scores stored in a new `conflict_predictions` table.

## User Story
As an intelligence analyst,
I want the system to continuously pull conflict event data from multiple providers,
normalize it into a standard format, and produce ML-derived escalation risk scores,
So that I can identify emerging conflict escalation patterns before they become crises.

## Solution Statement
Introduce `tools/conflict_mesh/` — a new module with four components:
1. **Provider adapters** — thin wrappers around existing DataBridge connectors (ACLED, GDELT)
   plus a new ReliefWeb adapter for humanitarian data.
2. **Mesh coordinator** — federated orchestrator that pulls from all providers concurrently
   and deduplicates by `(source, source_id)`.
3. **ETL pipeline** — normalizes raw provider output into `sg_conflict_events` canonical rows,
   applying data-quality checks from `data_canvas/quality_engine.py`.
4. **ML pattern engine + escalation predictor** — uses LLMRouter for NLP on narrative/unstructured
   text fields, extracts escalation signals (actor intensity, geography, violence type, tone),
   and computes a 0–1 risk score stored in `conflict_predictions`.

## COA Context
- **COA Name**: None
- **Architecture Pattern**: Modular tools — one job per file, deterministic execution
- **Scope**: Full — all four components required
- **Timeline**: N/A
- **Boundary Tier**: YELLOW — new data flows from external providers; SSP addendum needed
- **Key Risks**: LLM unavailability → degrade gracefully to rule-based scoring

## ATO Impact Assessment
- **Boundary Impact**: YELLOW — three new external data flows (ACLED, GDELT, ReliefWeb)
- **New NIST Controls**: AC-4 (information flow), AU-2 (event logging), SI-3 (malicious code — ML input validation), SC-28 (protection at rest for predictions)
- **SSP Impact**: Addendum required — document new external data sources and ML pipeline
- **Data Classification Change**: No — remains CUI // SP-CTI

## Relevant Files

### Existing (read/reuse)
- `tools/databridge/connectors/acled_connector.py` — ACLED REST connector
- `tools/databridge/connectors/gdelt_connector.py` — GDELT REST connector
- `tools/databridge/connector.py` — `ConnectorRequest`, `ConnectorResponse`, base ABCs
- `tools/databridge/registry.py` — `get_connector_instance()`
- `tools/databridge/connectors/saas_base.py` — `SaaSBaseConnector` base class
- `tools/db/storage.py` — `get_connection()`, `is_pg()`
- `tools/db/migrations/052_sg_conflict_events/up.py` — existing conflict event schema
- `tools/llm/router.py` — `LLMRouter` for NLP tasks
- `tools/data_canvas/quality_engine.py` — data quality checks
- `tests/conftest.py` — MINIMAL_ICDEV_SCHEMA, `db_path` fixture

### New Files
- `tools/conflict_mesh/__init__.py`
- `tools/conflict_mesh/providers/__init__.py`
- `tools/conflict_mesh/providers/base.py` — `MeshProvider` ABC
- `tools/conflict_mesh/providers/acled_provider.py` — ACLED wrapper
- `tools/conflict_mesh/providers/gdelt_provider.py` — GDELT wrapper
- `tools/conflict_mesh/providers/reliefweb_provider.py` — ReliefWeb REST adapter
- `tools/conflict_mesh/mesh_coordinator.py` — federated orchestrator
- `tools/conflict_mesh/etl_pipeline.py` — ETL normalization pipeline
- `tools/conflict_mesh/ml_pattern_engine.py` — NLP signal extraction
- `tools/conflict_mesh/escalation_predictor.py` — risk scoring + DB persistence
- `tools/db/migrations/070_conflict_predictions/up.py` — new predictions table
- `tools/db/migrations/070_conflict_predictions/down.py`
- `tools/db/migrations/070_conflict_predictions/meta.json`
- `tests/test_conflict_mesh.py` — full unit test suite (TDD first)
- `tools/manifest/conflict-mesh.md` — manifest shard

## Implementation Plan

### Phase 1: Tests (TDD — RED)
Write failing tests for all four components.

### Phase 2: Provider Layer
`MeshProvider` ABC → ACLED/GDELT wrappers → ReliefWeb adapter.

### Phase 3: Mesh Coordinator
Federated pull, deduplication, unified event stream.

### Phase 4: ETL Pipeline
Normalize to `sg_conflict_events` schema with quality checks.

### Phase 5: ML Pattern Engine + Escalation Predictor
NLP signal extraction → rule-based + LLM scoring → `conflict_predictions` table.

### Phase 6: DB Migration
Create `conflict_predictions` table.

### Phase 7: Registration
Manifest shard, conftest schema.

## Step by Step Tasks

### Step 1: Write Tests (TDD — RED)
- Create `tests/test_conflict_mesh.py`
- Tests for `MeshProvider` ABC contract
- Tests for ACLED/GDELT provider adapters (mock connector)
- Tests for ReliefWeb provider (mock HTTP)
- Tests for `MeshCoordinator` — deduplication logic
- Tests for `ETLPipeline` — normalization and field mapping
- Tests for `MLPatternEngine` — signal extraction (mock LLM)
- Tests for `EscalationPredictor` — scoring math, DB persistence

### Step 2: DB Migration 070 — conflict_predictions table
- Create `tools/db/migrations/070_conflict_predictions/`
- Columns: `id`, `event_id` (FK→sg_conflict_events), `source`, `prediction_date`,
  `escalation_risk` (REAL 0–1), `signals` (JSON), `model_version`, `confidence`,
  `created_at`
- Add table to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`
- Add schema to `tests/conftest.py` MINIMAL_ICDEV_SCHEMA

### Step 3: MeshProvider ABC (providers/base.py)
- Abstract methods: `provider_name`, `fetch(since_date, limit)` → `List[Dict]`
- `normalize(raw_record)` → canonical dict with: `id`, `event_type`, `event_date`,
  `geometry`, `source`, `headline`, `metadata`

### Step 4: ACLED Provider Adapter (providers/acled_provider.py)
- Wrap `get_connector_instance("acled")` → `read(ConnectorRequest(...))`
- Map ACLED fields: `notes` → `headline`, `country+location` → `geo_hint`, `event_date`
- event_type mapping: ACLED `event_type` → canonical enum values

### Step 5: GDELT Provider Adapter (providers/gdelt_provider.py)
- Wrap `get_connector_instance("gdelt")` → `read(ConnectorRequest(...))`
- Map GDELT fields: `MentionSourceName + Mentions` → `headline`, `ActionGeo_FullName` → `geo_hint`
- Filter to conflict-relevant CAMEO codes (>= 14: assault, >17: coerce)

### Step 6: ReliefWeb Provider (providers/reliefweb_provider.py)
- New `SaaSBaseConnector` subclass with `@register_connector`
- Base URL: `https://api.reliefweb.int/v1`
- Endpoint `/reports` with `filter[field]=type&filter[value]=Situation Report`
- Map: `title` → `headline`, `country` → `geo_hint`, `date.created` → `event_date`
- event_type: always `humanitarian`
- `_build_auth_headers` returns `{"User-Agent": "ICDEV-DataBridge/1.0"}`

### Step 7: Mesh Coordinator (mesh_coordinator.py)
- `MeshCoordinator(providers: List[MeshProvider])` class
- `fetch_all(since_date, limit_per_provider)` → `List[Dict]` deduplicated
- Dedup key: `(source, id)` — track seen set
- Returns unified stream sorted by `event_date` desc

### Step 8: ETL Pipeline (etl_pipeline.py)
- `ETLPipeline(coordinator: MeshCoordinator, db_path=None)`
- `run(since_date, limit_per_provider, dry_run=False)` → `ETLResult` dataclass
  - Pulls from coordinator
  - Validates required fields: `id`, `event_type`, `source`, `created_at`
  - Upserts into `sg_conflict_events` via `get_connection()`
  - Returns counts: `fetched`, `inserted`, `updated`, `skipped`, `errors`
- Quality check: skip records missing both `headline` and `event_date`
- CLI: `--since YYYY-MM-DD`, `--limit N`, `--dry-run`, `--json`

### Step 9: ML Pattern Engine (ml_pattern_engine.py)
- `MLPatternEngine(llm_router=None)` — LLMRouter is optional, falls back to rules
- `extract_signals(text: str, metadata: dict)` → `EscalationSignals` dataclass:
  - `violence_score` (0–1): keyword density (kills/attack/offensive/siege/etc.)
  - `actor_count` (int): distinct actor mentions
  - `geographic_spread` (0–1): number of distinct locations / 10.0 (capped)
  - `tone` (float): negative tone from GDELT if available, else 0.0
  - `llm_assessment` (Optional[str]): LLMRouter summary if available
- Rule-based scoring is primary; LLM is an optional enhancement
- VIOLENCE_KEYWORDS constant: `{"kills", "attack", "offensive", "siege", "assault",
  "bombardment", "airstrike", "artillery", "missile", "invasion", "occupation"}`

### Step 10: Escalation Predictor (escalation_predictor.py)
- `EscalationPredictor(pattern_engine: MLPatternEngine, db_path=None)`
- `predict(event: Dict)` → `float` — escalation risk score 0–1
  - Score = 0.4 * violence_score + 0.2 * min(actor_count/5, 1.0)
            + 0.2 * geographic_spread + 0.2 * abs(tone)/10 (GDELT tone range ±10)
- `predict_and_store(event, model_version="v1.0")` — persist to `conflict_predictions`
- `predict_batch(events: List[Dict], model_version="v1.0")` → `List[Dict]` with scores
- `get_high_risk(threshold=0.7, limit=20)` → `List[Dict]` from DB
- CLI: `--event-id ID`, `--batch-since YYYY-MM-DD`, `--threshold 0.7`, `--json`

### Step 11: Manifest Shard
- Create `tools/manifest/conflict-mesh.md`

### Step 12: conftest.py Schema
- Add `conflict_predictions` table to `MINIMAL_ICDEV_SCHEMA`

### Step 13: Run Tests to GREEN
- Fix any implementation issues until all tests pass

## Testing Strategy

### Unit Tests (TDD)
- `test_reliefweb_provider_normalize` — canonical field mapping
- `test_acled_provider_normalize` — ACLED → canonical field mapping
- `test_gdelt_provider_normalize` — GDELT → canonical field mapping, CAMEO filter
- `test_mesh_coordinator_deduplication` — same (source, id) appears once
- `test_mesh_coordinator_sort_order` — newest events first
- `test_etl_pipeline_normalize_fields` — required fields present
- `test_etl_pipeline_skips_incomplete` — missing headline+event_date → skipped
- `test_etl_pipeline_dry_run` — no DB writes in dry_run mode
- `test_ml_violence_score` — keyword density correct
- `test_ml_geographic_spread_capped` — spread ≤ 1.0
- `test_escalation_score_bounds` — score always 0 ≤ x ≤ 1.0
- `test_escalation_score_formula` — known inputs → known output
- `test_escalation_predictor_store` — inserts into DB
- `test_escalation_predictor_get_high_risk` — filters by threshold

### Edge Cases
- Empty provider response → 0 events, no error
- Provider raises exception → logged, coordinator continues with other providers
- LLM unavailable → rule-based scoring used (no crash)
- Duplicate events across providers → deduplicated by (source, id)
- Missing geometry → stored as None, not rejected
- event_date None → stored as None, not rejected if headline present

## Acceptance Criteria
1. `pytest tests/test_conflict_mesh.py -v` — 14+ tests, all pass
2. `python tools/conflict_mesh/etl_pipeline.py --dry-run --since 2024-01-01 --json` — exits 0, returns `{"fetched": N, "inserted": 0, "dry_run": true}`
3. `python tools/conflict_mesh/escalation_predictor.py --batch-since 2024-01-01 --threshold 0.5 --json` — exits 0
4. `python -m py_compile tools/conflict_mesh/*.py tools/conflict_mesh/providers/*.py` — 0 syntax errors
5. All new `.py` files contain `# CUI // SP-CTI` header
6. `conflict_predictions` table created in DB via migration 070

## Validation Commands
- `python -m py_compile tools/conflict_mesh/providers/base.py tools/conflict_mesh/mesh_coordinator.py tools/conflict_mesh/etl_pipeline.py tools/conflict_mesh/ml_pattern_engine.py tools/conflict_mesh/escalation_predictor.py`
- `ruff check tools/conflict_mesh/ --fix`
- `python -m pytest tests/test_conflict_mesh.py -v --tb=short`
- `python -m pytest tests/ -v --tb=short`
- `python tools/security/sast_runner.py --project-path . --json`
- `python tools/security/secret_detector.py --project-path . --json`
- `python tools/security/dependency_auditor.py --project-path . --json`
- `python tools/compliance/sbom_generator.py --project icdev`
- `python tools/compliance/control_mapper.py --activity "code.commit" --project-id "icdev"`

## NIST 800-53 Controls
- AC-4: Information flow enforcement (multi-provider data ingestion with classification)
- AU-2, AU-3, AU-12: Event logging (prediction audit trail in conflict_predictions)
- SI-3: Malicious code protection (ML input validation, no arbitrary code execution)
- SC-28: Protection at rest (predictions stored encrypted at backend level)
- SA-11: Developer security testing (TDD, SAST gates)

## Notes
- LLMRouter is optional — all scoring degrades to deterministic rules if unavailable
- ReliefWeb API is public (no auth required), rate limit ~1 req/sec
- ACLED requires `ACLED_API_KEY` env var; if absent, provider returns empty list gracefully
- GDELT is public; if offline, provider returns empty list gracefully
- Migration 070 chosen to avoid collision with existing 052–069 migrations

# CUI // SP-CTI
