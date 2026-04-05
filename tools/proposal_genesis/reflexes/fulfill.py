#!/usr/bin/env python3
# CUI // SP-CTI
"""R11: Fulfill Reflex — CDRL auto-generation + compliance refresh.

Scans active contracts for deliverables approaching their due date,
dispatches to the appropriate ICDEV generation tool (SSP, SBOM, STIG,
EVM report, etc.), records generation results, and flags stale
compliance documentation for refresh.

Pipeline: daily 09:00 (independent schedule).
YELLOW tier (reversible writes — generates files, updates deliverable status).
Scanner-tier LLM only (zero Claude tokens — fully deterministic).
"""

import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# CDRL type → ICDEV tool mapping  (D-CPMP-5)
# ---------------------------------------------------------------------------

TOOL_MAPPING: Dict[str, str] = {
    "ssp": "tools/compliance/ssp_generator.py",
    "sbom": "tools/compliance/sbom_generator.py",
    "poam": "tools/compliance/poam_generator.py",
    "stig_checklist": "tools/compliance/stig_checker.py",
    "evm_report": "tools/govcon/evm_engine.py",
    "icd": "tools/mosa/icd_generator.py",
    "tsp": "tools/mosa/tsp_generator.py",
    "test_report": "tools/testing/test_orchestrator.py",
    "security_scan": "tools/security/sast_runner.py",
}

# Deliverable types that map to known CDRL generators
DELIVERABLE_TYPE_TO_CDRL: Dict[str, str] = {
    "documentation": "ssp",
    "plan": "ssp",
    "test_result": "test_report",
    "software": "sbom",
    "data": "evm_report",
}


# ---------------------------------------------------------------------------
# Find deliverables due within N days
# ---------------------------------------------------------------------------


def _get_due_deliverables(days_ahead: int = 14) -> List[Dict]:
    """Find deliverables due within N days that haven't been generated yet."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT d.id, d.contract_id, d.cdrl_number, d.did_number,
                   d.title, d.deliverable_type, d.due_date, d.status,
                   d.days_overdue, d.generated_by_tool,
                   c.contract_number, c.title AS contract_title,
                   c.opportunity_id
            FROM cpmp_deliverables d
            JOIN cpmp_contracts c ON c.id = d.contract_id
            WHERE c.status IN ('active', 'option_pending')
            AND d.status IN ('not_started', 'in_progress', 'overdue')
            AND d.generated_by_tool IS NULL
            AND d.due_date IS NOT NULL
            AND d.due_date <= date('now', '+' || ? || ' days')
            ORDER BY d.due_date ASC
        """,
            (days_ahead,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_stale_documentation(max_age_days: int = 90) -> List[Dict]:
    """Find compliance documentation deliverables older than threshold.

    These need a compliance refresh — regenerate with latest data.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT d.id, d.contract_id, d.cdrl_number, d.title,
                   d.deliverable_type, d.due_date, d.status,
                   d.generated_by_tool, d.updated_at,
                   c.contract_number, c.title AS contract_title
            FROM cpmp_deliverables d
            JOIN cpmp_contracts c ON c.id = d.contract_id
            WHERE c.status IN ('active', 'option_pending')
            AND d.deliverable_type IN ('documentation', 'plan')
            AND d.status IN ('accepted', 'submitted', 'government_review')
            AND d.generated_by_tool IS NOT NULL
            AND d.updated_at < date('now', '-' || ? || ' days')
            ORDER BY d.updated_at ASC
        """,
            (max_age_days,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CDRL generation dispatch
# ---------------------------------------------------------------------------


def _resolve_cdrl_type(deliverable: Dict) -> Optional[str]:
    """Determine the CDRL type from deliverable metadata."""
    # Check cdrl_number for hints
    cdrl_num = (deliverable.get("cdrl_number") or "").lower()
    title = (deliverable.get("title") or "").lower()

    # Direct keyword matching
    if "ssp" in cdrl_num or "ssp" in title or "system security" in title:
        return "ssp"
    if "sbom" in cdrl_num or "sbom" in title or "bill of material" in title:
        return "sbom"
    if "poam" in cdrl_num or "poam" in title or "plan of action" in title:
        return "poam"
    if "stig" in cdrl_num or "stig" in title:
        return "stig_checklist"
    if "evm" in cdrl_num or "evm" in title or "earned value" in title:
        return "evm_report"
    if "icd" in cdrl_num or "interface control" in title:
        return "icd"
    if "tsp" in cdrl_num or "transition" in title:
        return "tsp"
    if "test" in cdrl_num or "test" in title:
        return "test_report"
    if "security" in cdrl_num or "security scan" in title or "vulnerability" in title:
        return "security_scan"

    # Fallback: map from deliverable_type
    dtype = deliverable.get("deliverable_type", "")
    return DELIVERABLE_TYPE_TO_CDRL.get(dtype)


def _generate_cdrl(deliverable: Dict, cdrl_type: str) -> Tuple[bool, Dict]:
    """Generate a CDRL by dispatching to the mapped ICDEV tool.

    Returns (success, result_dict).
    """
    tool_path = TOOL_MAPPING.get(cdrl_type)
    if not tool_path:
        return False, {"error": f"No tool mapping for cdrl_type '{cdrl_type}'"}

    tool_full = BASE_DIR / tool_path
    if not tool_full.exists():
        return False, {"error": f"Tool not found: {tool_path}"}

    contract_id = deliverable.get("contract_id", "")
    opportunity_id = deliverable.get("opportunity_id", "")
    project_id = opportunity_id or contract_id

    # Build safe environment
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("_") and "SECRET" not in k.upper()}

    try:
        args = [sys.executable, str(tool_full)]
        if project_id:
            args.extend(["--project-id", project_id])
        args.append("--json")

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(BASE_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
        )

        if result.returncode == 0:
            return True, {
                "tool": tool_path,
                "cdrl_type": cdrl_type,
                "stdout_len": len(result.stdout),
            }
        else:
            return False, {
                "tool": tool_path,
                "cdrl_type": cdrl_type,
                "error": (result.stderr or "non-zero exit")[:200],
            }
    except subprocess.TimeoutExpired:
        return False, {"tool": tool_path, "error": "timeout (300s)"}
    except Exception as exc:
        return False, {"tool": tool_path, "error": str(exc)[:200]}


# ---------------------------------------------------------------------------
# Record generation result
# ---------------------------------------------------------------------------


def _record_generation(
    deliverable_id: str,
    contract_id: str,
    cdrl_type: str,
    tool_path: str,
    success: bool,
    error_msg: Optional[str] = None,
) -> Optional[str]:
    """Write to cpmp_cdrl_generations (append-only)."""
    conn = get_connection()
    gen_id = _generate_id("pgcdrl")
    now = _utcnow_iso()
    try:
        conn.execute(
            "INSERT INTO cpmp_cdrl_generations "
            "(id, deliverable_id, contract_id, cdrl_type, generation_tool, "
            "status, error_message, generated_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                gen_id,
                deliverable_id,
                contract_id,
                cdrl_type,
                tool_path,
                "generated" if success else "failed",
                error_msg,
                "pg_fulfill",
                now,
            ),
        )
        # Update deliverable status if successful
        if success:
            conn.execute(
                "UPDATE cpmp_deliverables SET generated_by_tool = ?, "
                "status = 'in_progress', updated_at = ? WHERE id = ?",
                (tool_path, now, deliverable_id),
            )
        conn.commit()
        return gen_id
    except Exception:
        return None
    finally:
        conn.close()


def _record_compliance_refresh(deliverable_id: str, contract_id: str, cdrl_type: str) -> None:
    """Flag a deliverable for compliance refresh in audit trail."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, opportunity_id, "
            "details, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _generate_id("pgaudit"),
                "compliance_refresh_flagged",
                "fulfill",
                "yellow",
                contract_id,
                json.dumps(
                    {
                        "deliverable_id": deliverable_id,
                        "cdrl_type": cdrl_type,
                        "reason": "documentation_stale",
                    }
                ),
                1,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _audit_fulfill(event_type: str, contract_id: Optional[str], details: Dict, success: bool) -> None:
    """Log fulfill event to audit trail."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, opportunity_id, "
            "details, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _generate_id("pgaudit"),
                event_type,
                "fulfill",
                "yellow",
                contract_id,
                json.dumps(details),
                1 if success else 0,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Fulfill Reflex (R11).

    Steps:
      1. Find deliverables due within 14 days without generation
      2. Resolve CDRL type for each deliverable
      3. Dispatch to ICDEV generation tool
      4. Record generation results in cpmp_cdrl_generations
      5. Check for stale compliance documentation (>90 days)
      6. Flag stale docs for refresh
      7. Audit all decisions

    Returns standard reflex result dict.
    """
    days_ahead = config.get("days_ahead", 14)
    max_generations_per_run = config.get("max_generations_per_run", 10)
    stale_threshold_days = config.get("stale_threshold_days", 90)

    # Step 1: Find due deliverables
    due_deliverables = _get_due_deliverables(days_ahead)
    generated = 0
    gen_errors = 0
    skipped = 0
    generation_results = []

    # Steps 2-4: Process each deliverable
    for deliv in due_deliverables[:max_generations_per_run]:
        deliv_id = deliv.get("id", "")
        contract_id = deliv.get("contract_id", "")

        cdrl_type = _resolve_cdrl_type(deliv)
        if not cdrl_type:
            skipped += 1
            continue

        tool_path = TOOL_MAPPING.get(cdrl_type, "")
        success, result = _generate_cdrl(deliv, cdrl_type)

        gen_id = _record_generation(
            deliverable_id=deliv_id,
            contract_id=contract_id,
            cdrl_type=cdrl_type,
            tool_path=tool_path,
            success=success,
            error_msg=result.get("error") if not success else None,
        )

        if success:
            generated += 1
        else:
            gen_errors += 1

        generation_results.append(
            {
                "deliverable_id": deliv_id,
                "cdrl_type": cdrl_type,
                "success": success,
                "generation_id": gen_id,
                "contract_number": deliv.get("contract_number", ""),
            }
        )

        _audit_fulfill(
            "cdrl_generated" if success else "cdrl_generation_failed",
            contract_id,
            {
                "deliverable_id": deliv_id,
                "cdrl_type": cdrl_type,
                "tool": tool_path,
                "generation_id": gen_id,
                "error": result.get("error") if not success else None,
            },
            success=success,
        )

    # Steps 5-6: Check for stale compliance documentation
    stale_docs = _get_stale_documentation(stale_threshold_days)
    refreshes_flagged = 0
    for doc in stale_docs:
        cdrl_type = _resolve_cdrl_type(doc) or "documentation"
        _record_compliance_refresh(doc.get("id", ""), doc.get("contract_id", ""), cdrl_type)
        refreshes_flagged += 1

    return {
        "success": True,
        "metric_value": float(generated),
        "details": {
            "deliverables_due": len(due_deliverables),
            "cdrls_generated": generated,
            "generation_errors": gen_errors,
            "skipped_no_mapping": skipped,
            "stale_docs_flagged": refreshes_flagged,
            "generations": generation_results,
        },
    }
