#!/usr/bin/env python3
# CUI // SP-CTI
"""Append-only audit trail writer. Satisfies NIST 800-53 AU controls.
No UPDATE or DELETE operations — all entries are immutable."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import sys

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

# The hash-chain half of the writer: id reservation, the critical section, the
# digest and signature, and the cutover marker.
from icdev.tools.audit import chain as _chain

logger = get_logger("audit.audit_logger")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
ATOMIC_FALLBACK_PATH = BASE_DIR / "data" / "failed_audit_entries.jsonl"

try:
    from tools.compat.db_utils import get_db_connection
except ImportError:
    get_db_connection = None

VALID_EVENT_TYPES = (
    "project_created",
    "project_updated",
    "code_generated",
    "code_reviewed",
    "code_approved",
    "code_rejected",
    "test_written",
    "test_executed",
    "test_passed",
    "test_failed",
    "security_scan",
    "vulnerability_found",
    "vulnerability_resolved",
    "compliance_check",
    "ssp_generated",
    "poam_generated",
    "stig_checked",
    "sbom_generated",
    # SBOM 2026 Accommodation of Updates (sbx-prc-02). A corrected SBOM is a new
    # document superseding its predecessor, never an edit, so the correction itself
    # is an event: "sbom_generated" cannot distinguish a build from a fix, and the
    # standard now lets recipients weigh SBOM errors in their risk decisions about
    # the producer. See tools/compliance/sbom_revision.py.
    "sbom_revised",
    "sbom_corrected",
    # Migration Design Canvas (nmce-purge-01). tools/migration_canvas/blueprint.py
    # has bridged every _audit call here since it was written, under this
    # event_type — but it was never admitted to the vocabulary, so the CHECK
    # rejected all of them and the best-effort except swallowed the rejection.
    # The canvas had zero rows in audit_trail. Actions are namespaced in `action`
    # (net_session_status_changed, coa_selected, erb_submitted, …).
    "migration_canvas",
    # Supplemental harness state (exa-refine-05). A refinement cycle snapshots
    # the prompt/skill/goal state the harness rewrites about itself, and can be
    # rolled back as a unit; every snapshot, applied refinement and rollback is
    # a chained row so self-modification is verifiable rather than merely
    # logged. Actions are namespaced in `action` (snapshot_created,
    # refinement_applied, cycle_rolled_back) following the migration_canvas
    # precedent above — one event type, not three.
    "supplemental_state",
    "deployment_initiated",
    "deployment_succeeded",
    "deployment_failed",
    "rollback_executed",
    "decision_made",
    "approval_granted",
    "approval_denied",
    "agent_task_submitted",
    "agent_task_completed",
    "agent_task_failed",
    "self_heal_triggered",
    "pattern_detected",
    "knowledge_recorded",
    "config_changed",
    "secret_rotated",
    # RICOAS events (Phase 20)
    "intake_session_created",
    "intake_session_resumed",
    "intake_session_completed",
    "requirement_captured",
    "requirement_refined",
    "requirement_approved",
    "gap_detected",
    "ambiguity_detected",
    "readiness_scored",
    "complexity_scored",
    "decomposition_generated",
    "document_uploaded",
    "document_extracted",
    "bdd_criteria_generated",
    # Boundary & Supply Chain events
    "boundary_assessed",
    "boundary_impact_red",
    "boundary_alternative_generated",
    "ato_system_registered",
    "isa_created",
    "isa_expired",
    "isa_renewed",
    "scrm_assessed",
    "cve_triaged",
    "cve_impact_propagated",
    "supply_chain_risk_escalated",
    # Simulation & COA events
    "simulation_created",
    "simulation_completed",
    "monte_carlo_completed",
    "coa_generated",
    "coa_alternative_generated",
    "coa_compared",
    "coa_selected",
    "coa_rejected",
    "coa_presented",
    # Integration events
    "integration_configured",
    "integration_sync_push",
    "integration_sync_pull",
    "integration_sync_error",
    "reqif_exported",
    "approval_submitted",
    "approval_reviewed",
    "approval_approved",
    "approval_rejected",
    "approval_escalated",
    "rtm_generated",
    "rtm_gap_detected",
    # Observability events (Phase 39)
    "hook_event_logged",
    "agent_execution_started",
    "agent_execution_completed",
    "agent_execution_failed",
    "agent_execution_retried",
    # NLQ events (Phase 40)
    "nlq_query_executed",
    "nlq_query_blocked",
    # Worktree & GitLab events (Phase 41)
    "worktree_created",
    "worktree_cleaned",
    "gitlab_task_claimed",
    "gitlab_task_completed",
    "gitlab_task_failed",
    # Agent Orchestration events (Opus 4.6 Multi-Agent)
    "bedrock_invoked",
    "bedrock_fallback",
    "bedrock_rate_limited",
    "workflow_created",
    "workflow_completed",
    "workflow_failed",
    "subtask_dispatched",
    "subtask_completed",
    "subtask_failed",
    "agent_health_stale",
    "agent_veto_issued",
    "agent_veto_overridden",
    "agent_collaboration_started",
    "agent_collaboration_completed",
    "agent_message_sent",
    "agent_memory_stored",
    "agent_memory_recalled",
    "agent_escalation_created",
    # Remote Command Gateway events (Phase 28)
    "remote_binding_created",
    "remote_binding_provisioned",
    "remote_binding_revoked",
    "remote_command_received",
    "remote_command_rejected",
    "remote_command_completed",
    "remote_response_filtered",
    # Spec-kit events (D156-D161)
    "spec_quality_check",
    "spec_consistency_check",
    "constitution_added",
    "constitution_removed",
    "constitution_defaults_loaded",
    "clarification_analyzed",
    "spec.init",
    "spec.register",
    # Phase 29: Heartbeat + Auto-Resolution (D141-D145)
    "heartbeat_check_warning",
    "heartbeat_check_critical",
    "auto_resolution_started",
    "auto_resolution_completed",
    "auto_resolution_failed",
    "auto_resolution_escalated",
    # Cross-Language Translation events (Phase 43)
    "translation.job_created",
    "translation.job_completed",
    "translation.job_failed",
    "translation.extract",
    "translation.type_check",
    "translation.unit_translated",
    "translation.unit_mocked",
    "translation.unit_failed",
    "translation.repair_attempted",
    "translation.repair_succeeded",
    "translation.validation_passed",
    "translation.validation_failed",
    "translation.compliance_checked",
    "translation.assembly_completed",
    # Multi-Stream Chat events (Phase 44 — D257-D260)
    "chat.context_created",
    "chat.context_closed",
    "chat.context_archived",
    "chat.message_sent",
    "chat.message_received",
    "chat.intervention_requested",
    "chat.intervention_applied",
    "chat.agent_loop_started",
    "chat.agent_loop_completed",
    "chat.agent_loop_error",
    # Extension Hook events (Phase 44 — D261-D264)
    "extension.registered",
    "extension.unregistered",
    "extension.dispatched",
    "extension.completed",
    "extension.error",
    "extension.timeout",
    # Memory Consolidation events (Phase 44 — D276)
    "memory.consolidation_checked",
    "memory.consolidation_executed",
    "memory.consolidation_skipped",
    # Observability & Explainability events (Phase 46 — D280-D289)
    "trace.span_exported",
    "trace.batch_exported",
    "prov.entity_created",
    "prov.activity_recorded",
    "prov.relation_established",
    "shap.analysis_completed",
    "shap.analysis_failed",
    "xai.assessment_completed",
    "xai.gate_evaluated",
    # ANVIL Critique Phase (Phase 61 — Feature 3)
    "critique_session_created",
    "critique_completed",
    "critique_revision_requested",
    # Session Purpose (Phase 61 — D-ORCH-5)
    "session.purpose_declared",
    # Child App Generation (Phase 19/36)
    "child_app_generated",
    "child_app_registered",
    # Phase 67: Engineering Review Board (D-RB-2, D-RB-10, D-RB-11)
    "review_board.scan_completed",
    "review_board.finding_created",
    "review_board.remediation_completed",
    "review_board.remediation_failed",
    "review_board.escalation_created",
    "review_board.health_scored",
    "review_board.circuit_breaker_tripped",
    "review_board.report_generated",
    # Phase 69 — OpenClaw Bridge
    "openclaw_skill_imported",
    "openclaw_skill_promoted",
    "openclaw_skill_rejected",
    "openclaw_skill_exported",
    "openclaw_export_approved",
    "openclaw_export_rejected",
    "openclaw_quarantine_expired",
    # Event-Sourced Workflow Replay (NIST AU extension)
    "workflow_replay_started",
    "workflow_replay_completed",
    "workflow_replay_failed",
    "workflow_step_skipped",
    "workflow_resume_point_identified",
    # POA&M finding triage & bulk operations (Phase 73)
    "finding_approval",
    "poam.bulk_revert",
    "poam.bulk_remediate",
    "poam.bulk_file_github_issues",
    "poam.auto_remediate.remediated",
    "poam.auto_remediate.approved",
    "poam.auto_remediate.skipped",
    "poam.auto_remediate.failed",
    # Cross-Agency Data Transfer events (NIST AU-2, AU-9)
    "cross_agency_transfer_initiated",
    "cross_agency_transfer_completed",
    "cross_agency_transfer_failed",
    "cross_agency_transfer_rejected",
    # Alert dispatcher events (secure log store integration)
    "pir_alert_generated",
    # ANVIL build validation events
    "code.validation",
    # Agentic Research Pipeline — Governance Layer (Phase AADC-GOV)
    "pipeline.run_started",
    "pipeline.run_completed",
    "pipeline.run_failed",
    "pipeline.confidence_gate_passed",
    "pipeline.confidence_gate_failed",
    "pipeline.output_validated",
    "pipeline.output_rejected",
    # SIPA — software integrity HITL promote/reject events
    "integrity_promoted",
    "integrity_rejected",

    # GovCon subsystem (tools/govcon/*). These are written by 31 modules
    # whose _audit() had never succeeded: the CHECK constraint admitted none
    # of them, and every call site swallows the failure. Adding them here is
    # what makes the constraint accept them — init_icdev_db.py now generates
    # the CHECK from this tuple rather than keeping its own copy.
    "govcon.ai_clause",
    "govcon.amendment",
    "govcon.award_tracking",
    "govcon.capability_map",
    "govcon.capture_strategy",
    "govcon.clause_risk",
    "govcon.cluster",
    "govcon.cmmc_supply_chain",
    "govcon.color_review",
    "govcon.compliance_matrix",
    "govcon.extract",
    "govcon.far_dfars_verification",
    "govcon.gap_analysis",
    "govcon.key_personnel",
    "govcon.knowledge_base",
    "govcon.lifecycle",
    "govcon.pipeline",
    "govcon.procurement_vehicle",
    "govcon.program_bridge",
    "govcon.question_export",
    "govcon.question_generator",
    "govcon.reflex_sandbox",
    "govcon.response_draft",
    "govcon.scan",
    "govcon.telco_rfp",
    "govcon.win_theme",

    # GovCon modules whose _audit() takes event_type as a parameter, so the
    # literal lives at the call site rather than in the function. Those call
    # sites pass verb-level names without a govcon. prefix, and none of them
    # were admitted either — the same dead-write bug as the block above, just
    # invisible to a search for "govcon.*". They are kept verbatim rather than
    # folded into per-module govcon.* types because the fine-grained verb is
    # only carried here: these modules' `action` argument holds a rendered
    # sentence ("pWin=68% for OPP-1"), not the operation name. Verb-level
    # namespacing already matches poam.*, pipeline.*, chat.* and review_board.*
    # above.
    "benchmark.store",
    "bid_scoring.calibrate",
    "bid_scoring.compute_weights",
    "blueprint.exported",
    "blueprint.generated",
    "capability.enrich",
    "cost_volume.generate",
    "gsa_rate.add",
    "gsa_rate.seed",
    "idiq.team_created",
    "igce.add_line",
    "igce.calibrate",
    "igce.category_updated",
    "igce.created",
    "igce.generate",
    "igce.updated",
    "lcat.store",
    "market_rate.add",
    "procurement.created",
    "procurement.linked_to_initiative",
    "proposal.maturity_assessed",
    "pwin.compute",
    "quote.created",
    "talent.add_posting",
    "talent.velocity",
    "teaming.add_partner",
    "teaming.oci_screen",
    "teaming.score_partner",

    # LLM context compression (tools/llm/compression/context_compressor.py).
    # Its write had omitted event_type entirely; "config_changed" was the
    # nearest admitted type but records a compression as a configuration
    # change, which is not what happened.
    "llm.context_compressed",

    # SBOM Distribution and Delivery (sbx-gov-02,
    # tools/compliance/sbom_distribution.py). "sbom_generated" records that an
    # artifact was produced; these record who it was released to. The denial is
    # logged as deliberately as the grant, because the 2026 element allows
    # access control to limit sharing with unauthorized parties but not to
    # obstruct authorized ones — which is only auditable if both legs are kept.
    # Constraint rebuilt by migration 20260808071512.
    "sbom.distributed",
    "sbom.distribution_denied",

    # DIC human-in-the-loop dispositions (cef-ui-03). One event type, actions
    # namespaced per surface — docmod_finding.accepted|rejected,
    # dic_suggestion.accepted|rejected, dic_version.approved|rejected — following
    # the migration_canvas precedent above rather than six types. These are the
    # record that a HUMAN, not the sweep, decided a resolve-produced proposal.
    "dic.hitl_decision",

    # tools/document_intelligence/acoic.py::_review_fragment has written every
    # human SSP-fragment decision under this type since it was authored, and the
    # type was never admitted to the vocabulary — so log_event raised ValueError
    # on the very first line, before touching the database, on EVERY approval.
    # It is called with raise_on_error=True precisely so an unaudited approval
    # cannot stand; but blueprint.py's `except Exception:` fallback caught that
    # refusal and did the UPDATE anyway, unaudited. Same shape as
    # migration_canvas: the write was there, the vocabulary was not, and the
    # best-effort handler downstream made the rejection invisible.
    # Constraint rebuilt by migration 20260819021003.
    "dic.ssp_fragment.review",

    # The enumerated `restore` tier (autonomy-act-03,
    # tools/awareness/restore_acts.py). One type; the act and the phase are in
    # `action` — restore.<act>.intent is written BEFORE the act with
    # raise_on_error=True, so an act whose row cannot be written does not run,
    # and restore.<act>.<outcome> after it. An unaudited automatic repair is
    # indistinguishable from drift. Constraint rebuilt by migration
    # 20260821045946.
    "awareness.restore_act",
    # Zero-trust stub gate (rmf-zt-01). ICDEV_ZT_ALLOW_STUB decides whether an
    # unverifiable device posture / PDP answer may be honored, and until now it
    # decided that silently. One event type with the surface namespaced in
    # `action` (device_compliance_scanner.stub_honored,
    # device_compliance_scanner.fail_closed), following the migration_canvas
    # precedent rather than one type per adapter. Written by
    # tools/security/stub_gate.py::record_stub_decision.
    "zt.stub_gate",
    # PRE-EXISTING DRIFT, found while rebuilding the CHECK for zt.stub_gate
    # (rmf-zt-01). The DEPLOYED constraint admitted these two and this constant
    # did not — and unlike every other caller here, tools/compliance/
    # cato_monitor.py, cato_scheduler.py and oscal_generator.py write them with
    # a RAW `INSERT INTO audit_trail`, bypassing log_event entirely. So the
    # CHECK is the only thing letting those three writers through, and
    # regenerating it from a constant that omitted them would have started
    # REFUSING three live compliance audit writers. The constant was the stale
    # copy, not the constraint. (Zero rows today only means those modules have
    # not run on this database; converting them onto log_event is a separate
    # card, and adding the names here does not make that unnecessary.)
    "cato_evidence_collected",
    "oscal_generated",
)

EVENT_TYPE_CONSTRAINT = "audit_trail_event_type_check"


def event_type_check_sql() -> str:
    """The CHECK body admitting exactly VALID_EVENT_TYPES.

    Single source for every place the constraint is spelled out, so the
    constant and the constraint cannot drift. Event types are identifiers from
    a hardcoded tuple in this module, never user input, so inlining them as
    literals is safe — and DDL cannot be parameterised anyway.
    """
    values = ", ".join(f"'{t}'" for t in VALID_EVENT_TYPES)
    return f"CHECK (event_type IN ({values}))"


def rebuild_event_type_constraint(conn) -> bool:
    """Regenerate audit_trail's event_type CHECK from VALID_EVENT_TYPES.

    Call this from a migration whenever an event type is added. Returns True
    when the constraint was rebuilt, False on SQLite — SQLite cannot ALTER a
    CHECK, and rebuilding audit_trail there would mean copying an append-only,
    hash-chained table. Fresh SQLite databases get the generated constraint
    from init_icdev_db.py instead, and existing ones are dev-local.

    The connection is caller-owned: migrations run inside a larger transaction
    and closing it here would break the rest of their run.
    """
    if getattr(conn, "_backend", "") != "postgresql":
        return False
    conn.execute(
        f"ALTER TABLE audit_trail DROP CONSTRAINT IF EXISTS {EVENT_TYPE_CONSTRAINT}"
    )
    conn.execute(
        f"ALTER TABLE audit_trail ADD CONSTRAINT {EVENT_TYPE_CONSTRAINT} "
        f"{event_type_check_sql()}"
    )
    conn.commit()
    return True


def _chain_columns(conn, row_values: dict, placeholder: str):
    """Reserve an id and compute this row's chain columns, or degrade to none.

    Returns ``(reserved_id, (hash, previous_hash, signature))`` when the row can
    be chained, or ``(None, None)`` when it cannot — a database that never ran
    migration 149, or a signing/hashing failure. See log_event's docstring for
    why that degrades instead of raising.
    """
    try:
        if not _chain.has_chain_columns(conn):
            return None, None
        # Reserve, read-back and INSERT are one critical section: two writers
        # that both chained off the same predecessor would fork the chain.
        _chain.serialize_chain_writes(conn)
        reserved_id = _chain.reserve_entry_id(conn, placeholder)
        if reserved_id is None:
            return None, None
        return reserved_id, _chain.chain_insert_values(
            conn, reserved_id, row_values, placeholder
        )
    except Exception as exc:
        logger.warning(
            "audit chain columns unavailable for this row (%s); writing it "
            "unchained rather than dropping the event",
            exc,
        )
        return None, None


def log_event(
    event_type: str,
    actor: str,
    action: str,
    project_id: str = None,
    details: dict = None,
    affected_files: list = None,
    classification: str = "CUI",
    ip_address: str = None,
    session_id: str = None,
    db_path: Path = None,
    conn=None,
    raise_on_error: bool = False,
) -> int:
    """Write an immutable audit trail entry, hash-chained. Returns the entry ID.

    The row is linked into the audit hash chain: ``hash`` over the
    migration-149 field recipe, ``previous_hash`` pointing at the row before it,
    and ``signature`` over the hash. See :mod:`tools.audit.chain` for the
    mechanics — id reservation, the critical section that keeps two concurrent
    writers from forking the chain, and the cutover marker.

    **A chain failure is NOT fatal, and that is a deliberate choice.** If the
    hash or signature cannot be computed, the row is still written, with NULL
    chain columns, and the failure is logged. The alternative — refusing the
    write — trades a *detectable* problem for an *undetectable* one: an
    unchained row is visible to ``provenance_verifier`` and is exactly what
    ``chain_anchor.periodic_anchor`` already scans for, whereas an audit event
    that was never recorded leaves nothing behind to notice. NIST AU-2/AU-12
    want the event captured; the chain is how it is corroborated, not a
    precondition for capturing it. ``raise_on_error=True`` overrides this for
    critical-path callers, as it does for every other failure here.

    A swallowed write burns its reserved id and leaves a GAP. That is handled
    rather than prevented: ``previous_hash`` is defined as the hash of the row
    at ``id - 1``, the same rule the verifier applies, so at a gap both sides
    independently fall back to ``GENESIS_HASH`` and the chain restarts instead
    of falsely reporting tampering.

    Databases that have not run migration 149 keep the original 9-column
    INSERT — the columns are feature-detected, never assumed.

    Args:
        conn: Optional existing DB connection. When provided, the connection
            is NOT closed by this function, allowing the caller to manage
            the transaction scope (e.g. atomic blocks).
        raise_on_error: When True, re-raise database errors instead of
            silently returning -1. Use for critical-path writes (e.g. PIR
            alerts) where silent loss violates data integrity.
    """
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"Invalid event_type '{event_type}'. Valid: {VALID_EVENT_TYPES}")

    # Auto-populate session_id from correlation context (D149)
    if session_id is None:
        try:
            from tools.resilience.correlation import get_correlation_id

            session_id = get_correlation_id()
        except ImportError:
            pass

    close_conn = conn is None
    if close_conn:
        if db_path is not None:
            # Explicit db_path provided (e.g. test isolation): use SQLite directly
            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(str(db_path))
            conn.row_factory = _sqlite3.Row
            placeholder = "?"
        else:
            conn = get_connection()
            from tools.db.storage import sql_placeholder as _ph
            placeholder = _ph(conn)
    else:
        from tools.db.storage import sql_placeholder as _ph
        placeholder = _ph(conn)

    # The nine columns the audit row itself is made of. `details` is serialised
    # here, not at hash time, because the verifier hashes the string it reads
    # back out of the column — the two must be the same bytes.
    row_values = {
        "project_id": project_id,
        "event_type": event_type,
        "actor": actor,
        "action": action,
        "details": json.dumps(details) if details else None,
        "affected_files": json.dumps(affected_files) if affected_files else None,
        "classification": classification,
        "ip_address": ip_address,
        "session_id": session_id,
    }
    columns = list(row_values)
    params = [row_values[k] for k in columns]
    reserved_id = None

    c = conn.cursor()
    try:
        reserved_id, chain_values = _chain_columns(conn, row_values, placeholder)
        if chain_values is not None:
            # Explicit id: the id is the first field of the hash recipe, and an
            # append-only table gives no second chance to fill the hash in.
            columns = ["id"] + columns + list(_chain.CHAIN_COLUMNS)
            params = [reserved_id] + params + list(chain_values)

        c.execute(
            f"""INSERT INTO audit_trail ({", ".join(columns)})
               VALUES ({", ".join([placeholder] * len(columns))})""",
            tuple(params),
        )
        conn.commit()
        entry_id = reserved_id if chain_values is not None else c.lastrowid
        if chain_values is not None:
            _chain.record_chain_start(conn, entry_id, placeholder)
            conn.commit()
    except Exception:
        # Audit logging is non-fatal by default — never let an audit write
        # failure break business logic (e.g. FK violation when project_id not
        # in projects table).  For critical-path writes raise_on_error=True
        # surfaces the failure so alerts are not silently lost.
        try:
            conn.rollback()
        except Exception:
            pass
        if raise_on_error:
            raise
        entry_id = -1
    finally:
        if close_conn:
            conn.close()
    return entry_id


def atomic_log_event(
    event_type: str,
    actor: str,
    action: str,
    project_id: str = None,
    details: dict = None,
    affected_files: list = None,
    classification: str = "CUI",
    ip_address: str = None,
    session_id: str = None,
    db_path: Path = None,
    fallback_path: Path = None,
) -> dict:
    """Atomically write an audit trail entry with durable fallback.

    For SQLite, opens the connection with BEGIN IMMEDIATE to prevent
    writer starvation and ensure the write is truly atomic.  If the
    database write fails after rollback, the event payload is appended
    to a dead-letter JSONL file so alerts are never silently lost.

    Returns:
        {"status": "persisted", "entry_id": int} on success,
        {"status": "fallback", "fallback_path": str, "error": str} on failure.
    """
    fallback_path = fallback_path or ATOMIC_FALLBACK_PATH
    payload = {
        "event_type": event_type,
        "actor": actor,
        "action": action,
        "project_id": project_id,
        "details": details,
        "affected_files": affected_files,
        "classification": classification,
        "ip_address": ip_address,
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    conn = None
    try:
        if db_path is not None:
            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(str(db_path))
            conn.row_factory = _sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
        entry_id = log_event(
            event_type=event_type,
            actor=actor,
            action=action,
            project_id=project_id,
            details=details,
            affected_files=affected_files,
            classification=classification,
            ip_address=ip_address,
            session_id=session_id,
            db_path=None,
            conn=conn,
            raise_on_error=True,
        )
        return {"status": "persisted", "entry_id": entry_id}
    except Exception as exc:
        error = str(exc)
        try:
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            with open(fallback_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, default=str) + "\n")
        except Exception as write_err:
            error += f" | fallback_write_failed: {write_err}"
        return {"status": "fallback", "fallback_path": str(fallback_path), "error": error}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="Log an audit trail event")
    parser.add_argument("--event", required=True, choices=VALID_EVENT_TYPES, help="Event type")
    parser.add_argument("--actor", required=True, help="Who performed the action")
    parser.add_argument("--action", required=True, help="Human-readable description")
    parser.add_argument("--project-id", "--project", help="Project ID", dest="project_id")
    parser.add_argument("--details", help="JSON details string")
    parser.add_argument("--files", help="Comma-separated affected file paths")
    parser.add_argument("--classification", default="CUI", help="Classification marking")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    details = json.loads(args.details) if args.details else None
    affected_files = args.files.split(",") if args.files else None

    entry_id = log_event(
        event_type=args.event,
        actor=args.actor,
        action=args.action,
        project_id=args.project_id,
        details=details,
        affected_files=affected_files,
        classification=args.classification,
    )
    print(f"Audit entry #{entry_id} logged: [{args.event}] {args.action}")


if __name__ == "__main__":
    main()
