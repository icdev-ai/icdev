# CUI // SP-CTI
"""Canvas Reassessment Reflex — the thing that was never re-running (rem-hyg-11).

WHY THIS EXISTS. The Compliance Posture widget reads the newest assessment per
design, and every canvas on the live board was between 33 and 71 days stale:

    Security 33d · Observability 53d · Infra 56d · Boundary 56d · Data 71d

rem-hyg-09 made that staleness VISIBLE — the card now renders the age and says
"Not assessed" instead of a fabricated 100. This reflex fixes the CAUSE: a
canvas assessment was only ever written by a human clicking "assess" in the
canvas UI (``POST /api/designs/<id>/governance`` and its siblings). Nothing
scheduled one, so the newest evidence was simply whenever somebody last
happened to look. Measured 2026-08-20, ``infra_designs`` held 84 designs behind
5 assessments — 79 designs that had never been assessed at all.

WHAT IT DOES NOT REUSE, AND WHY. ``auto_remediator.persist_verify_assessment``
already inserts assessment rows and looked like the obvious building block. It
writes a HARDCODED ``score=100.0, grade="A"`` — because
``auto_remediator.reassess_design`` returns only the findings list and throws
the engine's score away one call earlier. That is defensible for its own narrow
job (make a fixed finding fall off a recent-N window) and catastrophic here:
the posture card AVERAGES these rows, so a scheduled writer built on it would
fabricate perfect compliance into the database on a cadence — the very defect
rem-hyg-09 removed from the read path. Verified 2026-08-20 that no
``auto_remediator_verify`` row exists yet on this board, so nothing is
contaminated today.

This module therefore calls the engine itself and persists what the engine
ACTUALLY returned.

  * ``CANVAS_REGISTRY`` is reused (one source of truth for canvas → engine).
  * A design is reassessed only when its newest assessment is older than
    ``stale_after_days`` or missing entirely.
  * ``max_per_run`` bounds the work, and what it SKIPPED is reported by name —
    a truncated sweep that reports only its successes reads as full coverage.
  * Nothing is written in ``dry_run``.
  * It never raises: a broken engine costs one design its refresh, not the
    scheduler its cycle.

GREEN WHILE BLIND (rmf-inert-03). Measured 2026-09-07: ``genesis_reflex_state``
read 17 runs / 17 successes / 0 failures for this reflex while the posture
widget carried six canvases 51-89 days stale. Both numbers were right. The
reflex could reach THREE of the widget's eleven canvases (plus GovLift and Zero
Trust, which are not canvases), and a run that touched 3 of 13 subjects
returned the same shape as one that touched all 13. Three things changed:

  1. THE REFLEX REPORTS ITS OWN COVERAGE, against the SURFACE's list
     (``posture.surface_rows()``, never a copy): ``covered`` and ``uncovered``
     BY NAME, each uncovered row carrying the reason it is not written. A
     surface row with NO decision is ``undecided`` and is an ERROR — the
     silence this card exists to refuse. See ``UNCOVERED`` for the per-canvas
     verdicts; Security stays excluded for the reason it always was, and is
     now visible.
  2. THE REPORT IS PERSISTED. The daemon records ``result["details"]`` and
     nothing else off a reflex result, and this module never set that key —
     so every one of the 17 runs recorded ``{}``: ``skipped_over_budget``,
     ``by_canvas`` and ``errors`` were reported to nobody. The report now
     rides under ``details`` (the claim_verifier_reflex idiom), which is what
     makes ``--starvation`` measurable from primary data from here on.
  3. THE ORDER IS OLDEST-FIRST ACROSS CANVASES, NOT ``ORDER BY d.id`` PER
     CANVAS. ``last_metric_value`` was exactly the 25 budget on 14 of 17 runs,
     and Infra had grown to 154 designs of which 8 had NEVER been assessed:
     each daily cohort of 25 re-stales together a week later and, sorted by
     id, reaches the budget ahead of a never-assessed design whose id sorts
     after it — forever. A starving roster is an ORDER problem before it is
     a size problem; ``DEFAULT_MAX_PER_RUN`` is unchanged, and the run now
     reports ``skipped_never_assessed`` / ``oldest_skipped_age_days`` so the
     next reader can see whether the tail is draining.

DO NOT MANUFACTURE FRESHNESS. Widening coverage makes a canvas's
``last_assessed`` CURRENT; it does not make the estate assessed. The surface
therefore reports ``last_assessed_source`` (scheduled | canvas | None) and
``last_reviewed`` (the newest NON-scheduled row) beside it — see
``posture._last_assessed_detail`` — and the widget renders both.

Reflex contract:
  - run(ctx, conn) -> dict
  - CADENCE_HOURS = 24
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from tools.canvas_compliance.posture import (
    SCHEDULED_ASSESSMENT_TYPE,
    surface_rows,
)
from tools.logging.icdev_logger import get_logger

IMPLEMENTATION_STATUS = "full"

logger = get_logger(__name__)

CADENCE_HOURS = 24

#: How old the newest assessment may be before a design is refreshed. A day
#: under the cadence would refresh everything every run; well over it means a
#: design is reassessed roughly weekly, which is frequent enough that the
#: posture card is never months stale and rare enough that 104 designs do not
#: re-run daily.
DEFAULT_STALE_AFTER_DAYS = 7

#: Bound on one cycle. 104 designs existed when this shipped (160 covered
#: designs on 2026-09-07); this keeps a cycle short and makes the sweep converge
#: over a few runs rather than spiking. NOT raised by rmf-inert-03: the
#: saturation it measured was an ordering defect (see module docstring), and
#: raising the number would have hidden it.
DEFAULT_MAX_PER_RUN = 25

#: ``assessment_type`` written by this reflex. Distinct from the human-triggered
#: types (``observability_compliance``, ``auto_stride``, …) so a reader can tell
#: a scheduled refresh from someone actually reviewing the design, and distinct
#: from ``auto_remediator_verify`` so the two writers never get confused. Spelled
#: ONCE, in posture.py, because the surface reads it back.
ASSESSMENT_TYPE = SCHEDULED_ASSESSMENT_TYPE

#: Canvases this reflex can REASSESS that ``auto_remediator.CANVAS_REGISTRY``
#: does not know. That registry is the remediator's roster of canvases it can
#: MUTATE (its handlers, its CLI choices); adding a canvas there widens the
#: remediator's reach, which is not this reflex's decision to make. Same shape,
#: plus ``score_key`` where the engine spells its score differently.
_REASSESS_ONLY: Dict[str, Dict[str, Any]] = {
    "data": {
        "db": "data_canvas.db",
        "design_table": "data_designs",
        "asmt_table": "dd_assessments",
        "asmt_time_col": "created_at",
        "engine_module": "tools.data_canvas.data_engine",
        "engine_func": "assess_data_design",
        "engine_takes_design_id": True,
        # The Data engine returns `risk_score` (100 - penalty) and the canvas's
        # own assess route writes THAT into dd_assessments.score.
        "score_key": "risk_score",
    },
}

#: Where the engine's score lives on its result, per canvas. Default "score".
_SCORE_KEY = {canvas: cfg["score_key"] for canvas, cfg in _REASSESS_ONLY.items() if "score_key" in cfg}

#: Per-canvas INSERT shape. Column lists match the ones
#: ``auto_remediator.persist_verify_assessment`` uses, so the rows are
#: indistinguishable in shape from what the canvas already stores — only the
#: VALUES are real rather than hardcoded.
_INSERTS = {
    "observability": (
        "INSERT INTO od_assessments (id, design_id, assessment_type, "
        "findings_json, score, grade, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        lambda nid, did, fj, score, grade, ts, res: (
            nid, did, ASSESSMENT_TYPE, fj, score, grade, ts),
    ),
    # The cat1/cat2/cat3 columns are NOT decorative: the posture card's Boundary
    # branch sums them for `open_findings`. `persist_verify_assessment` writes
    # 0/0/0 there, which would report ZERO findings for a design the engine
    # found 17 in — the same fabrication as its hardcoded 100.0, one column
    # over. They are carried through from the engine result instead.
    "boundary": (
        "INSERT INTO bd_assessments (id, design_id, assessment_type, findings_json, "
        "score, grade, cat1_findings, cat2_findings, cat3_findings, "
        "nist_coverage_json, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        lambda nid, did, fj, score, grade, ts, res: (
            nid, did, ASSESSMENT_TYPE, fj, score, grade,
            int(res.get("cat1_findings") or 0),
            int(res.get("cat2_findings") or 0),
            int(res.get("cat3_findings") or 0),
            json.dumps(res.get("nist_coverage") or {}), ts),
    ),
    "infra": (
        "INSERT INTO idc_assessments (id, design_id, assessment_type, findings_json, "
        "score, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
        lambda nid, did, fj, score, grade, ts, res: (
            nid, did, ASSESSMENT_TYPE, fj, score, ts),
    ),
    # The same six columns the Data canvas's own assess route writes
    # (data_canvas/blueprint.py); the posture reads `score` by newest
    # created_at per design, so a scheduled row replaces a stale one there.
    # Measured 2026-09-07: 6 designs, 6 rows, every one from 2026-06-09.
    "data": (
        "INSERT INTO dd_assessments (id, design_id, assessment_type, findings_json, "
        "score, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
        lambda nid, did, fj, score, grade, ts, res: (
            nid, did, ASSESSMENT_TYPE, fj, score, ts),
    ),
}

#: auto_remediator keys canvases lowercase; posture keys them by display name.
_DASHBOARD_NAME = {
    "observability": "Observability",
    "boundary": "Boundary",
    "infra": "Infra",
    "security": "Security",
    "data": "Data",
}

#: Every posture-surface row this reflex does NOT write, with the reason. Keyed
#: by the surface's display name. A row here is a STATED ABSENCE; a surface row
#: in neither ``covered_canvases()`` nor this map is ``undecided`` and fails the
#: run. Each reason is a constraint, not a preference — remove an entry only by
#: adding a WRITABLE ``_INSERTS`` shape, and re-read the reason first.
UNCOVERED: Dict[str, str] = {
    "Security": (
        "sc_assessments is scored from risk_score/posture_grade over a wider column "
        "set (total_threats, total_controls, trigger_source) and the engine takes a "
        "design id; a `score` row shaped like the others would be read by nothing. "
        "Excluded since rem-hyg-11, now visible."
    ),
    "Network": (
        "no design/assessment pair: nc_compliance_checks holds one row per CHECK per "
        "topology with no writer column, and the posture SUMs passed/failed over "
        "EVERY row -- a scheduled row would join the denominator forever instead of "
        "replacing a stale one."
    ),
    "Pipeline": (
        "same shape as Network: pc_compliance_checks is one row per CHECK per "
        "pipeline, summed over every row, with no writer column."
    ),
    "Agentic AI": (
        "aadc_assessments carries no assessment_type column, so a scheduled row "
        "would be indistinguishable from a review; the event-driven aadc_compliance "
        "reflex already writes this table on assess events."
    ),
    "AI/ML": (
        "aiml_assessments carries no assessment_type column (framework_id names WHAT "
        "was assessed, not who) and aiml_engine.run_assessment persists its own "
        "untagged row -- there is no engine-only call to re-derive from."
    ),
    "QDC": (
        "the newest qdc_assessments row also carries uqs_score/uqs_breakdown, derived "
        "by the assess route from qdc_gate_results rather than by the engine, and the "
        "canvas renders them off the newest row; a scheduled row would show UQS 0.0 "
        "-- a fabricated quality score wearing the newest timestamp. Measured "
        "2026-09-07: 153 designs, 148 never assessed."
    ),
    "Migration": (
        "the posture scores Migration from assessment_type='validation' rows ONLY, so "
        "a scheduled_reassess row would refresh the AGE of a score it cannot move -- "
        "manufactured freshness by construction. 0 designs on the live board "
        "2026-09-07."
    ),
    "GovLift": "not a canvas: STIG check rows in icdev.db; no design/engine pair to re-run.",
    "Zero Trust": "not a canvas: ZIG maturity scores; no design/engine pair to re-run.",
    "AI-ify": "not a canvas: a posture computed from scans, not from designs.",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _registry() -> Dict[str, Dict[str, Any]]:
    from tools.canvas.auto_remediator import CANVAS_REGISTRY
    merged = dict(CANVAS_REGISTRY)
    merged.update(_REASSESS_ONLY)
    return merged


def _canvas_conn(canvas: str):
    """Open the canvas's own backend-aware connection with RLS disabled."""
    from tools.canvas_compliance.posture import _open_canvas_connection
    return _open_canvas_connection(_DASHBOARD_NAME[canvas])


def covered_canvases(registry: Optional[Dict[str, Any]] = None) -> List[str]:
    """Surface display names this reflex can WRITE, in registry order.

    A canvas is covered only when it is in the registry AND has an ``_INSERTS``
    shape AND maps to a surface name — three conditions, because each one alone
    has been true of a canvas nothing refreshed.
    """
    reg = registry if registry is not None else _registry()
    return [_DASHBOARD_NAME[c] for c in reg if c in _INSERTS and c in _DASHBOARD_NAME]


def coverage_report(registry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """covered / uncovered / undecided BY NAME against ``posture.surface_rows()``.

    ``undecided`` is the finding: a row the widget renders that this reflex
    neither writes nor has a written reason for. ``decisions_for_absent_rows``
    is the converse — a reason kept for a row the surface no longer renders —
    reported so a stale entry cannot sit here unnoticed.
    """
    surface = surface_rows()
    covered_set = set(covered_canvases(registry))
    covered = [n for n in surface if n in covered_set]
    uncovered = {n: UNCOVERED[n] for n in surface if n not in covered_set and n in UNCOVERED}
    undecided = [n for n in surface if n not in covered_set and n not in UNCOVERED]
    return {
        "surface": surface,
        "surface_count": len(surface),
        "covered": covered,
        "covered_count": len(covered),
        "uncovered": uncovered,
        "undecided": undecided,
        "decisions_for_absent_rows": sorted(set(UNCOVERED) - set(surface)),
    }


def stale_designs_ranked(cc, cfg: Dict[str, Any], stale_after_days: int) -> List[Tuple[str, Optional[str]]]:
    """``(design_id, newest_assessment_or_None)`` for every stale design, OLDEST FIRST.

    A design with NO assessment is included and sorts FIRST — that is the
    79-design case on Infra, and it is exactly what the posture card cannot
    see. The order is the whole point (rmf-inert-03): sorted by id, a
    never-assessed design whose id sorts after a re-staling cohort never
    reaches the budget. ``(newest IS NULL) DESC`` sorts the same way on
    PostgreSQL (false < true) and SQLite (0 < 1); a bare ``ASC`` would put
    NULLs last on one and first on the other.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_after_days)).isoformat()
    design_tbl, asmt_tbl = cfg["design_table"], cfg["asmt_table"]
    tcol = cfg["asmt_time_col"]
    rows = cc.execute(
        f"SELECT d.id AS id, a.newest AS newest FROM {design_tbl} d "  # nosec B608 - registry-controlled
        f"LEFT JOIN (SELECT design_id, MAX({tcol}) AS newest "        # nosec B608
        f"           FROM {asmt_tbl} GROUP BY design_id) a "          # nosec B608
        f"  ON a.design_id = d.id "
        f"WHERE a.newest IS NULL OR a.newest < %s "
        f"ORDER BY (a.newest IS NULL) DESC, a.newest ASC, d.id",
        (cutoff,),
    ).fetchall()
    out: List[Tuple[str, Optional[str]]] = []
    for r in rows:
        if isinstance(r, dict) or hasattr(r, "keys"):
            did = r["id"]
            newest = r["newest"] if "newest" in r.keys() else None
        else:
            did, newest = r[0], (r[1] if len(r) > 1 else None)
        out.append((str(did), str(newest) if newest else None))
    return out


def stale_designs(cc, cfg: Dict[str, Any], stale_after_days: int) -> List[str]:
    """Design ids whose newest assessment is missing or older than the cutoff."""
    return [did for did, _newest in stale_designs_ranked(cc, cfg, stale_after_days)]


def assess_design(canvas: str, design_id: str, graph: dict) -> Dict[str, Any]:
    """Run the canvas engine and return its FULL result.

    Deliberately not ``auto_remediator.reassess_design``, which returns only
    ``findings`` and discards the score — the discard is what forces its
    persistence helper to invent a 100.0.
    """
    import importlib

    cfg = _registry()[canvas]
    eng = importlib.import_module(cfg["engine_module"])
    fn = getattr(eng, cfg["engine_func"])
    result = fn(design_id, graph) if cfg.get("engine_takes_design_id") else fn(graph)
    return result if isinstance(result, dict) else {}


def _persist(cc, canvas: str, design_id: str, result: Dict[str, Any]) -> bool:
    """Insert one assessment row carrying the engine's REAL score."""
    spec = _INSERTS.get(canvas)
    if not spec:
        return False
    score = result.get(_SCORE_KEY.get(canvas, "score"))
    # `is None`, NEVER `if not score`: three boundary designs score a real 0.0
    # on the live board, and a falsiness check would skip them every cycle —
    # leaving the worst-scoring designs permanently unrefreshed.
    if score is None:
        # The engine ran but produced no score. Writing a row without one would
        # put a NULL into the column the posture average reads.
        return False
    sql, build = spec
    cc.execute(sql, build(
        str(uuid.uuid4()), design_id,
        json.dumps(result.get("findings", []) or []),
        round(float(score), 1), str(result.get("grade") or result.get("posture_grade") or ""),
        _now_iso(),
        result,
    ))
    return True


def _age_days(ts: Optional[str]) -> Optional[float]:
    """Days since an ISO timestamp, or None when it cannot be parsed."""
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace(" ", "T").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0, 1)
    except (ValueError, TypeError):
        return None


def _rank_key(item: Tuple[str, str, Optional[str]]) -> Tuple[int, str, str, str]:
    """Oldest first ACROSS canvases; never-assessed first of all; then stable."""
    canvas, design_id, newest = item
    return (0 if newest is None else 1, newest or "", canvas, design_id)


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Reassess canvas designs whose newest assessment has gone stale."""
    ctx = ctx or {}
    dry_run = bool(ctx.get("dry_run", False))
    stale_after = int(ctx.get("stale_after_days", DEFAULT_STALE_AFTER_DAYS))
    budget = int(ctx.get("max_per_run", DEFAULT_MAX_PER_RUN))

    registry = _registry()
    coverage = coverage_report(registry)

    out: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "dry_run": dry_run,
        "stale_after_days": stale_after,
        "max_per_run": budget,
        "reassessed": 0,
        "by_canvas": {},
        # Named, never merely counted: a truncated sweep reporting only its
        # successes reads as full coverage.
        "skipped_over_budget": [],
        "skipped_never_assessed": 0,
        "oldest_skipped_age_days": None,
        "budget_saturated": False,
        # covered / uncovered BY NAME against the surface's own list. A run
        # that touched 3 of 13 subjects must not return the shape of one that
        # touched all 13 (rmf-inert-03).
        "coverage": coverage,
        "errors": [],
        "status": "ok",
    }
    for name in coverage["undecided"]:
        out["errors"].append(
            f"coverage: '{name}' is on the posture surface with no coverage decision "
            f"(neither written nor named in UNCOVERED)")

    # Phase 1 — gather every stale design across the covered canvases, with the
    # age of its newest assessment, so the budget is spent oldest-first across
    # the whole roster rather than canvas-by-canvas in id order.
    conns: Dict[str, Any] = {}
    queue: List[Tuple[str, str, Optional[str]]] = []
    try:
        for canvas, cfg in registry.items():
            if canvas not in _INSERTS:
                continue   # named in coverage["uncovered"], never silent
            try:
                cc = _canvas_conn(canvas)
                if cc is None:
                    out["errors"].append(f"{canvas}: no connection")
                    continue
                conns[canvas] = cc
                ranked = stale_designs_ranked(cc, cfg, stale_after)
                out["by_canvas"][canvas] = {
                    "stale": len(ranked),
                    "never_assessed": sum(1 for _d, newest in ranked if newest is None),
                    "reassessed": 0,
                }
                queue.extend((canvas, did, newest) for did, newest in ranked)
            except Exception as exc:              # one canvas, not the cycle
                out["errors"].append(f"{canvas}: {exc}")
                logger.warning("canvas_reassess: %s failed: %s", canvas, exc)

        queue.sort(key=_rank_key)
        work, skipped = queue[:budget], queue[budget:]
        out["skipped_over_budget"] = [f"{c}:{d}" for c, d, _n in skipped]
        out["skipped_never_assessed"] = sum(1 for _c, _d, n in skipped if n is None)
        out["budget_saturated"] = bool(skipped)
        skipped_ages = [a for a in (_age_days(n) for _c, _d, n in skipped) if a is not None]
        out["oldest_skipped_age_days"] = max(skipped_ages) if skipped_ages else None

        # Phase 2 — the work, oldest first.
        touched: Dict[str, int] = {}
        for canvas, design_id, _newest in work:
            cc = conns.get(canvas)
            cfg = registry[canvas]
            try:
                row = cc.execute(
                    f"SELECT graph_json FROM {cfg['design_table']} WHERE id = %s",  # nosec B608
                    (design_id,),
                ).fetchone()
                if not row:
                    continue
                raw = row["graph_json"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
                graph = json.loads(raw) if isinstance(raw, str) else (raw or {})
                result = assess_design(canvas, design_id, graph)
                if dry_run:
                    touched[canvas] = touched.get(canvas, 0) + 1
                    continue
                if _persist(cc, canvas, design_id, result):
                    touched[canvas] = touched.get(canvas, 0) + 1
            except Exception as exc:      # one design, not the cycle
                out["errors"].append(f"{canvas}:{design_id}: {exc}")
        for canvas, done in touched.items():
            out["by_canvas"].setdefault(canvas, {"stale": 0, "never_assessed": 0, "reassessed": 0})
            out["by_canvas"][canvas]["reassessed"] = done
            out["reassessed"] += done
            if done and not dry_run:
                try:
                    conns[canvas].commit()
                except Exception as exc:
                    out["errors"].append(f"{canvas}: commit failed: {exc}")
    finally:
        for cc in conns.values():
            try:
                cc.close()
            except Exception:
                pass

    out["metric_value"] = out["reassessed"]
    out["success"] = not out["errors"]
    if out["errors"]:
        out["status"] = "degraded"
    # The daemon persists `details` and NOTHING ELSE off a reflex result. For
    # 17 runs this module set no such key and recorded `{}` -- every field it
    # "reported" went nowhere. This is what the `--starvation` survey reads.
    out["details"] = {
        k: out[k] for k in (
            "status", "dry_run", "stale_after_days", "max_per_run", "reassessed",
            "by_canvas", "skipped_over_budget", "skipped_never_assessed",
            "oldest_skipped_age_days", "budget_saturated", "coverage", "errors",
        )
    }
    return out


# --------------------------------------------------------------------------- #
# Hand-run surveys. The reflex itself is dispatched by the genesis daemon.
# --------------------------------------------------------------------------- #
def starvation_survey(runs: int = 30) -> Dict[str, Any]:
    """Which designs were skipped over budget on EVERY recorded run?

    Read from ``genesis_audit.details`` — the daemon's own record of each run
    — never from this process's memory. Runs recorded before rmf-inert-03
    carry ``{}`` (see module docstring) and are counted as
    ``runs_without_report``; a survey over only such runs is UNMEASURABLE,
    never "nothing starved".
    """
    from tools.db.storage import get_connection

    out: Dict[str, Any] = {
        "runs_read": 0, "runs_with_report": 0, "runs_without_report": 0,
        "starved": [], "skip_counts": {}, "status": "unmeasurable",
    }
    conn = get_connection()
    try:
        try:
            conn.set_security_context(None)  # rls-bypass: genesis_audit carries no tenant/classification columns
        except Exception:
            pass
        rows = conn.execute(
            "SELECT details FROM genesis_audit WHERE reflex_name = %s "
            "AND (event_type LIKE %s OR event_type LIKE %s) "
            "ORDER BY created_at DESC LIMIT %s",
            ("canvas_reassess", "%.reflex.completed", "%.reflex.failed", int(runs)),
        ).fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    sets: List[set] = []
    for r in rows:
        out["runs_read"] += 1
        raw = r["details"] if isinstance(r, dict) or hasattr(r, "keys") else r[0]
        try:
            d = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (json.JSONDecodeError, TypeError):
            d = {}
        if "skipped_over_budget" not in d:
            out["runs_without_report"] += 1
            continue
        out["runs_with_report"] += 1
        skipped = set(d.get("skipped_over_budget") or [])
        sets.append(skipped)
        for name in skipped:
            out["skip_counts"][name] = out["skip_counts"].get(name, 0) + 1
    if sets:
        out["starved"] = sorted(set.intersection(*sets))
        out["status"] = "measured"
        out["reason"] = None
    else:
        out["reason"] = ("no recorded run carries a skipped list; runs before rmf-inert-03 "
                         "persisted {} and cannot be surveyed")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="canvas_reassess: coverage report, dry run, or starvation survey")
    ap.add_argument("--coverage", action="store_true",
                    help="covered / uncovered BY NAME against the posture surface; touches no database")
    ap.add_argument("--dry-run", action="store_true",
                    help="run the sweep against the live canvases and WRITE NOTHING")
    ap.add_argument("--starvation", action="store_true",
                    help="designs skipped over budget on every recorded run (genesis_audit)")
    ap.add_argument("--runs", type=int, default=30, help="how many recorded runs --starvation reads")
    args = ap.parse_args(argv)
    if args.starvation:
        print(json.dumps(starvation_survey(args.runs), indent=2, default=str))
    elif args.dry_run:
        print(json.dumps(run({"dry_run": True}), indent=2, default=str))
    else:
        print(json.dumps(coverage_report(), indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
