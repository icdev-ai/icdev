-- CUI // SP-CTI
-- Migration 167: canvas_ai_decisions — cross-canvas AI decision store
-- Phase ai-trace ep1 (D-TRACE)
-- Append-only: never UPDATE or DELETE rows (NIST AU-2/AU-12)

CREATE TABLE IF NOT EXISTS canvas_ai_decisions (
    id               TEXT PRIMARY KEY,
    canvas_type      TEXT NOT NULL
                         CHECK (canvas_type IN (
                             'ndc', 'sdc', 'pdc', 'bdc', 'ddc', 'odc', 'idc',
                             'aadc', 'aimc', 'mc', 'genesis', 'qdc'
                         )),
    record_id        TEXT,                        -- design_id / topology_id / dataset_id
    decision_type    TEXT NOT NULL
                         CHECK (decision_type IN (
                             'compliance_finding', 'threat_assessment', 'remediation',
                             'narrative', 'classification', 'anomaly', 'risk_score',
                             'readiness_assessment', 'boundary_impact',
                             'confabulation_flag', 'chain_of_thought',
                             'chain_of_debate', 'quality_assessment'
                         )),
    decision         TEXT NOT NULL,               -- primary finding / summary
    rationale        TEXT,                        -- explanation / justification
    model_used       TEXT,                        -- LLM model identifier
    confidence       REAL,                        -- 0.0–1.0 if available
    alternatives     TEXT DEFAULT '[]',           -- JSON array of alternatives
    trace_id         TEXT,                        -- OTel trace_id cross-reference
    span_id          TEXT,                        -- OTel span_id cross-reference
    actor            TEXT NOT NULL DEFAULT 'icdev-system',
    project_id       TEXT,
    classification   TEXT NOT NULL DEFAULT 'CUI',
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_cad_canvas_type
    ON canvas_ai_decisions (canvas_type);

CREATE INDEX IF NOT EXISTS idx_cad_record_id
    ON canvas_ai_decisions (record_id);

CREATE INDEX IF NOT EXISTS idx_cad_trace_id
    ON canvas_ai_decisions (trace_id);

CREATE INDEX IF NOT EXISTS idx_cad_created_at
    ON canvas_ai_decisions (created_at DESC);
