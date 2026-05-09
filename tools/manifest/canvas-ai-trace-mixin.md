# Canvas AI Trace Mixin

## Module: `tools/canvas/ai_trace_mixin.py`

**Purpose:** One-import AI traceability bridge for all canvas blueprints. Writes a structured AI decision record to `canvas_ai_decisions` (NIST AU-2) and cross-references it with the active OTel span from the LLM router (NIST AI RMF MEASURE 2.7).

**Primary function:**
```python
from tools.canvas.ai_trace_mixin import record_canvas_decision

decision_id = record_canvas_decision(
    canvas_type="ndc",           # 'ndc','sdc','pdc','bdc','ddc','odc','idc','aadc','aimc','mc'
    decision_type="compliance_finding",  # see DECISION_TYPES in constants.py
    decision="FISMA HIGH gap detected",
    record_id="topology-uuid",   # optional: the canvas record being assessed
    rationale="LLM rationale",   # optional
    model_used="qwen3-local",    # optional
    confidence=0.92,             # 0.0–1.0
    alternatives=[],             # optional: other options considered
    project_id="my-project",     # optional
    actor="icdev-system",        # default
    classification="CUI",        # default
)
```

**Dual write pattern:** `canvas_ai_decisions` (analytics/query) + `audit_trail` via `decision_recorder` (NIST AU-2 append-only).

**Tracer integration:** Reads `get_tracer().get_active_span()` to populate `trace_id`/`span_id` — linking the AI decision to the LLM router's OTel span. Falls back to `NullTracer` if tracing is unavailable (air-gap safe).

**Graceful degradation:** All canvas imports use try/except with a no-op `_record_decision` fallback — canvas blueprints never break if mixin fails to import.

## DB Table: `canvas_ai_decisions` (migration 121)

Stored in `icdev.db` (main DB, NOT canvas-specific). Columns: `id, canvas_type, record_id, decision_type, decision, rationale, model_used, confidence, alternatives, trace_id, span_id, actor, project_id, classification, created_at`. Append-only (NIST AU).

## Compliance Coverage

| Requirement | How Satisfied |
|-------------|---------------|
| NIST AI RMF MEASURE 2.5 | `confidence` field stored per decision |
| NIST AI RMF MEASURE 2.7 | `trace_id`/`span_id` cross-reference to LLM router OTel spans |
| NIST AI RMF MEASURE 2.8 | `rationale` + `alternatives` stored for each decision |
| DoD RAI Traceable | Per-decision record with actor, model, timestamp |
| FedRAMP SI-4 | Continuous monitoring evidence in append-only table |
| NIST AU-2 | Dual-write to `audit_trail` via `decision_recorder` |
| OMB M-25-21 | AI use case inventory via `canvas_type` + `decision_type` |

## Canvas Wiring Status (all 10 canvases)

| Canvas | Blueprint | Injection Points | /api/ai-trace |
|--------|-----------|-----------------|---------------|
| NDC | `tools/network/blueprint.py` | `run_compliance_audit`, `_add_narrative` | ✓ |
| SDC | `tools/security_canvas/blueprint.py` | `sc_api_assess`, `sc_api_llm_threats` | ✓ |
| PDC | `tools/pipeline/blueprint.py` | `pc_api_analyze` | ✓ |
| BDC | `tools/boundary_canvas/blueprint.py` | `bdc_api_assess` | ✓ |
| DDC | `tools/data_canvas/anomaly_detector.py` | `detect_anomalies` | ✓ |
| ODC | `tools/observability_canvas/blueprint.py` | `oc_api_assess` | ✓ |
| IDC | `tools/infra_canvas/blueprint.py` | `assess_infra_design` | ✓ |
| AADC | `tools/agentic_ai_canvas/blueprint.py` | `run_assessment` | ✓ |
| AIMC | `tools/aiml_canvas/blueprint.py` | `api_assess`, `api_assess_gov` | ✓ |
| MC | `tools/migration_canvas/blueprint.py` + `network_migration.py` | `mc_api_assess`, hardware rec | ✓ |

## IQE Collections (per canvas)

Each canvas IQE adapter exposes a `<canvas>.ai_decisions` collection backed by `canvas_ai_decisions WHERE canvas_type='<canvas_type>'`.

| Canvas | Collection | Adapter |
|--------|-----------|---------|
| NDC | `network.ai_decisions` | `tools/iqe/adapters/ndc.py` |
| SDC | `security.ai_decisions` | `tools/iqe/adapters/security.py` |
| PDC | `pipeline.ai_decisions` | `tools/iqe/adapters/pipeline.py` |
| BDC | `bdc.ai_decisions` | `tools/iqe/adapters/bdc.py` |
| DDC | `data.ai_decisions` | `tools/iqe/adapters/data.py` |
| ODC | `observability.ai_decisions` | `tools/iqe/adapters/observability.py` |
| IDC | `infra.ai_decisions` | `tools/iqe/adapters/infra.py` |
| AADC | `aadc.ai_decisions` | `tools/iqe/adapters/aadc.py` |
| AIMC | `aimc.ai_decisions` | `tools/iqe/adapters/aimc.py` |
| MC | `mc.ai_decisions` | `tools/iqe/adapters/mc.py` |
| Observatory | `observatory.decisions`, `observatory.confabulation_flags` | `tools/iqe/adapters/ai_observatory.py` |

## Related

- Migration 122: `tools/db/migrations/122_trace_linkage/up.py` — adds `trace_id`/`span_id` to `kanban_tasks` and `reflex_observations`
- AI Observatory: `tools/ai_observatory/blueprint.py` — unified dashboard at `/ai-observatory`
- AI Observatory analytics: `tools/ai_observatory/analytics.py`
- Confabulation detector: `tools/security/confabulation_detector.py` — wired into Genesis synthesize reflex
