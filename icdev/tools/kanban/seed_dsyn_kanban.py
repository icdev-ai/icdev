# CUI // SP-CTI
"""Seed Kanban tasks for DIC Canvas Synergy (DSYN).

Project: dsyn  (task_prefix 'dsyn-')
Purpose: Wire every ICDEV canvas and child app into the Document Intelligence
         Canvas so that state changes (network config pushes, compliance findings,
         posture shifts, AI-ify gaps, etc.) automatically surface as AI-drafted
         document update suggestions queued for HITL review.

Architecture:
  canvas_events bus (existing, migration 037)
       ↓
  canvas_adapter.py — maps event_type → affected DIC collection tags (YAML config)
       ↓
  DIC integration Genesis reflex — polls events, drafts targeted patches
       ↓
  dic_suggestions table → HITL review queue in DIC UI
       ↓
  Editor accepts/rejects → section updated, edit history recorded

Epics:
  adapt    — Canvas adapter + dic_suggestions table
  reflex   — DIC integration Genesis reflex
  emit     — Tier-1 canvas event emission (8 canvases)
  patch    — Targeted patch mode + notifications
  suggest  — Viewer suggest-edit + scheduled review cadence
  consist  — Cross-doc KG consistency propagation
  vv       — End-to-end validation

Run:
    python tools/kanban/seed_dsyn_kanban.py --dry-run
    python tools/kanban/seed_dsyn_kanban.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402

PROJECT_ID = "dsyn"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [

    # ── EPIC adapt — Canvas Adapter + dic_suggestions table ──────────────────

    {
        "id": "dsyn-adapt-01",
        "title": "Create args/dic_canvas_integrations.yaml — event-type to collection mapping",
        "description": (
            "Create `args/dic_canvas_integrations.yaml` as the single source of truth for "
            "the DIC-Canvas integration.  Each entry maps a canvas event_type (from the "
            "canvas_events bus) to: target_collection_tags (list of DIC collection tags), "
            "doc_types (list of document categories to search, e.g. 'sop,runbook,policy'), "
            "priority ('high'|'medium'|'low'), and a human-readable rationale string. "
            "Must cover all 8 Tier-1 canvases: NDC (topology/config), Network Canvas "
            "(migration, anomaly), ZIG (posture, pillar_gap), Compliance (finding_created, "
            "poam_overdue), SIPA (vulnerability_found, quarantine), DevSecOps (pipeline_fail, "
            "stig_finding), CloudForge (resource_provisioned, runbook_executed), AI-ify "
            "(canvas_scored, gap_identified).  Include a 'fallback' catch-all entry for "
            "unknown event types (priority: low, doc_types: general).  "
            "File must be valid YAML parseable by PyYAML.  "
            "Acceptance: `python -c 'import yaml; yaml.safe_load(open(\"args/dic_canvas_integrations.yaml\"))'` "
            "exits 0; file contains entries for all 8 Tier-1 canvases; each entry has "
            "target_collection_tags, doc_types, priority, and rationale keys."
        ),
        "acceptance_criteria": (
            "GIVEN the YAML file exists "
            "WHEN PyYAML parses it "
            "THEN no exception and all 8 Tier-1 canvas event types are present as top-level keys. "
            "GIVEN an unknown event_type 'foo.bar' "
            "WHEN the adapter looks it up "
            "THEN the fallback entry is returned with priority=low."
        ),
        "test_path": "tests/test_dsyn_canvas_integrations_yaml.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": None,
    },

    {
        "id": "dsyn-adapt-02",
        "title": "Implement tools/document_intelligence/canvas_adapter.py",
        "description": (
            "Create `tools/document_intelligence/canvas_adapter.py`.  "
            "Primary function: `resolve_affected_collections(event: dict) -> list[dict]`. "
            "Input: a canvas_events row dict (event_type, payload, source_canvas, created_at). "
            "Output: list of dicts — each has collection_id, collection_tags, doc_type, "
            "priority, rationale.  Implementation: (1) load args/dic_canvas_integrations.yaml "
            "(cached, reloads if mtime changes); (2) match event_type against config keys "
            "using exact match then prefix match then fallback; (3) query dic_collections "
            "WHERE tags overlap with target_collection_tags (Python-side set intersection, "
            "not SQL JSON function, to comply with PG portability rule); "
            "(4) return list of {collection_id, matched_tags, doc_type, priority, rationale}. "
            "Secondary function: `mark_event_processed(event_id, conn)` — writes "
            "dic_canvas_event_id to a processed-events set to prevent double-processing. "
            "Acceptance: unit tests cover exact match, prefix match, fallback, "
            "and empty-collection result (returns empty list, never raises)."
        ),
        "acceptance_criteria": (
            "GIVEN event_type='ndc.topology_change' and a collection tagged 'network_architecture' "
            "WHEN resolve_affected_collections() is called "
            "THEN the collection appears in the result list with priority from YAML config. "
            "GIVEN event_type='unknown.event' with no matching config entry "
            "WHEN resolve_affected_collections() is called "
            "THEN result uses the fallback entry and does not raise. "
            "GIVEN no collections match the target_collection_tags "
            "WHEN resolve_affected_collections() is called "
            "THEN an empty list is returned without error."
        ),
        "test_path": "tests/test_dsyn_canvas_adapter.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "dsyn-adapt-01",
    },

    {
        "id": "dsyn-adapt-03",
        "title": "dic_suggestions table + dic_suggestion_decisions (append-only) + lazy-init",
        "description": (
            "Create two new tables via lazy-init _ensure_tables() in "
            "`tools/document_intelligence/suggestion_store.py`. "
            "Table 1 — dic_suggestions (mutable): suggestion_id TEXT PK, section_id TEXT, "
            "doc_id TEXT, collection_id TEXT, trigger_event_id TEXT, canvas_source TEXT, "
            "suggested_content TEXT NOT NULL, current_content TEXT, rationale TEXT, "
            "status TEXT DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected','superseded')), "
            "created_at TEXT NOT NULL, updated_at TEXT, tenant_id TEXT, classification TEXT DEFAULT 'CUI'. "
            "Table 2 — dic_suggestion_decisions (append-only, NIST AU): decision_id TEXT PK, "
            "suggestion_id TEXT NOT NULL, decision TEXT NOT NULL, decided_by TEXT, "
            "decided_at TEXT NOT NULL, note TEXT, tenant_id TEXT, classification TEXT DEFAULT 'CUI'. "
            "Add 'dic_suggestion_decisions' to APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py. "
            "Implement: create_suggestion(...) -> str (returns suggestion_id); "
            "get_pending_suggestions(collection_id=None) -> list[dict]; "
            "decide_suggestion(suggestion_id, decision, decided_by, note='') -> bool. "
            "Acceptance: pytest covers create, get_pending filters by status and collection, "
            "decide transitions status and inserts decision row."
        ),
        "acceptance_criteria": (
            "GIVEN create_suggestion() is called with valid args "
            "WHEN get_pending_suggestions() is called "
            "THEN the new suggestion appears with status='pending'. "
            "GIVEN a suggestion exists with status='pending' "
            "WHEN decide_suggestion(id, 'accepted', 'reviewer') is called "
            "THEN suggestion status becomes 'accepted' and a decision row is inserted in "
            "dic_suggestion_decisions (immutable — cannot be updated or deleted)."
        ),
        "test_path": "tests/test_dsyn_suggestion_store.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "dsyn-adapt-02",
    },

    {
        "id": "dsyn-adapt-04",
        "title": "API routes for suggestion review in DIC blueprint",
        "description": (
            "Add 4 endpoints to `tools/document_intelligence/blueprint.py` for suggestion management: "
            "GET /api/suggestions — list pending suggestions for the user's accessible collections; "
            "supports ?collection_id=, ?canvas_source=, ?status= filters. "
            "GET /api/suggestions/<suggestion_id> — full detail including suggested_content, "
            "current_content, rationale, trigger_event_id, canvas_source. "
            "POST /api/suggestions/<suggestion_id>/accept — apply suggested_content to the section "
            "(calls api_section_update_content logic), records decision row, returns updated section. "
            "Requires 'editor' role on the collection. "
            "POST /api/suggestions/<suggestion_id>/reject — mark rejected, record decision with "
            "optional note from request body.  Requires 'reviewer' or 'editor' role. "
            "All routes respect RLS via get_connection().  "
            "Acceptance: Flask test-client tests for list, accept (verifies section content updated "
            "and history recorded), reject (verifies status change), and 403 for wrong role."
        ),
        "acceptance_criteria": (
            "GIVEN a pending suggestion for section S "
            "WHEN POST /api/suggestions/<id>/accept is called by an editor "
            "THEN section S content is updated to suggested_content, "
            "a dic_edit_history row is inserted, and suggestion status becomes 'accepted'. "
            "GIVEN the same suggestion is accepted "
            "WHEN POST /api/suggestions/<id>/reject is called "
            "THEN 409 (already decided). "
            "GIVEN a viewer-role user calls POST .../accept "
            "THEN 403 Forbidden."
        ),
        "test_path": "tests/test_dsyn_suggestion_api.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "dsyn-adapt-03",
    },

    # ── EPIC reflex — DIC Integration Genesis Reflex ─────────────────────────

    {
        "id": "dsyn-reflex-01",
        "title": "tools/genesis/reflexes/dic_integration.py — integration reflex",
        "description": (
            "Create `tools/genesis/reflexes/dic_integration.py` as a Genesis reflex that "
            "polls canvas_events for unprocessed DIC-relevant events and queues AI-drafted "
            "suggestions for HITL review.  Logic: (1) query canvas_events WHERE event_type "
            "IN (keys from dic_canvas_integrations.yaml) AND id NOT IN already-processed set "
            "(tracked in a lightweight dic_processed_canvas_events table or the "
            "dic_suggestion.trigger_event_id lookup); (2) for each event, call "
            "canvas_adapter.resolve_affected_collections(event); (3) for each affected "
            "collection, identify sections whose content is likely impacted (keyword overlap "
            "between event payload and section content — Python-side, no LLM required for "
            "identification); (4) call the DIC AI generation route in patch mode to draft a "
            "targeted suggested update (or create a stub suggestion when AI is unavailable); "
            "(5) call suggestion_store.create_suggestion(); (6) emit notification_log row. "
            "Reflex must be idempotent (re-run safe), catch all exceptions per-event "
            "(one bad event must not block others), and complete within 60s. "
            "Acceptance: pytest mocks canvas_events and verifies suggestions are created "
            "and notifications emitted; idempotency test confirms re-run creates no duplicates."
        ),
        "acceptance_criteria": (
            "GIVEN 3 unprocessed canvas_events of type 'ndc.topology_change' "
            "WHEN the reflex runs "
            "THEN 3 suggestion rows are created (one per affected collection found) and "
            "3 notification_log rows are emitted. "
            "GIVEN the same 3 events are in the table and reflex runs a second time "
            "WHEN the reflex completes "
            "THEN no duplicate suggestions are created (idempotent)."
        ),
        "test_path": "tests/test_dsyn_dic_integration_reflex.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "dsyn-adapt-03",
    },

    {
        "id": "dsyn-reflex-02",
        "title": "Register DIC integration reflex in Genesis daemon and config",
        "description": (
            "Register 'dic_integration' in `tools/genesis/daemon.py` REFLEX_NAMES list "
            "so the Genesis daemon picks it up automatically on restart. "
            "Add a reflex entry in `args/awareness_config.yaml` (or the relevant reflexes "
            "config file): name='dic_integration', cadence_minutes=15, "
            "enabled=true, description='Poll canvas_events and queue DIC update suggestions'. "
            "Verify the daemon's per-reflex watchdog timeout covers the 60s reflex budget "
            "(set watchdog to 90s for this reflex). "
            "Update `tools/manifest/document-intelligence.md` with the new reflex entry. "
            "Acceptance: `python tools/genesis/daemon.py --list-reflexes` (or equivalent) "
            "shows dic_integration; config file parses without error; "
            "health_check.py --json reports the reflex as registered."
        ),
        "acceptance_criteria": (
            "GIVEN the Genesis daemon is started after this change "
            "WHEN `python tools/genesis/daemon.py --list-reflexes` runs "
            "THEN 'dic_integration' appears in the output with cadence_minutes=15. "
            "GIVEN the reflex is enabled in config "
            "WHEN 15 minutes elapse "
            "THEN the reflex runs and logs a completion entry in genesis_reflex_log."
        ),
        "test_path": "tests/test_dsyn_reflex_registration.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "dsyn-reflex-01",
    },

    # ── EPIC emit — Tier-1 canvas event emission ──────────────────────────────

    {
        "id": "dsyn-emit-01",
        "title": "NDC: emit canvas_events on topology/config change",
        "description": (
            "In the Network Device Canvas blueprint or relevant module "
            "(`tools/ndc/` or equivalent), add canvas_events emission at state-change points: "
            "topology_change (device added/removed/moved), config_push (config applied to device), "
            "baseline_deviation (running config differs from golden config). "
            "Use the standard emit pattern: INSERT INTO canvas_events "
            "(id, event_type, source_canvas, payload, created_at, tenant_id, classification) "
            "VALUES (...) with event_type='ndc.topology_change' / 'ndc.config_push' / "
            "'ndc.baseline_deviation'. Payload should include device_id, change_summary, "
            "and affected_segments as a JSON string.  "
            "Use get_connection() with %s placeholders.  "
            "Wrap emit in try/except so a failed event write never blocks the primary operation. "
            "Acceptance: pytest verifies that calling the NDC state-change function results "
            "in a canvas_events row with the correct event_type and non-empty payload."
        ),
        "acceptance_criteria": (
            "GIVEN an NDC topology change occurs "
            "WHEN the change is processed "
            "THEN a canvas_events row with event_type='ndc.topology_change' is inserted "
            "and the payload contains device_id and change_summary. "
            "GIVEN the canvas_events INSERT raises an exception "
            "WHEN the NDC change is processed "
            "THEN the primary NDC operation still completes without error."
        ),
        "test_path": "tests/test_dsyn_emit_ndc.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "dsyn-adapt-01",
    },

    {
        "id": "dsyn-emit-02",
        "title": "Network Canvas: emit canvas_events on migration phase and anomaly",
        "description": (
            "In the Network Canvas blueprint (`tools/network/blueprint.py` or equivalent), "
            "add canvas_events emission for: migration_phase_complete (a migration phase "
            "transitions to done/approved), anomaly_detected (migration analysis flags a "
            "deviation, misconfiguration, or risk).  "
            "Event types: 'network.migration_phase_complete', 'network.anomaly_detected'. "
            "Payload: include phase_name, migration_id, severity (for anomaly), and a "
            "one-sentence summary. "
            "Follow same emit pattern as dsyn-emit-01: try/except wrapper, %s placeholders, "
            "get_connection(). "
            "Acceptance: pytest verifies emit on phase completion and anomaly detection; "
            "verify emit failure does not block migration state transition."
        ),
        "acceptance_criteria": (
            "GIVEN a migration phase transitions to 'approved' "
            "WHEN the phase update is processed "
            "THEN a canvas_events row with event_type='network.migration_phase_complete' is inserted. "
            "GIVEN an anomaly is detected in the migration analysis "
            "WHEN the analysis completes "
            "THEN a canvas_events row with event_type='network.anomaly_detected' "
            "and severity in payload is inserted."
        ),
        "test_path": "tests/test_dsyn_emit_network.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "dsyn-adapt-01",
    },

    {
        "id": "dsyn-emit-03",
        "title": "ZIG: emit canvas_events on posture score drop and pillar gap",
        "description": (
            "In the Zero Trust Implementation Guide canvas (`tools/security/zig/` or equivalent), "
            "add canvas_events emission for: posture_score_drop (ZIG overall score decreases "
            "by >=5 points vs. previous assessment), pillar_gap_detected (a ZIG pillar is "
            "assessed below threshold, e.g. <70/100). "
            "Event types: 'zig.posture_score_drop', 'zig.pillar_gap_detected'. "
            "Payload: pillar_name, previous_score, current_score, gap_description. "
            "Follow same emit pattern: try/except, %s placeholders, get_connection(). "
            "Acceptance: pytest verifies emit fires when score drops by >=5 "
            "and does NOT fire when score improves or stays the same."
        ),
        "acceptance_criteria": (
            "GIVEN ZIG posture score drops from 85 to 78 "
            "WHEN the assessment is saved "
            "THEN a canvas_events row with event_type='zig.posture_score_drop' is inserted. "
            "GIVEN ZIG posture score improves from 78 to 82 "
            "WHEN the assessment is saved "
            "THEN no canvas_events row is inserted for this event type."
        ),
        "test_path": "tests/test_dsyn_emit_zig.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "dsyn-adapt-01",
    },

    {
        "id": "dsyn-emit-04",
        "title": "Compliance Canvas: emit canvas_events on new finding and POAM overdue",
        "description": (
            "In the Compliance Canvas blueprint (`tools/compliance/blueprint.py` or relevant "
            "compliance module), add canvas_events emission for: finding_created (a new "
            "CAT1 or CAT2 STIG/compliance finding is recorded), poam_overdue (a POAM item "
            "passes its scheduled completion date without being closed). "
            "Event types: 'compliance.finding_created', 'compliance.poam_overdue'. "
            "Payload: control_id, finding_severity, poam_id (where applicable), "
            "framework (NIST/FedRAMP/CMMC), and a one-sentence description. "
            "Follow same emit pattern: try/except, %s placeholders, get_connection(). "
            "Acceptance: pytest verifies emit on finding creation; verifies POAM overdue "
            "emit fires in the nightly compliance scanner when due_date < today."
        ),
        "acceptance_criteria": (
            "GIVEN a new CAT1 STIG finding is recorded "
            "WHEN create_finding() is called "
            "THEN a canvas_events row with event_type='compliance.finding_created' "
            "and severity='CAT1' in payload is inserted. "
            "GIVEN a POAM item with due_date yesterday is not closed "
            "WHEN the nightly scanner runs "
            "THEN a canvas_events row with event_type='compliance.poam_overdue' is inserted."
        ),
        "test_path": "tests/test_dsyn_emit_compliance.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "dsyn-adapt-01",
    },

    {
        "id": "dsyn-emit-05",
        "title": "SIPA: emit canvas_events on vulnerability finding and quarantine",
        "description": (
            "In SIPA (`tools/integrity/blueprint.py` or `tools/integrity/scanner.py`), "
            "add canvas_events emission for: vulnerability_found (SIPA integrity scan finds "
            "a high/critical severity finding that is not a known false positive), "
            "quarantine_triggered (a file is quarantined by SIPA's quarantine mechanism). "
            "Event types: 'sipa.vulnerability_found', 'sipa.quarantine_triggered'. "
            "Payload: file_path, finding_type, severity, verdict_id (from integrity_verdicts). "
            "Follow same emit pattern: try/except, %s placeholders, get_connection(). "
            "Acceptance: pytest verifies emit fires on high/critical findings and suppresses "
            "on info/low; verifies quarantine event includes file_path and verdict_id."
        ),
        "acceptance_criteria": (
            "GIVEN SIPA finds a critical vulnerability in a scanned file "
            "WHEN the verdict is recorded in integrity_verdicts "
            "THEN a canvas_events row with event_type='sipa.vulnerability_found' is inserted "
            "with severity='critical' and verdict_id in payload. "
            "GIVEN SIPA finds only an info-level finding "
            "WHEN the verdict is recorded "
            "THEN no canvas_events row is inserted for this event type."
        ),
        "test_path": "tests/test_dsyn_emit_sipa.py",
        "status": "backlog",
        "priority": "high",
        "depends_on_task_id": "dsyn-adapt-01",
    },

    {
        "id": "dsyn-emit-06",
        "title": "DevSecOps Canvas: emit canvas_events on pipeline fail and STIG finding",
        "description": (
            "In the DevSecOps Canvas blueprint (`tools/devsecops/blueprint.py` or equivalent), "
            "add canvas_events emission for: pipeline_stage_failed (a CI/CD pipeline stage "
            "fails, e.g. SAST, dependency audit, or STIG check), stig_finding_new (a new "
            "STIG finding is added to the pipeline audit results). "
            "Event types: 'devsecops.pipeline_stage_failed', 'devsecops.stig_finding_new'. "
            "Payload: pipeline_id, stage_name, finding_id, severity, component. "
            "Follow same emit pattern: try/except, %s placeholders, get_connection(). "
            "Acceptance: pytest verifies emit fires on stage failure with correct stage_name; "
            "verifies emit fires on new STIG finding with severity and component in payload."
        ),
        "acceptance_criteria": (
            "GIVEN a DevSecOps pipeline SAST stage fails "
            "WHEN the stage result is recorded "
            "THEN a canvas_events row with event_type='devsecops.pipeline_stage_failed' "
            "and stage_name='sast' in payload is inserted. "
            "GIVEN an existing STIG finding is re-detected (not new) "
            "WHEN the pipeline runs "
            "THEN no duplicate canvas_events row is inserted."
        ),
        "test_path": "tests/test_dsyn_emit_devsecops.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "dsyn-adapt-01",
    },

    {
        "id": "dsyn-emit-07",
        "title": "CloudForge: emit canvas_events on resource provision and runbook execution",
        "description": (
            "In CloudForge (`tools/cloudforge/blueprint.py` or cf_* modules), add "
            "canvas_events emission for: resource_provisioned (a new cloud resource is "
            "successfully provisioned — VPC, VM, bucket, IAM role), "
            "runbook_executed (a CloudForge runbook completes — success or failure). "
            "Event types: 'cloudforge.resource_provisioned', 'cloudforge.runbook_executed'. "
            "Payload: resource_type, resource_id, cloud_provider, runbook_id, outcome. "
            "Follow same emit pattern: try/except, %s placeholders, get_connection(). "
            "Acceptance: pytest verifies emit fires on provision success and includes "
            "resource_type and cloud_provider; verifies runbook event includes outcome field."
        ),
        "acceptance_criteria": (
            "GIVEN CloudForge provisions an EC2 instance on AWS "
            "WHEN the provision completes successfully "
            "THEN a canvas_events row with event_type='cloudforge.resource_provisioned' "
            "and resource_type='ec2_instance', cloud_provider='aws' is inserted. "
            "GIVEN a provision fails "
            "WHEN the failure is recorded "
            "THEN no canvas_events row is inserted (only successful provisions trigger DIC)."
        ),
        "test_path": "tests/test_dsyn_emit_cloudforge.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "dsyn-adapt-01",
    },

    {
        "id": "dsyn-emit-08",
        "title": "AI-ify Canvas: emit canvas_events on canvas score and gap detection",
        "description": (
            "In the AI-ify Canvas (`tools/aiify/blueprint.py` or scoring engine), add "
            "canvas_events emission for: canvas_scored (AI-ify completes a full canvas "
            "assessment and the grade changes from the previous run), gap_identified (a "
            "specific AI-ify opportunity is assessed as 'not built' or 'partially built' "
            "with high value). "
            "Event types: 'aiify.canvas_scored', 'aiify.gap_identified'. "
            "Payload: canvas_name, previous_grade, current_grade, gap_id, "
            "gap_description, estimated_value. "
            "Follow same emit pattern: try/except, %s placeholders, get_connection(). "
            "Acceptance: pytest verifies emit fires when grade changes and suppresses "
            "when grade is unchanged; gap event includes gap_id and estimated_value."
        ),
        "acceptance_criteria": (
            "GIVEN AI-ify scores a canvas at grade B, up from C "
            "WHEN the score is saved "
            "THEN a canvas_events row with event_type='aiify.canvas_scored' is inserted "
            "with previous_grade='C' and current_grade='B' in payload. "
            "GIVEN AI-ify scores a canvas at the same grade as the last run "
            "WHEN the score is saved "
            "THEN no canvas_events row is inserted (no change, no DIC trigger)."
        ),
        "test_path": "tests/test_dsyn_emit_aiify.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "dsyn-adapt-01",
    },

    # ── EPIC patch — Targeted patch mode + notifications ──────────────────────

    {
        "id": "dsyn-patch-01",
        "title": "Targeted patch mode in DIC AI generation route",
        "description": (
            "Modify `POST /api/generate/section` in the DIC blueprint to support a "
            "'patch_mode' flag in the request body.  When patch_mode=true: the LLM prompt "
            "instructs the model to produce ONLY the changed lines/paragraphs rather than a "
            "full section rewrite — 'Given this current content and this change context, "
            "produce the minimal edit that incorporates the change.  Return only the affected "
            "paragraph(s) with [KEEP] markers for unchanged text.'  "
            "Also accept a 'change_context' field (the canvas event payload summary) that is "
            "injected into the prompt alongside the current section content. "
            "When patch_mode=false (default), behavior is unchanged (backward compatible). "
            "The response adds 'patch_mode': true and 'change_context_used': str to the JSON. "
            "Acceptance: integration test verifies patch_mode=true returns shorter content "
            "than the full section; test with no LLM (stub) verifies prompt contains the "
            "change_context string and [KEEP] instruction."
        ),
        "acceptance_criteria": (
            "GIVEN a section with 10 paragraphs and a change_context about one paragraph "
            "WHEN POST /api/generate/section with patch_mode=true "
            "THEN the returned suggested_content is shorter than the full section "
            "and contains [KEEP] markers for unchanged paragraphs. "
            "GIVEN patch_mode=false (default) "
            "WHEN POST /api/generate/section is called "
            "THEN behavior is identical to the pre-patch implementation."
        ),
        "test_path": "tests/test_dsyn_patch_mode.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "dsyn-reflex-01",
    },

    {
        "id": "dsyn-patch-02",
        "title": "Notification wiring — emit to notification_log when suggestion created",
        "description": (
            "When `suggestion_store.create_suggestion()` is called, also insert a row into "
            "`notification_log` (existing Hermes table) with: "
            "recipient=collection owner(s) (query dic_collection_members WHERE role IN "
            "('editor','reviewer')), notification_type='dic_suggestion_created', "
            "payload=JSON {suggestion_id, canvas_source, section_title, rationale}, "
            "status='pending', created_at=now().  "
            "This should be a best-effort emit: wrap in try/except so a notification "
            "failure never blocks suggestion creation. "
            "Also add a similar notification on POAM-overdue and stale-document events "
            "from the freshness engine (GET /api/freshness/heatmap already identifies "
            "these — add an emit call after the heatmap query). "
            "Acceptance: pytest verifies notification_log row is created after "
            "create_suggestion(); verifies try/except means notification failure does "
            "not cause create_suggestion() to raise."
        ),
        "acceptance_criteria": (
            "GIVEN create_suggestion() is called for a collection with 2 editor members "
            "WHEN the function completes "
            "THEN 2 notification_log rows are inserted (one per editor) "
            "with notification_type='dic_suggestion_created'. "
            "GIVEN notification_log INSERT raises an exception "
            "WHEN create_suggestion() is called "
            "THEN the suggestion is still created and no exception propagates."
        ),
        "test_path": "tests/test_dsyn_notifications.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "dsyn-adapt-04",
    },

    {
        "id": "dsyn-patch-03",
        "title": "Suggestion review panel UI in doc_detail.html",
        "description": (
            "Add a 'Suggestions' section to `doc_detail.html` and the per-section action row. "
            "Page-level: a collapsible 'AI Suggestions' panel above the sections list showing "
            "pending suggestions for this document — each card shows: canvas_source chip "
            "(e.g. 'NDC', 'Compliance'), rationale, a diff preview (current vs suggested), "
            "and Accept/Reject buttons. "
            "Per-section: a yellow '⚡ N suggestions' badge on sections that have pending "
            "suggestions; clicking opens the suggestion detail in the page-level panel. "
            "Accept calls POST /api/suggestions/<id>/accept; Reject calls POST .../reject "
            "with an optional note prompt. "
            "On accept/reject the card fades out; page reloads only if the section content "
            "was updated (accept path). "
            "Acceptance: manual test — create a suggestion via the API, load doc page, "
            "verify suggestion card appears with diff preview and canvas source chip; "
            "accept updates section content; reject clears the card."
        ),
        "acceptance_criteria": (
            "GIVEN a pending suggestion exists for section S in document D "
            "WHEN the doc_detail page for D is loaded "
            "THEN the AI Suggestions panel shows the suggestion with canvas_source, rationale, "
            "and a diff preview comparing current_content to suggested_content. "
            "GIVEN the editor clicks Accept "
            "WHEN the accept API call completes "
            "THEN section S content is updated and the suggestion card disappears."
        ),
        "test_path": "tests/test_dsyn_suggestion_ui.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "dsyn-adapt-04",
    },

    # ── EPIC suggest — Viewer suggest-edit + scheduled review cadence ─────────

    {
        "id": "dsyn-suggest-01",
        "title": "Viewer-role suggest-edit mode — propose changes without editor access",
        "description": (
            "Add a 'Suggest Edit' button to each section in doc_detail.html, visible to "
            "users with 'viewer' role (currently viewers only have the annotation/comment path). "
            "On click, open a textarea pre-populated with the current section content. "
            "On submit, call a new route POST /api/sections/<id>/suggest with body "
            "{proposed_content, rationale}.  "
            "The route: validates the user has at least 'viewer' role on the collection; "
            "calls suggestion_store.create_suggestion() with canvas_source='crowdsource', "
            "trigger_event_id=None, suggested_content=proposed_content. "
            "The suggestion lands in the same HITL review queue as AI-generated suggestions. "
            "Add 'crowdsource' as a valid canvas_source value in dic_canvas_integrations.yaml. "
            "Acceptance: pytest verifies a viewer can POST to /api/sections/<id>/suggest "
            "and a suggestion row is created; verifies an unauthenticated caller gets 403."
        ),
        "acceptance_criteria": (
            "GIVEN a user with 'viewer' role submits a suggested edit via POST .../suggest "
            "WHEN the request is processed "
            "THEN a dic_suggestions row is created with canvas_source='crowdsource' "
            "and status='pending', accessible via GET /api/suggestions. "
            "GIVEN an unauthenticated user calls POST .../suggest "
            "THEN 403 Forbidden is returned and no suggestion is created."
        ),
        "test_path": "tests/test_dsyn_viewer_suggest.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "dsyn-adapt-04",
    },

    {
        "id": "dsyn-suggest-02",
        "title": "Scheduled review cadence — review_interval_days per collection + nightly reflex",
        "description": (
            "Add a `review_interval_days` column (INTEGER DEFAULT 90) to the "
            "`dic_collections` table (ALTER TABLE or lazy migration).  "
            "Expose it in the collection create/update API.  "
            "Create a nightly Genesis reflex `tools/genesis/reflexes/dic_review_cadence.py` "
            "that: queries dic_collections WHERE review_interval_days IS NOT NULL; "
            "for each, checks when the last HITL review was approved for any section in the "
            "collection (from dic_edit_history or HITL approval records); if "
            "now() - last_review_date > review_interval_days, creates a canvas_events row "
            "with event_type='dic.review_overdue' and emits a notification_log row. "
            "Register 'dic_review_cadence' in daemon.py with cadence=1440min (nightly). "
            "Acceptance: pytest verifies overdue detection fires when last review > interval "
            "and does not fire when review is within the interval."
        ),
        "acceptance_criteria": (
            "GIVEN a collection with review_interval_days=30 and last review 35 days ago "
            "WHEN the nightly reflex runs "
            "THEN a canvas_events row with event_type='dic.review_overdue' is inserted "
            "and a notification_log row is created for collection editors. "
            "GIVEN the same collection reviewed 20 days ago "
            "WHEN the nightly reflex runs "
            "THEN no canvas_events row is inserted."
        ),
        "test_path": "tests/test_dsyn_review_cadence.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "dsyn-reflex-02",
    },

    {
        "id": "dsyn-suggest-03",
        "title": "Auto-create kanban HITL task on overdue review via task_factory",
        "description": (
            "Extend the dic_review_cadence reflex (dsyn-suggest-02) to also auto-create a "
            "Kanban task via `tools/kanban/task_factory.py::create_tasks()` when a collection "
            "review is overdue.  Task spec: title='Review overdue: <collection_name>', "
            "description includes collection ID, last review date, interval, and a link to "
            "the collection in DIC; priority='medium'; project_id inferred from collection "
            "metadata or defaulted to a configurable DSYN_DEFAULT_PROJECT_ID env var. "
            "Gate: only create the task if no open kanban task with the same title already "
            "exists (idempotency check via SELECT before INSERT). "
            "Acceptance: pytest verifies task is created on overdue detection; "
            "verifies second reflex run does not create a duplicate task."
        ),
        "acceptance_criteria": (
            "GIVEN a collection review is overdue and no open task exists for it "
            "WHEN the nightly reflex runs "
            "THEN a kanban task with title containing the collection name is created "
            "with status='backlog' and priority='medium'. "
            "GIVEN the task was already created in a prior run "
            "WHEN the nightly reflex runs again "
            "THEN no duplicate task is created."
        ),
        "test_path": "tests/test_dsyn_hitl_task_creation.py",
        "status": "backlog",
        "priority": "low",
        "depends_on_task_id": "dsyn-suggest-02",
    },

    # ── EPIC consist — Cross-doc consistency propagation ─────────────────────

    {
        "id": "dsyn-consist-01",
        "title": "KG neighbor walk — identify docs that reference the same concepts",
        "description": (
            "Create `tools/document_intelligence/consistency_checker.py`.  "
            "Primary function: `find_related_docs(doc_id, changed_concepts: list[str]) -> list[dict]`. "
            "Uses the existing KG (kg_retrieval_log, KG nodes) to find documents that: "
            "(a) are in the same tenant, (b) contain KG nodes matching any of changed_concepts "
            "(concept labels from the updated doc's sections), (c) are not the source doc itself. "
            "Returns list of {doc_id, doc_title, matching_concepts, collection_id, last_updated}. "
            "Implementation: query kg nodes WHERE label IN (changed_concepts) — Python-side "
            "join, not SQL JSON.  Cap results at 20 related docs to avoid combinatorial blowup. "
            "Secondary function: `extract_changed_concepts(before: str, after: str) -> list[str]` "
            "— extracts noun phrases that appear in `after` but not `before` using basic "
            "regex tokenization (no spaCy/NLTK required for air-gap safety). "
            "Acceptance: pytest with a mock KG verifies find_related_docs returns docs "
            "sharing concept nodes; cap-at-20 test; empty result for unrecognized concepts."
        ),
        "acceptance_criteria": (
            "GIVEN doc A references concept 'Zero Trust' and doc B also has a KG node 'Zero Trust' "
            "WHEN find_related_docs(doc_A_id, ['Zero Trust']) is called "
            "THEN doc B appears in the result list. "
            "GIVEN 50 documents share a concept "
            "WHEN find_related_docs() is called "
            "THEN at most 20 are returned (cap enforced)."
        ),
        "test_path": "tests/test_dsyn_consistency_checker.py",
        "status": "backlog",
        "priority": "low",
        "depends_on_task_id": "dsyn-adapt-03",
    },

    {
        "id": "dsyn-consist-02",
        "title": "Consistency flag — propagate review flags when a referenced doc changes",
        "description": (
            "Extend `record_edit()` in `tools/document_intelligence/history_recorder.py`: "
            "after a successful save, call consistency_checker.extract_changed_concepts() "
            "and consistency_checker.find_related_docs().  For each related doc found, "
            "create a canvas_events row with event_type='dic.consistency_flag' and payload "
            "{source_doc_id, source_section_id, related_doc_id, matching_concepts, "
            "change_summary}.  The DIC integration reflex (dsyn-reflex-01) already handles "
            "'dic.*' event types via the fallback config entry, so related docs will "
            "automatically receive a review suggestion. "
            "Gate: only propagate when char_delta > 50 (minor typo fixes should not trigger "
            "cross-doc flags).  Wrap in try/except — consistency propagation must never "
            "block a section save.  "
            "Acceptance: pytest verifies consistency_flag event is created after a >50-char "
            "edit to a doc with KG-linked neighbors; verifies no event for <50-char edits."
        ),
        "acceptance_criteria": (
            "GIVEN doc A is edited with char_delta=200 and has a KG-linked neighbor doc B "
            "WHEN record_edit() completes "
            "THEN a canvas_events row with event_type='dic.consistency_flag' is inserted "
            "referencing doc B as related_doc_id. "
            "GIVEN doc A is edited with char_delta=10 (minor fix) "
            "WHEN record_edit() completes "
            "THEN no canvas_events row is inserted (below threshold)."
        ),
        "test_path": "tests/test_dsyn_consistency_propagation.py",
        "status": "backlog",
        "priority": "low",
        "depends_on_task_id": "dsyn-consist-01",
    },

    # ── EPIC vv — End-to-end validation ──────────────────────────────────────

    {
        "id": "dsyn-vv-01",
        "title": "V&V — end-to-end integration smoke test for full DIC-Canvas synergy loop",
        "description": (
            "Write an end-to-end smoke test in `tests/test_dsyn_vv_smoke.py` that exercises "
            "the complete synergy loop: (1) Insert a canvas_events row simulating an NDC "
            "topology change; (2) run dic_integration reflex; (3) verify a dic_suggestions "
            "row is created for the affected collection; (4) verify a notification_log row "
            "is created for the collection editor; (5) call POST /api/suggestions/<id>/accept "
            "and verify the section content is updated + edit history row recorded; "
            "(6) verify the accepted suggestion status is 'accepted' and a decision row "
            "exists in dic_suggestion_decisions. "
            "Also run: `python tools/testing/health_check.py --json` (expect 0 errors); "
            "`python tools/workflow/coherence_checker.py --all --gate` (expect green). "
            "Create `docs/features/phase-dsyn-canvas-synergy.md` documenting the integration "
            "pattern, all 8 Tier-1 canvas event types, and the HITL review flow. "
            "Acceptance: all 6 smoke assertions pass; coherence gate green; feature doc exists."
        ),
        "acceptance_criteria": (
            "GIVEN a simulated 'ndc.topology_change' canvas_events row "
            "WHEN the dic_integration reflex runs "
            "THEN a suggestion is created, notification emitted, editor can accept, "
            "section updated, history recorded, and decision row in dic_suggestion_decisions. "
            "GIVEN the full loop completes "
            "WHEN coherence_checker runs "
            "THEN zero gate failures."
        ),
        "test_path": "tests/test_dsyn_vv_smoke.py",
        "status": "backlog",
        "priority": "medium",
        "depends_on_task_id": "dsyn-patch-03",
    },
]


def seed(dry_run: bool = False) -> None:
    now = _now()
    if dry_run:
        print(f"[DRY RUN] Would seed {len(TASKS)} tasks for project '{PROJECT_ID}':")
        for t in TASKS:
            dep = f" (depends: {t['depends_on_task_id']})" if t.get("depends_on_task_id") else ""
            print(f"  {t['id']:30s}  {t['priority']:8s}  {t['title'][:60]}{dep}")
        return

    with get_connection() as conn:
        inserted = skipped = 0
        for task in TASKS:
            existing = conn.execute(
                "SELECT id FROM kanban_tasks WHERE id = %s", (task["id"],)
            ).fetchone()
            if existing:
                skipped += 1
                print(f"  SKIP  {task['id']}")
                continue

            full_desc = task["description"]
            ac = task.get("acceptance_criteria", "")
            tp = task.get("test_path", "")
            if ac:
                full_desc += f"\n\nAcceptance Criteria:\n{ac}"
            if tp:
                full_desc += f"\n\nTest Path: {tp}"

            conn.execute(
                """
                INSERT INTO kanban_tasks
                    (id, title, description, status, priority, project_id,
                     depends_on_task_id, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    task["id"], task["title"], full_desc,
                    task["status"], task["priority"], PROJECT_ID,
                    task.get("depends_on_task_id"), now, now,
                ),
            )
            inserted += 1
            print(f"  INSERT {task['id']}")

        conn.commit()
        print(f"\nDone — {inserted} inserted, {skipped} skipped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed DIC Canvas Synergy tasks")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    seed(dry_run=args.dry_run)
