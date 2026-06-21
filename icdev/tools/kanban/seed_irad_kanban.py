# CUI // SP-CTI
"""Seed Kanban tasks for the IRAD IL5 AI Capabilities initiative.

Project: irad  (task_prefix 'irad-')
Epics: infra, aidp, cls, lac
Plan: C:/Users/schuo/.claude/plans/review-the-three-pdf-groovy-marble.md

PDFs analyzed:
  - ai_data_provenance_charter.pdf       -> Epic aidp (DIC canvas + RAG subsystem)
  - AI_Enablement_IL5_Architectural_Review.pdf -> Epic cls (RAG entitlement + ZTA)
  - technical_design_document.pdf        -> Epic lac (ZTA LAC Simulator tab)

Task IDs: irad-<epic>-<NN>
depends_on_task_id is single-parent (engine limitation).
Secondary prerequisites are described in the task description text.

Run:
    python tools/kanban/seed_irad_kanban.py
    python tools/kanban/seed_irad_kanban.py --dry-run
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

PROJECT_ID = "irad"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [
    # =========================================================================
    # EPIC infra — Project infrastructure
    # =========================================================================
    {
        "id": "irad-infra-01",
        "title": "Register IRAD project in args/projects.yaml with 4 epics (infra/aidp/cls/lac)",
        "description": (
            "Confirm the IRAD project block is present in args/projects.yaml with: "
            "key=irad, task_prefix='irad-', description (IL5 IRAD AIDP+CLS+LAC), and epics: "
            "infra, aidp, cls, lac each with title and priority=critical. "
            "If already present (seeder adds it during setup), mark complete. "
            "Verify: python -c \"import yaml; d=yaml.safe_load(open('args/projects.yaml')); "
            "p=[x for x in d['projects'] if x['key']=='irad'][0]; print(p['epics'])\" "
            "Expected: 4 epics listed. This is the root dependency for all IRAD tasks."
        ),
        "task_type": "chore",
        "priority": "critical",
        "depends_on_task_id": None,
    },
    {
        "id": "irad-infra-02",
        "title": "Update tools/manifest/rag-subsystem.md — add provenance_engine and entitlement_rag entries",
        "description": (
            "Add two entries to tools/manifest/rag-subsystem.md (or equivalent RAG manifest shard): "
            "(1) tools/rag/provenance_engine.py — WORM audit ledger, chain of custody, SHA-256 "
            "lineage binding, DLT-003 classification spillage check; "
            "(2) tools/rag/entitlement_rag.py — 5-step Entitlement-Driven RAG pipeline, TTLIndex, "
            "UUID citation grounding enforcer. "
            "Secondary prereqs: irad-aidp-02 (provenance_engine.py exists) and irad-cls-01 "
            "(entitlement_rag.py exists) — run after both. "
            "Follow existing shard format (tool path, description, key functions, args config key)."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "irad-aidp-02",
    },
    {
        "id": "irad-infra-03",
        "title": "Update tools/manifest/devsecops-zero-trust-architecture.md — add LAC Simulator and CLS posture entries",
        "description": (
            "Add entries to tools/manifest/devsecops-zero-trust-architecture.md (ZTA manifest shard): "
            "(1) tools/zta/lac_simulator.py — OPA/Python ABAC policy evaluator, PrincipalContext + "
            "ResourceMetadata dataclasses, 4-scenario what-if testing; "
            "(2) tools/zta/lac_scenarios.py — canonical scenario library (COI/ECI enforcement); "
            "(3) tools/zta/twin_phase_tracker.py — 4-phase Digital Twin state machine; "
            "(4) CLS_COMPLIANCE_SCORE pillar — AI warehouse posture assessment. "
            "Secondary prereqs: irad-lac-01 (constants done) and irad-cls-05 (CLS_COMPLIANCE_SCORE done)."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "irad-lac-01",
    },
    {
        "id": "irad-infra-04",
        "title": "Create feature docs: docs/features/phase-irad-aidp.md, phase-irad-cls.md, phase-irad-lac.md",
        "description": (
            "Create three feature documentation files after all E2E verifications pass: "
            "(1) docs/features/phase-irad-aidp.md — AI Data Provenance: chunk lineage (DLT-001/002), "
            "WORM audit (AIA-001/002), classification spillage (DLT-003), attribution UI, Genesis reflex; "
            "(2) docs/features/phase-irad-cls.md — Entitlement-Driven RAG: 5-step pipeline, TTLIndex, "
            "UUID citation grounding, ZTA CLS posture assessment; "
            "(3) docs/features/phase-irad-lac.md — LAC Simulator: OPA/Python evaluator, 4 canonical "
            "scenarios, Digital Twin 4-phase tracker, ZTA canvas tab. "
            "Secondary prereqs: irad-aidp-10, irad-cls-08, and irad-lac-10 must all be complete. "
            "Use standard feature doc format from existing docs/features/ files."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "irad-aidp-10",
    },

    # =========================================================================
    # EPIC aidp — AI Data Provenance (RAG subsystem + DIC canvas)
    # Implements: ai_data_provenance_charter.pdf (IL5-DLT, IL5-AIA, IL5-SAD, IL5-GUI)
    # =========================================================================
    {
        "id": "irad-aidp-01",
        "title": "Extend tools/rag/ingestion_manager.py — emit per-chunk lineage record (DLT-001/DLT-002)",
        "description": (
            "Extend the RAG ingestion pipeline in tools/rag/ingestion_manager.py to emit a lineage "
            "record for every chunk produced. Each record must include: chunk_uuid (UUID4), "
            "parent_doc_uuid, sha256_hash (SHA-256 of raw chunk text), token_count (max 512 per DLT-001), "
            "classification_label (CUI/FEDCON/NOFORN per DLT-003), version_tree_ref (doc version string), "
            "ingest_timestamp (UTC ISO-8601). "
            "Implements DLT-001 (chunk-level lineage) and DLT-002 (SHA-256 versioning tree) from "
            "ai_data_provenance_charter.pdf. "
            "Do NOT write to the DB yet — emit the record as a dict returned from the chunking step; "
            "irad-aidp-02 (provenance_engine.py) will persist it. "
            "Use hashlib.sha256, uuid.uuid4, datetime.now(timezone.utc) (cross-platform). "
            "Prereq: irad-infra-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "irad-infra-01",
    },
    {
        "id": "irad-aidp-02",
        "title": "Create tools/rag/provenance_engine.py — WORM audit + chain of custody (AIA-001/AIA-002)",
        "description": (
            "Create tools/rag/provenance_engine.py implementing the AIA layer from "
            "ai_data_provenance_charter.pdf: "
            "(1) bind_chunk_to_source(chunk_uuid, doc_uuid, sha256_hash, classification) — validates "
            "hash, inserts into rag_provenance_ledger (append-only); implements DLT-002 versioning tree ref; "
            "(2) log_chain_of_custody(model_id, hyperparams_dict, chunk_uuids, prompt_sha256, signature) — "
            "writes immutable AIA-002 chain-of-custody block to rag_provenance_ledger; "
            "(3) get_lineage(chunk_uuid) -> dict — fetch full lineage for a given chunk (used by DIC adapter); "
            "(4) Writes to append-only table rag_provenance_ledger — NEVER UPDATE or DELETE. "
            "Reuse: tools/observability/provenance_tracker.py W3C PROV-AGENT primitives where applicable; "
            "tools/compliance/classification_manager.py for classification validation. "
            "Prereq: irad-aidp-01 (lineage records emitted by ingestion)."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "irad-aidp-01",
    },
    {
        "id": "irad-aidp-03",
        "title": "Add rag_provenance_ledger append-only table — DB schema + APPEND_ONLY_TABLES + conftest schema",
        "description": (
            "Three-part infrastructure task: "
            "(1) Add rag_provenance_ledger table to tools/rag/db/init_db.py (or tools/db/init_icdev_db.py): "
            "columns: id (PK), chunk_uuid, parent_doc_uuid, sha256_hash, token_count, classification_label, "
            "version_tree_ref, model_id, hyperparams_json, prompt_sha256, signature, event_type "
            "CHECK('ingest','chain_of_custody'), ingest_timestamp, created_at. No UPDATE/DELETE ever. "
            "(2) Add 'rag_provenance_ledger' to APPEND_ONLY_TABLES list in .claude/hooks/pre_tool_use.py "
            "(same pattern as existing audit tables). "
            "(3) Add minimal rag_provenance_ledger schema to MINIMAL_ICDEV_SCHEMA in tests/conftest.py "
            "so pytest can create it without a full migration. "
            "Prereq: irad-aidp-02 (provenance_engine.py defines what to store)."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "irad-aidp-02",
    },
    {
        "id": "irad-aidp-04",
        "title": "Add check_classification_spillage() to provenance_engine.py — DLT-003 mixed-classification prevention",
        "description": (
            "Add check_classification_spillage(chunk_uuids: list[str], user_clearance: str) -> list[str] "
            "to tools/rag/provenance_engine.py. "
            "Implements DLT-003: mixed-classification tracking to prevent CUI/FEDCON/NOFORN spillage. "
            "Logic: for each chunk_uuid, look up classification_label in rag_provenance_ledger; "
            "return list of chunk_uuids whose classification exceeds user_clearance per a tiered "
            "access matrix (UNCLASS < CUI < FEDCON < NOFORN). "
            "Reuse tools/compliance/classification_manager.py for classification level ordering. "
            "This function is called by the retrieval path (irad-aidp-05) before injecting chunks "
            "into LLM context. "
            "Prereq: irad-aidp-02; secondary prereq: irad-aidp-03 (table exists for lookups)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "irad-aidp-02",
    },
    {
        "id": "irad-aidp-05",
        "title": "Extend RAG retrieval path — attach [chunk_uuid, sha256, classification] metadata to LLM context chunks",
        "description": (
            "Modify tools/rag/retrieval.py (or the equivalent retrieval entry point in the RAG subsystem) "
            "to attach provenance metadata to each chunk returned to the LLM context: "
            "{ 'text': '...', 'chunk_uuid': '...', 'sha256': '...', 'classification': '...', "
            "'source_doc_uuid': '...' }. "
            "Also call check_classification_spillage() (from provenance_engine.py) against the user's "
            "clearance before injecting chunks — filter out or raise on unauthorized chunks. "
            "This enables the attribution UI (irad-aidp-09) to display source metadata per paragraph. "
            "Prereq: irad-aidp-02; secondary prereq: irad-aidp-04 (spillage check available)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "irad-aidp-02",
    },
    {
        "id": "irad-aidp-06",
        "title": "Write tests/rag/test_provenance_engine.py — SHA-256 binding, spillage, append-only, chain of custody",
        "description": (
            "TDD: write tests/rag/test_provenance_engine.py covering: "
            "(1) test_bind_chunk_sha256 — bind_chunk_to_source() correctly stores SHA-256 and is "
            "idempotent (duplicate bind raises, not silently overwrites); "
            "(2) test_chain_of_custody_completeness — log_chain_of_custody() stores model_id, "
            "hyperparams_json, prompt_sha256, and all chunk_uuids; "
            "(3) test_classification_spillage_detection — check_classification_spillage() correctly "
            "flags NOFORN chunks for a CUI-cleared user; "
            "(4) test_provenance_ledger_append_only — verify no UPDATE/DELETE is callable on the table "
            "via the pre_tool_use hook (or assert at DB level); "
            "(5) test_get_lineage_full_chain — get_lineage() returns complete chain for a known chunk_uuid. "
            "Use pytest + conftest MINIMAL_ICDEV_SCHEMA (which includes rag_provenance_ledger). "
            "All tests RED -> GREEN. "
            "Prereqs: irad-aidp-03 (table schema), irad-aidp-04 (spillage), irad-aidp-05 (retrieval)."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "irad-aidp-03",
    },
    {
        "id": "irad-aidp-07",
        "title": "Create icdev/tools/genesis/reflexes/aidp_monitor.py — daily provenance integrity reflex",
        "description": (
            "Create icdev/tools/genesis/reflexes/aidp_monitor.py (GREEN tier, daily cadence, 24h): "
            "Scans rag_provenance_ledger for integrity violations: "
            "(1) Re-computes SHA-256 of stored chunk text vs stored sha256_hash — flags mismatches; "
            "(2) Checks for chain_of_custody records with missing prompt_sha256 or signature fields; "
            "(3) Detects classification_label changes between versions of the same parent_doc_uuid; "
            "(4) Emits findings to oracle_predictions (status='suggested', type='provenance_violation', "
            "confidence >= 0.9) and kanban_tasks (status='suggested') for any critical findings. "
            "Register in tools/genesis/daemon.py REFLEX_NAMES list (same pattern as audit.py, comply.py). "
            "Zero LLM in hot path — all checks are deterministic hash comparisons. "
            "Prereq: irad-aidp-03 (table exists)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "irad-aidp-03",
    },
    {
        "id": "irad-aidp-08",
        "title": "Create tools/dic/provenance_adapter.py — bridge DIC search results to provenance_engine metadata",
        "description": (
            "Create tools/dic/provenance_adapter.py exposing: "
            "get_chunk_provenance(chunk_uuid: str) -> dict — returns {sha256, classification, "
            "source_doc_uuid, version_tree_ref, ingest_timestamp, attribution_score} by querying "
            "rag_provenance_ledger via provenance_engine.get_lineage(). "
            "Attribution score: token-overlap ratio between chunk text and LLM output paragraph "
            "(deterministic, no LLM call) — proxy for attention-weight attribution from GUI-001. "
            "Used by the DIC search blueprint to annotate each search result with provenance metadata "
            "before rendering the footnote popover (irad-aidp-09). "
            "Prereq: irad-aidp-02 (provenance_engine.get_lineage() exists)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "irad-aidp-02",
    },
    {
        "id": "irad-aidp-09",
        "title": "Build icdev/tools/dashboard/templates/dic/_footnote_popover.html — attribution UI partial (GUI-001/GUI-002)",
        "description": (
            "Create icdev/tools/dashboard/templates/dic/_footnote_popover.html (Jinja2 partial): "
            "Renders a Bootstrap popover on hover over each search result paragraph showing: "
            "- Source text excerpt (truncated to 200 chars); "
            "- Classification label badge (CUI/FEDCON/NOFORN with color coding); "
            "- SHA-256 hash (last 8 chars displayed, full hash in tooltip); "
            "- Archive link (link to source document in DIC collections); "
            "- Attribution score as percentage (e.g. '73% match'); "
            "Implements IL5-GUI-001 (quantitative attribution scores) and GUI-002 (dynamic footnoting). "
            "Wire the partial into the DIC search results template by including it with chunk provenance "
            "data from provenance_adapter.get_chunk_provenance(). "
            "Mirror to tools/dashboard/templates/dic/_footnote_popover.html. "
            "Prereq: irad-aidp-08 (adapter provides data)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "irad-aidp-08",
    },
    {
        "id": "irad-aidp-10",
        "title": "Playwright E2E: DIC search result → hover footnote → SHA-256 + classification popover (screenshot)",
        "description": (
            "Playwright E2E verification for AIDP attribution UI: "
            "(1) Navigate to /document-intelligence/search; "
            "(2) Perform a search query against a seeded test collection; "
            "(3) Hover over a result paragraph's footnote marker; "
            "(4) Assert popover appears with: SHA-256 hash visible, classification badge, "
            "attribution percentage, archive link; "
            "(5) Screenshot to playwright/screenshots/aidp_footnote.png. "
            "If DIC canvas is not yet running, seed a minimal test collection via the DIC blueprint "
            "test fixtures. "
            "Use @mcp__playwright__browser_navigate, browser_hover, browser_snapshot pattern. "
            "Prereq: irad-aidp-09 (UI exists)."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "irad-aidp-09",
    },

    # =========================================================================
    # EPIC cls — Entitlement-Driven RAG + ZTA CLS Posture
    # Implements: AI_Enablement_IL5_Architectural_Review.pdf
    # =========================================================================
    {
        "id": "irad-cls-01",
        "title": "Create tools/rag/entitlement_rag.py — 5-step Entitlement-Driven RAG pipeline",
        "description": (
            "Create tools/rag/entitlement_rag.py implementing the 5-step workflow from "
            "AI_Enablement_IL5_Architectural_Review.pdf: "
            "(1) translate_intent(prompt: str, user_context: dict) -> str — LLM converts analyst "
            "question to SQL/SPARQL query (two-tier: qwen3 local first, fallback Claude); "
            "(2) execute_as_user(query: str, user_auth_token: str) -> list[dict] — runs query against "
            "data warehouse using the USER's credentials (not system creds) so native CLS filters apply; "
            "(3) tag_cells_with_uuids(result_set: list[dict]) -> list[dict] — appends unique [UUID4] "
            "tag to each data cell returned; "
            "(4) synthesize_with_grounding(tagged_data: list, prompt: str) -> str — LLM prompt with "
            "explicit instruction to cite [UUID] inline for every factual claim; "
            "(5) verify_citation_coverage(output: str, uuid_set: set) -> dict — audits output for "
            "ungrounded sentences (sentences without a [UUID] citation); returns {covered: int, "
            "uncovered: int, ungrounded_sentences: list}. "
            "Stateless design: no persistent vector index per user, no fine-tuning on mission data. "
            "Prereq: irad-infra-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "irad-infra-01",
    },
    {
        "id": "irad-cls-02",
        "title": "Implement TTLIndex class in entitlement_rag.py — embedding TTL + event-driven purge (ghost data prevention)",
        "description": (
            "Add TTLIndex class to tools/rag/entitlement_rag.py: "
            "- __init__(ttl_seconds: int = 3600) — configurable TTL; "
            "- register(uuid: str, embedding_ref: str) — records uuid + expiry timestamp; "
            "- is_expired(uuid: str) -> bool — check TTL; "
            "- purge_expired() -> list[str] — hard-delete expired embeddings; "
            "- register_purge_handler(event_name: str, handler: callable) — event-driven purge "
            "on data lifecycle events (e.g. 'record.deleted', 'record.aged_off'); "
            "Integrate with tools/rag/retention_manager.py: call TTLIndex.purge_expired() from "
            "retention_manager's scheduled sweep. "
            "This prevents 'ghost data' (purged records persisting in vector embeddings). "
            "Implements the Stateless Reasoning & TTL Indices pattern from the PDF. "
            "Prereq: irad-cls-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "irad-cls-01",
    },
    {
        "id": "irad-cls-03",
        "title": "Implement UUID citation grounding enforcer in entitlement_rag.py — forced grounding pattern",
        "description": (
            "Enhance verify_citation_coverage() and synthesize_with_grounding() in entitlement_rag.py: "
            "(1) System-prompt injection in synthesize_with_grounding: append explicit grounding "
            "instruction — 'For every factual claim you make, you MUST cite the source UUID inline "
            "as [UUID]. If you cannot cite a UUID for a claim, do not make that claim.' "
            "(2) Post-synthesis audit in verify_citation_coverage: regex-parse [UUID] markers, "
            "verify each UUID exists in uuid_set (the tagged_cells set), flag phantom UUIDs "
            "(cited but not in the authorized set = potential hallucination). "
            "(3) Return grounding_report: {coverage_pct: float, ungrounded: list[str], "
            "phantom_uuids: list[str], decision: 'PASS'|'WARN'|'FAIL'}. "
            "FAIL if coverage_pct < 0.80 or phantom_uuids non-empty. "
            "Implements 'Forced Grounding & Traceability' from the PDF. "
            "Prereq: irad-cls-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "irad-cls-01",
    },
    {
        "id": "irad-cls-04",
        "title": "Write tests/rag/test_entitlement_rag.py — pipeline, TTL, UUID grounding, stateless verification",
        "description": (
            "TDD: write tests/rag/test_entitlement_rag.py covering: "
            "(1) test_five_step_pipeline_e2e — mock translate/execute/tag/synthesize/verify steps, "
            "assert full pipeline returns grounding_report with decision='PASS'; "
            "(2) test_ttl_index_expiry — register UUID, advance mock clock past TTL, assert is_expired; "
            "(3) test_ttl_purge_removes_expired — purge_expired() returns list of deleted UUIDs; "
            "(4) test_uuid_grounding_enforcer_pass — output with all claims cited returns PASS; "
            "(5) test_uuid_grounding_enforcer_fail — output with uncited sentences returns FAIL; "
            "(6) test_phantom_uuid_detection — UUID in output but not in authorized set => phantom; "
            "(7) test_stateless_no_persistent_index — assert no global vector index state between calls. "
            "All RED -> GREEN. "
            "Prereqs: irad-cls-01, irad-cls-02, irad-cls-03."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "irad-cls-01",
    },
    {
        "id": "irad-cls-05",
        "title": "Add CLS_COMPLIANCE_SCORE dimension and assessment checks to tools/zta/constants.py",
        "description": (
            "Extend tools/zta/constants.py with: "
            "(1) CLS_COMPLIANCE_SCORE — new assessment dimension key (string constant); "
            "(2) CLS_ASSESSMENT_CHECKS — ordered list of check names: "
            "['no_persistent_ai_warehouse_access', 'cls_native_enforcement_active', "
            "'uuid_grounding_enabled', 'ttl_purge_registered', 'stateless_context_injection', "
            "'ghost_data_prevention_configured']; "
            "(3) CLS_CHECK_WEIGHTS — dict mapping each check to a weight (total sums to 1.0); "
            "(4) CLS_SCORE_THRESHOLDS — dict: green >= 0.80, yellow >= 0.50, red < 0.50. "
            "This mirrors the existing ZTA pillar scoring pattern. "
            "Prereq: irad-infra-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "irad-infra-01",
    },
    {
        "id": "irad-cls-06",
        "title": "Add CLS posture assessment routes to tools/zta/blueprint.py — GET /zta/cls-posture",
        "description": (
            "Add to tools/zta/blueprint.py: "
            "GET /zta/cls-posture — runs all CLS_ASSESSMENT_CHECKS against the current system state "
            "and returns JSON: {score: float, tier: 'green'|'yellow'|'red', "
            "checks: [{name, passed, detail}]}. "
            "Assessment is deterministic (no LLM): checks query config values, verify TTLIndex is "
            "registered in retention_manager, confirm entitlement_rag.py import succeeds, "
            "check grounding enforcement is enabled in args/rag_config.yaml. "
            "Reuse existing ZTA pillar scoring pattern from tools/zta/zta_engine.py (or equivalent). "
            "Prereqs: irad-cls-01 (entitlement_rag.py importable), irad-cls-05 (constants defined)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "irad-cls-01",
    },
    {
        "id": "irad-cls-07",
        "title": "Build ZTA canvas 'AI Data Warehouse Posture' card — template extension with CLS score tile",
        "description": (
            "Add an 'AI Data Warehouse Posture' card to the ZTA canvas dashboard template "
            "icdev/tools/dashboard/templates/zta/ (and mirror to tools/dashboard/templates/zta/): "
            "- Calls GET /zta/cls-posture via AJAX on page load; "
            "- Renders a score tile: green/yellow/red badge + percentage score; "
            "- Expandable checklist showing each CLS_ASSESSMENT_CHECK with pass/fail icon + detail; "
            "- Styled consistently with existing ZTA pillar cards in the template. "
            "Mirror: copy/symlink the template to icdev/ mirror path per companion sync pattern. "
            "Prereq: irad-cls-06 (API route exists)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "irad-cls-06",
    },
    {
        "id": "irad-cls-08",
        "title": "Playwright E2E: /zta page → AI Data Warehouse Posture card visible + CLS score rendered",
        "description": (
            "Playwright E2E verification for CLS posture card: "
            "(1) Navigate to http://localhost:5050/zta (dashboard must be running); "
            "(2) Assert 'AI Data Warehouse Posture' card is present in the DOM; "
            "(3) Assert CLS score tile shows a percentage (numeric) and tier badge; "
            "(4) Expand the checklist, assert at least one check item is visible; "
            "(5) Screenshot to playwright/screenshots/cls_posture.png. "
            "Use @mcp__playwright__browser_navigate, browser_snapshot, browser_wait_for. "
            "Prereq: irad-cls-07 (UI card exists)."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "irad-cls-07",
    },

    # =========================================================================
    # EPIC lac — LAC Simulator (ZTA canvas extension)
    # Implements: technical_design_document.pdf (Digital Twin + LAC Simulator)
    # =========================================================================
    {
        "id": "irad-lac-01",
        "title": "Add COI_TYPES, ECI_RESTRICTIONS, LAC_SIMULATION_STATES constants to tools/zta/constants.py",
        "description": (
            "Extend tools/zta/constants.py with LAC Simulator constants from technical_design_document.pdf: "
            "(1) COI_TYPES: dict mapping COI names to metadata (e.g. 'COI_ALPHA': {'name': 'Alpha', "
            "'classification': 'UNCLASS'}); "
            "(2) ECI_RESTRICTIONS: dict — ECI access requires US citizenship; FVEY partners are denied; "
            "emergency break-glass role grants temporary access with mandatory audit; "
            "(3) LAC_SIMULATION_STATES: list ['configured', 'simulating', 'completed', 'error'] "
            "for twin_phase_tracker state machine; "
            "(4) TWIN_PHASES: list ['scope_and_ingestion', 'model_development', 'state_sync', "
            "'simulation_execution'] for the 4-phase Crawl-Walk-Run tracker; "
            "(5) LAC_DECISION: dict {'PERMIT': 'permit', 'DENY': 'deny'} for simulator output. "
            "Prereq: irad-infra-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "irad-infra-01",
    },
    {
        "id": "irad-lac-02",
        "title": "Create tools/zta/lac_simulator.py — PrincipalContext + ResourceMetadata + simulate_access()",
        "description": (
            "Create tools/zta/lac_simulator.py implementing the Access Policy Simulator from "
            "technical_design_document.pdf: "
            "(1) @dataclass PrincipalContext: citizenship: str, clearance_level: str, "
            "cois: list[str], roles: list[str] — simulated user profile (no real PII/PHI); "
            "(2) @dataclass ResourceMetadata: resource_id: str, is_eci: bool, cois: list[str], "
            "classification: str, ownership: str — target object descriptor; "
            "(3) simulate_access(principal: PrincipalContext, resource: ResourceMetadata, "
            "action: str, environment: dict) -> dict — returns: "
            "{'decision': 'PERMIT'|'DENY', 'deny_reasons': list[str], 'audit_trail': dict, "
            "'scenario_name': str}; "
            "(4) Policy logic (Python, no external deps): has_matching_coi(), "
            "meets_eci_requirements(), is_break_glass() — mirrors the Rego policy from the PDF; "
            "(5) break-glass PERMIT includes mandatory audit trail with reason + timestamp. "
            "Prereq: irad-lac-01 (constants imported)."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "irad-lac-01",
    },
    {
        "id": "irad-lac-03",
        "title": "Create tools/zta/lac_scenarios.py — 4 canonical scenarios + extensible scenario library",
        "description": (
            "Create tools/zta/lac_scenarios.py with the 4 canonical scenarios from "
            "technical_design_document.pdf: "
            "(1) SCENARIO_AUTHORIZED_COI: US citizen, COI Alpha member, COI Alpha non-ECI resource "
            "-> PERMIT (validates basic mission workflow); "
            "(2) SCENARIO_ECI_SPILLAGE_PREVENTION: Allied partner (non-US), COI Alpha member, "
            "COI Alpha ECI-restricted resource -> DENY (prevents export violations); "
            "(3) SCENARIO_CROSS_COI_VIOLATION: US citizen, COI Bravo member, COI Alpha non-ECI "
            "resource -> DENY (enforces compartmentalization); "
            "(4) SCENARIO_BREAK_GLASS: US citizen, incident-response role, COI Alpha ECI "
            "resource -> PERMIT with audit trail (validates emergency override). "
            "Structure: list[dict] with keys: id, name, principal_config, resource_config, "
            "action, environment, expected_decision, mission_rationale. "
            "get_scenario(scenario_id) and list_scenarios() helper functions. "
            "Prereq: irad-lac-02 (PrincipalContext/ResourceMetadata dataclasses defined)."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "irad-lac-02",
    },
    {
        "id": "irad-lac-04",
        "title": "Implement OPA sidecar integration in lac_simulator.py — stdlib urllib fallback to Python policy",
        "description": (
            "Add OPA integration to tools/zta/lac_simulator.py: "
            "(1) simulate_via_opa(principal, resource, action, environment) -> dict — uses stdlib "
            "urllib (NOT requests, per PDF's low-dependency design) to POST to OPA sidecar at "
            "http://localhost:8181/v1/data/dow/access/simulator; handles ConnectionError gracefully; "
            "(2) Auto-fallback: if OPA sidecar is unavailable, fall back to Python policy evaluator "
            "(existing simulate_access logic — same semantics, different execution engine); "
            "(3) simulate_access() selects OPA or Python mode based on config flag "
            "LAC_OPA_MODE in .env (default='python' for air-gap); "
            "(4) Both modes return identical {decision, deny_reasons, audit_trail} schema. "
            "Air-gap native: Python fallback requires zero external dependencies beyond stdlib. "
            "Prereq: irad-lac-02."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "irad-lac-02",
    },
    {
        "id": "irad-lac-05",
        "title": "Create tools/zta/twin_phase_tracker.py — 4-phase Crawl-Walk-Run Digital Twin state machine",
        "description": (
            "Create tools/zta/twin_phase_tracker.py tracking the 4-phase execution from "
            "technical_design_document.pdf: "
            "(1) @dataclass TwinPhase: phase_id str, name str, status str CHECK(TWIN_PHASES), "
            "deliverables list[str], started_at, completed_at; "
            "(2) TwinPhaseTracker: manages per-environment phase progression via state machine; "
            "transition(env_id, phase_id, status) — validates allowed transitions (same pattern "
            "as tools/govcon/contract_manager.py state machine — reuse that pattern directly); "
            "(3) Phases: scope_and_ingestion (topology maps, static configs), "
            "model_development (containerized replicas spec), state_sync (differential sync config), "
            "simulation_execution (test scripts, failure injections, delta reports); "
            "(4) Stores phase state in zta_twin_phases table (add to init_db.py); "
            "(5) get_phase_status(env_id) -> dict for the LAC simulator dashboard. "
            "Prereq: irad-lac-01 (TWIN_PHASES constant defined)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "irad-lac-01",
    },
    {
        "id": "irad-lac-06",
        "title": "Add LAC simulator routes to tools/zta/blueprint.py — POST /simulate + GET /scenarios + GET /audit",
        "description": (
            "Add to tools/zta/blueprint.py: "
            "POST /zta/lac/simulate — accepts {principal: dict, resource: dict, action: str, "
            "environment: dict}, calls lac_simulator.simulate_access(), logs audit_trail to "
            "zta_lac_audit table (append-only), returns decision JSON; "
            "GET /zta/lac/scenarios — returns list_scenarios() from lac_scenarios.py; "
            "GET /zta/lac/audit — returns paginated audit trail from zta_lac_audit; "
            "GET /zta/lac/twin/<env_id>/status — returns get_phase_status() from twin_phase_tracker; "
            "POST /zta/lac/twin/<env_id>/advance — transitions a twin phase. "
            "All routes guarded by existing ZTA auth pattern. "
            "Prereqs: irad-lac-02, irad-lac-03, irad-lac-04, irad-lac-05."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "irad-lac-02",
    },
    {
        "id": "irad-lac-07",
        "title": "Build icdev/tools/dashboard/templates/zta/lac_simulator.html — LAC Simulator tab in ZTA canvas",
        "description": (
            "Create icdev/tools/dashboard/templates/zta/lac_simulator.html: "
            "A new tab/section in the ZTA canvas (/zta/lac-simulator) with: "
            "(1) Principal Builder form: citizenship dropdown, clearance level, COI membership "
            "checkboxes, roles multiselect (from COI_TYPES and ECI_RESTRICTIONS constants); "
            "(2) Resource Tagger form: resource_id, is_eci toggle, COI assignment, classification; "
            "(3) Scenario Library panel: dropdown of canonical scenarios (from GET /zta/lac/scenarios), "
            "load pre-fills the principal/resource forms; "
            "(4) Simulation Results panel: large PERMIT (green) / DENY (red) verdict badge, "
            "deny_reasons list, audit_trail JSON viewer; "
            "(5) Audit Trail table: paginated log from GET /zta/lac/audit; "
            "(6) Digital Twin Phase Tracker: 4-step progress bar for current environment's phase. "
            "Mirror to tools/dashboard/templates/zta/lac_simulator.html. "
            "Prereq: irad-lac-06 (routes exist)."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "irad-lac-06",
    },
    {
        "id": "irad-lac-08",
        "title": "Add ≥3 IQE seed queries for LAC simulation to context/iqe/queries/zta/",
        "description": (
            "Add at least 3 IQE (ICDEV Query Engine) seed queries for the ZTA canvas LAC Simulator: "
            "(1) 'Show me all DENY decisions for ECI resources in the last 30 days'; "
            "(2) 'How many access simulations resulted in cross-COI violations this week?'; "
            "(3) 'Which scenarios have triggered break-glass access and what was the mission rationale?'. "
            "Files go in context/iqe/queries/zta/ (YAML format matching existing seed files). "
            "Verify the ZTA IQE adapter (tools/iqe/adapters/zta.py) includes LAC-related collections "
            "so these queries are routable. "
            "Prereq: irad-lac-07 (LAC simulator tab and data exist)."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "irad-lac-07",
    },
    {
        "id": "irad-lac-09",
        "title": "Write tests/zta/test_lac_simulator.py — all 4 canonical scenarios, OPA fallback, deny_reasons, audit",
        "description": (
            "TDD: write tests/zta/test_lac_simulator.py covering: "
            "(1) test_authorized_coi_permit — SCENARIO_AUTHORIZED_COI returns PERMIT, no deny_reasons; "
            "(2) test_eci_spillage_deny — SCENARIO_ECI_SPILLAGE_PREVENTION returns DENY with "
            "'eci_citizenship_required' in deny_reasons; "
            "(3) test_cross_coi_deny — SCENARIO_CROSS_COI_VIOLATION returns DENY with "
            "'no_matching_coi' in deny_reasons; "
            "(4) test_break_glass_permit_with_audit — SCENARIO_BREAK_GLASS returns PERMIT and "
            "audit_trail contains reason and timestamp fields; "
            "(5) test_opa_fallback_mode — when OPA sidecar unavailable (mock ConnectionError), "
            "falls back to Python mode and returns same decision; "
            "(6) test_deny_reasons_completeness — DENY decision always has non-empty deny_reasons list; "
            "(7) test_audit_trail_append_only — simulate_access audit entries are written, not updated. "
            "All RED -> GREEN. "
            "Prereqs: irad-lac-02, irad-lac-03, irad-lac-04."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "irad-lac-02",
    },
    {
        "id": "irad-lac-10",
        "title": "Playwright E2E: /zta LAC Simulator tab → ECI Spillage scenario → DENY + deny_reasons rendered",
        "description": (
            "Playwright E2E verification for LAC Simulator: "
            "(1) Navigate to http://localhost:5050/zta/lac-simulator; "
            "(2) Load scenario SCENARIO_ECI_SPILLAGE_PREVENTION from the Scenario Library dropdown; "
            "(3) Click 'Run Simulation'; "
            "(4) Assert DENY verdict badge is visible (red); "
            "(5) Assert deny_reasons panel shows at least one reason text; "
            "(6) Assert audit trail table has at least one row after simulation; "
            "(7) Screenshot to playwright/screenshots/lac_simulator.png. "
            "Secondary prereq: irad-lac-08 (IQE seed queries seeded). "
            "Use @mcp__playwright__browser_navigate, browser_select_option, browser_click, "
            "browser_snapshot. "
            "Prereq: irad-lac-07 (UI tab exists)."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "irad-lac-07",
    },
]


def seed(dry_run: bool = False) -> int:
    conn = get_connection()
    now = _now()
    inserted = 0
    skipped = 0
    try:
        for t in TASKS:
            existing = conn.execute(
                "SELECT id FROM kanban_tasks WHERE id=?", (t["id"],)
            ).fetchone()
            if existing:
                skipped += 1
                continue
            if dry_run:
                inserted += 1
                continue
            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority, status,
                    scheduled_at, created_at, updated_at, depends_on_task_id,
                    project_id, classification)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    t["id"],
                    t["title"],
                    t["description"],
                    t.get("task_type", "build"),
                    t.get("priority", "medium"),
                    "scheduled",
                    now,
                    now,
                    now,
                    t.get("depends_on_task_id"),
                    PROJECT_ID,
                    "CUI",
                ),
            )
            inserted += 1
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    verb = "Would seed" if dry_run else "Seeded"
    print(f"{verb} {inserted} tasks, skipped {skipped} existing (total defined: {len(TASKS)}).")
    return inserted


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed IRAD IL5 AI Capabilities Kanban tasks")
    ap.add_argument("--dry-run", action="store_true", help="Print only, no DB write")
    args = ap.parse_args()
    seed(dry_run=args.dry_run)
