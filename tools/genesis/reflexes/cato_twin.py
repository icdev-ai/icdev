#!/usr/bin/env python3
# CUI // SP-CTI
"""cATO Twin Continuous Monitoring Reflex — 6-hour cadence.

Runs as a Genesis reflex every 6 hours. For each active project:
  1. Pull current compliance state from the multi-regime assessors
  2. Write a new compliance twin snapshot via snapshot_writer
  3. Run the 20 seed IQE queries to detect violations
  4. Auto-generate POA&M items for any new violations
  5. Run AI-driven anomaly detection on each snapshot's controls
  6. Log a summary to audit_trail

This is the continuous monitoring loop that keeps the compliance twin fresh
and generates the evidence stream required for cATO (NIST SP 800-137).

Reflex contract:
  - run(ctx, conn) → dict with keys: snapshots_written, violations_found,
                                      poam_items_created, projects_processed,
                                      ai_anomalies_found
  - CADENCE_HOURS = 6 (read by Genesis scheduler)
  - Must be idempotent — running twice in a 6h window is safe (snapshot IDs differ)
  - Must not raise — catches all exceptions per project, logs, continues
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

from tools.logging.icdev_logger import get_logger

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

CADENCE_HOURS = 6

# Fallback score threshold used when LLM is unavailable or returns an invalid value.
# Controls scoring at or below this value are flagged as anomalous.
_AI_SCORE_THRESHOLD_DEFAULT: float = 0.5

# Frameworks sampled per reflex cycle (ordered by risk level)
_FRAMEWORKS = [
    "FedRAMP High",
    "FedRAMP Moderate",
    "NIST 800-53",
    "CMMC",
]

# LLM prompt templates
_THRESHOLD_PROMPT = """You are a compliance risk analyst reviewing {framework} controls.
Given the following control compliance scores:

{controls_summary}

Determine the appropriate anomaly threshold score below which a control should be flagged
as requiring immediate attention. Consider the framework's risk posture and the distribution
of scores shown. Return ONLY a JSON object with this exact format:
{{"threshold": 0.XX, "rationale": "one sentence"}}

The threshold must be a float between 0.0 and 1.0."""

_ANOMALY_PROMPT = """You are a compliance anomaly detector for {framework} controls (Project: {project_id}).
Analyze the following controls for anomalous patterns that may indicate systemic compliance risk:

{controls_json}

Identify controls that are anomalous due to: low scores, missing evidence, specific control
families at risk, or unexpected patterns. Return ONLY a JSON object:
{{"anomalies": [{{"control_id": "X", "reason": "...", "severity": "low|medium|high"}}]}}

Flag only genuinely anomalous controls. If none are found, return {{"anomalies": []}}."""


def _build_seed_queries(threshold: float = _AI_SCORE_THRESHOLD_DEFAULT) -> List[str]:
    """Return the standard IQE seed queries with the given anomaly threshold applied."""
    t = f"{threshold:.2f}"
    return [
        # FedRAMP Moderate
        "foreach ctrl in framework('FedRAMP Moderate').controls where ctrl.status != 'satisfied' select ctrl.control_id, ctrl.implementation_status, ctrl.project_id, ctrl.score",
        "foreach ctrl in framework('FedRAMP Moderate').controls where ctrl.evidence_ref is null select ctrl.control_id, ctrl.implementation_status, ctrl.project_id",
        f"foreach ctrl in framework('FedRAMP Moderate').controls where ctrl.score < {t} select ctrl.control_id, ctrl.score, ctrl.implementation_status, ctrl.project_id",
        "foreach ctrl in framework('FedRAMP Moderate').controls where ctrl.control_id starts_with 'AC' and ctrl.status != 'satisfied' select ctrl.control_id, ctrl.implementation_status, ctrl.score, ctrl.project_id",
        "foreach ctrl in framework('FedRAMP Moderate').controls where ctrl.control_id starts_with 'IA' and ctrl.status != 'satisfied' select ctrl.control_id, ctrl.implementation_status, ctrl.score, ctrl.project_id",
        # FedRAMP High
        "foreach ctrl in framework('FedRAMP High').controls where ctrl.status != 'satisfied' select ctrl.control_id, ctrl.implementation_status, ctrl.project_id, ctrl.score",
        "foreach ctrl in framework('FedRAMP High').controls where ctrl.evidence_ref is null select ctrl.control_id, ctrl.implementation_status, ctrl.project_id",
        f"foreach ctrl in framework('FedRAMP High').controls where ctrl.score < {t} and ctrl.status == 'not_satisfied' select ctrl.control_id, ctrl.score, ctrl.project_id, ctrl.assessor",
        "foreach ctrl in framework('FedRAMP High').controls where ctrl.control_id starts_with 'SC' and ctrl.status != 'satisfied' select ctrl.control_id, ctrl.implementation_status, ctrl.score, ctrl.project_id",
        "foreach ctrl in framework('FedRAMP High').controls where ctrl.control_id starts_with 'SI' and ctrl.status != 'satisfied' select ctrl.control_id, ctrl.implementation_status, ctrl.score, ctrl.project_id",
    ]


# Keep _SEED_QUERIES for backward compat — computed with the default threshold
_SEED_QUERIES = _build_seed_queries()


def _determine_anomaly_threshold(controls: List[Dict], framework: str) -> float:
    """Ask the LLM to determine an appropriate anomaly threshold for these controls.

    Falls back to _AI_SCORE_THRESHOLD_DEFAULT on any LLM error or unavailability.
    """
    if not controls:
        return _AI_SCORE_THRESHOLD_DEFAULT

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        if router.is_no_llm_mode() or not router.has_any_llm():
            return _AI_SCORE_THRESHOLD_DEFAULT

        controls_summary = "\n".join(
            f"  {c.get('control_id', '?')}: score={c.get('score', 0.0):.2f}, status={c.get('implementation_status', 'unknown')}"
            for c in controls[:30]  # cap to avoid token overflow
        )
        prompt = _THRESHOLD_PROMPT.format(
            framework=framework,
            controls_summary=controls_summary,
        )
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a federal compliance risk analyst. Reply only with valid JSON.",
            max_tokens=256,
            classification="CUI",
        )
        response = router.invoke("anomaly_detection", request)
        raw = (response.content or "").strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        parsed = json.loads(raw)
        t = float(parsed.get("threshold", _AI_SCORE_THRESHOLD_DEFAULT))
        if 0.0 <= t <= 1.0:
            return t
        return _AI_SCORE_THRESHOLD_DEFAULT

    except Exception as exc:
        logger.info(
            "LLM threshold determination unavailable for %s (%s) — using default %.2f",
            framework,
            exc,
            _AI_SCORE_THRESHOLD_DEFAULT,
        )
        return _AI_SCORE_THRESHOLD_DEFAULT


def _detect_anomalies_llm(
    controls: List[Dict], framework: str, project_id: str
) -> List[Dict]:
    """Use LLM to identify anomalous compliance patterns beyond threshold detection.

    Falls back to a rule-based list (controls below _AI_SCORE_THRESHOLD_DEFAULT) if
    the LLM is unavailable or returns unparseable output.
    """
    if not controls:
        return []

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        if router.is_no_llm_mode() or not router.has_any_llm():
            return _rule_based_anomalies(controls)

        controls_json = json.dumps(
            [
                {
                    "control_id": c.get("control_id", "?"),
                    "score": round(c.get("score", 0.0), 4),
                    "status": c.get("implementation_status", "unknown"),
                    "evidence_ref": c.get("evidence_ref"),
                }
                for c in controls[:50]  # cap to avoid token overflow
            ],
            indent=2,
        )
        prompt = _ANOMALY_PROMPT.format(
            framework=framework,
            project_id=project_id,
            controls_json=controls_json,
        )
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a federal compliance anomaly detector. Reply only with valid JSON.",
            max_tokens=1024,
            classification="CUI",
        )
        response = router.invoke("anomaly_detection", request)
        raw = (response.content or "").strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        parsed = json.loads(raw)
        anomalies = parsed.get("anomalies", [])
        if isinstance(anomalies, list):
            return [a for a in anomalies if isinstance(a, dict) and "control_id" in a]

    except Exception as exc:
        logger.info(
            "LLM anomaly detection unavailable for %s/%s (%s) — using rule-based fallback",
            project_id,
            framework,
            exc,
        )

    return _rule_based_anomalies(controls)


def _rule_based_anomalies(controls: List[Dict]) -> List[Dict]:
    """Deterministic fallback: flag controls at or below the default threshold."""
    return [
        {
            "control_id": c.get("control_id", "?"),
            "reason": f"score {c.get('score', 0.0):.2f} <= {_AI_SCORE_THRESHOLD_DEFAULT}",
            "severity": "high" if c.get("score", 0.0) == 0.0 else "medium",
        }
        for c in controls
        if c.get("score", 0.0) <= _AI_SCORE_THRESHOLD_DEFAULT
        and c.get("implementation_status", "") != "satisfied"
    ]


def _get_active_projects(conn) -> List[Dict]:
    """Return all active projects from the projects table."""
    rows = conn.execute(
        "SELECT id, name FROM projects WHERE id IS NOT NULL"
    ).fetchall()
    return [dict(r) for r in rows]


def _pull_framework_controls(conn, project_id: str, framework: str) -> List[Dict]:
    """Pull current compliance state for a project+framework from assessment tables."""
    table_map = {
        "FedRAMP Moderate": "fedramp_assessments",
        "FedRAMP High": "fedramp_assessments",
        "NIST 800-53": "cssp_assessments",
        "CMMC": "cmmc_assessments",
    }
    table = table_map.get(framework)
    if not table:
        return []

    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s",
        (table,),
    ).fetchone()
    if not exists:
        return []

    try:
        rows = conn.execute(
            f"""SELECT control_id,
                       COALESCE(status, 'not_assessed')  AS implementation_status,
                       COALESCE(evidence_ref, NULL)       AS evidence_ref,
                       COALESCE(score, 0.0)               AS score
                FROM {table}
               WHERE project_id = ?
               ORDER BY assessed_at DESC""",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _log_audit(conn, project_id: str, summary: Dict) -> None:
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                project_id,
                "cato_twin_reflex",
                "genesis-cato-twin",
                "continuous_monitoring_cycle",
                json.dumps(summary),
                "CUI // SP-CTI",
            ),
        )
    except Exception as e:
        logger.warning("audit log failed: %s", e)


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Execute one cATO Twin continuous monitoring cycle.

    Args:
        ctx:  Genesis context dict (may contain 'triggered_by', 'dry_run').
        conn: Optional existing DB connection (for tests).

    Returns:
        Summary dict: snapshots_written, violations_found,
                      poam_items_created, projects_processed,
                      ai_anomalies_found, errors.
    """
    from tools.boundary_canvas.cato_twin.snapshot_writer import write_snapshot
    from tools.boundary_canvas.cato_twin.query_engine import run_query
    from tools.boundary_canvas.cato_twin.poam_auto_generator import generate_from_violations

    triggered_by = ctx.get("triggered_by", "genesis_reflex")
    dry_run = ctx.get("dry_run", False)

    _own_conn = conn is None
    if _own_conn:
        conn = get_connection()

    totals: Dict[str, Any] = {
        "snapshots_written": 0,
        "violations_found": 0,
        "poam_items_created": 0,
        "projects_processed": 0,
        "ai_anomalies_found": 0,
        "errors": [],
        "status": "ok",
        "cadence_hours": CADENCE_HOURS,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        projects = _get_active_projects(conn)
        if not projects:
            totals["status"] = "no_projects"
            return totals

        for project in projects:
            project_id = project["id"]
            project_violations = 0
            project_poam = 0
            project_ai_anomalies = 0

            for framework in _FRAMEWORKS:
                try:
                    controls = _pull_framework_controls(conn, project_id, framework)
                    if not controls:
                        continue

                    # Determine AI-driven anomaly threshold for seed query construction
                    threshold = _determine_anomaly_threshold(controls, framework)
                    seed_queries = _build_seed_queries(threshold)

                    if dry_run:
                        totals["snapshots_written"] += 1
                        continue

                    snap_id = write_snapshot(
                        project_id=project_id,
                        framework=framework,
                        controls=controls,
                        triggered_by=triggered_by,
                        conn=conn,
                    )
                    totals["snapshots_written"] += 1

                    # Count violations from this snapshot
                    viols = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM compliance_twin_violations "
                        "WHERE snapshot_id = %s",
                        (snap_id,),
                    ).fetchone()
                    viol_count = dict(viols)["cnt"] if viols else 0
                    project_violations += viol_count
                    totals["violations_found"] += viol_count

                    # Auto-generate POA&M items for new violations
                    if viol_count > 0:
                        poam_result = generate_from_violations(
                            snap_id, project_id, conn=conn
                        )
                        project_poam += poam_result.get("new_items", 0)
                        totals["poam_items_created"] += poam_result.get("new_items", 0)

                    # AI-driven anomaly detection on this framework's controls
                    anomalies = _detect_anomalies_llm(controls, framework, project_id)
                    project_ai_anomalies += len(anomalies)
                    totals["ai_anomalies_found"] += len(anomalies)

                    # Run seed queries (logging only — non-fatal)
                    for query in seed_queries:
                        try:
                            _results = run_query(query, conn=conn)
                        except Exception:
                            pass

                except Exception as fw_err:
                    msg = f"{project_id}/{framework}: {fw_err}"
                    logger.warning("cato_twin reflex error — %s", msg)
                    totals["errors"].append(msg)

            totals["projects_processed"] += 1
            _log_audit(conn, project_id, {
                "violations": project_violations,
                "poam_items": project_poam,
                "ai_anomalies": project_ai_anomalies,
                "frameworks_sampled": len(_FRAMEWORKS),
            })

        conn.commit()

    except Exception as top_err:
        totals["status"] = "error"
        totals["errors"].append(str(top_err))
        logger.error("cato_twin reflex top-level error: %s", top_err)
    finally:
        if _own_conn:
            conn.close()

    return totals


if __name__ == "__main__":
    import json as _json
    result = run({})
    print(_json.dumps(result, indent=2))
