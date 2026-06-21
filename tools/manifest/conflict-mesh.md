# CUI // SP-CTI
# Tool Manifest — Conflict Mesh

## Module: `tools/conflict_mesh/`

Federated data mesh for conflict intelligence with ETL normalization and
ML-driven escalation prediction. Supports predictive analysis of conflict
escalation from multiple unstructured data sources.

---

### providers/base.py — MeshProvider ABC
- **Class**: `MeshProvider(ABC)`
- **Methods**: `provider_name` (property), `fetch(since_date, limit)`, `normalize(raw)`
- **Purpose**: Abstract base for all conflict data provider adapters

### providers/acled_provider.py — ACLED Adapter
- **Class**: `ACLEDProvider(MeshProvider)`
- **CLI**: N/A (library only)
- **Purpose**: Wraps DataBridge ACLED connector; maps to canonical event schema
- **Auth**: `ACLED_API_KEY` env var (returns empty if absent)
- **Event types**: kinetic, narrative_event, diplomatic, humanitarian

### providers/gdelt_provider.py — GDELT Adapter
- **Class**: `GDELTProvider(MeshProvider)`
- **CLI**: N/A (library only)
- **Purpose**: Wraps DataBridge GDELT connector; filters to CAMEO codes >= 140
- **Auth**: None (public API)
- **Event types**: narrative_event

### providers/reliefweb_provider.py — ReliefWeb Adapter
- **Class**: `ReliefWebProvider(MeshProvider)`
- **CLI**: N/A (library only)
- **Purpose**: Pulls humanitarian situation reports from ReliefWeb REST API
- **Auth**: None (public API; User-Agent only)
- **Event types**: humanitarian

### mesh_coordinator.py — Federated Orchestrator
- **Class**: `MeshCoordinator(providers: List[MeshProvider])`
- **Method**: `fetch_all(since_date, limit_per_provider)` → `List[Dict]`
- **Purpose**: Pull from all providers, deduplicate by id, sort newest-first
- **Behavior**: Provider failures are caught and logged; others continue

### etl_pipeline.py — ETL Normalization
- **Class**: `ETLPipeline(coordinator, db_path=None)`
- **Method**: `run(since_date, limit_per_provider, dry_run)` → `ETLResult`
- **CLI**: `python tools/conflict_mesh/etl_pipeline.py --since YYYY-MM-DD --dry-run --json`
- **Purpose**: Normalize events → sg_conflict_events; skips records missing headline+date
- **Output**: `ETLResult(fetched, inserted, updated, skipped, errors, dry_run)`

### ml_pattern_engine.py — ML Signal Extraction
- **Class**: `MLPatternEngine(llm_router=None)`
- **Method**: `extract_signals(text, metadata)` → `EscalationSignals`
- **Purpose**: Extract violence_score, actor_count, geographic_spread, tone from text
- **Behavior**: Rule-based primary; LLMRouter NLP is optional enhancement
- **Signals**: `EscalationSignals(violence_score, actor_count, geographic_spread, tone, llm_assessment)`

### escalation_predictor.py — Risk Scoring
- **Class**: `EscalationPredictor(pattern_engine, db_path=None)`
- **Methods**: `predict(event)`, `predict_and_store(event)`, `predict_batch(events)`, `get_high_risk(threshold, limit)`
- **CLI**: `python tools/conflict_mesh/escalation_predictor.py --batch-since YYYY-MM-DD --threshold 0.7 --json`
- **Purpose**: Compute 0–1 escalation risk, persist to conflict_predictions table
- **Formula**: 0.4*violence + 0.2*actors/5 + 0.2*spread + 0.2*abs(tone)/10

---

## Database Tables

| Table | Migration | Type |
|-------|-----------|------|
| `sg_conflict_events` | 052 | canonical event store (existing) |
| `conflict_predictions` | 158 | ML predictions (append-only, NIST AU) |

## NIST 800-53 Controls
- AC-4: Information flow enforcement (external data ingestion)
- AU-2, AU-12: Event logging (conflict_predictions append-only audit trail)
- SI-3: Malicious code protection (ML input validation)
- SC-28: Protection at rest (predictions at backend level)

# CUI // SP-CTI
