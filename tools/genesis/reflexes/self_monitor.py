#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Self-Monitor Reflex — project internal health into operator alerts.

Wires real self-monitoring onto the ``/monitoring`` dashboard page. The
Internal Awareness Engine already produces rich health snapshots in
``awareness_component_health`` (http_head, module_import, coherence_status,
gap, twin probes). Those tables are analyst-facing; the operator-facing
``/monitoring`` page reads ``alerts`` + ``failure_log`` instead, which no
running process populated — so the page rendered empty.

This reflex is a thin PROJECTION layer (no new health logic):

  1. refresh — re-run the cheap ``http_head`` probe live so "is the app up"
     is current truth every cycle (configurable via ``refresh_probes``).
  2. read    — take the latest snapshot per component node and keep the ones
     whose current status is ``fail``.
  3. project —
       * one AGGREGATED ``alerts`` row per failing category (e.g. "46 tools
         fail to import"), deduped on a stable source signature and
         auto-resolved when the category recovers.
       * one ``failure_log`` row per failing component, deduped against
         existing unresolved identical rows (capped per cycle).

Healing stays the heal reflex's job — this reflex only surfaces signal.

GREEN tier (read analysis + bounded inserts into alerts/failure_log only;
no code mutation). Zero LLM in the hot path; fully deterministic.

CLI (manual / smoke):
    python tools/genesis/reflexes/self_monitor.py --json
    python tools/genesis/reflexes/self_monitor.py --no-refresh --json
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

LOG = get_logger("self_monitor")

try:
    from tools.db.storage import get_connection
except ImportError:  # pragma: no cover - slim env
    get_connection = None  # type: ignore[assignment]

try:
    from tools.awareness.health_prober import run_all as _prober_run_all
except ImportError:  # pragma: no cover - slim env
    _prober_run_all = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Defaults (overridable from genesis_config.yaml reflex entry)
# ---------------------------------------------------------------------------

# Probes re-run live each cycle for current truth. http_head is cheap (~20
# HEAD requests) and most operationally meaningful ("is the dashboard up").
# Heavier probes (module_import, coherence, gap, twin) are reused from the
# most recent awareness cycle so this reflex stays light on a short cadence.
DEFAULT_REFRESH_PROBES = ["http_head"]

# Per-category minimum failing count before an alert fires.
DEFAULT_MIN_FAIL_TO_ALERT = 1

# Cap failure_log inserts per cycle so a mass regression can't flood the log.
DEFAULT_MAX_FAILURE_ROWS = 200

# probe_type → (severity, human label). Severity is constrained by the alerts
# table CHECK to one of: critical | warning | info. Categories not listed
# default to 'warning'.
SEVERITY_MAP: Dict[str, Dict[str, str]] = {
    "module_import":            {"severity": "critical", "label": "tool(s) failing to import"},
    "http_head":               {"severity": "warning",  "label": "dashboard route(s) returning errors"},
    "coherence_status":        {"severity": "warning",  "label": "coherence check(s) failing"},
    "twin_probe":              {"severity": "warning",  "label": "digital twin probe(s) failing"},
    "gap::tool_not_in_manifest": {"severity": "info",   "label": "tool(s) missing from manifest"},
}

# All alerts this reflex owns carry a source prefixed with this token so we can
# dedup + auto-resolve only our own rows without touching alerts from other
# subsystems (vuln_scanner, watchcon, alert_correlator).
SOURCE_PREFIX = "self_monitor"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Read: latest failing snapshot per component node
# ---------------------------------------------------------------------------


def _latest_failures(conn: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Return current failures grouped by probe_type.

    Takes the most recent snapshot per node_id and keeps only those whose
    current status is 'fail' (not 'warn' — warn is pre-confirmation and not a
    confirmed regression). Portable across SQLite + PostgreSQL (no DISTINCT ON
    / window functions).
    """
    sql = (
        "SELECT h.node_id, h.probe_type, h.status, h.detail "
        "FROM awareness_component_health h "
        "JOIN (SELECT node_id, MAX(probed_at) AS mx "
        "      FROM awareness_component_health GROUP BY node_id) m "
        "  ON h.node_id = m.node_id AND h.probed_at = m.mx "
        "WHERE h.status = 'fail'"
    )
    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        rows = conn.execute(sql).fetchall()
    except Exception as exc:
        LOG.warning("latest-failures query failed: %s", exc)
        return out
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else {
            "node_id": r[0], "probe_type": r[1], "status": r[2], "detail": r[3],
        }
        detail: Dict[str, Any] = {}
        try:
            detail = json.loads(d.get("detail") or "{}")
        except Exception:
            detail = {}
        cat = d.get("probe_type") or "unknown"
        out.setdefault(cat, []).append({"node_id": d.get("node_id"), "detail": detail})
    return out


# ---------------------------------------------------------------------------
# Project: failure_log
# ---------------------------------------------------------------------------


def _existing_open_failures(conn: Any) -> set:
    """Signatures of currently-unresolved failure_log rows, to avoid duplicates."""
    sigs: set = set()
    try:
        rows = conn.execute(
            "SELECT source, error_type, error_message FROM failure_log WHERE resolved = 0"
        ).fetchall()
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {"source": r[0], "error_type": r[1], "error_message": r[2]}
            sigs.add((d.get("source"), d.get("error_type"), d.get("error_message")))
    except Exception as exc:
        LOG.warning("read open failure_log failed: %s", exc)
    return sigs


def _record_failures(conn: Any, failures_by_cat: Dict[str, List[Dict[str, Any]]], cap: int) -> int:
    """Insert one failure_log row per failing component, deduped + capped."""
    existing = _existing_open_failures(conn)
    inserted = 0
    now = _utcnow_iso()
    for cat, items in failures_by_cat.items():
        source = f"{SOURCE_PREFIX}:{cat}"
        for item in items:
            if inserted >= cap:
                LOG.info("failure_log cap (%d) reached; remaining failures not logged this cycle", cap)
                return inserted
            node_id = item.get("node_id") or "unknown"
            detail = item.get("detail") or {}
            error_type = cat
            reason = str(
                detail.get("error")
                or detail.get("message")
                or detail.get("route")
                or detail.get("file_path")
                or "failing probe"
            )[:300]
            # Prefix with the component id so each failing component is a
            # distinct, informative row (otherwise a shared generic message
            # like "file not found" collapses many failures into one).
            error_message = f"{node_id} — {reason}"[:500]
            sig = (source, error_type, error_message)
            if sig in existing:
                continue
            context = json.dumps({"node_id": node_id, **detail}, ensure_ascii=False)[:2000]
            try:
                conn.execute(
                    "INSERT INTO failure_log "
                    "(project_id, source, error_type, error_message, context, resolved, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 0, ?)",
                    (None, source, error_type, error_message, context, now),
                )
                conn.commit()
                existing.add(sig)
                inserted += 1
            except Exception as exc:
                LOG.warning("failure_log insert failed (%s): %s", node_id, exc)
                try:
                    conn.rollback()
                except Exception:
                    pass
    return inserted


# ---------------------------------------------------------------------------
# Project: alerts (aggregated, deduped, auto-resolved)
# ---------------------------------------------------------------------------


def _firing_self_alerts(conn: Any) -> Dict[str, Dict[str, Any]]:
    """Map of source → firing alert row, for alerts this reflex owns."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        rows = conn.execute(
            "SELECT id, source, title, severity FROM alerts "
            "WHERE status = 'firing' AND source LIKE ?",
            (f"{SOURCE_PREFIX}:%",),
        ).fetchall()
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {"id": r[0], "source": r[1], "title": r[2], "severity": r[3]}
            out[d["source"]] = d
    except Exception as exc:
        LOG.warning("read firing alerts failed: %s", exc)
    return out


def _sync_alerts(conn: Any, failures_by_cat: Dict[str, List[Dict[str, Any]]], min_fail: int) -> Dict[str, int]:
    """One aggregated alert per failing category; auto-resolve recovered ones.

    Returns counts: {"opened": n, "resolved": n, "firing": n}.
    """
    now = _utcnow_iso()
    firing = _firing_self_alerts(conn)
    opened = 0
    resolved = 0
    updated = 0

    # Categories that currently breach the threshold.
    breaching: Dict[str, int] = {
        cat: len(items) for cat, items in failures_by_cat.items() if len(items) >= min_fail
    }

    # 1) Open (or refresh) one aggregated alert per breaching category.
    for cat, count in breaching.items():
        source = f"{SOURCE_PREFIX}:{cat}"
        meta = SEVERITY_MAP.get(cat, {"severity": "warning", "label": f"{cat} probe(s) failing"})
        title = f"{count} {meta['label']}"
        description = (
            f"Self-monitor detected {count} component(s) currently failing the "
            f"'{cat}' probe (latest health snapshot). Source: Internal Awareness Engine."
        )
        if source in firing:
            # Already firing — keep the count accurate as failures rise/fall.
            if firing[source].get("title") == title:
                continue  # unchanged, nothing to do
            try:
                conn.execute(
                    "UPDATE alerts SET title = ?, description = ?, severity = ? WHERE id = ?",
                    (title, description, meta["severity"], firing[source]["id"]),
                )
                conn.commit()
                updated += 1
            except Exception as exc:
                LOG.warning("alert refresh failed (%s): %s", cat, exc)
                try:
                    conn.rollback()
                except Exception:
                    pass
            continue
        try:
            conn.execute(
                "INSERT INTO alerts "
                "(project_id, severity, source, title, description, status, auto_healed, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'firing', ?, ?)",
                (None, meta["severity"], source, title, description, False, now),
            )
            conn.commit()
            opened += 1
        except Exception as exc:
            LOG.warning("alert insert failed (%s): %s", cat, exc)
            try:
                conn.rollback()
            except Exception:
                pass

    # 2) Auto-resolve firing alerts whose category no longer breaches.
    for source, row in firing.items():
        cat = source.split(":", 1)[1] if ":" in source else source
        if cat in breaching:
            continue
        try:
            conn.execute(
                "UPDATE alerts SET status = 'resolved', resolved_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            conn.commit()
            resolved += 1
        except Exception as exc:
            LOG.warning("alert resolve failed (%s): %s", source, exc)
            try:
                conn.rollback()
            except Exception:
                pass

    return {"opened": opened, "updated": updated, "resolved": resolved, "firing": len(breaching)}


# ---------------------------------------------------------------------------
# Refresh: re-run cheap probes for current truth
# ---------------------------------------------------------------------------


def _refresh_probes(probe_types: List[str]) -> Dict[str, Any]:
    """Re-run the configured probes so the latest snapshot reflects 'now'."""
    if not probe_types or _prober_run_all is None:
        return {"skipped": True, "reason": "no probes or prober unavailable"}
    summary: Dict[str, Any] = {}
    for pt in probe_types:
        try:
            summary[pt] = _prober_run_all(probe_types=[pt])
        except Exception as exc:
            LOG.warning("probe refresh failed (%s): %s", pt, exc)
            summary[pt] = {"error": str(exc)[:200]}
    return summary


# ---------------------------------------------------------------------------
# Reflex entry point
# ---------------------------------------------------------------------------


def run(config: Optional[Dict[str, Any]] = None, trust: Any = None) -> Dict[str, Any]:
    """Execute one self-monitor cycle. Daemon calls this on the reflex cadence."""
    config = config or {}
    refresh_probes = config.get("refresh_probes", DEFAULT_REFRESH_PROBES)
    min_fail = int(config.get("min_fail_to_alert", DEFAULT_MIN_FAIL_TO_ALERT))
    cap = int(config.get("max_failure_rows", DEFAULT_MAX_FAILURE_ROWS))

    start = time.time()

    if get_connection is None:
        return {"success": False, "metric_value": 0.0, "details": {"error": "get_connection unavailable"}}

    # 1) refresh cheap probes (best-effort; reuse snapshots if it fails)
    refresh_summary = _refresh_probes(list(refresh_probes))

    conn = get_connection()
    try:
        # Background task: no request/tenant context available.
        conn.set_security_context(None)  # rls-bypass: background reflex, no user session
    except Exception:
        pass

    try:
        failures_by_cat = _latest_failures(conn)
        total_failing = sum(len(v) for v in failures_by_cat.values())
        failures_logged = _record_failures(conn, failures_by_cat, cap)
        alert_counts = _sync_alerts(conn, failures_by_cat, min_fail)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    elapsed_ms = int((time.time() - start) * 1000)
    by_category = {cat: len(items) for cat, items in failures_by_cat.items()}

    LOG.info(
        "self_monitor: %d failing component(s) across %d categor(ies); "
        "alerts opened=%d updated=%d resolved=%d firing=%d; failure_log +%d; %dms",
        total_failing, len(failures_by_cat), alert_counts["opened"], alert_counts["updated"],
        alert_counts["resolved"], alert_counts["firing"], failures_logged, elapsed_ms,
    )

    return {
        "success": True,
        # success_metric: alerts currently firing (gte 0 ⇒ always passes; >0 is signal)
        "metric_value": float(alert_counts["firing"]),
        "details": {
            "total_failing_components": total_failing,
            "by_category": by_category,
            "alerts_opened": alert_counts["opened"],
            "alerts_updated": alert_counts["updated"],
            "alerts_resolved": alert_counts["resolved"],
            "alerts_firing": alert_counts["firing"],
            "failures_logged": failures_logged,
            "refresh": refresh_summary,
            "elapsed_ms": elapsed_ms,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="self_monitor",
        description="Project Internal Awareness health into operator alerts + failure_log",
    )
    parser.add_argument(
        "--no-refresh", action="store_true",
        help="Skip live probe refresh; project from existing snapshots only",
    )
    parser.add_argument("--min-fail", type=int, default=DEFAULT_MIN_FAIL_TO_ALERT,
                        help="Per-category minimum failing count before an alert fires")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    cfg: Dict[str, Any] = {"min_fail_to_alert": args.min_fail}
    if args.no_refresh:
        cfg["refresh_probes"] = []

    result = run(cfg, None)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        d = result.get("details", {})
        print(f"  success: {result.get('success')}")
        print(f"  failing components: {d.get('total_failing_components')}")
        print(f"  by category: {d.get('by_category')}")
        print(f"  alerts opened/resolved/firing: "
              f"{d.get('alerts_opened')}/{d.get('alerts_resolved')}/{d.get('alerts_firing')}")
        print(f"  failure_log inserted: {d.get('failures_logged')}")
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
