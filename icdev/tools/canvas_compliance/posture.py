# CUI // SP-CTI
"""Canonical canvas compliance-posture aggregation (cnr-cc-02).

Single source of truth for the per-canvas compliance *scores* shown on the
dashboard home page (Compliance Posture widget) and available to the MCP /
summary surfaces. Previously this logic lived inline in
``tools/dashboard/app.py``'s index route AND was duplicated (against stale
per-canvas SQLite files) by ``canvas_aggregator.get_canvas_compliance_summary``.
Both now delegate here.

Backend-aware: each canvas is read through its own ``db/init_db.get_connection``
(honoring the canvas's STORAGE_BACKEND / PG_DATABASE env vars) with RLS disabled,
rather than opening the stale per-canvas ``.db`` file. This is the runtime
*posture* view (numeric scores + open/closed counts). It is complementary to,
not the same as, the two other canvas surfaces:

  * ``compliance.py::get_all_cards`` — per-canvas badge cards (POA&M counts, ISA
    expiry, MITRE coverage, …) rendered on **/canvas-compliance** (Canvas
    Posture page).
  * ``tools/canvas_health`` — file-existence QA rendered on **/health/canvases**
    (Canvas Health).

The per-canvas scoring rules here are intentionally NOT rewritten — they are the
exact rules the index route has always used, lifted verbatim into one place.
"""
from __future__ import annotations

import importlib
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.canvas_compliance.posture")

# Map dashboard-facing canvas names to the canvas init_db module that exports a
# backend-aware get_connection(). This respects each canvas's own
# STORAGE_BACKEND / PG_DATABASE env vars so the dashboard reads from PostgreSQL
# when the canvas is configured for PG instead of the stale per-canvas SQLite file.
_CANVAS_MODULES = {
    "Security": "tools.security_canvas.db.init_db",
    "Network": "tools.network.db.init_db",
    "Pipeline": "tools.pipeline.db.init_db",
    "Infra": "tools.infra_canvas.db.init_db",
    "Data": "tools.data_canvas.db.init_db",
    "Boundary": "tools.boundary_canvas.db.init_db",
    "Observability": "tools.observability_canvas.db.init_db",
    "Agentic AI": "tools.agentic_ai_canvas.db.init_db",
    "AI/ML": "tools.aiml_canvas.db.init_db",
    "QDC": "tools.qdc_canvas.db.init_db",
    "Migration": "tools.migration_canvas.db.init_db",
}


def _open_canvas_connection(canvas_name: str) -> Any | None:
    """Open a backend-aware canvas connection with RLS disabled."""
    mod_name = _CANVAS_MODULES.get(canvas_name)
    if not mod_name:
        return None
    try:
        mod = importlib.import_module(mod_name)
        cconn = mod.get_connection()
        try:
            cconn.set_security_context(None)  # rls-bypass: canvas tables lack tenant_id/classification; module-level canvas connection disables RLS
        except Exception:
            pass
        return cconn
    except Exception:
        return None


def _score_or_none(cc, table: str, score_col: str = "score"):
    """Latest-per-design average, or None when the table holds NO evidence.

    Every caller used to be ``round(_latest_per_design_avg(...), 1)``, and that
    helper coerces a NULL average to ``0.0`` — so an empty table produced a
    confident score rather than an absence. For most canvases the number was
    ``0.0`` (which at least renders as "No data"); for Security it is
    ``100 - avg(risk)`` and an empty table scored a PERFECT 100.

    Asking for the row count first is what separates "assessed, and the average
    is zero" from "nothing has ever been assessed" (rem-hyg-09).
    """
    if not _has_rows(cc, table):
        return None
    return round(_latest_per_design_avg(cc, table, score_col), 1)


def _latest_per_design_avg(cc, table: str, score_col: str = "score") -> float:
    q = (
        f"SELECT AVG({score_col}) FROM {table} a1 "  # nosec B608
        f"WHERE created_at = ("
        f"  SELECT MAX(created_at) FROM {table} a2 "  # nosec B608
        f"  WHERE a2.design_id = a1.design_id"
        f")"
    )
    try:
        r = cc.execute(q).fetchone()
        return float(r[0] or 0)
    except Exception:
        fallback = cc.execute(f"SELECT AVG({score_col}) FROM {table}").fetchone()  # nosec B608
        return float(fallback[0] or 0)


#: Where each canvas's newest evidence timestamp lives. Used ONLY to report
#: staleness — never to compute a score. A canvas absent from this map reports
#: ``last_assessed: None``, which the surface renders as "unknown", not "fresh".
_ASSESSED_AT = {
    "Security":      ("sc_assessments", "ran_at"),
    "Network":       ("nc_compliance_checks", "created_at"),
    "Pipeline":      ("pc_compliance_checks", "created_at"),
    "Infra":         ("idc_assessments", "created_at"),
    "Data":          ("dd_assessments", "created_at"),
    "Boundary":      ("bd_assessments", "created_at"),
    "Observability": ("od_assessments", "created_at"),
    "Agentic AI":    ("aadc_assessments", "created_at"),
    "AI/ML":         ("aiml_assessments", "created_at"),
    "QDC":           ("qdc_assessments", "created_at"),
    "Migration":     ("mc_assessments", "created_at"),
}


def _has_rows(cc, table: str) -> bool:
    """Does this table hold ANY evidence? Fail-closed to False.

    A score derived from SUMs of an empty table is arithmetic on nothing —
    ``100 - 0 - 0 - 0`` is 100 — so the row count has to be asked separately.
    An unreadable table is not evidence either, hence False on error.
    """
    try:
        r = cc.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()  # nosec B608
        return int((r["c"] if isinstance(r, dict) else r[0]) or 0) > 0
    except Exception:
        return False


def _max_ts(cc, table: str, col: str):
    """Newest value of a timestamp column, or None. Never raises."""
    try:
        r = cc.execute(f"SELECT MAX({col}) AS m FROM {table}").fetchone()  # nosec B608
        value = r["m"] if isinstance(r, dict) else r[0]
        return str(value) if value else None
    except Exception:
        return None


def _last_assessed(cc, canvas_name: str):
    """Newest evidence timestamp as an ISO string, or None if there is none.

    Reported so the surface can render an AGE. Measured on the live board
    2026-08-20, every canvas was between 33 and 71 days stale and the widget
    said nothing at all — a score from June was indistinguishable from one taken
    that morning. None means "no evidence / could not read", which a renderer
    must show as unknown rather than as fresh.
    """
    spec = _ASSESSED_AT.get(canvas_name)
    if not spec:
        return None
    return _max_ts(cc, *spec)


def daily_trend(scores) -> tuple[str, Any]:
    """30-day direction from a list of ``{"score", "date"}`` — like with like.

    This used to be ``scores[0] - scores[-1]`` over rows ordered by timestamp
    across ALL designs, so a canvas holding one row per design (Boundary: 6
    rows, 6 designs) had design F's score subtracted from design A's and the
    difference reported as a trend. That is design-mix noise, and it produces a
    confident red ▼ from data that never moved.

    Averaging per DAY first makes both ends the same kind of quantity: the mean
    across whatever designs were assessed that day.

    Returns ``("unmeasured", None)`` when fewer than two DAYS are present.
    ``flat`` claims a measurement held steady; with one point there was no
    measurement, and on this board that is the state of every canvas — none has
    been assessed inside the window at all. The two must not render alike.
    """
    by_day: dict = {}
    for s in scores or []:
        by_day.setdefault(str(s["date"]), []).append(s["score"])
    days = sorted(by_day)
    if len(days) < 2:
        return "unmeasured", None
    newest = sum(by_day[days[-1]]) / len(by_day[days[-1]])
    oldest = sum(by_day[days[0]]) / len(by_day[days[0]])
    delta = round(newest - oldest, 1)
    if delta >= 2.0:
        return "up", delta
    if delta <= -2.0:
        return "down", delta
    return "flat", delta


def compute_canvas_posture(conn) -> tuple[list[dict], float]:
    """Return ``(canvas_compliance, overall_score)`` across all canvases.

    ``conn`` is the main ICDEV database connection (used for GovLift STIG checks,
    which live in icdev.db rather than a canvas DB). Each canvas is otherwise read
    through its own backend-aware connection.

    Each ``canvas_compliance`` row::

        {"name": str, "score": float, "open_findings": int, "closed_findings": int}
    """
    canvas_compliance: list[dict] = []
    overall_scores: list[float] = []

    for canvas_name in _CANVAS_MODULES:
        cconn = _open_canvas_connection(canvas_name)
        if not cconn:
            continue
        try:
            if canvas_name == "Security":
                try:
                    r = cconn.execute(
                        "SELECT AVG(risk_score) FROM sc_assessments a1 "
                        "WHERE ran_at = (SELECT MAX(ran_at) FROM sc_assessments a2 "
                        "WHERE a2.design_id = a1.design_id)"
                    ).fetchone()
                    avg_risk = float(r[0] or 0)
                except Exception:
                    avg_risk = float(cconn.execute(
                        "SELECT AVG(risk_score) FROM sc_assessments"
                    ).fetchone()[0] or 0)
                total_threats = int(cconn.execute(
                    "SELECT COUNT(*) FROM sc_assessments"
                ).fetchone()[0] or 0)
                # NOT ASSESSED, never 100.0 (rem-hyg-09): with no rows the
                # average risk is 0 and `100 - 0` is a perfect score for a
                # canvas nobody has assessed. `total_threats` is the row count,
                # so it already answers "is there evidence".
                score = round(max(0.0, 100.0 - avg_risk), 1) if total_threats > 0 else None
                open_f = total_threats
                closed_f = 0
            elif canvas_name in ("Network", "Pipeline"):
                checks_tbl = "nc_compliance_checks" if canvas_name == "Network" else "pc_compliance_checks"
                findings_tbl = "nc_compliance_findings" if canvas_name == "Network" else "pc_compliance_findings"
                try:
                    r = cconn.execute(
                        f"SELECT SUM(passed) as p, SUM(failed) as f FROM {checks_tbl}"  # nosec B608
                    ).fetchone()
                    passed_c = int(r["p"] or 0)
                    failed_c = int(r["f"] or 0)
                    total_c = passed_c + failed_c
                    if total_c == 0:
                        raise ValueError("no checks")
                    score = round(passed_c / total_c * 100, 1)
                    open_f = failed_c
                    closed_f = passed_c
                except Exception:
                    open_f = cconn.execute(
                        f"SELECT COUNT(*) as cnt FROM {findings_tbl} WHERE status = 'open'"  # nosec B608
                    ).fetchone()["cnt"]
                    closed_f = cconn.execute(
                        f"SELECT COUNT(*) as cnt FROM {findings_tbl} WHERE status != 'open'"  # nosec B608
                    ).fetchone()["cnt"]
                    total_f = open_f + closed_f
                    # NOT ASSESSED, never 100.0 (rem-hyg-09). This branch is
                    # reached when the checks table is empty, and the old
                    # `else 100.0` then rendered a canvas nobody had ever
                    # assessed as a full green bar at perfect compliance.
                    # Measured on the live board: nc_compliance_checks and
                    # pc_compliance_checks both held ZERO rows and both scored
                    # 100.0, inflating the headline from 87.9 to 90.7.
                    score = round(closed_f / total_f * 100, 1) if total_f > 0 else None
            elif canvas_name in ("Infra", "Data"):
                tbl = "idc_assessments" if canvas_name == "Infra" else "dd_assessments"
                score = _score_or_none(cconn, tbl)
                open_f = 0
                closed_f = 0
            elif canvas_name == "Boundary":
                try:
                    score = _score_or_none(cconn, "bd_assessments")
                    cat_row = cconn.execute(
                        "SELECT SUM(cat1_findings) as c1, SUM(cat2_findings) as c2, "
                        "SUM(cat3_findings) as c3 FROM bd_assessments a1 "
                        "WHERE created_at = (SELECT MAX(created_at) FROM bd_assessments a2 "
                        "WHERE a2.design_id = a1.design_id)"
                    ).fetchone()
                    open_f = int((cat_row["c1"] or 0) + (cat_row["c2"] or 0) + (cat_row["c3"] or 0))
                except Exception:
                    row = cconn.execute(
                        "SELECT SUM(cat1_findings) as cat1, SUM(cat2_findings) as cat2, "
                        "SUM(cat3_findings) as cat3, AVG(score) as avg_score FROM bd_assessments"
                    ).fetchone()
                    score = round(float(row["avg_score"] or 0), 1)
                    open_f = int((row["cat1"] or 0) + (row["cat2"] or 0) + (row["cat3"] or 0))
                closed_f = 0
            elif canvas_name == "Observability":
                score = _score_or_none(cconn, "od_assessments")
                open_f = 0
                closed_f = 0
            elif canvas_name == "Agentic AI":
                score = _score_or_none(cconn, "aadc_assessments")
                open_f = 0
                closed_f = 0
            elif canvas_name == "AI/ML":
                score = _score_or_none(cconn, "aiml_assessments")
                open_f = 0
                closed_f = 0
            elif canvas_name == "QDC":
                score = _score_or_none(cconn, "qdc_assessments")
                open_f = 0
                closed_f = 0
            elif canvas_name == "Migration":
                try:
                    cat_row = cconn.execute(
                        "SELECT SUM(cat1_findings) as c1, SUM(cat2_findings) as c2, "
                        "SUM(cat3_findings) as c3 FROM mc_assessments a1 "
                        "WHERE assessment_type = 'validation' "
                        "AND created_at = (SELECT MAX(created_at) FROM mc_assessments a2 "
                        "WHERE a2.design_id = a1.design_id AND a2.assessment_type = 'validation')"
                    ).fetchone()
                    c1 = int(cat_row["c1"] or 0)
                    c2 = int(cat_row["c2"] or 0)
                    c3 = int(cat_row["c3"] or 0)
                    # NOT ASSESSED, never 100.0 (rem-hyg-09): every SUM is NULL
                    # on an empty table, so `100 - 0 - 0 - 0` scored a canvas
                    # nobody had assessed as PERFECT. mc_assessments held ZERO
                    # rows on the live board and rendered a full green bar.
                    score = (round(max(0.0, 100.0 - c1 * 20 - c2 * 10 - c3 * 5), 1)
                             if _has_rows(cconn, "mc_assessments") else None)
                    open_f = c1 + c2 + c3
                except Exception:
                    row = cconn.execute(
                        "SELECT SUM(cat1_findings) as cat1, SUM(cat2_findings) as cat2, "
                        "SUM(cat3_findings) as cat3 FROM mc_assessments"
                    ).fetchone()
                    c1 = int(row["cat1"] or 0)
                    c2 = int(row["cat2"] or 0)
                    c3 = int(row["cat3"] or 0)
                    # NOT ASSESSED, never 100.0 (rem-hyg-09): every SUM is NULL
                    # on an empty table, so `100 - 0 - 0 - 0` scored a canvas
                    # nobody had assessed as PERFECT. mc_assessments held ZERO
                    # rows on the live board and rendered a full green bar.
                    score = (round(max(0.0, 100.0 - c1 * 20 - c2 * 10 - c3 * 5), 1)
                             if _has_rows(cconn, "mc_assessments") else None)
                    open_f = c1 + c2 + c3
                closed_f = 0
            else:
                continue

            canvas_compliance.append(
                {
                    "name": canvas_name,
                    "score": score,
                    "open_findings": open_f,
                    "closed_findings": closed_f,
                    # When the newest evidence was written, or None if there is
                    # none. Rendered as an age so a two-month-old score cannot
                    # look like one taken this morning (rem-hyg-09).
                    "last_assessed": _last_assessed(cconn, canvas_name),
                }
            )
            # `score is None` means NOT ASSESSED and must never be averaged; a
            # measured 0.0 is likewise excluded, which is the behaviour this
            # function already had.
            if score is not None and score > 0:
                overall_scores.append(score)
        except Exception:
            pass  # Graceful if canvas has no data / table missing
        finally:
            try:
                cconn.close()
            except Exception:
                pass

    # GovLift STIG checks (stored in main icdev.db, not a canvas DB)
    try:
        stig_row = conn.execute(
            "SELECT "
            "SUM(CASE WHEN status IN ('not_a_finding','not_applicable') THEN 1 ELSE 0 END) as passed, "
            "SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_cnt, "
            "COUNT(*) as total FROM govlift_stig_checks"
        ).fetchone()
        stig_total = int(stig_row["total"] or 0)
        if stig_total > 0:
            stig_passed = int(stig_row["passed"] or 0)
            stig_open = int(stig_row["open_cnt"] or 0)
            stig_score = round(stig_passed / stig_total * 100, 1)
            canvas_compliance.append({
                "name": "GovLift",
                "score": stig_score,
                "open_findings": stig_open,
                "closed_findings": stig_passed,
                # These two rows are appended outside the canvas loop, so they
                # need the field explicitly or they would be the only rows
                # without one — and a MISSING key renders differently from a
                # known-absent timestamp (rem-hyg-09).
                "last_assessed": _max_ts(conn, "govlift_stig_checks", "checked_at"),
            })
            if stig_score > 0:
                overall_scores.append(stig_score)
    except Exception:
        pass  # GovLift tables may not be initialized yet

    # ZIG Zero Trust maturity (security_canvas backend — zig_* tables)
    try:
        zconn = _open_canvas_connection("Security")
        if zconn:
            try:
                r = zconn.execute(
                    "SELECT AVG(score) FROM zig_maturity_scores m1 "
                    "WHERE assessment_run_at = ("
                    "  SELECT MAX(assessment_run_at) FROM zig_maturity_scores m2 "
                    "  WHERE m2.pillar_slug = m1.pillar_slug)"
                ).fetchone()
                zig_raw = float(r[0] or 0)
                if zig_raw == 0:
                    cap_r = zconn.execute(
                        "SELECT COUNT(*) as total, "
                        "SUM(CASE WHEN implementation_status='implemented' THEN 1.0 "
                        "    WHEN implementation_status='in_progress' THEN 0.5 ELSE 0.0 END) as impl "
                        "FROM zig_capabilities"
                    ).fetchone()
                    cap_total = float(cap_r["total"] or 0)
                    cap_rate = (float(cap_r["impl"] or 0) / cap_total) if cap_total > 0 else 0.0
                    act_r = zconn.execute(
                        "SELECT COUNT(za.id) as total, "
                        "SUM(CASE WHEN zac.status='complete' THEN 1.0 "
                        "    WHEN zac.status='in_progress' THEN 0.5 ELSE 0.0 END) as comp "
                        "FROM zig_activities za "
                        "LEFT JOIN zig_activity_completions zac ON zac.activity_id = za.id"
                    ).fetchone()
                    act_total = float(act_r["total"] or 0)
                    act_rate = (float(act_r["comp"] or 0) / act_total) if act_total > 0 else 0.0
                    zig_raw = 0.6 * act_rate + 0.4 * cap_rate
                zig_score = round(zig_raw * 100, 1)
                canvas_compliance.append({
                    "name": "Zero Trust",
                    "score": zig_score,
                    "open_findings": 0,
                    "closed_findings": 0,
                    "last_assessed": _max_ts(
                        zconn, "zig_maturity_scores", "assessment_run_at"),
                })
                if zig_score > 0:
                    overall_scores.append(zig_score)
            finally:
                zconn.close()
    except Exception:
        pass  # ZIG tables may not be initialized yet

    # AI-ify compliance posture (deterministic AI-governance grade —
    # the same score shown on /ai-ify/posture and the /compliance hub).
    # Uses the backend-aware canvas connection (PG or SQLite), NOT the
    # stale SQLite file, and the posture engine, NOT opportunity value.
    try:
        from tools.aiify.posture import compute_posture as _aiify_cp
        from tools.aiify.db.init_db import get_connection as _aiify_cn
        _ac = _aiify_cn()
        try:
            _ac.set_security_context(None)  # rls-bypass: aiify canvas tables lack tenant_id/classification; use canvas connection with RLS disabled
        except Exception:
            pass
        try:
            _ap = _aiify_cp(_ac)
        finally:
            _ac.close()
        if _ap.get("counts", {}).get("total_scans", 0) > 0:
            aiify_score = _ap.get("overall_score", 0.0)
            _weak = [d for d in _ap.get("dimensions", [])
                     if d.get("score") is not None and d["score"] < 80]
            canvas_compliance.append({
                "name": "AI-ify",
                "score": aiify_score,
                "open_findings": len(_weak),
                "closed_findings": 0,
            })
            if aiify_score > 0:
                overall_scores.append(aiify_score)
    except Exception:
        pass  # AI-ify tables may not be initialized yet

    overall_score = round(sum(overall_scores) / len(overall_scores), 1) if overall_scores else 0.0
    return canvas_compliance, overall_score
