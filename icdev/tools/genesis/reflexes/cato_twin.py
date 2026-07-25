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

from tools.db.storage import get_connection, table_exists  # noqa: E402

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

# ---------------------------------------------------------------------------
# Assessor → snapshot normalization (bdt-sch-1)
# ---------------------------------------------------------------------------
# The reflex freezes real per-control evidence out of the multi-regime assessor
# tables. Those tables predate the twin and each uses its own control-identifier
# column and its own status vocabulary, so we normalize both to the OSCAL-style
# shape that snapshot_writer.write_snapshot expects (control_id,
# implementation_status, evidence_ref, score). No scores are fabricated — the
# score is derived from the *assessed* status, and controls with no evidence
# keep evidence_ref=None.

# FedRAMP assessment status → OSCAL implementation status.
_FEDRAMP_STATUS_MAP = {
    "satisfied": "satisfied",
    "other_than_satisfied": "not_satisfied",
    "not_applicable": "not_applicable",
    "risk_accepted": "risk_accepted",
    "not_assessed": "not_assessed",
}

# CSSP assessment status → OSCAL implementation status (vocab already aligns).
_CSSP_STATUS_MAP = {
    "satisfied": "satisfied",
    "partially_satisfied": "partially_satisfied",
    "not_satisfied": "not_satisfied",
    "not_applicable": "not_applicable",
    "risk_accepted": "risk_accepted",
    "not_assessed": "not_assessed",
}

# CMMC practice status → OSCAL implementation status.
_CMMC_STATUS_MAP = {
    "met": "satisfied",
    "partially_met": "partially_satisfied",
    "not_met": "not_satisfied",
    "not_applicable": "not_applicable",
    "not_assessed": "not_assessed",
}

# Honest OSCAL status → numeric score. Kept identical to snapshot_writer's
# _STATUS_TO_SCORE so the anomaly pass and the persisted snapshot agree. A value
# of None means "not scorable" (not assessed / not applicable) — never 0.0.
_STATUS_TO_SCORE = {
    "satisfied": 1.0,
    "partially_satisfied": 0.5,
    "not_satisfied": 0.0,
    "not_applicable": None,
    "not_assessed": None,
    "risk_accepted": 0.5,
}

# Per-framework assessor source: physical table, the column holding the
# per-control identifier, an optional baseline filter, and the status map.
_ASSESSOR_SOURCES: Dict[str, Dict[str, Any]] = {
    "FedRAMP Moderate": {
        "table": "fedramp_assessments",
        "id_col": "control_id",
        "baseline": "moderate",
        "status_map": _FEDRAMP_STATUS_MAP,
    },
    "FedRAMP High": {
        "table": "fedramp_assessments",
        "id_col": "control_id",
        "baseline": "high",
        "status_map": _FEDRAMP_STATUS_MAP,
    },
    "NIST 800-53": {
        "table": "cssp_assessments",
        "id_col": "requirement_id",
        "baseline": None,
        "status_map": _CSSP_STATUS_MAP,
    },
    "CMMC": {
        "table": "cmmc_assessments",
        "id_col": "practice_id",
        "baseline": None,
        "status_map": _CMMC_STATUS_MAP,
    },
}

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
    """Return the standard IQE seed queries with the given anomaly threshold applied.

    Authored in the maintained IQE grammar (``tools/iqe/parser.py``) against the
    ``compliance.twin_snapshots`` collection registered by
    ``tools/iqe/adapters/compliance.py``. ``run_query`` injects the per-project
    scope, so each query carries only the framework argument. NULL evidence is
    expressed as ``== null`` and prefix matching as ``startswith`` (the IQE
    operators) — the retired regex engine's ``is null`` / ``starts_with`` forms
    are not part of this grammar.
    """
    t = f"{threshold:.2f}"
    return [
        # FedRAMP Moderate
        'foreach ctrl in compliance.twin_snapshots("FedRAMP Moderate") where ctrl.status != "satisfied" select ctrl.control_id, ctrl.implementation_status, ctrl.project_id, ctrl.score',
        'foreach ctrl in compliance.twin_snapshots("FedRAMP Moderate") where ctrl.evidence_ref == null select ctrl.control_id, ctrl.implementation_status, ctrl.project_id',
        f'foreach ctrl in compliance.twin_snapshots("FedRAMP Moderate") where ctrl.score < {t} select ctrl.control_id, ctrl.score, ctrl.implementation_status, ctrl.project_id',
        'foreach ctrl in compliance.twin_snapshots("FedRAMP Moderate") where ctrl.control_id startswith "AC" and ctrl.status != "satisfied" select ctrl.control_id, ctrl.implementation_status, ctrl.score, ctrl.project_id',
        'foreach ctrl in compliance.twin_snapshots("FedRAMP Moderate") where ctrl.control_id startswith "IA" and ctrl.status != "satisfied" select ctrl.control_id, ctrl.implementation_status, ctrl.score, ctrl.project_id',
        # FedRAMP High
        'foreach ctrl in compliance.twin_snapshots("FedRAMP High") where ctrl.status != "satisfied" select ctrl.control_id, ctrl.implementation_status, ctrl.project_id, ctrl.score',
        'foreach ctrl in compliance.twin_snapshots("FedRAMP High") where ctrl.evidence_ref == null select ctrl.control_id, ctrl.implementation_status, ctrl.project_id',
        f'foreach ctrl in compliance.twin_snapshots("FedRAMP High") where ctrl.score < {t} and ctrl.status == "not_satisfied" select ctrl.control_id, ctrl.score, ctrl.project_id, ctrl.assessor',
        'foreach ctrl in compliance.twin_snapshots("FedRAMP High") where ctrl.control_id startswith "SC" and ctrl.status != "satisfied" select ctrl.control_id, ctrl.implementation_status, ctrl.score, ctrl.project_id',
        'foreach ctrl in compliance.twin_snapshots("FedRAMP High") where ctrl.control_id startswith "SI" and ctrl.status != "satisfied" select ctrl.control_id, ctrl.implementation_status, ctrl.score, ctrl.project_id',
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
            f"  {c.get('control_id', '?')}: score={_fmt_score(c.get('score'))}, status={c.get('implementation_status', 'unknown')}"
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
                    "score": (
                        round(c["score"], 4)
                        if isinstance(c.get("score"), (int, float))
                        else None
                    ),
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


def _fmt_score(score: Any) -> str:
    """Format a score for prompt display, tolerating an unscored (None) control."""
    return f"{score:.2f}" if isinstance(score, (int, float)) else "n/a"


def _rule_based_anomalies(controls: List[Dict]) -> List[Dict]:
    """Deterministic fallback: flag scored controls at or below the default threshold.

    Controls with no numeric score (not_assessed / not_applicable) are skipped —
    they cannot be score-flagged, and their missing evidence is already captured
    as a snapshot violation by the writer.
    """
    out: List[Dict] = []
    for c in controls:
        score = c.get("score")
        if not isinstance(score, (int, float)):
            continue
        if score <= _AI_SCORE_THRESHOLD_DEFAULT and c.get("implementation_status", "") != "satisfied":
            out.append({
                "control_id": c.get("control_id", "?"),
                "reason": f"score {score:.2f} <= {_AI_SCORE_THRESHOLD_DEFAULT}",
                "severity": "high" if score == 0.0 else "medium",
            })
    return out


def _get_active_projects(conn) -> List[Dict]:
    """Return all active projects from the projects table."""
    rows = conn.execute(
        "SELECT id, name FROM projects WHERE id IS NOT NULL"
    ).fetchall()
    return [dict(r) for r in rows]


def _normalize_assessor_rows(rows, status_map: Dict[str, str]) -> List[Dict]:
    """Turn raw assessor rows into snapshot_writer control records.

    - De-duplicates to the latest row per control_id (rows arrive newest-first).
    - Maps the assessor's native status vocabulary to OSCAL status.
    - Derives evidence_ref from evidence_path (fallback evidence_description);
      absent evidence stays None — no fabrication.
    - Derives an honest numeric score from the *assessed* status (may be None).
    """
    seen = set()
    controls: List[Dict] = []
    for r in rows:
        rec = dict(r)
        cid = rec.get("control_id")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        raw_status = rec.get("status") or "not_assessed"
        status = status_map.get(raw_status, "not_assessed")
        evidence_ref = rec.get("evidence_path") or rec.get("evidence_description") or None
        controls.append({
            "control_id": cid,
            "implementation_status": status,
            "evidence_ref": evidence_ref,
            "score": _STATUS_TO_SCORE.get(status),
            "assessed_at": rec.get("assessed_at"),
        })
    return controls


def _pull_framework_controls(conn, project_id: str, framework: str) -> List[Dict]:
    """Pull current per-control compliance state for a project+framework.

    Reads the real multi-regime assessor tables (``fedramp_assessments``,
    ``cssp_assessments``, ``cmmc_assessments``) and normalizes each into the
    control-record shape ``snapshot_writer.write_snapshot`` expects. Returns an
    empty list (never raises) when the source table is missing or the query
    fails, rolling back first so a PG transaction is not left aborted.
    """
    src = _ASSESSOR_SOURCES.get(framework)
    if not src:
        return []

    table = src["table"]
    # Shared backend-aware probe: information_schema on PG, sqlite_master on
    # SQLite. Never raises for a missing table (returns False), so a missing
    # assessor table degrades to an empty control list, never a raised probe.
    if not table_exists(conn, table):
        return []

    where = "project_id = %s"
    params: List[Any] = [project_id]
    if src["baseline"] is not None:
        where += " AND baseline = %s"
        params.append(src["baseline"])

    # Note: authored PG-native (%s placeholders, no SQLite JSON dialect). The
    # id column is a fixed, code-controlled value from _ASSESSOR_SOURCES (never
    # user input), so interpolating it into the SELECT list is safe.
    sql = (
        f"SELECT {src['id_col']} AS control_id, "
        "       status, evidence_path, evidence_description, "
        "       COALESCE(updated_at, assessment_date) AS assessed_at "
        f"FROM {table} "
        f"WHERE {where} "
        "ORDER BY assessed_at DESC"
    )

    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    except Exception as exc:
        # A query error here (e.g. a schema/column mismatch on the live backend)
        # must NOT poison the shared transaction. On PostgreSQL a failed statement
        # aborts the whole transaction, so without a rollback EVERY subsequent
        # query in this reflex cycle fails with "current transaction is aborted",
        # cascading one framework's error across all the rest (and every project).
        try:
            conn.rollback()
        except Exception:
            pass
        logger.debug(
            "cato_twin: controls query failed for %s/%s — %s (rolled back; skipping framework)",
            project_id, table, exc,
        )
        return []

    return _normalize_assessor_rows(rows, src["status_map"])


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


def _stamp_dispatch_contract(totals: Dict[str, Any]) -> Dict[str, Any]:
    """Add the (success, metric_value, details) keys the Genesis daemon reads.

    ``tools/daemon/base.py::run_reflex`` derives a reflex's run outcome from
    ``result.get("success")`` / ``result.get("metric_value")`` /
    ``result.get("details")``. A reflex that returns only a flat ``status``/count
    dict (as this one historically did) is scored ``success=False`` on every run
    and sent to ``record_failure`` — which, repeated, trips the reflex circuit
    breaker and permanently stops it being dispatched. Stamping these keys makes
    a healthy cycle record as a success (metric = snapshots_written).
    """
    totals["success"] = totals.get("status") != "error"
    totals["metric_value"] = float(totals.get("snapshots_written", 0) or 0)
    totals["details"] = {
        k: totals.get(k)
        for k in (
            "snapshots_written",
            "violations_found",
            "poam_items_created",
            "projects_processed",
            "ai_anomalies_found",
            "status",
            "errors",
        )
    }
    return totals


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Execute one cATO Twin continuous monitoring cycle.

    Args:
        ctx:  Genesis context dict (may contain 'triggered_by', 'dry_run').
        conn: Optional existing DB connection (for tests). The Genesis daemon
              dispatches reflexes as ``run(config, trust)`` — the second
              positional arg is the TrustKernel, NOT a DB connection — so this
              is only reused when it actually quacks like a connection.

    Returns:
        Summary dict: snapshots_written, violations_found,
                      poam_items_created, projects_processed,
                      ai_anomalies_found, errors.
    """
    from tools.boundary_canvas.cato_twin.snapshot_writer import write_snapshot
    from tools.iqe.adapters.compliance import run_query
    from tools.boundary_canvas.cato_twin.poam_auto_generator import generate_from_violations

    triggered_by = ctx.get("triggered_by", "genesis_reflex")
    dry_run = ctx.get("dry_run", False)

    # bdr-ops-1: the daemon passes the TrustKernel as the 2nd positional; only
    # reuse it when it is a real DB connection (tests pass one), else open our own.
    if conn is not None and not hasattr(conn, "execute"):
        conn = None
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
            return _stamp_dispatch_contract(totals)

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

                    # Run seed queries (logging only — non-fatal).
                    # Scope to the current project so a per-project monitoring
                    # cycle never reads another project's compliance state.
                    for query in seed_queries:
                        try:
                            _results = run_query(query, conn=conn, project_id=project_id)
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

    return _stamp_dispatch_contract(totals)


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    import json as _json
    result = run({})
    print(_json.dumps(result, indent=2))
