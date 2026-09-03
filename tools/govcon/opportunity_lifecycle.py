#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Opportunity Lifecycle Manager — Workflow Discipline Engine integration for Proposal Genesis (D-WF-1, SS3.3).

Provides structured PLAN-APPLY-UNIFY lifecycle management per opportunity,
acceptance criteria verification for phase transitions, reconciliation
tracking for planned-vs-actual reflex execution, handoff document generation,
and cross-opportunity next-action prioritisation.

Integrates directly with the ``workflow_loops``, ``workflow_acceptance_criteria``,
``workflow_reconciliations`` and ``workflow_handoffs`` tables created by Phase 66.
All inserts are append-only where required (NIST AU compliance, D6).

Usage:
    python tools/govcon/opportunity_lifecycle.py --create-loop --opportunity-id "opp-xxx" --title "My Opp" --json
    python tools/govcon/opportunity_lifecycle.py --verify --opportunity-id "opp-xxx" --from-phase CAPTURE --to-phase EXTRACT --json
    python tools/govcon/opportunity_lifecycle.py --reconcile --opportunity-id "opp-xxx" --reflex discover --expected 10 --actual 8 --json
    python tools/govcon/opportunity_lifecycle.py --handoff --opportunity-id "opp-xxx" --json
    python tools/govcon/opportunity_lifecycle.py --next-action --json
    python tools/govcon/opportunity_lifecycle.py --status --opportunity-id "opp-xxx" --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path bootstrapping
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection, column_exists  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.opportunity_lifecycle")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
HANDOFF_DIR = _ROOT / "data" / "proposal_genesis" / "handoffs"

# Phase transition acceptance criteria definitions (Gherkin-style)
TRANSITION_CRITERIA: List[Dict[str, str]] = [
    {
        "from": "CAPTURE",
        "to": "EXTRACT",
        "given": "opportunity discovered from SAM.gov",
        "when": "SAM.gov data is complete (title, agency, response_date populated)",
        "then": "SAM.gov data complete",
    },
    {
        "from": "EXTRACT",
        "to": "MAP",
        "given": "shall/must statements extracted from solicitation",
        "when": "shall statement count is evaluated",
        "then": "Shall statement count >= 5",
    },
    {
        "from": "MAP",
        "to": "DRAFT",
        "given": "capabilities mapped to extracted requirements",
        "when": "capability coverage is computed from compliance matrix",
        "then": "Capability coverage >= 60%",
    },
    {
        "from": "DRAFT",
        "to": "POLISH",
        "given": "section drafts generated for the opportunity",
        "when": "all drafts are checked for substantive content",
        "then": "No stub sections (all SUBSTANTIVE)",
    },
    {
        "from": "POLISH",
        "to": "DECIDE",
        "given": "section drafts polished via WriteGuard quality check",
        "when": "average quality score is evaluated",
        "then": "Average quality score >= 70",
    },
    {
        "from": "DECIDE",
        "to": "DELIVER",
        "given": "bid/no-bid analysis completed",
        "when": "human bid approval is checked",
        "then": "Human bid approval received",
    },
]

# Active opportunity statuses (used by Proposal Genesis reflexes)
ACTIVE_STATUSES = ("tracking", "drafting", "reviewing")

# Next-action priority weights (D-WF-2 adapted)
WEIGHT_STALENESS = 0.30
WEIGHT_DEADLINE = 0.20
WEIGHT_QUALITY_GAP = 0.25
WEIGHT_LOOP_STATE = 0.15
WEIGHT_HANDOFF_AGE = 0.10

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_ID_COUNTER = 0


def _now() -> str:
    """UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix: str) -> str:
    """Generate a prefixed unique ID."""
    global _ID_COUNTER
    _ID_COUNTER += 1
    ts = _now()
    h = hashlib.sha256(f"{ts}-{_ID_COUNTER}-{id(object())}".encode()).hexdigest()[:12]
    return f"{prefix}-{h}"


def _get_db():
    """Return a DB connection via the storage abstraction layer."""
    conn = get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass  # PostgreSQL doesn't support PRAGMA
    return conn


def _audit(conn, action: str, details: str = "", project_id: str = "") -> None:
    """Append-only audit trail entry (NIST AU, D6)."""
    try:
        conn.execute(
            "INSERT INTO audit_trail "
            "(created_at, event_type, actor, action, details, session_id, project_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _now(),
                "govcon.lifecycle",
                "opportunity-lifecycle",
                action,
                details,
                "proposal-genesis",
                project_id,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def _ensure_tables(conn) -> None:
    """Ensure workflow tables exist (graceful — Phase 66 likely created them)."""
    stmts = [
        """CREATE TABLE IF NOT EXISTS workflow_loops (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            phase_name TEXT NOT NULL,
            loop_type TEXT DEFAULT 'build',
            status TEXT DEFAULT 'planning',
            plan_summary TEXT,
            boundaries TEXT,
            task_count INTEGER DEFAULT 0,
            tasks_completed INTEGER DEFAULT 0,
            acceptance_criteria_count INTEGER DEFAULT 0,
            acceptance_pass_count INTEGER DEFAULT 0,
            acceptance_fail_count INTEGER DEFAULT 0,
            acceptance_skip_count INTEGER DEFAULT 0,
            planned_at TEXT,
            apply_started_at TEXT,
            apply_completed_at TEXT,
            unify_started_at TEXT,
            closed_at TEXT,
            created_by TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS workflow_acceptance_criteria (
            id TEXT PRIMARY KEY,
            loop_id TEXT NOT NULL,
            criterion_number INTEGER NOT NULL,
            given_text TEXT,
            when_text TEXT,
            then_text TEXT,
            bdd_story_id TEXT,
            status TEXT DEFAULT 'pending',
            evidence TEXT,
            verified_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (loop_id) REFERENCES workflow_loops(id)
        )""",
        """CREATE TABLE IF NOT EXISTS workflow_reconciliations (
            id TEXT PRIMARY KEY,
            loop_id TEXT NOT NULL,
            planned_tasks INTEGER DEFAULT 0,
            completed_tasks INTEGER DEFAULT 0,
            deviations TEXT,
            lessons_learned TEXT,
            process_checks TEXT,
            required_processes_invoked INTEGER DEFAULT 0,
            required_processes_total INTEGER DEFAULT 0,
            overall_result TEXT,
            classification TEXT DEFAULT 'CUI',
            reconciled_at TEXT NOT NULL,
            FOREIGN KEY (loop_id) REFERENCES workflow_loops(id)
        )""",
        """CREATE TABLE IF NOT EXISTS workflow_handoffs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            loop_id TEXT,
            loop_status TEXT,
            chat_context_id TEXT,
            decisions_made TEXT,
            blockers TEXT,
            next_actions TEXT,
            context_summary TEXT,
            handoff_to TEXT,
            created_by TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL
        )""",
    ]
    for stmt in stmts:
        try:
            conn.execute(stmt)
        except Exception:
            pass
    try:
        conn.commit()
    except Exception:
        pass


def _ensure_workflow_loop_id_column(conn) -> bool:
    """Add workflow_loop_id column to proposal_opportunities if missing."""
    try:
        # Backend-aware column probe — works on PG + SQLite without translate_sql.
        if column_exists(conn, "proposal_opportunities", "workflow_loop_id"):
            return True
        conn.execute("ALTER TABLE proposal_opportunities ADD COLUMN workflow_loop_id TEXT")
        conn.commit()
        return True
    except Exception:
        return False


def _get_loop_id_for_opp(conn, opportunity_id: str) -> Optional[str]:
    """Retrieve the workflow_loop_id for an opportunity."""
    try:
        row = conn.execute(
            "SELECT workflow_loop_id FROM proposal_opportunities WHERE id = %s",
            (opportunity_id,),
        ).fetchone()
        if not row:
            return None
        return row["workflow_loop_id"] if isinstance(row, dict) else row[0]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def create_opportunity_loop(
    opportunity_id: str,
    title: str,
    sam_gov_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a workflow loop for a new opportunity.

    Inserts into ``workflow_loops``, adds 6 acceptance criteria (one per
    phase transition), and stores the ``loop_id`` back on the opportunity row.

    Returns:
        Dict with ``loop_id`` on success, ``error`` key on failure.
    """
    conn = _get_db()
    try:
        _ensure_tables(conn)
        _ensure_workflow_loop_id_column(conn)

        # Check opportunity exists
        opp = conn.execute(
            "SELECT id, title FROM proposal_opportunities WHERE id = %s",
            (opportunity_id,),
        ).fetchone()
        if not opp:
            return {"error": f"Opportunity {opportunity_id} not found"}

        project_id = f"pg-{opportunity_id}"
        phase_name = (title or "Untitled Opportunity")[:100]
        loop_id = _gen_id("wl")
        now = _now()

        # Insert workflow loop
        conn.execute(
            """INSERT INTO workflow_loops
               (id, project_id, phase_name, loop_type, status,
                created_by, created_at, acceptance_criteria_count)
               VALUES (%s, %s, %s, 'build', 'planning', %s, %s, %s)""",
            (loop_id, project_id, phase_name, "proposal-genesis", now, len(TRANSITION_CRITERIA)),
        )

        # Insert 6 acceptance criteria
        for idx, tc in enumerate(TRANSITION_CRITERIA, 1):
            crit_id = _gen_id("wac")
            conn.execute(
                """INSERT INTO workflow_acceptance_criteria
                   (id, loop_id, criterion_number, given_text, when_text,
                    then_text, status, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)""",
                (crit_id, loop_id, idx, tc["given"], tc["when"], tc["then"], now),
            )

        # Store loop_id on opportunity row
        conn.execute(
            "UPDATE proposal_opportunities SET workflow_loop_id = %s WHERE id = %s",
            (loop_id, opportunity_id),
        )

        conn.commit()

        _audit(
            conn,
            f"Created lifecycle loop {loop_id} for opportunity {opportunity_id}",
            json.dumps(
                {
                    "loop_id": loop_id,
                    "opportunity_id": opportunity_id,
                    "sam_gov_id": sam_gov_id,
                    "criteria_count": len(TRANSITION_CRITERIA),
                }
            ),
            project_id,
        )
        conn.commit()

        return {
            "loop_id": loop_id,
            "project_id": project_id,
            "opportunity_id": opportunity_id,
            "phase_name": phase_name,
            "status": "planning",
            "criteria_count": len(TRANSITION_CRITERIA),
            "created_at": now,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase transition verification
# ---------------------------------------------------------------------------


def _check_capture_to_extract(conn, opportunity_id: str) -> Tuple[bool, str]:
    """CAPTURE->EXTRACT: SAM.gov data complete (title, agency, response_date)."""
    row = conn.execute(
        "SELECT title, agency, due_date FROM proposal_opportunities WHERE id = %s",
        (opportunity_id,),
    ).fetchone()
    if not row:
        return False, "Opportunity not found"
    title = (row["title"] if isinstance(row, dict) else row[0]) or ""
    agency = (row["agency"] if isinstance(row, dict) else row[1]) or ""
    due_date = (row["due_date"] if isinstance(row, dict) else row[2]) or ""
    missing = []
    if not title.strip():
        missing.append("title")
    if not agency.strip():
        missing.append("agency")
    if not due_date.strip():
        missing.append("due_date")
    if missing:
        return False, f"Missing fields: {', '.join(missing)}"
    return True, f"All required fields present (title={title[:40]}, agency={agency[:40]})"


def _check_extract_to_map(conn, opportunity_id: str) -> Tuple[bool, str]:
    """EXTRACT->MAP: shall statement count >= 5."""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM rfp_shall_statements WHERE proposal_opportunity_id = %s",
        (opportunity_id,),
    ).fetchone()
    count = row["cnt"] if isinstance(row, dict) else row[0]
    if count >= 5:
        return True, f"Shall statement count: {count} (>= 5)"
    return False, f"Shall statement count: {count} (< 5 required)"


def _check_map_to_draft(conn, opportunity_id: str) -> Tuple[bool, str]:
    """MAP->DRAFT: capability coverage >= 60%.

    Reads proposal_compliance_matrix, THE ONE matrix (rmf-rfp-01). This used
    to read pg_compliance_matrix, which held 0 rows for every opportunity, so
    the gate reported "No compliance matrix entries found" regardless of what
    the auto-populate route had written.
    """
    rows = conn.execute(
        "SELECT compliance_status FROM proposal_compliance_matrix WHERE opportunity_id = %s",
        (opportunity_id,),
    ).fetchall()
    if not rows:
        return False, "No compliance matrix entries found"
    total = len(rows)
    addressed = sum(
        1 for r in rows if (r["compliance_status"] if isinstance(r, dict) else r[0]) in ("compliant", "partial")
    )
    coverage = addressed / total if total > 0 else 0.0
    if coverage >= 0.60:
        return True, f"Capability coverage: {coverage:.0%} (>= 60%)"
    return False, f"Capability coverage: {coverage:.0%} (< 60% required)"


def _check_draft_to_polish(conn, opportunity_id: str) -> Tuple[bool, str]:
    """DRAFT->POLISH: all drafts substantive (word_count > 50)."""
    rows = conn.execute(
        "SELECT id, draft_content FROM proposal_section_drafts WHERE opportunity_id = %s",
        (opportunity_id,),
    ).fetchall()
    if not rows:
        return False, "No section drafts found"
    stub_ids = []
    for r in rows:
        content = (r["draft_content"] if isinstance(r, dict) else r[1]) or ""
        word_count = len(content.split())
        draft_id = r["id"] if isinstance(r, dict) else r[0]
        if word_count <= 50:
            stub_ids.append(draft_id)
    if stub_ids:
        return False, f"{len(stub_ids)} stub section(s) with <= 50 words: {stub_ids[:5]}"
    return True, f"All {len(rows)} section(s) are substantive (> 50 words each)"


def _check_polish_to_decide(conn, opportunity_id: str) -> Tuple[bool, str]:
    """POLISH->DECIDE: average quality composite_score >= 0.70."""
    rows = conn.execute(
        "SELECT composite_score FROM pg_proposal_quality_scores WHERE opportunity_id = %s",
        (opportunity_id,),
    ).fetchall()
    if not rows:
        return False, "No quality scores found"
    scores = [(r["composite_score"] if isinstance(r, dict) else r[0]) for r in rows]
    # Filter out None values
    scores = [s for s in scores if s is not None]
    if not scores:
        return False, "All quality scores are NULL"
    avg_score = sum(scores) / len(scores)
    if avg_score >= 0.70:
        return True, f"Average quality score: {avg_score:.2f} (>= 0.70)"
    return False, f"Average quality score: {avg_score:.2f} (< 0.70 required)"


def _check_decide_to_deliver(conn, opportunity_id: str) -> Tuple[bool, str]:
    """DECIDE->DELIVER: human bid approval received (recommendation = 'bid')."""
    row = conn.execute(
        "SELECT decision, decided_by FROM pg_bid_decisions WHERE opportunity_id = %s ORDER BY created_at DESC LIMIT 1",
        (opportunity_id,),
    ).fetchone()
    if not row:
        return False, "No bid decision found"
    decision = (row["decision"] if isinstance(row, dict) else row[0]) or ""
    decided_by = (row["decided_by"] if isinstance(row, dict) else row[1]) or ""
    if decision.lower() == "bid":
        return True, f"Bid decision: {decision} (by {decided_by})"
    return False, f"Bid decision: {decision} (need 'bid', got '{decision}')"


_TRANSITION_CHECKERS = {
    ("CAPTURE", "EXTRACT"): _check_capture_to_extract,
    ("EXTRACT", "MAP"): _check_extract_to_map,
    ("MAP", "DRAFT"): _check_map_to_draft,
    ("DRAFT", "POLISH"): _check_draft_to_polish,
    ("POLISH", "DECIDE"): _check_polish_to_decide,
    ("DECIDE", "DELIVER"): _check_decide_to_deliver,
}


def verify_phase_transition(
    opportunity_id: str,
    from_phase: str,
    to_phase: str,
) -> Dict[str, Any]:
    """Check if acceptance criteria for a phase transition are met.

    Queries actual DB data to verify the transition conditions, then updates
    the corresponding ``workflow_acceptance_criteria`` row.

    Returns:
        Dict with ``transition``, ``passed``, ``details``.
    """
    key = (from_phase.upper(), to_phase.upper())
    checker = _TRANSITION_CHECKERS.get(key)
    if not checker:
        return {
            "error": f"Unknown transition: {from_phase} -> {to_phase}",
            "valid_transitions": [f"{k[0]}->{k[1]}" for k in _TRANSITION_CHECKERS],
        }

    conn = _get_db()
    try:
        _ensure_tables(conn)

        passed, details = checker(conn, opportunity_id)
        status = "pass" if passed else "fail"
        now = _now()

        # Find and update the matching acceptance criterion
        loop_id = _get_loop_id_for_opp(conn, opportunity_id)
        criterion_updated = False
        if loop_id:
            # Match by then_text prefix from our TRANSITION_CRITERIA
            for tc in TRANSITION_CRITERIA:
                if tc["from"] == key[0] and tc["to"] == key[1]:
                    row = conn.execute(
                        "SELECT id FROM workflow_acceptance_criteria WHERE loop_id = %s AND then_text = %s",
                        (loop_id, tc["then"]),
                    ).fetchone()
                    if row:
                        crit_id = row["id"] if isinstance(row, dict) else row[0]
                        conn.execute(
                            "UPDATE workflow_acceptance_criteria "
                            "SET status = %s, evidence = %s, verified_at = %s "
                            "WHERE id = %s",
                            (status, details, now, crit_id),
                        )
                        # Update acceptance counts on loop
                        counts = conn.execute(
                            "SELECT "
                            "  SUM(CASE WHEN status='pass' THEN 1 ELSE 0 END) as p, "
                            "  SUM(CASE WHEN status='fail' THEN 1 ELSE 0 END) as f, "
                            "  SUM(CASE WHEN status='skip' THEN 1 ELSE 0 END) as s "
                            "FROM workflow_acceptance_criteria WHERE loop_id = %s",
                            (loop_id,),
                        ).fetchone()
                        if counts:
                            pc = counts["p"] if isinstance(counts, dict) else counts[0]
                            fc = counts["f"] if isinstance(counts, dict) else counts[1]
                            sc = counts["s"] if isinstance(counts, dict) else counts[2]
                            conn.execute(
                                "UPDATE workflow_loops SET "
                                "acceptance_pass_count = %s, acceptance_fail_count = %s, "
                                "acceptance_skip_count = %s WHERE id = %s",
                                (pc or 0, fc or 0, sc or 0, loop_id),
                            )
                        criterion_updated = True
                    break

        conn.commit()

        _audit(
            conn,
            f"Phase transition verified: {from_phase}->{to_phase} = {status}",
            json.dumps(
                {
                    "opportunity_id": opportunity_id,
                    "transition": f"{from_phase}->{to_phase}",
                    "passed": passed,
                    "details": details,
                }
            ),
            f"pg-{opportunity_id}",
        )
        conn.commit()

        return {
            "transition": f"{from_phase}->{to_phase}",
            "passed": passed,
            "status": status,
            "details": details,
            "criterion_updated": criterion_updated,
            "loop_id": loop_id,
            "verified_at": now,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def reconcile_reflex(
    opportunity_id: str,
    reflex_name: str,
    expected_count: int,
    actual_count: int,
) -> Dict[str, Any]:
    """Record planned-vs-actual after reflex execution.

    Calculates gap percentage and severity, then inserts into
    ``workflow_reconciliations`` (append-only, NIST AU).

    Returns:
        Dict with ``severity``, ``gap_pct``, ``reconciliation_id``.
    """
    conn = _get_db()
    try:
        _ensure_tables(conn)

        loop_id = _get_loop_id_for_opp(conn, opportunity_id)
        if not loop_id:
            return {"error": f"No workflow loop found for opportunity {opportunity_id}"}

        gap_pct = abs(expected_count - actual_count) / max(expected_count, 1)
        if gap_pct < 0.10:
            severity = "minor"
        elif gap_pct < 0.30:
            severity = "moderate"
        else:
            severity = "major"

        recon_id = _gen_id("wr")
        now = _now()
        deviations = json.dumps(
            {
                "reflex": reflex_name,
                "expected": expected_count,
                "actual": actual_count,
                "gap_pct": round(gap_pct, 4),
                "severity": severity,
            }
        )

        overall = "success" if severity == "minor" else ("partial" if severity == "moderate" else "failed")

        conn.execute(
            """INSERT INTO workflow_reconciliations
               (id, loop_id, planned_tasks, completed_tasks, deviations,
                overall_result, classification, reconciled_at)
               VALUES (%s, %s, %s, %s, %s, %s, 'CUI', %s)""",
            (recon_id, loop_id, expected_count, actual_count, deviations, overall, now),
        )
        conn.commit()

        _audit(
            conn,
            f"Reconciled reflex {reflex_name}: {severity} ({gap_pct:.0%} gap)",
            deviations,
            f"pg-{opportunity_id}",
        )
        conn.commit()

        return {
            "reconciliation_id": recon_id,
            "loop_id": loop_id,
            "reflex": reflex_name,
            "expected": expected_count,
            "actual": actual_count,
            "gap_pct": round(gap_pct, 4),
            "severity": severity,
            "overall_result": overall,
            "reconciled_at": now,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Handoff generation
# ---------------------------------------------------------------------------


def generate_handoff(opportunity_id: str) -> Dict[str, Any]:
    """Generate a markdown handoff document for human review transition.

    Gathers opportunity details, shall count, capability coverage, draft
    quality scores, bid recommendation, and open deviations into a
    structured markdown document.

    Returns:
        Dict with ``handoff_id``, ``markdown``, ``file_path``.
    """
    conn = _get_db()
    try:
        _ensure_tables(conn)

        # Gather opportunity details
        opp = conn.execute(
            "SELECT id, title, agency, due_date, status, solicitation_number, "
            "naics_code, set_aside_type, estimated_value_low, estimated_value_high "
            "FROM proposal_opportunities WHERE id = %s",
            (opportunity_id,),
        ).fetchone()
        if not opp:
            return {"error": f"Opportunity {opportunity_id} not found"}

        opp_title = opp["title"] if isinstance(opp, dict) else opp[1]
        opp_agency = opp["agency"] if isinstance(opp, dict) else opp[2]
        opp_due = opp["due_date"] if isinstance(opp, dict) else opp[3]
        opp_status = opp["status"] if isinstance(opp, dict) else opp[4]
        opp_sol = opp["solicitation_number"] if isinstance(opp, dict) else opp[5]

        # Shall statement count
        shall_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM rfp_shall_statements WHERE proposal_opportunity_id = %s",
            (opportunity_id,),
        ).fetchone()
        shall_count = shall_row["cnt"] if isinstance(shall_row, dict) else shall_row[0]

        # Capability coverage
        cmatrix_rows = conn.execute(
            "SELECT compliance_status FROM proposal_compliance_matrix WHERE opportunity_id = %s",
            (opportunity_id,),
        ).fetchall()
        total_reqs = len(cmatrix_rows)
        addressed = sum(
            1
            for r in cmatrix_rows
            if (r["compliance_status"] if isinstance(r, dict) else r[0]) in ("compliant", "partial")
        )
        coverage = addressed / total_reqs if total_reqs > 0 else 0.0

        # Quality scores
        quality_rows = conn.execute(
            "SELECT composite_score FROM pg_proposal_quality_scores WHERE opportunity_id = %s",
            (opportunity_id,),
        ).fetchall()
        quality_scores = [
            (r["composite_score"] if isinstance(r, dict) else r[0])
            for r in quality_rows
            if (r["composite_score"] if isinstance(r, dict) else r[0]) is not None
        ]
        avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0

        # Bid recommendation
        bid_row = conn.execute(
            "SELECT decision, rationale, decided_by FROM pg_bid_decisions "
            "WHERE opportunity_id = %s ORDER BY created_at DESC LIMIT 1",
            (opportunity_id,),
        ).fetchone()
        bid_decision = "No decision yet"
        bid_rationale = ""
        if bid_row:
            bid_decision = bid_row["decision"] if isinstance(bid_row, dict) else bid_row[0]
            bid_rationale = (bid_row["rationale"] if isinstance(bid_row, dict) else bid_row[1]) or ""

        # Open deviations (reconciliation records with severity != minor)
        loop_id = _get_loop_id_for_opp(conn, opportunity_id)
        deviations: List[Dict[str, Any]] = []
        if loop_id:
            recon_rows = conn.execute(
                "SELECT deviations, overall_result, reconciled_at "
                "FROM workflow_reconciliations WHERE loop_id = %s "
                "ORDER BY reconciled_at DESC",
                (loop_id,),
            ).fetchall()
            for rr in recon_rows:
                dev_json = (rr["deviations"] if isinstance(rr, dict) else rr[0]) or "{}"
                try:
                    dev_data = json.loads(dev_json)
                except (json.JSONDecodeError, TypeError):
                    dev_data = {"raw": dev_json}
                if dev_data.get("severity", "minor") != "minor":
                    deviations.append(dev_data)

        # Acceptance criteria status
        criteria_summary: List[Dict[str, str]] = []
        if loop_id:
            ac_rows = conn.execute(
                "SELECT then_text, status, evidence "
                "FROM workflow_acceptance_criteria WHERE loop_id = %s "
                "ORDER BY criterion_number",
                (loop_id,),
            ).fetchall()
            for ac in ac_rows:
                criteria_summary.append(
                    {
                        "criterion": ac["then_text"] if isinstance(ac, dict) else ac[0],
                        "status": ac["status"] if isinstance(ac, dict) else ac[1],
                        "evidence": (ac["evidence"] if isinstance(ac, dict) else ac[2]) or "",
                    }
                )

        # Generate markdown
        md_lines = [
            "# CUI // SP-CTI",
            "",
            f"# Handoff: {opp_title}",
            "",
            "## Summary",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| Opportunity ID | `{opportunity_id}` |",
            f"| Solicitation | {opp_sol or 'N/A'} |",
            f"| Agency | {opp_agency or 'N/A'} |",
            f"| Due Date | {opp_due or 'N/A'} |",
            f"| Current Status | {opp_status or 'N/A'} |",
            "",
            "## Requirements",
            "",
            f"- **Shall Statements Extracted:** {shall_count}",
            f"- **Compliance Matrix Entries:** {total_reqs}",
            "",
            "## Coverage",
            "",
            f"- **Capability Coverage:** {coverage:.0%} ({addressed}/{total_reqs} addressed/partial)",
            "",
            "## Quality",
            "",
            f"- **Average Quality Score:** {avg_quality:.2f}",
            f"- **Quality Checks Completed:** {len(quality_scores)}",
            "",
            "## Recommendation",
            "",
            f"- **Bid Decision:** {bid_decision}",
        ]
        if bid_rationale:
            md_lines.append(f"- **Rationale:** {bid_rationale}")

        # Acceptance criteria section
        md_lines.extend(["", "## Acceptance Criteria", ""])
        if criteria_summary:
            md_lines.append("| Criterion | Status | Evidence |")
            md_lines.append("|-----------|--------|----------|")
            for cs in criteria_summary:
                status_icon = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}.get(cs["status"], "PENDING")
                md_lines.append(f"| {cs['criterion']} | {status_icon} | {cs['evidence'][:60]} |")
        else:
            md_lines.append("No acceptance criteria linked.")

        # Open issues
        md_lines.extend(["", "## Open Issues", ""])
        if deviations:
            for dv in deviations:
                md_lines.append(
                    f"- **{dv.get('reflex', 'unknown')}**: {dv.get('severity', '?')} deviation "
                    f"(expected {dv.get('expected', '?')}, actual {dv.get('actual', '?')}, "
                    f"gap {dv.get('gap_pct', 0):.0%})"
                )
        else:
            md_lines.append("No open deviations.")

        # Next steps
        md_lines.extend(
            [
                "",
                "## Next Steps",
                "",
                "1. Review this handoff document",
                "2. Verify acceptance criteria pass/fail status",
                "3. Address any open deviations before proceeding",
                "4. Approve or reject bid decision",
                "",
                "---",
                "",
                f"*Generated: {_now()} by opportunity-lifecycle*",
                "",
                "# CUI // SP-CTI",
            ]
        )

        markdown = "\n".join(md_lines)

        # Save to file
        HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
        file_path = HANDOFF_DIR / f"{opportunity_id}.md"
        file_path.write_text(markdown, encoding="utf-8", newline="")

        # Insert into workflow_handoffs
        handoff_id = _gen_id("wh")
        now = _now()
        conn.execute(
            """INSERT INTO workflow_handoffs
               (id, project_id, loop_id, loop_status, context_summary,
                decisions_made, blockers, next_actions,
                handoff_to, created_by, classification, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CUI', %s)""",
            (
                handoff_id,
                f"pg-{opportunity_id}",
                loop_id,
                opp_status,
                markdown[:2000],
                json.dumps({"bid_decision": bid_decision, "rationale": bid_rationale}),
                json.dumps([d.get("reflex", "unknown") for d in deviations]) if deviations else "[]",
                "Review handoff, verify criteria, approve bid decision",
                "human-reviewer",
                "opportunity-lifecycle",
                now,
            ),
        )
        conn.commit()

        _audit(
            conn,
            f"Generated handoff for opportunity {opportunity_id}",
            json.dumps({"handoff_id": handoff_id, "file_path": str(file_path)}),
            f"pg-{opportunity_id}",
        )
        conn.commit()

        return {
            "handoff_id": handoff_id,
            "opportunity_id": opportunity_id,
            "loop_id": loop_id,
            "file_path": str(file_path),
            "markdown": markdown,
            "created_at": now,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Next action recommender
# ---------------------------------------------------------------------------


def next_action_recommender(limit: int = 10) -> Dict[str, Any]:
    """Prioritise across all active opportunities using weighted scoring.

    Weights (D-WF-2 adapted for Proposal Genesis):
      - staleness (0.30): days since last reflex run
      - deadline_proximity (0.20): days until response_date (inverted)
      - quality_gap (0.25): 1.0 - avg_quality_score
      - loop_state (0.15): penalty if loop is stalled
      - handoff_age (0.10): days since handoff generated

    Returns:
        Dict with ``recommendations`` list sorted by priority descending.
    """
    conn = _get_db()
    try:
        _ensure_tables(conn)
        _ensure_workflow_loop_id_column(conn)

        opps = conn.execute(
            "SELECT id, title, agency, due_date, status, workflow_loop_id "
            "FROM proposal_opportunities "
            "WHERE status IN ('tracking', 'drafting', 'reviewing') "
            "ORDER BY due_date ASC",
        ).fetchall()

        if not opps:
            return {"recommendations": [], "count": 0}

        now_dt = datetime.now(timezone.utc)
        recommendations: List[Dict[str, Any]] = []

        for opp in opps:
            opp_id = opp["id"] if isinstance(opp, dict) else opp[0]
            opp_title = opp["title"] if isinstance(opp, dict) else opp[1]
            opp_agency = opp["agency"] if isinstance(opp, dict) else opp[2]
            opp_due = opp["due_date"] if isinstance(opp, dict) else opp[3]
            opp_status = opp["status"] if isinstance(opp, dict) else opp[4]
            loop_id = opp["workflow_loop_id"] if isinstance(opp, dict) else opp[5]

            # --- staleness: days since last audit event for this opp ---
            last_event = conn.execute(
                "SELECT MAX(created_at) as latest FROM audit_trail WHERE project_id = %s",
                (f"pg-{opp_id}",),
            ).fetchone()
            staleness_days = 999.0
            if last_event:
                latest = last_event["latest"] if isinstance(last_event, dict) else last_event[0]
                if latest:
                    try:
                        last_dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
                        staleness_days = (now_dt - last_dt).total_seconds() / 86400
                    except (ValueError, TypeError):
                        pass
            # Normalise staleness to 0..1 (30 days = 1.0)
            staleness_norm = min(staleness_days / 30.0, 1.0)

            # --- deadline proximity: days until due (inverted: closer = higher) ---
            deadline_norm = 0.5  # default if no due date
            if opp_due:
                try:
                    due_dt = datetime.fromisoformat(opp_due.replace("Z", "+00:00"))
                    days_until = (due_dt - now_dt).total_seconds() / 86400
                    # Closer deadline = higher priority, max at 1.0 when <= 0 days
                    deadline_norm = max(0.0, min(1.0, 1.0 - (days_until / 60.0)))
                except (ValueError, TypeError):
                    pass

            # --- quality gap: 1.0 - avg quality ---
            quality_rows = conn.execute(
                "SELECT composite_score FROM pg_proposal_quality_scores WHERE opportunity_id = %s",
                (opp_id,),
            ).fetchall()
            scores = [
                (r["composite_score"] if isinstance(r, dict) else r[0])
                for r in quality_rows
                if (r["composite_score"] if isinstance(r, dict) else r[0]) is not None
            ]
            avg_quality = sum(scores) / len(scores) if scores else 0.0
            quality_gap = 1.0 - avg_quality

            # --- loop state: penalty if stalled ---
            loop_penalty = 0.0
            loop_status = "none"
            if loop_id:
                lrow = conn.execute(
                    "SELECT status, apply_started_at FROM workflow_loops WHERE id = %s",
                    (loop_id,),
                ).fetchone()
                if lrow:
                    loop_status = lrow["status"] if isinstance(lrow, dict) else lrow[0]
                    if loop_status in ("planning",):
                        loop_penalty = 0.8  # stuck in planning
                    elif loop_status in ("applying",):
                        # Check if stalled in apply
                        apply_start = lrow["apply_started_at"] if isinstance(lrow, dict) else lrow[1]
                        if apply_start:
                            try:
                                apply_dt = datetime.fromisoformat(apply_start.replace("Z", "+00:00"))
                                apply_days = (now_dt - apply_dt).total_seconds() / 86400
                                if apply_days > 7:
                                    loop_penalty = 0.6
                            except (ValueError, TypeError):
                                pass

            # --- handoff age: days since last handoff ---
            handoff_norm = 0.0
            if loop_id:
                hrow = conn.execute(
                    "SELECT MAX(created_at) as latest FROM workflow_handoffs WHERE loop_id = %s",
                    (loop_id,),
                ).fetchone()
                if hrow:
                    h_latest = hrow["latest"] if isinstance(hrow, dict) else hrow[0]
                    if h_latest:
                        try:
                            h_dt = datetime.fromisoformat(h_latest.replace("Z", "+00:00"))
                            h_days = (now_dt - h_dt).total_seconds() / 86400
                            handoff_norm = min(h_days / 14.0, 1.0)  # 14 days = 1.0
                        except (ValueError, TypeError):
                            pass

            # Compute weighted priority
            priority = (
                WEIGHT_STALENESS * staleness_norm
                + WEIGHT_DEADLINE * deadline_norm
                + WEIGHT_QUALITY_GAP * quality_gap
                + WEIGHT_LOOP_STATE * loop_penalty
                + WEIGHT_HANDOFF_AGE * handoff_norm
            )

            # Determine recommended action
            if opp_status == "tracking":
                action = "Run extract reflex to mine shall statements"
            elif opp_status == "drafting":
                if avg_quality < 0.70:
                    action = "Run polish reflex to improve quality scores"
                else:
                    action = "Run decide reflex to generate bid recommendation"
            elif opp_status == "reviewing":
                action = "Complete review and generate handoff for approval"
            else:
                action = "Check opportunity status and advance pipeline"

            recommendations.append(
                {
                    "opportunity_id": opp_id,
                    "title": opp_title,
                    "agency": opp_agency,
                    "due_date": opp_due,
                    "status": opp_status,
                    "loop_status": loop_status,
                    "priority": round(priority, 4),
                    "action": action,
                    "scores": {
                        "staleness": round(staleness_norm, 3),
                        "deadline_proximity": round(deadline_norm, 3),
                        "quality_gap": round(quality_gap, 3),
                        "loop_state": round(loop_penalty, 3),
                        "handoff_age": round(handoff_norm, 3),
                    },
                }
            )

        # Sort by priority descending
        recommendations.sort(key=lambda x: x["priority"], reverse=True)
        recommendations = recommendations[:limit]

        return {
            "recommendations": recommendations,
            "count": len(recommendations),
            "weights": {
                "staleness": WEIGHT_STALENESS,
                "deadline_proximity": WEIGHT_DEADLINE,
                "quality_gap": WEIGHT_QUALITY_GAP,
                "loop_state": WEIGHT_LOOP_STATE,
                "handoff_age": WEIGHT_HANDOFF_AGE,
            },
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Lifecycle status
# ---------------------------------------------------------------------------


def get_lifecycle_status(opportunity_id: str) -> Dict[str, Any]:
    """Full lifecycle status for an opportunity.

    Returns loop state, acceptance criteria, reconciliation deviations,
    handoff documents, and current phase information.
    """
    conn = _get_db()
    try:
        _ensure_tables(conn)
        _ensure_workflow_loop_id_column(conn)

        opp = conn.execute(
            "SELECT id, title, agency, due_date, status, workflow_loop_id, "
            "solicitation_number "
            "FROM proposal_opportunities WHERE id = %s",
            (opportunity_id,),
        ).fetchone()
        if not opp:
            return {"error": f"Opportunity {opportunity_id} not found"}

        opp_title = opp["title"] if isinstance(opp, dict) else opp[1]
        opp_status = opp["status"] if isinstance(opp, dict) else opp[4]
        loop_id = opp["workflow_loop_id"] if isinstance(opp, dict) else opp[5]

        result: Dict[str, Any] = {
            "opportunity_id": opportunity_id,
            "title": opp_title,
            "status": opp_status,
            "loop_id": loop_id,
        }

        if not loop_id:
            result["loop_status"] = "none"
            result["acceptance_criteria"] = []
            result["reconciliations"] = []
            result["handoffs"] = []
            return result

        # Loop state
        lrow = conn.execute(
            "SELECT * FROM workflow_loops WHERE id = %s",
            (loop_id,),
        ).fetchone()
        if lrow:
            result["loop_status"] = lrow["status"] if isinstance(lrow, dict) else lrow[4]
            result["loop_phase"] = lrow["phase_name"] if isinstance(lrow, dict) else lrow[2]
            result["tasks"] = {
                "total": lrow["task_count"] if isinstance(lrow, dict) else lrow[7],
                "completed": lrow["tasks_completed"] if isinstance(lrow, dict) else lrow[8],
            }

        # Acceptance criteria
        ac_rows = conn.execute(
            "SELECT id, criterion_number, given_text, when_text, then_text, "
            "status, evidence, verified_at "
            "FROM workflow_acceptance_criteria WHERE loop_id = %s "
            "ORDER BY criterion_number",
            (loop_id,),
        ).fetchall()
        criteria = []
        for ac in ac_rows:
            criteria.append(
                {
                    "id": ac["id"] if isinstance(ac, dict) else ac[0],
                    "number": ac["criterion_number"] if isinstance(ac, dict) else ac[1],
                    "given": ac["given_text"] if isinstance(ac, dict) else ac[2],
                    "when": ac["when_text"] if isinstance(ac, dict) else ac[3],
                    "then": ac["then_text"] if isinstance(ac, dict) else ac[4],
                    "status": ac["status"] if isinstance(ac, dict) else ac[5],
                    "evidence": (ac["evidence"] if isinstance(ac, dict) else ac[6]) or "",
                    "verified_at": ac["verified_at"] if isinstance(ac, dict) else ac[7],
                }
            )
        result["acceptance_criteria"] = criteria

        # Reconciliations
        recon_rows = conn.execute(
            "SELECT id, planned_tasks, completed_tasks, deviations, "
            "overall_result, reconciled_at "
            "FROM workflow_reconciliations WHERE loop_id = %s "
            "ORDER BY reconciled_at DESC LIMIT 10",
            (loop_id,),
        ).fetchall()
        reconciliations = []
        for rr in recon_rows:
            dev_raw = (rr["deviations"] if isinstance(rr, dict) else rr[3]) or "{}"
            try:
                dev_data = json.loads(dev_raw)
            except (json.JSONDecodeError, TypeError):
                dev_data = {"raw": dev_raw}
            reconciliations.append(
                {
                    "id": rr["id"] if isinstance(rr, dict) else rr[0],
                    "planned": rr["planned_tasks"] if isinstance(rr, dict) else rr[1],
                    "completed": rr["completed_tasks"] if isinstance(rr, dict) else rr[2],
                    "deviations": dev_data,
                    "overall_result": rr["overall_result"] if isinstance(rr, dict) else rr[4],
                    "reconciled_at": rr["reconciled_at"] if isinstance(rr, dict) else rr[5],
                }
            )
        result["reconciliations"] = reconciliations

        # Handoffs
        handoff_rows = conn.execute(
            "SELECT id, loop_status, context_summary, created_at "
            "FROM workflow_handoffs WHERE loop_id = %s "
            "ORDER BY created_at DESC LIMIT 5",
            (loop_id,),
        ).fetchall()
        handoffs = []
        for hr in handoff_rows:
            handoffs.append(
                {
                    "id": hr["id"] if isinstance(hr, dict) else hr[0],
                    "loop_status": hr["loop_status"] if isinstance(hr, dict) else hr[1],
                    "summary_preview": ((hr["context_summary"] if isinstance(hr, dict) else hr[2]) or "")[:200],
                    "created_at": hr["created_at"] if isinstance(hr, dict) else hr[3],
                }
            )
        result["handoffs"] = handoffs

        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_human(data: Dict[str, Any]) -> str:
    """Format output for human-readable terminal display."""
    lines = []
    if "error" in data:
        lines.append(f"ERROR: {data['error']}")
        return "\n".join(lines)

    if "recommendations" in data:
        lines.append(f"Next Actions ({data.get('count', 0)} recommendations):")
        lines.append("-" * 70)
        for rec in data["recommendations"]:
            lines.append(f"  [{rec['priority']:.3f}] {rec['title'][:50]} ({rec['agency'][:20]}) — {rec['status']}")
            lines.append(f"          Action: {rec['action']}")
            lines.append(f"          Due: {rec.get('due_date', 'N/A')}")
        return "\n".join(lines)

    if "loop_id" in data and "criteria_count" in data:
        lines.append(f"Created lifecycle loop: {data['loop_id']}")
        lines.append(f"  Project: {data.get('project_id', '')}")
        lines.append(f"  Phase: {data.get('phase_name', '')}")
        lines.append(f"  Criteria: {data['criteria_count']}")
        return "\n".join(lines)

    if "transition" in data:
        status_label = "PASS" if data.get("passed") else "FAIL"
        lines.append(f"Transition {data['transition']}: {status_label}")
        lines.append(f"  Details: {data.get('details', '')}")
        return "\n".join(lines)

    if "reconciliation_id" in data:
        lines.append(f"Reconciliation: {data['reconciliation_id']}")
        lines.append(f"  Reflex: {data.get('reflex', '')}")
        lines.append(f"  Gap: {data.get('gap_pct', 0):.0%} ({data.get('severity', '')})")
        return "\n".join(lines)

    if "handoff_id" in data:
        lines.append(f"Handoff: {data['handoff_id']}")
        lines.append(f"  File: {data.get('file_path', '')}")
        return "\n".join(lines)

    if "acceptance_criteria" in data:
        lines.append(f"Lifecycle Status: {data.get('title', '')}")
        lines.append(f"  Opportunity: {data.get('opportunity_id', '')}")
        lines.append(f"  Status: {data.get('status', '')}")
        lines.append(f"  Loop: {data.get('loop_id', 'none')} ({data.get('loop_status', 'none')})")
        if data.get("acceptance_criteria"):
            lines.append("  Acceptance Criteria:")
            for ac in data["acceptance_criteria"]:
                lines.append(f"    [{ac['status'].upper():7s}] {ac['then']}")
        return "\n".join(lines)

    return json.dumps(data, indent=2)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Opportunity Lifecycle Manager — Workflow Discipline Engine integration",
    )
    parser.add_argument("--opportunity-id", help="Opportunity ID")
    parser.add_argument("--create-loop", action="store_true", help="Create workflow loop for opportunity")
    parser.add_argument("--title", help="Opportunity title (for --create-loop)")
    parser.add_argument("--sam-gov-id", help="SAM.gov opportunity ID (optional)")
    parser.add_argument("--verify", action="store_true", help="Verify phase transition")
    parser.add_argument("--from-phase", help="Source phase (e.g. CAPTURE)")
    parser.add_argument("--to-phase", help="Target phase (e.g. EXTRACT)")
    parser.add_argument("--reconcile", action="store_true", help="Record reflex reconciliation")
    parser.add_argument("--reflex", help="Reflex name for reconciliation")
    parser.add_argument("--expected", type=int, help="Expected count")
    parser.add_argument("--actual", type=int, help="Actual count")
    parser.add_argument("--handoff", action="store_true", help="Generate handoff document")
    parser.add_argument("--next-action", action="store_true", help="Prioritised next actions")
    parser.add_argument("--limit", type=int, default=10, help="Max recommendations (default 10)")
    parser.add_argument("--status", action="store_true", help="Full lifecycle status")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()

    result: Dict[str, Any] = {}

    if args.create_loop:
        if not args.opportunity_id:
            result = {"error": "--opportunity-id required for --create-loop"}
        elif not args.title:
            result = {"error": "--title required for --create-loop"}
        else:
            result = create_opportunity_loop(
                opportunity_id=args.opportunity_id,
                title=args.title,
                sam_gov_id=args.sam_gov_id,
            )
    elif args.verify:
        if not args.opportunity_id:
            result = {"error": "--opportunity-id required for --verify"}
        elif not args.from_phase or not args.to_phase:
            result = {"error": "--from-phase and --to-phase required for --verify"}
        else:
            result = verify_phase_transition(
                opportunity_id=args.opportunity_id,
                from_phase=args.from_phase,
                to_phase=args.to_phase,
            )
    elif args.reconcile:
        if not args.opportunity_id:
            result = {"error": "--opportunity-id required for --reconcile"}
        elif not args.reflex or args.expected is None or args.actual is None:
            result = {"error": "--reflex, --expected, and --actual required for --reconcile"}
        else:
            result = reconcile_reflex(
                opportunity_id=args.opportunity_id,
                reflex_name=args.reflex,
                expected_count=args.expected,
                actual_count=args.actual,
            )
    elif args.handoff:
        if not args.opportunity_id:
            result = {"error": "--opportunity-id required for --handoff"}
        else:
            result = generate_handoff(opportunity_id=args.opportunity_id)
    elif args.next_action:
        result = next_action_recommender(limit=args.limit)
    elif args.status:
        if not args.opportunity_id:
            result = {"error": "--opportunity-id required for --status"}
        else:
            result = get_lifecycle_status(opportunity_id=args.opportunity_id)
    else:
        parser.print_help()
        return

    if args.human:
        print(_format_human(result))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

# CUI // SP-CTI
