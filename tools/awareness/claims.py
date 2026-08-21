# CUI // SP-CTI
"""The registered claims (rem-hyg-17).

Every claim here was a REAL DEFECT found by hand on 2026-08-20. That is
deliberate on two counts:

  * the verifier has known true positives from day one, so "it reports clean"
    cannot be confused with "it does nothing" — the failure mode this codebase
    ships most; and
  * the fixes made that day gain a guard they otherwise lack. Their unit tests
    run against FIXTURES. These run against the LIVE surface and the LIVE
    primary source, so a reduction that regresses in production is caught even
    when every fixture-based test still passes.

THE RULE FOR ADDING ONE. ``reported`` and ``derived`` must not share code. If
the verifier calls what the surface calls, it proves the function is
deterministic — which was never in question. Every defect below survived
because one computation was trusted twice.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from tools.awareness.claim_verifier import Claim, independent_observations


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


# --------------------------------------------------------------------------- #
# 1. A score requires evidence  (rem-hyg-09)
# --------------------------------------------------------------------------- #
#: Canvas -> the table its score is derived from. Kept here rather than imported
#: from posture.py ON PURPOSE: importing posture's own mapping would make the
#: "independent" side a re-run of the reported side.
_EVIDENCE_TABLE = {
    "Security": "sc_assessments", "Network": "nc_compliance_checks",
    "Pipeline": "pc_compliance_checks", "Infra": "idc_assessments",
    "Data": "dd_assessments", "Boundary": "bd_assessments",
    "Observability": "od_assessments", "Agentic AI": "aadc_assessments",
    "AI/ML": "aiml_assessments", "QDC": "qdc_assessments",
    "Migration": "mc_assessments",
}


def _posture_scored_canvases() -> List[str]:
    """What the posture surface REPORTS a number for."""
    from tools.canvas_compliance.posture import compute_canvas_posture
    conn = _conn()
    try:
        rows, _overall = compute_canvas_posture(conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return sorted(r["name"] for r in rows if r.get("score") is not None)


def _canvases_with_evidence() -> List[str]:
    """Which canvases actually HOLD evidence — counted straight off each table."""
    from tools.canvas_compliance.posture import _open_canvas_connection
    out = []
    for name, table in _EVIDENCE_TABLE.items():
        cc = _open_canvas_connection(name)
        if cc is None:
            continue
        try:
            row = cc.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()  # nosec B608
            if int(dict(row).get("c") or 0) > 0:
                out.append(name)
        except Exception:
            continue          # unreadable table is not evidence
        finally:
            try:
                cc.close()
            except Exception:
                pass
    return sorted(out)


def _scored_implies_evidence(reported: List[str], derived: List[str]) -> bool:
    """Every canvas showing a NUMBER must hold evidence.

    One-directional on purpose. A canvas holding evidence but scoring None is
    fine (it may be unreadable, or out of scope); a canvas scoring 100.0 with
    zero rows behind it is the defect — measured 2026-08-20, Network, Pipeline
    and Migration all did, and inflated the headline from 87.9 to 90.7.

    GovLift and Zero Trust are scored outside the canvas loop from tables this
    map does not cover, so they are not asserted here rather than being asserted
    wrongly.
    """
    known = set(_EVIDENCE_TABLE)
    return not [c for c in reported if c in known and c not in set(derived)]


# --------------------------------------------------------------------------- #
# 2. `unlogged` is measured, not inferred  (cch-obs-03)
# --------------------------------------------------------------------------- #
def _reported_unlogged() -> Any:
    from tools.cache_savings.savings import get_savings_stats
    return get_savings_stats().get("unlogged")


def _actual_unlogged() -> Any:
    """Straight from the PostgreSQL catalogue. 'u' is UNLOGGED, 'p' permanent."""
    conn = _conn()
    try:
        if not str(getattr(conn, "_backend", "")).startswith("postgres"):
            return None                      # not a PG concept -> unmeasurable
        row = conn.execute(
            "SELECT relpersistence FROM pg_class WHERE relname = 'llm_response_cache'"
        ).fetchone()
        if not row:
            return None
        value = dict(row).get("relpersistence")
        return str(value) == "u"
    finally:
        try:
            conn.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 3. Recovery counts OUTCOMES, not attempts  (rem-hyg-16)
# --------------------------------------------------------------------------- #
def _reported_recoveries() -> int:
    """What the panel would headline as recovered."""
    from tools.dashboard.recovery_summary import summarize_recovery
    return sum(1 for r in summarize_recovery(_recovery_rows(), limit=10_000)
               if r["outcome"] == "recovered")


def _derived_recoveries() -> int:
    """Re-derived here without touching summarize_recovery.

    A task is recovered only if the watcher attempted it, it merged, and it was
    never escalated — escalation being the watcher's own "manual intervention
    required". A merge after an escalation is a HUMAN's merge.
    """
    attempted, escalated, merged = set(), set(), set()
    for row in _recovery_rows():
        record = dict(row)
        kind = str(record.get("action") or "").split(".")[-1]
        try:
            payload = json.loads(record.get("d") or "{}")
        except (ValueError, TypeError):
            continue
        task_id = payload.get("task_id")
        if not task_id:
            continue
        if kind in ("resume", "rebase"):
            attempted.add(task_id)
        elif kind == "escalate":
            escalated.add(task_id)
        elif kind == "merge":
            merged.add(task_id)
    return len(attempted & merged - escalated)


def _recovery_rows() -> List[Dict[str, Any]]:
    conn = _conn()
    try:
        pg = str(getattr(conn, "_backend", "")).startswith("postgres")
        details = "details::text" if pg else "details"
        cut = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        return [dict(r) for r in conn.execute(
            f"SELECT action, {details} AS d, created_at FROM audit_trail "  # nosec B608
            "WHERE action IN ('pr_watcher.rebase','pr_watcher.resume',"
            "'pr_watcher.escalate','pr_watcher.merge') AND created_at >= %s",
            (cut,),
        ).fetchall()]
    finally:
        try:
            conn.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 4. Repetition is not corroboration — a stuck writer  (the trap itself)
# --------------------------------------------------------------------------- #
#: A series is SUSPECT when it holds many rows carrying almost no distinct
#: facts. Measured 2026-08-20: odc_gap_scores held 91 rows spanning a month with
#: ONE distinct value for ONE subject — a single stuck writer that any
#: row-counting confidence model would rate as extremely well corroborated.
_STUCK_MIN_ROWS = 20
_STUCK_SERIES = [
    ("Observability", "odc_gap_scores", "design_id", "overall_gap_score"),
]


def _reported_series_health() -> Dict[str, str]:
    """What the row count alone would say about each series."""
    from tools.canvas_compliance.posture import _open_canvas_connection
    out: Dict[str, str] = {}
    for canvas, table, _subj, _val in _STUCK_SERIES:
        cc = _open_canvas_connection(canvas)
        if cc is None:
            continue
        try:
            row = cc.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()  # nosec B608
            n = int(dict(row).get("c") or 0)
            out[table] = "well_corroborated" if n >= _STUCK_MIN_ROWS else "sparse"
        except Exception:
            continue
        finally:
            try:
                cc.close()
            except Exception:
                pass
    return out


def _derived_series_health() -> Dict[str, str]:
    """What INDEPENDENT observations say — distinct (subject, value) pairs."""
    from tools.canvas_compliance.posture import _open_canvas_connection
    out: Dict[str, str] = {}
    for canvas, table, subj, val in _STUCK_SERIES:
        cc = _open_canvas_connection(canvas)
        if cc is None:
            continue
        try:
            rows = cc.execute(
                f"SELECT {subj} AS s, {val} AS v FROM {table}"  # nosec B608
            ).fetchall()
            n = len(rows)
            distinct = independent_observations(
                [{"s": dict(r).get("s"), "v": dict(r).get("v")} for r in rows], "s", "v")
            if n >= _STUCK_MIN_ROWS and distinct <= 1:
                out[table] = "stuck_writer"
            else:
                out[table] = "well_corroborated" if n >= _STUCK_MIN_ROWS else "sparse"
        except Exception:
            continue
        finally:
            try:
                cc.close()
            except Exception:
                pass
    return out


REGISTRY: List[Claim] = [
    Claim(
        claim_id="posture_score_needs_evidence",
        description=(
            "A canvas that shows a compliance NUMBER must hold at least one "
            "assessment row. On 2026-08-20 Network, Pipeline and Migration each "
            "scored 100.0 with zero rows behind them and inflated the headline "
            "from 87.9 to 90.7."
        ),
        reported=_posture_scored_canvases,
        derived=_canvases_with_evidence,
        agree=_scored_implies_evidence,
        tier="propose",
        tags=["compliance", "rem-hyg-09"],
    ),
    Claim(
        claim_id="cache_unlogged_is_measured",
        description=(
            "The reported `unlogged` flag must match pg_class.relpersistence. It "
            "was `backend.startswith('postgres')` — a constant wearing the name "
            "of a measurement — and asserted the opposite of the truth from the "
            "day migration 20260816123233 made the table LOGGED."
        ),
        reported=_reported_unlogged,
        derived=_actual_unlogged,
        tier="propose",
        tags=["cache", "cch-obs-03"],
    ),
    Claim(
        claim_id="recovery_counts_outcomes_not_attempts",
        description=(
            "'Auto-recovered' must count tasks that recovered, not audit rows. "
            "Over 7 days the panel claimed 331 where the honest figure was 46 of "
            "86 — because a task retried to the five-attempt cap and then fixed "
            "by hand contributed FIVE rows to the success list."
        ),
        reported=_reported_recoveries,
        derived=_derived_recoveries,
        tier="propose",
        tags=["autonomy", "rem-hyg-16"],
    ),
    Claim(
        claim_id="repetition_is_not_corroboration",
        description=(
            "A long series carrying one distinct value is a STUCK WRITER, not a "
            "stable measurement. odc_gap_scores holds 91 rows over a month with "
            "one value for one subject; anything that gains confidence from row "
            "count rates that as strongly corroborated."
        ),
        reported=_reported_series_health,
        derived=_derived_series_health,
        tier="propose",
        tags=["evidence-quality"],
    ),
]
