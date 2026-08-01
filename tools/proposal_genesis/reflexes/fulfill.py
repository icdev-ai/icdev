#!/usr/bin/env python3
# CUI // SP-CTI
"""R11: Fulfill Reflex — CDRL auto-generation + compliance refresh.

Scans active contracts for deliverables approaching their due date,
dispatches to the appropriate ICDEV™ generation tool (SSP, SBOM, STIG,
EVM report, etc.), records generation results, and flags stale
compliance documentation for refresh.

Enhancements (§3.7/§3.8):
  - GovEval quality gate on compliance CDRLs (ssp, sbom, poam, stig_checklist)
  - DocHub portfolio integration (module registration + health scoring)

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

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.proposal_genesis.reflexes.fulfill")

# ---------------------------------------------------------------------------
# Module-level constants — Fulfill Reflex (R11) thresholds & limits.
# Extracted from inline magic numbers (AI-ify opp 5404/5405, hardcoded_threshold
# → anomaly_detection).  Overridable from proposal_genesis_config.yaml under
# reflexes.fulfill.  Change config, not code.
# ---------------------------------------------------------------------------
_DEFAULT_DAYS_AHEAD           = 14   # deliverables-due lookahead window (config: days_ahead)
_DEFAULT_MAX_GENERATIONS      = 10   # CDRL generations dispatched per run (config: max_generations_per_run)
_DEFAULT_STALE_THRESHOLD_DAYS = 90   # age (days) after which compliance docs are flagged stale (config: stale_threshold_days)
_GOVEVAL_GATE_THRESHOLD       = 0.5  # GovEval composite below which a CDRL is flagged needs_review (anomaly; config: goveval_gate_threshold)
_CDRL_GEN_TIMEOUT_SECS        = 300  # per-CDRL subprocess generation timeout


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# CDRL type → ICDEV™ tool mapping  (D-CPMP-5)
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

# CDRL types that warrant GovEval quality gating (§3.7)
COMPLIANCE_CDRL_TYPES = {"ssp", "sbom", "poam", "stig_checklist"}


# ---------------------------------------------------------------------------
# Find deliverables due within N days
# ---------------------------------------------------------------------------


def _get_due_deliverables(days_ahead: int = _DEFAULT_DAYS_AHEAD) -> List[Dict]:
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
            AND d.due_date <= date('now', '+' || %s || ' days')
            ORDER BY d.due_date ASC
        """,
            (days_ahead,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_stale_documentation(max_age_days: int = _DEFAULT_STALE_THRESHOLD_DAYS) -> List[Dict]:
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
            AND d.updated_at < date('now', '-' || %s || ' days')
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


# ---------------------------------------------------------------------------
# GovEval quality gate (§3.7 — D-VL-9)
# ---------------------------------------------------------------------------


def _run_goveval_gate(project_id: str, cdrl_type: str) -> Dict[str, Any]:
    """Run GovEval benchmark after compliance CDRL generation.

    Only executes for compliance CDRLs (ssp, sbom, poam, stig_checklist).
    Returns dict with ``passed``, ``score``, ``dimensions``.
    Gracefully skips on import failure or non-compliance CDRL types.
    """
    if cdrl_type not in COMPLIANCE_CDRL_TYPES:
        return {"passed": True, "score": 0, "skipped": True, "reason": "non_compliance_cdrl"}

    try:
        from tools.testing.goveval import run_evaluation
    except (ImportError, Exception):
        return {"passed": True, "score": 0, "skipped": True, "reason": "goveval_import_failed"}

    try:
        result = run_evaluation(project_id=project_id)
        composite = result.get("composite_score", 0.0)
        gate_passed = result.get("gate_passed", False)

        # Build per-dimension summary
        dimensions: Dict[str, float] = {}
        for dim_result in result.get("dimension_results", []):
            dim_name = dim_result.get("dimension", "unknown")
            dimensions[dim_name] = dim_result.get("score", 0.0)

        return {
            "passed": gate_passed,
            "score": composite,
            "skipped": False,
            "dimensions": dimensions,
            "findings": result.get("total_findings", 0),
        }
    except Exception as exc:
        return {"passed": True, "score": 0, "skipped": True, "reason": f"goveval_error: {str(exc)[:100]}"}


# ---------------------------------------------------------------------------
# DocHub portfolio integration (§3.8 — D-DH-11/D-DH-12)
# ---------------------------------------------------------------------------


def _register_dochub_module(contract_id: str, contract_number: str) -> Dict[str, Any]:
    """Register a contract as a DocHub module for documentation tracking.

    Gracefully skips if the DocHub tenant_manager is unavailable.
    """
    try:
        from tools.dochub.tenant_manager import register_module
    except (ImportError, Exception):
        return {"registered": False, "skipped": True, "reason": "dochub_tenant_manager_unavailable"}

    try:
        result = register_module(
            project_id=contract_id,
            module_name=contract_number or contract_id,
            module_slug=contract_id.replace("-", "_"),
        )
        return {"registered": True, "skipped": False, "module_id": result.get("module_id", ""), "details": result}
    except Exception as exc:
        return {"registered": False, "skipped": True, "reason": f"dochub_register_error: {str(exc)[:100]}"}


def _compute_dochub_health(contract_id: str) -> Dict[str, Any]:
    """Compute DocHub health score for a contract's documentation.

    Health score is a composite of freshness, completeness, and gaps
    (D-DH-4 pattern: freshness=0.35, completeness=0.40, gaps=0.25).
    Gracefully skips if the DocHub health_scorer is unavailable.
    """
    try:
        from tools.dochub.health_scorer import compute_health
    except (ImportError, Exception):
        return {"score": 0, "skipped": True, "reason": "dochub_health_scorer_unavailable"}

    try:
        result = compute_health(project_id=contract_id)
        return {
            "score": result.get("health_score", 0),
            "freshness": result.get("freshness_score", 0),
            "completeness": result.get("completeness_score", 0),
            "gaps": result.get("gap_score", 0),
            "skipped": False,
            "details": result,
        }
    except Exception as exc:
        return {"score": 0, "skipped": True, "reason": f"dochub_health_error: {str(exc)[:100]}"}


# ---------------------------------------------------------------------------
# CDRL generation dispatch
# ---------------------------------------------------------------------------


def _generate_cdrl(
    deliverable: Dict,
    cdrl_type: str,
    goveval_gate_threshold: float = _GOVEVAL_GATE_THRESHOLD,
) -> Tuple[bool, Dict]:
    """Generate a CDRL by dispatching to the mapped ICDEV™ tool.

    ``goveval_gate_threshold`` is the GovEval composite below which a generated
    CDRL is flagged ``needs_review`` (the anomaly gate).  It defaults to the
    module constant but is injectable so ``run()`` can override it from
    proposal_genesis_config.yaml (reflexes.fulfill.goveval_gate_threshold).

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
            timeout=_CDRL_GEN_TIMEOUT_SECS,
            cwd=str(BASE_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
        )

        if result.returncode == 0:
            gen_result: Dict[str, Any] = {
                "tool": tool_path,
                "cdrl_type": cdrl_type,
                "stdout_len": len(result.stdout),
            }

            # §3.7 — Run GovEval quality gate on compliance CDRLs
            goveval = _run_goveval_gate(project_id, cdrl_type)
            gen_result["goveval_score"] = goveval.get("score", 0)
            gen_result["goveval_passed"] = goveval.get("passed", True)
            gen_result["goveval_skipped"] = goveval.get("skipped", False)

            # If GovEval fails (score < threshold), flag for review (anomaly)
            if not goveval.get("skipped", False) and goveval.get("score", 0) < goveval_gate_threshold:
                gen_result["needs_review"] = True
                gen_result["goveval_reason"] = "score_below_threshold"

            return True, gen_result
        else:
            return False, {
                "tool": tool_path,
                "cdrl_type": cdrl_type,
                "error": (result.stderr or "non-zero exit")[:200],
            }
    except subprocess.TimeoutExpired:
        return False, {"tool": tool_path, "error": f"timeout ({_CDRL_GEN_TIMEOUT_SECS}s)"}
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
    goveval_score: Optional[float] = None,
    goveval_status: Optional[str] = None,
) -> Optional[str]:
    """Write to cpmp_cdrl_generations (append-only).

    GovEval results (§3.7) are stored in the ``metadata`` JSON column
    as ``goveval_score`` (float 0-1) and ``goveval_status`` (passed/
    failed/skipped).  If GovEval fails (score < 0.5) the status is set
    to ``generated_needs_review`` via the metadata field (the DB CHECK
    constraint uses the canonical ``generated`` status value).
    """
    conn = get_connection()
    gen_id = _generate_id("pgcdrl")
    now = _utcnow_iso()

    # Determine effective status — GovEval failure flags for review
    needs_review = goveval_status == "failed"
    effective_status = "generated" if success else "failed"

    # Encode goveval data in the existing metadata JSON column
    metadata: Dict[str, Any] = {}
    if goveval_score is not None:
        metadata["goveval_score"] = goveval_score
    if goveval_status is not None:
        metadata["goveval_status"] = goveval_status
    if needs_review:
        metadata["needs_review"] = True
        metadata["review_reason"] = "goveval_score_below_threshold"

    try:
        conn.execute(
            "INSERT INTO cpmp_cdrl_generations "
            "(id, deliverable_id, contract_id, cdrl_type, generation_tool, "
            "status, error_message, generated_by, metadata, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                gen_id,
                deliverable_id,
                contract_id,
                cdrl_type,
                tool_path,
                effective_status,
                error_msg,
                "pg_fulfill",
                json.dumps(metadata) if metadata else "{}",
                now,
            ),
        )
        # Update deliverable status if successful
        if success:
            conn.execute(
                "UPDATE cpmp_deliverables SET generated_by_tool = %s, "
                "status = 'in_progress', updated_at = %s WHERE id = %s",
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
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
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
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_record_compliance_refresh: best-effort INSERT into pg_proposal_genesis_audit failed (non-blocking): %s",
            exc,
        )
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
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
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
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_audit_fulfill: best-effort INSERT into pg_proposal_genesis_audit failed (non-blocking): %s",
            exc,
        )
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
      3. Dispatch to ICDEV™ generation tool
      4. Run GovEval quality gate on compliance CDRLs (§3.7)
      5. Record generation results in cpmp_cdrl_generations
      6. Check for stale compliance documentation (>90 days)
      7. Flag stale docs for refresh
      8. Register contracts as DocHub modules (§3.8)
      9. Compute DocHub portfolio health scores (§3.8)
     10. Audit all decisions

    Returns standard reflex result dict.
    """
    days_ahead = config.get("days_ahead", _DEFAULT_DAYS_AHEAD)
    max_generations_per_run = config.get("max_generations_per_run", _DEFAULT_MAX_GENERATIONS)
    stale_threshold_days = config.get("stale_threshold_days", _DEFAULT_STALE_THRESHOLD_DAYS)
    goveval_gate_threshold = config.get("goveval_gate_threshold", _GOVEVAL_GATE_THRESHOLD)

    # Step 1: Find due deliverables
    due_deliverables = _get_due_deliverables(days_ahead)
    generated = 0
    gen_errors = 0
    skipped = 0
    goveval_failures = 0
    generation_results: List[Dict] = []
    seen_contracts: Dict[str, str] = {}  # contract_id -> contract_number

    # Steps 2-5: Process each deliverable
    for deliv in due_deliverables[:max_generations_per_run]:
        deliv_id = deliv.get("id", "")
        contract_id = deliv.get("contract_id", "")
        contract_number = deliv.get("contract_number", "")

        # Track unique contracts for DocHub registration
        if contract_id and contract_id not in seen_contracts:
            seen_contracts[contract_id] = contract_number

        cdrl_type = _resolve_cdrl_type(deliv)
        if not cdrl_type:
            skipped += 1
            continue

        tool_path = TOOL_MAPPING.get(cdrl_type, "")
        success, result = _generate_cdrl(deliv, cdrl_type, goveval_gate_threshold)

        # Extract GovEval results from generation output (§3.7)
        goveval_score: Optional[float] = None
        goveval_status: Optional[str] = None
        if success:
            goveval_score = result.get("goveval_score")
            ge_passed = result.get("goveval_passed", True)
            ge_skipped = result.get("goveval_skipped", False)
            if ge_skipped:
                goveval_status = "skipped"
            elif ge_passed:
                goveval_status = "passed"
            else:
                goveval_status = "failed"
                goveval_failures += 1

        gen_id = _record_generation(
            deliverable_id=deliv_id,
            contract_id=contract_id,
            cdrl_type=cdrl_type,
            tool_path=tool_path,
            success=success,
            error_msg=result.get("error") if not success else None,
            goveval_score=goveval_score,
            goveval_status=goveval_status,
        )

        if success:
            generated += 1
        else:
            gen_errors += 1

        gen_entry: Dict[str, Any] = {
            "deliverable_id": deliv_id,
            "cdrl_type": cdrl_type,
            "success": success,
            "generation_id": gen_id,
            "contract_number": contract_number,
        }
        if goveval_score is not None:
            gen_entry["goveval_score"] = goveval_score
            gen_entry["goveval_passed"] = result.get("goveval_passed", True)
        if result.get("needs_review"):
            gen_entry["needs_review"] = True

        generation_results.append(gen_entry)

        _audit_fulfill(
            "cdrl_generated" if success else "cdrl_generation_failed",
            contract_id,
            {
                "deliverable_id": deliv_id,
                "cdrl_type": cdrl_type,
                "tool": tool_path,
                "generation_id": gen_id,
                "goveval_score": goveval_score,
                "goveval_status": goveval_status,
                "error": result.get("error") if not success else None,
            },
            success=success,
        )

    # Steps 6-7: Check for stale compliance documentation
    stale_docs = _get_stale_documentation(stale_threshold_days)
    refreshes_flagged = 0
    for doc in stale_docs:
        cdrl_type = _resolve_cdrl_type(doc) or "documentation"
        _record_compliance_refresh(doc.get("id", ""), doc.get("contract_id", ""), cdrl_type)
        refreshes_flagged += 1

    # Step 8: Register contracts as DocHub modules (§3.8)
    dochub_registrations: List[Dict] = []
    for cid, cnum in seen_contracts.items():
        reg = _register_dochub_module(cid, cnum)
        dochub_registrations.append({"contract_id": cid, **reg})

    # Step 9: Compute DocHub portfolio health scores (§3.8)
    dochub_health_scores: List[Dict] = []
    portfolio_total = 0.0
    portfolio_count = 0
    for cid in seen_contracts:
        health = _compute_dochub_health(cid)
        dochub_health_scores.append({"contract_id": cid, **health})
        if not health.get("skipped", False):
            portfolio_total += health.get("score", 0)
            portfolio_count += 1

    portfolio_health: Dict[str, Any] = {
        "contracts_assessed": portfolio_count,
        "average_score": round(portfolio_total / portfolio_count, 3) if portfolio_count > 0 else 0,
        "scores": dochub_health_scores,
    }

    return {
        "success": True,
        "metric_value": float(generated),
        "details": {
            "deliverables_due": len(due_deliverables),
            "cdrls_generated": generated,
            "generation_errors": gen_errors,
            "goveval_failures": goveval_failures,
            "skipped_no_mapping": skipped,
            "stale_docs_flagged": refreshes_flagged,
            "generations": generation_results,
            "dochub_registrations": dochub_registrations,
        },
        "portfolio_health": portfolio_health,
    }
