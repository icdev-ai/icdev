#!/usr/bin/env python3
# CUI // SP-CTI
"""R1: Discover Reflex — SAM.gov polling + pre-solicitation + amendment detection.

Wraps tools/govcon/sam_scanner.py and tools/govcon/amendment_tracker.py.
Integrates Workflow Discipline Engine (D-WF-1) — one loop per opportunity.
Scanner-tier only (zero Claude tokens).  Air-gap safe (graceful degradation).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import column_exists, get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.proposal_genesis.reflexes.discover")

# ---------------------------------------------------------------------------
# Module-level constants — Discover Reflex (R1) thresholds & limits.
# Extracted from inline magic numbers (AI-ify opp 5398, hardcoded_threshold).
# Overridable from proposal_genesis_config.yaml under reflexes.discover.
# Change config, not code.
# ---------------------------------------------------------------------------
_AMENDMENT_CHECK_LIMIT     = 20   # active opps scanned for amendments per run
_NEW_OPP_FETCH_LIMIT       = 20   # floor on new opps fetched for loop creation
_TRACKED_VERIFY_LIMIT      = 20   # tracked opps whose workflow criteria are verified
_NEXT_ACTION_PROJECT_LIMIT = 10   # pg-* loops evaluated for next-action recommend
_PHASE_NAME_MAX_CHARS      = 80   # truncation cap for workflow loop phase name
_MIN_SHALL_STATEMENTS      = 5    # CAPTURE→PROPOSE: shall statements gate
_MIN_CAPABILITY_COVERAGE   = 60   # CAPTURE→PROPOSE: capability coverage % gate


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_air_gapped() -> bool:
    import os

    return os.environ.get("ICDEV_ENVIRONMENT", "").lower() == "air-gapped"


def _scan_sam_gov(config: Dict[str, Any]) -> Dict[str, Any]:
    """Run SAM.gov scanner and return results."""
    try:
        from tools.govcon.sam_scanner import scan_sam_gov

        result = scan_sam_gov()
        return result if isinstance(result, dict) else {"status": "ok", "raw": str(result)}
    except ImportError:
        return {"status": "import_error", "message": "sam_scanner not available"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _check_amendments(config: Dict[str, Any]) -> Dict[str, Any]:
    """Check for amendments on tracked opportunities."""
    try:
        from tools.govcon.amendment_tracker import list_amendments

        conn = get_connection()
        try:
            # Get active opportunities to check for amendments
            rows = conn.execute(
                "SELECT id, title, sam_gov_opportunity_id FROM proposal_opportunities "
                "WHERE status IN ('tracking', 'drafting', 'reviewing') "
                f"ORDER BY created_at DESC LIMIT {_AMENDMENT_CHECK_LIMIT}"
            ).fetchall()
        finally:
            conn.close()

        amendment_count = 0
        for row in rows:
            try:
                amendments = list_amendments(row["id"])
                if isinstance(amendments, dict):
                    amendment_count += amendments.get("total", 0)
            except Exception:
                pass

        return {"amendments_checked": len(rows), "amendments_found": amendment_count}
    except ImportError:
        return {"status": "import_error", "message": "amendment_tracker not available"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _store_amendment_diff(opp_id: str, diff_type: str, section: str, old_text: str, new_text: str) -> None:
    """Store an amendment diff for R5 re-extraction."""
    import uuid

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO pg_amendment_diffs
                (id, opportunity_id, diff_type, section, old_text, new_text, re_extracted, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, 0, %s)
        """,
            (f"pgad-{uuid.uuid4().hex[:10]}", opp_id, diff_type, section, old_text, new_text, _utcnow_iso()),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_store_amendment_diff: best-effort INSERT into pg_amendment_diffs failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Workflow Discipline Engine integration (D-WF-1, Enhancement §3.3)
# ---------------------------------------------------------------------------


def _ensure_workflow_loop_id_column() -> bool:
    """Add workflow_loop_id column to proposal_opportunities if missing.

    Safe to call repeatedly — ALTER TABLE is a no-op when column exists.
    Returns True if column is available, False on error.
    """
    conn = get_connection()
    try:
        # Backend-aware column probe (pgrt-sweep-06) — no PRAGMA/translation reliance.
        if column_exists(conn, "proposal_opportunities", "workflow_loop_id"):
            return True
        conn.execute("ALTER TABLE proposal_opportunities ADD COLUMN workflow_loop_id TEXT")
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def _create_workflow_loop(opp_id: str, opp_title: str) -> Optional[str]:
    """Create a Workflow Discipline Engine loop for a discovered opportunity.

    Creates a PLAN-APPLY-UNIFY loop with CAPTURE→PROPOSE acceptance criteria
    in Gherkin format.  Stores the loop_id back on the opportunity row.

    Returns loop_id on success, None on failure (graceful degradation).
    """
    try:
        from tools.workflow.loop_engine import (
            add_acceptance_criterion,
            create_loop,
        )
    except ImportError:
        return None

    try:
        project_id = f"pg-{opp_id}"
        phase_name = opp_title[:_PHASE_NAME_MAX_CHARS] if opp_title else f"Opportunity {opp_id}"

        result = create_loop(
            project_id=project_id,
            phase_name=phase_name,
            loop_type="build",
            created_by="proposal-genesis-r1",
        )
        if not isinstance(result, dict) or "error" in result:
            return None

        loop_id = result.get("loop_id")
        if not loop_id:
            return None

        # Add CAPTURE→PROPOSE transition acceptance criteria (Gherkin)
        add_acceptance_criterion(
            loop_id=loop_id,
            given_text="opportunity registered in SAM.gov",
            when_text=(
                f"shall statements >= {_MIN_SHALL_STATEMENTS} "
                f"AND capability coverage >= {_MIN_CAPABILITY_COVERAGE}%"
            ),
            then_text="proceed to proposal drafting",
        )

        # Store loop_id on the opportunity row
        if _ensure_workflow_loop_id_column():
            conn = get_connection()
            try:
                conn.execute(
                    "UPDATE proposal_opportunities SET workflow_loop_id = %s WHERE id = %s",
                    (loop_id, opp_id),
                )
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()

        return loop_id
    except Exception:
        return None


def _verify_workflow_criteria(opp_id: str) -> Dict[str, Any]:
    """Check workflow criteria status for an existing opportunity.

    Looks up the workflow_loop_id from the opportunity row and returns
    the loop status including any pending acceptance criteria.

    Returns verification dict or graceful skip on failure.
    """
    try:
        from tools.workflow.loop_engine import get_loop_status  # noqa: F811
    except ImportError:
        return {"status": "skipped", "reason": "loop_engine not available"}

    try:
        # Ensure column exists before querying
        if not _ensure_workflow_loop_id_column():
            return {"status": "skipped", "reason": "workflow_loop_id column unavailable"}

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT workflow_loop_id FROM proposal_opportunities WHERE id = %s",
                (opp_id,),
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return {"status": "skipped", "reason": f"opportunity {opp_id} not found"}

        loop_id = row["workflow_loop_id"] if isinstance(row, dict) else row[0]
        if not loop_id:
            return {"status": "skipped", "reason": "no workflow loop linked"}

        status_result = get_loop_status(loop_id=loop_id)
        if isinstance(status_result, dict) and "error" not in status_result:
            ac_summary = status_result.get("acceptance_summary", {})
            return {
                "status": "verified",
                "loop_id": loop_id,
                "loop_status": status_result.get("status", "unknown"),
                "acceptance_criteria_count": ac_summary.get("total", 0),
                "criteria_passed": ac_summary.get("pass", 0),
            }
        return {
            "status": "error",
            "loop_id": loop_id,
            "reason": status_result.get("error", "unknown") if isinstance(status_result, dict) else "bad response",
        }
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}


def _get_next_action_recommendation() -> Optional[Dict[str, Any]]:
    """Get the highest-priority next action across all pg-* workflow loops.

    Queries workflow_loops for project_ids starting with 'pg-' and calls
    the next-action recommender on the most active one.

    Returns recommendation dict or None on failure (graceful degradation).
    """
    try:
        from tools.workflow.next_action import recommend
    except ImportError:
        return None

    try:
        conn = get_connection()
        try:
            # Find distinct pg-* project IDs with open loops
            rows = conn.execute(
                "SELECT DISTINCT project_id FROM workflow_loops "
                "WHERE project_id LIKE 'pg-%' "
                "AND status NOT IN ('closed', 'abandoned') "
                f"ORDER BY created_at DESC LIMIT {_NEXT_ACTION_PROJECT_LIMIT}"
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return None

        # Evaluate each project and pick the highest-priority recommendation
        best: Optional[Dict[str, Any]] = None
        best_score = -1.0
        for row in rows:
            pid = row["project_id"] if isinstance(row, dict) else row[0]
            try:
                rec = recommend(project_id=pid)
                if isinstance(rec, dict) and rec.get("score", 0) > best_score:
                    best_score = rec.get("score", 0)
                    best = rec
            except Exception:
                continue

        return best
    except Exception:
        return None


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Discover Reflex (R1).

    Returns:
        {"success": bool, "metric_value": float, "details": dict}
    """
    if _is_air_gapped():
        return {
            "success": True,
            "metric_value": 0,
            "details": {"status": "air_gapped", "message": "Skipped -- air-gapped mode"},
        }

    # Step 1: Scan SAM.gov for new opportunities
    scan_result = _scan_sam_gov(config)
    new_opportunities = 0
    if isinstance(scan_result, dict):
        new_opportunities = scan_result.get("new_count", scan_result.get("inserted", 0))

    # Step 2: Create workflow loops for NEW opportunities (D-WF-1, §3.3)
    workflow_loops_created = 0
    if new_opportunities > 0:
        try:
            conn = get_connection()
            try:
                # Fetch recently-created opportunities that lack a workflow loop
                _ensure_workflow_loop_id_column()
                new_rows = conn.execute(
                    "SELECT id, title FROM proposal_opportunities "
                    "WHERE (workflow_loop_id IS NULL OR workflow_loop_id = '') "
                    "ORDER BY created_at DESC LIMIT %s",
                    (max(new_opportunities, _NEW_OPP_FETCH_LIMIT),),
                ).fetchall()
            finally:
                conn.close()

            for nr in new_rows:
                oid = nr["id"] if isinstance(nr, dict) else nr[0]
                otitle = nr["title"] if isinstance(nr, dict) else nr[1]
                loop_id = _create_workflow_loop(oid, otitle)
                if loop_id:
                    workflow_loops_created += 1
        except Exception:
            pass

    # Step 3: Check amendments on tracked opportunities
    amend_result = _check_amendments(config)
    amendments_found = 0
    if isinstance(amend_result, dict):
        amendments_found = amend_result.get("amendments_found", 0)

    # Step 4: Verify workflow criteria on existing (tracked) opportunities
    workflow_status: Dict[str, Any] = {"verified": 0, "skipped": 0, "errors": 0}
    try:
        conn = get_connection()
        try:
            tracked = conn.execute(
                "SELECT id FROM proposal_opportunities "
                "WHERE status IN ('tracking', 'drafting', 'reviewing') "
                f"ORDER BY created_at DESC LIMIT {_TRACKED_VERIFY_LIMIT}"
            ).fetchall()
        finally:
            conn.close()

        for tr in tracked:
            tid = tr["id"] if isinstance(tr, dict) else tr[0]
            v = _verify_workflow_criteria(tid)
            st = v.get("status", "error")
            if st == "verified":
                workflow_status["verified"] += 1
            elif st == "skipped":
                workflow_status["skipped"] += 1
            else:
                workflow_status["errors"] += 1
    except Exception:
        pass

    # Step 5: Get next-action recommendation across pg-* loops
    next_action_recommendation = _get_next_action_recommendation()

    total_signals = new_opportunities + amendments_found

    return {
        "success": True,
        "metric_value": float(total_signals),
        "details": {
            "new_opportunities": new_opportunities,
            "amendments_found": amendments_found,
            "workflow_loops_created": workflow_loops_created,
            "workflow_status": workflow_status,
            "next_action_recommendation": next_action_recommendation,
            "scan_result": scan_result,
            "amendment_result": amend_result,
        },
    }
