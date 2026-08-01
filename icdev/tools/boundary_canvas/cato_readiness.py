# CUI // SP-CTI
"""BDC cATO Readiness Scorer (task bdr-feat-2).

Computes a real-time Continuous Authority to Operate (cATO) readiness score
(0-100) for a boundary design from four independent components:

  * control_coverage — mean control-satisfaction of the latest compliance
                       snapshot (compliance_twin_snapshots, MAIN DB).
  * poam             — open POA&M items linked to the design's twin
                       violations (compliance_twin_violations -> poam_items,
                       MAIN DB).
  * isa_expiry       — expiry health of the design's interconnection security
                       agreements (bd_isa_tracker, CANVAS DB).
  * freshness        — age of the latest compliance snapshot (exponential
                       decay).

Design notes
------------
* Weights and band thresholds live in ``args/bdc_readiness_config.yaml`` and
  are loaded with a safe default fallback — NO weights are hardcoded in the
  scoring logic itself.
* All analytics are computed in Python from plain-column reads. There is NO
  SQLite-dialect JSON SQL (``json_extract`` etc.) in the runtime path; JSON
  columns (when read) are parsed with ``json.loads``.
* Missing tables / empty data degrade gracefully: an unavailable component is
  excluded from the weighted average (weights re-normalise over available
  components). If NO component has data, ``score`` is ``None`` and ``band`` is
  ``"unknown"`` — never a 500.
* Main-DB SQL uses PG-native ``%s`` placeholders (the storage layer translates
  for the SQLite init-fallback). The canvas-DB read uses no bound parameters
  (rows are filtered in Python) so it is dialect-agnostic.

Response shape mirrors the ``canvas_compute_readiness`` MCP tool where it makes
sense: each component carries a ``score`` key (like the MCP ``breakdown``), and
``readiness_score`` is exposed as an alias of ``score``. The primary contract
for this task is ``{score, band, components}``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration (weights + thresholds) — file-driven with safe fallback
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "args" / "bdc_readiness_config.yaml"

_DEFAULT_CONFIG: Dict[str, Any] = {
    "weights": {
        "control_coverage": 0.40,
        "poam": 0.25,
        "isa_expiry": 0.20,
        "freshness": 0.15,
    },
    "bands": {"green": 80, "amber": 50},
    "freshness_half_life_days": 30,
    "poam_full_penalty_count": 20,
    "isa_warn_days": 90,
}

_COMPONENT_KEYS = ("control_coverage", "poam", "isa_expiry", "freshness")


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load readiness config from YAML, falling back to _DEFAULT_CONFIG.

    Missing file, unreadable file, or malformed YAML all resolve to the
    built-in default. Partial files are merged over the default so that an
    operator can override only the keys they care about.
    """
    cfg_path = Path(path) if path is not None else _CONFIG_PATH
    merged: Dict[str, Any] = json.loads(json.dumps(_DEFAULT_CONFIG))  # deep copy
    try:
        import yaml  # local import — optional dependency

        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            if isinstance(raw.get("weights"), dict):
                merged["weights"].update(
                    {k: v for k, v in raw["weights"].items() if isinstance(v, (int, float))}
                )
            if isinstance(raw.get("bands"), dict):
                merged["bands"].update(
                    {k: v for k, v in raw["bands"].items() if isinstance(v, (int, float))}
                )
            for scalar in ("freshness_half_life_days", "poam_full_penalty_count", "isa_warn_days"):
                if isinstance(raw.get(scalar), (int, float)):
                    merged[scalar] = raw[scalar]
    except FileNotFoundError:
        logger.info("bdc_readiness_config.yaml not found at %s — using defaults", cfg_path)
    except Exception as exc:  # noqa: BLE001 — any parse error -> safe default
        logger.warning("Failed to load bdc_readiness_config.yaml (%s) — using defaults", exc)
    return merged


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a timestamp string (ISO-8601 or 'YYYY-MM-DD HH:MM:SS') to UTC.

    Returns None on any failure. Naive timestamps are assumed UTC.
    """
    if not value:
        return None
    text = str(value).strip()
    # Normalise a trailing 'Z' and a space separator for fromisoformat.
    candidate = text.replace("Z", "+00:00").replace(" ", "T", 1)
    for attempt in (candidate, text[:19].replace(" ", "T")):
        try:
            dt = datetime.fromisoformat(attempt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _rows_as_dicts(rows) -> List[Dict[str, Any]]:
    """Normalise DB rows (sqlite3.Row / dict / DictRow / tuple) to plain dicts."""
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        if isinstance(r, dict):
            out.append(dict(r))
        else:
            try:
                out.append(dict(r))  # sqlite3.Row / DictRow support dict()
            except (TypeError, ValueError):
                # Fall back to keys() if available
                keys = getattr(r, "keys", None)
                if callable(keys):
                    out.append({k: r[k] for k in r.keys()})
    return out


def _band_for(score: Optional[float], bands: Dict[str, Any]) -> str:
    if score is None:
        return "unknown"
    if score >= bands.get("green", 80):
        return "green"
    if score >= bands.get("amber", 50):
        return "amber"
    return "red"


# ---------------------------------------------------------------------------
# Component scorers — each returns (score_or_None, detail_dict)
# ---------------------------------------------------------------------------

def _load_snapshot_rows(conn, project_id: str) -> Optional[List[Dict[str, Any]]]:
    """Return plain-column snapshot rows for a project, or None if unavailable."""
    try:
        rows = conn.execute(
            "SELECT snapshot_id, implementation_status, score, created_at "
            "FROM compliance_twin_snapshots WHERE project_id = %s",
            (project_id,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — missing table etc. -> unavailable
        logger.info("compliance_twin_snapshots unavailable for %s: %s", project_id, exc)
        return None
    return _rows_as_dicts(rows)


def _latest_snapshot(rows: List[Dict[str, Any]]):
    """Return (snapshot_id, [rows], latest_created_at_datetime) for newest snapshot."""
    if not rows:
        return None, [], None
    # Group by snapshot_id; the newest snapshot is the one whose max created_at
    # is greatest (fall back to lexical snapshot_id ordering on ties/None).
    by_snap: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_snap.setdefault(r.get("snapshot_id"), []).append(r)

    def _snap_ts(snap_rows: List[Dict[str, Any]]) -> str:
        stamps = [str(r.get("created_at") or "") for r in snap_rows]
        return max(stamps) if stamps else ""

    latest_id = max(by_snap, key=lambda sid: (_snap_ts(by_snap[sid]), str(sid or "")))
    latest_rows = by_snap[latest_id]
    return latest_id, latest_rows, _parse_ts(_snap_ts(latest_rows))


def _score_control_coverage(latest_id, latest_rows) -> tuple[Optional[float], Dict[str, Any]]:
    if not latest_rows:
        return None, {"score": None, "reason": "no_snapshot"}
    # Mean of non-null control scores (0.0-1.0). not_applicable/not_assessed
    # rows carry a NULL score and are excluded from the denominator.
    scored = [
        float(r["score"])
        for r in latest_rows
        if r.get("score") is not None
    ]
    satisfied = sum(
        1 for r in latest_rows if r.get("implementation_status") == "satisfied"
    )
    if not scored:
        return None, {
            "score": None,
            "reason": "no_scored_controls",
            "snapshot_id": latest_id,
            "total_controls": len(latest_rows),
        }
    coverage = round((sum(scored) / len(scored)) * 100.0, 1)
    return coverage, {
        "score": coverage,
        "snapshot_id": latest_id,
        "total_controls": len(latest_rows),
        "scored_controls": len(scored),
        "satisfied_controls": satisfied,
    }


def _score_freshness(latest_ts, config) -> tuple[Optional[float], Dict[str, Any]]:
    if latest_ts is None:
        return None, {"score": None, "reason": "no_snapshot"}
    half_life = float(config.get("freshness_half_life_days", 30)) or 30.0
    age_days = max(0.0, (datetime.now(timezone.utc) - latest_ts).total_seconds() / 86400.0)
    freshness = round(100.0 * (0.5 ** (age_days / half_life)), 1)
    return freshness, {
        "score": freshness,
        "latest_snapshot_at": latest_ts.isoformat(),
        "age_days": round(age_days, 2),
        "half_life_days": half_life,
    }


def _score_poam(conn, project_id: str, config) -> tuple[Optional[float], Dict[str, Any]]:
    try:
        viol_rows = conn.execute(
            "SELECT poam_id FROM compliance_twin_violations WHERE project_id = %s",
            (project_id,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — missing table -> unavailable
        logger.info("compliance_twin_violations unavailable for %s: %s", project_id, exc)
        return None, {"score": None, "reason": "violations_unavailable"}

    viols = _rows_as_dicts(viol_rows)
    if not viols:
        return None, {"score": None, "reason": "no_violations"}

    linked_ids = {v["poam_id"] for v in viols if v.get("poam_id") is not None}

    open_count = 0
    if linked_ids:
        try:
            poam_rows = conn.execute(
                "SELECT id, status FROM poam_items WHERE project_id = %s",
                (project_id,),
            ).fetchall()
            for p in _rows_as_dicts(poam_rows):
                if p.get("id") in linked_ids and str(p.get("status")) == "open":
                    open_count += 1
        except Exception as exc:  # noqa: BLE001 — poam_items missing -> treat as none open
            logger.info("poam_items unavailable for %s: %s", project_id, exc)

    full_penalty = float(config.get("poam_full_penalty_count", 20)) or 20.0
    score = round(100.0 * max(0.0, 1.0 - (open_count / full_penalty)), 1)
    return score, {
        "score": score,
        "open_poam_count": open_count,
        "linked_violations": len(viols),
        "full_penalty_count": full_penalty,
    }


def _score_isa_expiry(canvas_conn, design_id: str, config) -> tuple[Optional[float], Dict[str, Any]]:
    """Score ISA expiry health from bd_isa_tracker (canvas DB).

    Reads all tracker rows with no bound parameters (dialect-agnostic) and
    filters by design_id in Python. Reuses isa_expiry._parse_expiry for date
    parsing. Per ISA credit: valid=1.0, expiring-soon=0.5, expired=0.0.
    """
    if canvas_conn is None:
        return None, {"score": None, "reason": "no_canvas_conn"}
    try:
        rows = canvas_conn.execute(
            "SELECT design_id, status, expiry_date, isa_expiry_date FROM bd_isa_tracker"
        ).fetchall()
    except Exception:
        # isa_expiry_date column may not exist yet — retry without it.
        try:
            rows = canvas_conn.execute(
                "SELECT design_id, status, expiry_date FROM bd_isa_tracker"
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 — missing table -> unavailable
            logger.info("bd_isa_tracker unavailable for %s: %s", design_id, exc)
            return None, {"score": None, "reason": "isa_tracker_unavailable"}

    from tools.boundary_canvas.isa_expiry import _parse_expiry

    isas = [
        r for r in _rows_as_dicts(rows)
        if r.get("design_id") == design_id
        and str(r.get("status")) not in ("terminated", "expired")
    ]
    if not isas:
        return None, {"score": None, "reason": "no_isas"}

    warn_days = int(config.get("isa_warn_days", 90) or 90)
    today = datetime.now(timezone.utc).date()
    expired = 0
    expiring_soon = 0
    credit_total = 0.0
    for isa in isas:
        expiry_raw = isa.get("isa_expiry_date") or isa.get("expiry_date")
        expiry = _parse_expiry(expiry_raw) if expiry_raw else None
        if expiry is None:
            # No expiry recorded — treat as valid (credit 1.0).
            credit_total += 1.0
            continue
        days_left = (expiry - today).days
        if days_left < 0:
            expired += 1
            credit_total += 0.0
        elif days_left <= warn_days:
            expiring_soon += 1
            credit_total += 0.5
        else:
            credit_total += 1.0

    score = round((credit_total / len(isas)) * 100.0, 1)
    return score, {
        "score": score,
        "total_isas": len(isas),
        "expired": expired,
        "expiring_soon": expiring_soon,
        "warn_days": warn_days,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_readiness(
    design_id: str,
    project_id: Optional[str] = None,
    *,
    conn=None,
    canvas_conn=None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute the 0-100 cATO readiness composite for a boundary design.

    Args:
        design_id:   Boundary design id (used to look up ISAs on the canvas DB).
        project_id:  Compliance project id for snapshot/POA&M lookups on the
                     MAIN DB. Defaults to ``design_id`` (the twin page uses the
                     design id as the snapshot project_id).
        conn:        Optional MAIN-DB connection (StorageConnection-style, `%s`
                     placeholders). Opens its own via tools.db.storage if None.
        canvas_conn: Optional CANVAS-DB connection for bd_isa_tracker. Opens its
                     own via boundary_canvas.db.init_db if None.
        config:      Optional pre-loaded config dict. Loaded from YAML if None.

    Returns:
        dict: ``{design_id, project_id, score, readiness_score, band,
                 components{...}, weights{...}}``. ``score``/``readiness_score``
        are ``None`` and ``band`` is ``"unknown"`` when no component has data.
    """
    cfg = config if config is not None else load_config()
    project_id = project_id or design_id
    weights = cfg.get("weights", _DEFAULT_CONFIG["weights"])
    bands = cfg.get("bands", _DEFAULT_CONFIG["bands"])

    _own_conn = conn is None
    _own_canvas = canvas_conn is None
    try:
        if _own_conn:
            try:
                from tools.db.storage import get_connection
                conn = get_connection()
            except Exception as exc:  # noqa: BLE001 — degrade gracefully
                logger.warning("main DB connection failed: %s", exc)
                conn = None
        if _own_canvas:
            try:
                from tools.boundary_canvas.db.init_db import get_connection as _bdc_conn
                canvas_conn = _bdc_conn()
            except Exception as exc:  # noqa: BLE001 — degrade gracefully
                logger.warning("canvas DB connection failed: %s", exc)
                canvas_conn = None

        # --- snapshot-derived components (main DB) ---
        snap_rows = _load_snapshot_rows(conn, project_id) if conn is not None else None
        if snap_rows is None:
            cov_score, cov_detail = None, {"score": None, "reason": "snapshots_unavailable"}
            fresh_score, fresh_detail = None, {"score": None, "reason": "snapshots_unavailable"}
        else:
            latest_id, latest_rows, latest_ts = _latest_snapshot(snap_rows)
            cov_score, cov_detail = _score_control_coverage(latest_id, latest_rows)
            fresh_score, fresh_detail = _score_freshness(latest_ts, cfg)

        # --- POA&M component (main DB) ---
        if conn is not None:
            poam_score, poam_detail = _score_poam(conn, project_id, cfg)
        else:
            poam_score, poam_detail = None, {"score": None, "reason": "main_db_unavailable"}

        # --- ISA expiry component (canvas DB) ---
        isa_score, isa_detail = _score_isa_expiry(canvas_conn, design_id, cfg)

        component_scores = {
            "control_coverage": cov_score,
            "poam": poam_score,
            "isa_expiry": isa_score,
            "freshness": fresh_score,
        }
        components = {
            "control_coverage": cov_detail,
            "poam": poam_detail,
            "isa_expiry": isa_detail,
            "freshness": fresh_detail,
        }

        # --- weighted composite over available components (re-normalised) ---
        available = {k: v for k, v in component_scores.items() if v is not None}
        if not available:
            score: Optional[float] = None
        else:
            total_w = sum(float(weights.get(k, 0.0)) for k in available)
            if total_w <= 0:
                # Degenerate weights — fall back to a simple mean.
                score = round(sum(available.values()) / len(available), 1)
            else:
                score = round(
                    sum(available[k] * float(weights.get(k, 0.0)) for k in available) / total_w,
                    1,
                )

        band = _band_for(score, bands)

        return {
            "design_id": design_id,
            "project_id": project_id,
            "score": score,
            "readiness_score": score,  # alias mirroring canvas_compute_readiness
            "band": band,
            "components": components,
            "weights": {k: float(weights.get(k, 0.0)) for k in _COMPONENT_KEYS},
        }
    finally:
        if _own_conn and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        if _own_canvas and canvas_conn is not None:
            try:
                canvas_conn.close()
            except Exception:  # noqa: BLE001
                pass


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="BDC cATO readiness scorer (0-100)")
    parser.add_argument("--design-id", required=True, dest="design_id")
    parser.add_argument("--project-id", dest="project_id", default=None)
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()

    result = compute_readiness(args.design_id, args.project_id)
    if args.json_out:
        print(json.dumps(result, indent=2))
    else:
        print(f"cATO readiness for {args.design_id}: {result['score']} ({result['band']})")
        for name, detail in result["components"].items():
            print(f"  {name}: {detail.get('score')}")


if __name__ == "__main__":
    main()
