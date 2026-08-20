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

Reflex contract:
  - run(ctx, conn) -> dict
  - CADENCE_HOURS = 24
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

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

#: Bound on one cycle. 104 designs exist today; this keeps a cycle short and
#: makes the sweep converge over a few runs rather than spiking.
DEFAULT_MAX_PER_RUN = 25

#: ``assessment_type`` written by this reflex. Distinct from the human-triggered
#: types (``observability_compliance``, ``auto_stride``, …) so a reader can tell
#: a scheduled refresh from someone actually reviewing the design, and distinct
#: from ``auto_remediator_verify`` so the two writers never get confused.
ASSESSMENT_TYPE = "scheduled_reassess"

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
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _registry():
    from tools.canvas.auto_remediator import CANVAS_REGISTRY
    return CANVAS_REGISTRY


def _canvas_conn(canvas: str):
    """Open the canvas's own backend-aware connection with RLS disabled."""
    from tools.canvas_compliance.posture import _open_canvas_connection
    return _open_canvas_connection(_DASHBOARD_NAME[canvas])


#: auto_remediator keys canvases lowercase; posture keys them by display name.
_DASHBOARD_NAME = {
    "observability": "Observability",
    "boundary": "Boundary",
    "infra": "Infra",
    "security": "Security",
}


def stale_designs(cc, cfg: Dict[str, Any], stale_after_days: int) -> List[str]:
    """Design ids whose newest assessment is missing or older than the cutoff.

    A design with NO assessment is included — that is the 79-design case on
    Infra, and it is exactly what the posture card cannot see.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_after_days)).isoformat()
    design_tbl, asmt_tbl = cfg["design_table"], cfg["asmt_table"]
    tcol = cfg["asmt_time_col"]
    rows = cc.execute(
        f"SELECT d.id AS id FROM {design_tbl} d "          # nosec B608 - registry-controlled
        f"LEFT JOIN (SELECT design_id, MAX({tcol}) AS newest "  # nosec B608
        f"           FROM {asmt_tbl} GROUP BY design_id) a "    # nosec B608
        f"  ON a.design_id = d.id "
        f"WHERE a.newest IS NULL OR a.newest < %s "
        f"ORDER BY d.id",
        (cutoff,),
    ).fetchall()
    return [(r["id"] if isinstance(r, dict) else r[0]) for r in rows]


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
    score = result.get("score")
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
        round(float(score), 1), str(result.get("grade") or ""), _now_iso(),
        result,
    ))
    return True


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Reassess canvas designs whose newest assessment has gone stale."""
    ctx = ctx or {}
    dry_run = bool(ctx.get("dry_run", False))
    stale_after = int(ctx.get("stale_after_days", DEFAULT_STALE_AFTER_DAYS))
    budget = int(ctx.get("max_per_run", DEFAULT_MAX_PER_RUN))

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
        "errors": [],
        "status": "ok",
    }

    for canvas, cfg in _registry().items():
        if canvas not in _INSERTS:
            # Security's engine takes a design id and its table carries a much
            # wider column set; it is deliberately out of scope here rather
            # than half-written.
            continue
        cc = None
        try:
            cc = _canvas_conn(canvas)
            if cc is None:
                out["errors"].append(f"{canvas}: no connection")
                continue
            stale = stale_designs(cc, cfg, stale_after)
            done = 0
            for design_id in stale:
                if out["reassessed"] + done >= budget:
                    out["skipped_over_budget"].append(f"{canvas}:{design_id}")
                    continue
                try:
                    row = cc.execute(
                        f"SELECT graph_json FROM {cfg['design_table']} WHERE id = %s",  # nosec B608
                        (design_id,),
                    ).fetchone()
                    if not row:
                        continue
                    raw = row["graph_json"] if isinstance(row, dict) else row[0]
                    graph = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    result = assess_design(canvas, design_id, graph)
                    if dry_run:
                        done += 1
                        continue
                    if _persist(cc, canvas, design_id, result):
                        done += 1
                except Exception as exc:      # one design, not the cycle
                    out["errors"].append(f"{canvas}:{design_id}: {exc}")
            if done and not dry_run:
                cc.commit()
            out["by_canvas"][canvas] = {"stale": len(stale), "reassessed": done}
            out["reassessed"] += done
        except Exception as exc:              # one canvas, not the cycle
            out["errors"].append(f"{canvas}: {exc}")
            logger.warning("canvas_reassess: %s failed: %s", canvas, exc)
        finally:
            try:
                if cc is not None:
                    cc.close()
            except Exception:
                pass

    out["metric_value"] = out["reassessed"]
    out["success"] = not out["errors"]
    if out["errors"]:
        out["status"] = "degraded"
    return out
