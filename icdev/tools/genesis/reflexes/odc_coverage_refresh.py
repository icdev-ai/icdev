# CUI // SP-CTI
"""Genesis Reflex — ODC MITRE ATT&CK Coverage Refresh + Drift Detection (6h cadence).

The seeded ODC runbook ``rb-odc-siem-gap-detected`` triggers on
"MITRE ATT&CK coverage drops >15%", but nothing computed that delta on a
schedule: signal-source coverage (``odc_gap_scores`` /
``odc_technique_coverage``) only refreshed when a *user* opened a design and
hit the coverage-compute route (obx-cov-01, PR #504). A design whose graph
silently loses a signal source — or whose required-source catalog widens — would
drift out of coverage with no one watching.

This reflex closes that loop. On every cadence it:

  1. Iterates ``observability_designs`` via the ODC canvas connection
     (bounded — ``max_designs`` per run, default 50; overflow logged + deferred).
  2. Captures the *previous* latest ``odc_gap_scores`` row per design (the
     baseline coverage) BEFORE recomputing.
  3. Recomputes coverage with ``mitre_coverage_twin.compute_gap_score`` — the
     SAME writer the obx-cov-01 compute route calls — which persists a fresh
     ``odc_gap_scores`` + ``odc_technique_coverage`` snapshot as a side effect.
  4. Computes the coverage delta (previous coverage_pct − new coverage_pct).
  5. If coverage DROPPED by more than the configured threshold (default 15
     percentage points — matching the runbook trigger wording), records a drift
     event: an ``od_audit`` row AND a ``status='suggested'`` kanban card (one per
     drifted design, idempotency-keyed so re-runs never duplicate).

COVERAGE METRIC (interpretation of "coverage drops >15%")
---------------------------------------------------------
"Coverage" here is ``coverage_pct`` from ``compute_gap_score`` — the percentage
of catalog techniques in the ``covered`` state (``100 * covered_count / total``).
"Drops >15%" is read as an ABSOLUTE percentage-point drop (e.g. 80% → 62% is an
18-point drop → drift), which is directly comparable to the persisted
``odc_gap_scores`` columns and unambiguous to test. A design with no prior
``odc_gap_scores`` row has no baseline to drop from: it is recomputed (seeding
the baseline) but never flagged on its first pass.

Config: ``args/observability_config.yaml`` → ``coverage_drift`` block. Missing
file / block falls back to the documented defaults below.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

CADENCE_HOURS: int = 6

# ---------------------------------------------------------------------------
# Defaults (mirrored in args/observability_config.yaml — keep in sync)
# ---------------------------------------------------------------------------
_DEFAULT_THRESHOLD_PCT = 15.0   # percentage-point coverage drop that trips drift
_DEFAULT_MAX_DESIGNS = 50       # designs recomputed per run (bounded)

_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "args", "observability_config.yaml"
)


# ---------------------------------------------------------------------------
# Config loading (tolerant of a missing file / block)
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    try:
        with open(os.path.normpath(_CONFIG_PATH), encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _resolve_threshold(cfg: Dict[str, Any]) -> float:
    """Coverage-drop threshold in percentage points (default 15.0)."""
    cd = (cfg or {}).get("coverage_drift", {}) or {}
    try:
        return float(cd.get("threshold_pct", _DEFAULT_THRESHOLD_PCT))
    except (TypeError, ValueError):
        return _DEFAULT_THRESHOLD_PCT


def _resolve_max_designs(cfg: Dict[str, Any]) -> int:
    """Max designs recomputed per run (default 50, floor 1)."""
    cd = (cfg or {}).get("coverage_drift", {}) or {}
    try:
        return max(1, int(cd.get("max_designs", _DEFAULT_MAX_DESIGNS)))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_DESIGNS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coverage_pct_from_row(covered: Any, total: Any) -> Optional[float]:
    """Recreate coverage_pct from a persisted odc_gap_scores row.

    Mirrors mitre_coverage_twin.compute_gap_score's
    ``round(100 * covered_count / max(total, 1), 1)``. Returns None if the row
    carries no technique total (nothing meaningful to compare against).
    """
    try:
        total_i = int(total)
        covered_i = int(covered)
    except (TypeError, ValueError):
        return None
    if total_i <= 0:
        return None
    return round(100.0 * covered_i / total_i, 1)


def _prev_coverage(conn, design_id: str) -> Optional[float]:
    """Coverage_pct of the latest existing odc_gap_scores row, or None."""
    try:
        row = conn.execute(
            "SELECT covered_count, total_techniques FROM odc_gap_scores "
            "WHERE design_id=%s ORDER BY assessed_at DESC LIMIT 1",
            (design_id,),
        ).fetchone()
    except Exception as exc:
        logger.debug("odc_coverage_refresh: prev-score read failed for %s (%s)", design_id, exc)
        return None
    if not row:
        return None
    try:
        covered = row["covered_count"]
        total = row["total_techniques"]
    except (KeyError, IndexError, TypeError):
        covered, total = row[0], row[1]
    return _coverage_pct_from_row(covered, total)


def _parse_graph(graph_raw: Any) -> Dict[str, Any]:
    """Parse a design's graph_json into a dict (best-effort)."""
    if isinstance(graph_raw, dict):
        return graph_raw
    try:
        parsed = json.loads(graph_raw) if isinstance(graph_raw, str) else {}
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_audit(conn, design_id: str, detail: str, now_iso: str) -> None:
    """Append a coverage-drift event to the ODC canvas od_audit table."""
    conn.execute(
        "INSERT INTO od_audit (design_id, actor, action, detail, created_at) "
        "VALUES (%s,%s,%s,%s,%s)",
        (design_id, "genesis:odc_coverage_refresh", "coverage_drift_detected", detail, now_iso),
    )


def _card_specs(design_id: str, design_name: str, prev_cov: float, new_cov: float,
                delta: float, day: str) -> Dict[str, Any]:
    """Build the suggested-card spec for a drifted design (idempotency-keyed)."""
    prefix = str(design_id)[:8]
    card_id = f"odc-drift-{prefix}-{day}"
    idem = f"odc-drift-{design_id}-{day}"
    name = design_name or design_id
    title = f"ODC MITRE coverage drift: {name} (-{delta:.1f} pts)"
    description = (
        f"Scheduled ODC MITRE ATT&CK coverage recompute detected a drift for "
        f"design '{name}' ({design_id}).\n\n"
        f"Coverage before: {prev_cov:.1f}%\n"
        f"Coverage after:  {new_cov:.1f}%\n"
        f"Drop:            {delta:.1f} percentage points "
        f"(exceeds the configured drift threshold).\n\n"
        f"This matches the rb-odc-siem-gap-detected runbook trigger "
        f"('MITRE ATT&CK coverage drops >15%'). Review the design's signal "
        f"sources and required-technique coverage at "
        f"/observability/coverage/{design_id} and remediate the gap "
        f"(add the missing signal sources or update the detection baseline)."
    )
    return {
        "id": card_id,
        "title": title,
        "description": description,
        "task_type": "chore",
        "priority": "high",
        "status": "suggested",
        "idempotency_key": idem,
        "dispatch_source": "odc_coverage_refresh",
    }


# ---------------------------------------------------------------------------
# Reflex entry point
# ---------------------------------------------------------------------------

def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Recompute ODC MITRE coverage on a schedule and flag >threshold drops.

    Args:
        ctx:  Genesis config/context dict. Honours ``dry_run`` (report only —
              no recompute, no audit rows, no cards).
        conn: Unused trust-kernel handle from the daemon dispatch contract.

    Returns:
        Reflex dict with per-design outcomes plus the Genesis success contract
        keys (success, metric_value, details).
    """
    cfg = _load_config()
    threshold = _resolve_threshold(cfg)
    max_designs = _resolve_max_designs(cfg)
    dry_run: bool = bool(ctx.get("dry_run", False))
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y%m%d")

    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "threshold_pct": threshold,
        "max_designs": max_designs,
        "dry_run": dry_run,
        "designs_processed": 0,
        "designs_skipped": 0,
        "designs_total": 0,
        "drift_count": 0,
        "cards_created": 0,
        "drifted": [],
        "errors": [],
        "status": "ok",
    }

    # Deferred imports so tests can patch the exact objects the reflex/twin use
    # (init_db.get_connection is shared with compute_gap_score's persistence).
    from tools.observability_canvas.db.init_db import get_connection
    from tools.observability_canvas.mitre_coverage_twin import compute_gap_score

    # ---- Phase 1: read the design list + baseline coverage (bounded) --------
    designs: List[Dict[str, Any]] = []
    db = None
    try:
        db = get_connection()
        # rls-bypass: ODC canvas tables (observability_designs, odc_gap_scores,
        # od_audit) carry no tenant_id and this reflex runs in the Genesis daemon
        # OUTSIDE any Flask request context, so no security context auto-attaches
        # (tools/db/storage.py::_attach_flask_security_context). We clear it
        # explicitly to stay correct even if invoked in-request — coverage drift
        # must be evaluated across ALL designs regardless of caller scope.
        if hasattr(db, "set_security_context"):
            db.set_security_context(None)

        rows = db.execute(
            "SELECT id, name, graph_json FROM observability_designs "
            "ORDER BY updated_at DESC"
        ).fetchall()
        result["designs_total"] = len(rows)

        for r in rows:
            try:
                did = r["id"]
                dname = r["name"]
                graph_raw = r["graph_json"]
            except (KeyError, IndexError, TypeError):
                did, dname, graph_raw = r[0], r[1], r[2]
            if len(designs) >= max_designs:
                break
            designs.append({
                "id": did,
                "name": dname,
                "graph": _parse_graph(graph_raw),
                "prev_coverage": _prev_coverage(db, did),
            })

        result["designs_skipped"] = max(0, result["designs_total"] - len(designs))
        if result["designs_skipped"]:
            logger.info(
                "odc_coverage_refresh: %d designs over the per-run cap (%d) deferred to next cadence",
                result["designs_skipped"], max_designs,
            )
    except Exception as exc:
        logger.error("odc_coverage_refresh: design enumeration failed: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass

    if result["status"] == "error":
        return _finalize(result)

    if dry_run:
        # Report the resolved policy + what WOULD be recomputed; touch nothing.
        result["details_note"] = "dry_run: no recompute, no audit, no cards"
        result["success"] = True
        result["metric_value"] = 0.0
        result["details"] = {
            "threshold_pct": threshold,
            "designs_total": result["designs_total"],
            "would_process": len(designs),
            "dry_run": True,
            "status": "ok",
        }
        return result

    # ---- Phase 2: recompute coverage + decide drift per design --------------
    drifted: List[Dict[str, Any]] = []
    for d in designs:
        did = d["id"]
        try:
            score = compute_gap_score(did, d["graph"])  # persists a fresh snapshot
        except Exception as exc:
            logger.error("odc_coverage_refresh: compute_gap_score failed for %s: %s", did, exc)
            result["errors"].append(f"{did}: {exc}")
            continue
        result["designs_processed"] += 1

        new_cov = float(score.get("coverage_pct", 0.0))
        prev_cov = d["prev_coverage"]
        if prev_cov is None:
            # No baseline — this run seeds it; nothing to drop from yet.
            continue
        delta = round(prev_cov - new_cov, 1)  # positive == coverage dropped
        if delta > threshold:
            result["drift_count"] += 1
            drifted.append({
                "design_id": did,
                "design_name": d["name"],
                "prev_coverage_pct": prev_cov,
                "new_coverage_pct": new_cov,
                "drop_pct": delta,
            })

    # ---- Phase 3: persist drift events (audit rows + suggested cards) --------
    if drifted:
        audit_db = None
        try:
            audit_db = get_connection()
            if hasattr(audit_db, "set_security_context"):
                audit_db.set_security_context(None)
            for dr in drifted:
                detail = (
                    f"coverage {dr['prev_coverage_pct']:.1f}% -> {dr['new_coverage_pct']:.1f}% "
                    f"(drop {dr['drop_pct']:.1f} pts > threshold {threshold:.1f})"
                )
                try:
                    _write_audit(audit_db, dr["design_id"], detail, now.isoformat())
                except Exception as exc:
                    logger.error("odc_coverage_refresh: audit write failed for %s: %s",
                                 dr["design_id"], exc)
                    result["errors"].append(f"{dr['design_id']} audit: {exc}")
            audit_db.commit()
        except Exception as exc:
            logger.error("odc_coverage_refresh: audit phase failed: %s", exc)
            result["errors"].append(str(exc))
        finally:
            try:
                if audit_db is not None:
                    audit_db.close()
            except Exception:
                pass

        # Suggested cards — one per drifted design, idempotency-keyed so a
        # same-day re-run never duplicates.
        try:
            from tools.kanban.task_factory import create_tasks
            specs = [
                _card_specs(dr["design_id"], dr["design_name"],
                            dr["prev_coverage_pct"], dr["new_coverage_pct"],
                            dr["drop_pct"], day)
                for dr in drifted
            ]
            created = create_tasks(specs)
            result["cards_created"] = len(created)
        except Exception as exc:
            logger.error("odc_coverage_refresh: card creation failed: %s", exc)
            result["errors"].append(f"cards: {exc}")

    result["drifted"] = drifted
    return _finalize(result)


def _finalize(result: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the Genesis daemon success contract (success/metric_value/details)."""
    if result["errors"] and result["status"] != "error":
        result["status"] = "partial"

    # Genesis daemon success contract (see tools/daemon/base.py::run_reflex):
    # without success/metric_value/details a healthy run is scored a FAILURE and
    # trips the circuit breaker after 3 cycles.
    result["success"] = result["status"] in ("ok", "partial")
    result["metric_value"] = float(result["drift_count"])
    result["details"] = {
        "designs_processed": result["designs_processed"],
        "designs_skipped": result["designs_skipped"],
        "drift_count": result["drift_count"],
        "cards_created": result["cards_created"],
        "threshold_pct": result["threshold_pct"],
        "status": result["status"],
        "errors": result["errors"],
    }
    return result


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

    print(_json.dumps(run({}), indent=2))
