# CUI // SP-CTI
"""Genesis Reflex — Observability Retention (archive-then-prune, 24h cadence).

With tracing activated (PR #467) the five high-volume observability tables grow
without bound, degrading list_traces (GROUP BY trace_id) and trace_stats over
time:

  otel_spans, prov_entities, prov_activities, prov_relations, shap_attributions

APPEND-ONLY DECISION (obx-trc-05)
---------------------------------
ALL FIVE tables are declared append-only in
``.claude/hooks/pre_tool_use.py`` ``APPEND_ONLY_TABLES``. ``otel_spans`` is
append-only BY DESIGN (D6); the W3C-PROV tables and ``shap_attributions`` are
NIST AU immutable evidence. This reflex therefore NEVER raw-DELETEs expired
rows. For every table it performs *archive-then-prune*:

  1. Idempotently create a cold ``<table>_archive`` twin (same columns).
  2. Copy the batch of expired rows into the archive twin.
  3. Delete that same batch from the hot table.

No trace or provenance record is destroyed — only relocated out of the hot
path. (The pre_tool_use append-only hook governs Claude's own tool edits, not
runtime Python SQL, so it does not intercept this reflex; we honour the design
intent regardless.)

Batching: each table is processed in bounded batches (default 5000 rows) up to
a per-run iteration cap (default 20) so a huge backlog can never wedge the
daemon in one unbroken transaction. Leftover rows are handled next cadence.

Config: ``args/observability_config.yaml`` → ``trace_retention`` block. Missing
file / block falls back to the documented defaults below.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

import yaml

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

CADENCE_HOURS: int = 24

# ---------------------------------------------------------------------------
# Defaults (mirrored in args/observability_config.yaml — keep in sync)
# ---------------------------------------------------------------------------
_DEFAULT_SPANS_DAYS = 30
_DEFAULT_PROVENANCE_DAYS = 180
_DEFAULT_SHAP_DAYS = 90
_DEFAULT_BATCH_SIZE = 5000
_DEFAULT_MAX_ITERATIONS = 20

_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "args", "observability_config.yaml"
)

# (source_table, timestamp_column, id_column, retention_window_key)
_TABLE_SPECS: List[Tuple[str, str, str, str]] = [
    ("otel_spans", "created_at", "id", "spans_days"),
    ("prov_entities", "created_at", "id", "provenance_days"),
    ("prov_activities", "created_at", "id", "provenance_days"),
    ("prov_relations", "created_at", "id", "provenance_days"),
    ("shap_attributions", "analyzed_at", "id", "shap_days"),
]


# ---------------------------------------------------------------------------
# Config loading (tolerant of a missing file / block)
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    try:
        with open(os.path.normpath(_CONFIG_PATH), encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _resolve_windows(cfg: Dict[str, Any]) -> Dict[str, int]:
    """Return the retention window (in days) per window-key, applying defaults."""
    tr = (cfg or {}).get("trace_retention", {}) or {}
    return {
        "spans_days": int(tr.get("spans_days", _DEFAULT_SPANS_DAYS)),
        "provenance_days": int(tr.get("provenance_days", _DEFAULT_PROVENANCE_DAYS)),
        "shap_days": int(tr.get("shap_days", _DEFAULT_SHAP_DAYS)),
    }


def _resolve_batch(cfg: Dict[str, Any]) -> Tuple[int, int]:
    """Return (batch_size, max_iterations), applying defaults."""
    tr = (cfg or {}).get("trace_retention", {}) or {}
    batch_size = int(tr.get("batch_size", _DEFAULT_BATCH_SIZE))
    max_iterations = int(tr.get("max_iterations", _DEFAULT_MAX_ITERATIONS))
    # Guard against pathological config that would disable pruning.
    batch_size = max(1, batch_size)
    max_iterations = max(1, max_iterations)
    return batch_size, max_iterations


# ---------------------------------------------------------------------------
# Per-table archive-then-prune
# ---------------------------------------------------------------------------

def _prune_table(
    conn,
    src: str,
    ts_col: str,
    id_col: str,
    cutoff_iso: str,
    batch_size: int,
    max_iterations: int,
) -> Dict[str, Any]:
    """Archive-then-prune rows in *src* older than *cutoff_iso* in bounded batches.

    Returns {archived, iterations, capped, table_missing}.
    """
    archive = f"{src}_archive"
    stats = {"archived": 0, "iterations": 0, "capped": False, "table_missing": False}

    # Idempotent cold twin. Cross-dialect: both SQLite and PostgreSQL support
    # CREATE TABLE IF NOT EXISTS <t> AS SELECT ... WHERE 1=0 (empty schema copy).
    try:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {archive} AS SELECT * FROM {src} WHERE 1=0"  # nosec B608
        )
        conn.commit()
    except Exception as exc:
        # Hot table absent (e.g. migration not yet applied) — skip gracefully.
        logger.debug("observability_retention: skip %s (%s)", src, exc)
        stats["table_missing"] = True
        return stats

    while stats["iterations"] < max_iterations:
        rows = conn.execute(
            f"SELECT {id_col} FROM {src} WHERE {ts_col} < ? "  # nosec B608
            f"ORDER BY {ts_col} LIMIT {int(batch_size)}",  # nosec B608
            (cutoff_iso,),
        ).fetchall()
        if not rows:
            break
        ids = [r[0] for r in rows]
        placeholders = ",".join("?" for _ in ids)
        # Copy the expired batch into the cold twin, THEN delete from the hot
        # table. Order matters: archive first so a mid-batch failure never loses
        # rows (worst case = duplicate archive rows, which the twin tolerates).
        conn.execute(
            f"INSERT INTO {archive} SELECT * FROM {src} WHERE {id_col} IN ({placeholders})",  # nosec B608
            ids,
        )
        conn.execute(
            f"DELETE FROM {src} WHERE {id_col} IN ({placeholders})",  # nosec B608
            ids,
        )
        conn.commit()
        stats["archived"] += len(ids)
        stats["iterations"] += 1
        if len(ids) < batch_size:
            break

    # Capped == there may be MORE expired rows than this run drained.
    if stats["iterations"] >= max_iterations:
        remaining = conn.execute(
            f"SELECT 1 FROM {src} WHERE {ts_col} < ? LIMIT 1",  # nosec B608
            (cutoff_iso,),
        ).fetchall()
        stats["capped"] = bool(remaining)

    return stats


# ---------------------------------------------------------------------------
# Reflex entry point
# ---------------------------------------------------------------------------

def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Archive-then-prune expired observability rows across the five hot tables.

    Args:
        ctx:  Genesis config/context dict. Honours ``dry_run`` (report only).
        conn: Unused trust-kernel handle from the daemon dispatch contract.

    Returns:
        Reflex dict with per-table stats plus the Genesis success contract keys
        (success, metric_value, details).
    """
    cfg = _load_config()
    windows = _resolve_windows(cfg)
    batch_size, max_iterations = _resolve_batch(cfg)
    dry_run: bool = bool(ctx.get("dry_run", False))
    now = datetime.now(timezone.utc)

    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "windows": windows,
        "batch_size": batch_size,
        "max_iterations": max_iterations,
        "dry_run": dry_run,
        "tables": {},
        "total_archived": 0,
        "errors": [],
        "status": "ok",
    }

    if dry_run:
        # In dry-run we only report the resolved policy; no rows are touched.
        result["success"] = True
        result["metric_value"] = 0.0
        result["details"] = {"windows": windows, "dry_run": True, "status": "ok"}
        return result

    db = None
    try:
        db = get_connection()
        # rls-bypass: retention must operate across ALL classifications and
        # tenants. otel_spans/prov_* carry a `classification` column but no
        # `tenant_id`; if a Flask security context were attached, _inject_rls
        # would scope the DELETE to the caller's dominated classifications and
        # silently leave other-classification rows un-pruned. This reflex runs
        # in the Genesis daemon OUTSIDE any Flask request context, so no context
        # auto-attaches (tools/db/storage.py::_attach_flask_security_context);
        # we clear it explicitly to stay correct even if invoked in-request.
        if hasattr(db, "set_security_context"):
            db.set_security_context(None)

        for src, ts_col, id_col, window_key in _TABLE_SPECS:
            cutoff_iso = (now - timedelta(days=windows[window_key])).isoformat()
            try:
                tstats = _prune_table(
                    db, src, ts_col, id_col, cutoff_iso, batch_size, max_iterations
                )
                tstats["cutoff"] = cutoff_iso
                result["tables"][src] = tstats
                result["total_archived"] += tstats["archived"]
            except Exception as exc:
                logger.error("observability_retention: %s failed: %s", src, exc)
                result["errors"].append(f"{src}: {exc}")
                result["tables"][src] = {"error": str(exc)}
    except Exception as exc:
        logger.error("observability_retention reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass

    if result["errors"] and result["status"] != "error":
        result["status"] = "partial"

    # Genesis daemon success contract (see tools/daemon/base.py::run_reflex):
    # without success/metric_value/details a healthy run is scored a FAILURE and
    # trips the circuit breaker after 3 cycles.
    result["success"] = result["status"] in ("ok", "partial")
    result["metric_value"] = float(result["total_archived"])
    result["details"] = {
        "total_archived": result["total_archived"],
        "tables": {
            t: (s.get("archived", 0) if isinstance(s, dict) else 0)
            for t, s in result["tables"].items()
        },
        "windows": windows,
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
