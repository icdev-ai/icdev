# CUI // SP-CTI
"""Genesis reflex health trends + critical-failure alerting (crx-gen-02).

Closes the loop left open by genesis_daemon_reflex.md gaps #3/#5:

  * reflex_observer.py / genesis_audit already capture per-reflex timing and
    outcome, but nothing turned a *critical* reflex failure into an operator
    alert (the genesis_config ``circuit_breaker.notify_on_trip: true`` flag was
    never wired), and nothing surfaced failure-rate / duration trends over time.

This module provides, reading only the existing ``genesis_audit`` rows written
by ``tools/daemon/base.py``:

  1. compute_reflex_health(days) — per-reflex attempt/failure counts, failure
     rate, and p50/p95 duration percentiles over a rolling window. Backs the
     doc's cited queries ("all failures last 7 days", "highest error rate").
  2. open_critical_reflex_alerts(config) — opens/refreshes rows in the existing
     ``alerts`` table (the surface the /monitoring page already reads) for
     reflexes flagged critical in genesis_config, with a per-reflex cooldown so
     a flapping reflex alerts once per window. Auto-resolves when recovered.

Compute-in-Python (no json_extract / dialect SQL) keeps it PG/SQLite portable.
"""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = get_logger(__name__)

# genesis_audit event_type tokens (event_prefix "genesis" — see daemon.base).
EVENT_STARTED = "genesis.reflex.started"
EVENT_COMPLETED = "genesis.reflex.completed"
EVENT_FAILED = "genesis.reflex.failed"
EVENT_TRIPPED = "genesis.circuit_breaker.tripped"

# Alert source prefix — lets us dedup/auto-resolve only our own alert rows.
ALERT_SOURCE_PREFIX = "genesis-reflex-health"

# Defaults when args/genesis_config.yaml has no reflex_health block.
DEFAULT_WINDOW_DAYS = 7
DEFAULT_COOLDOWN_MINUTES = 60
DEFAULT_FAILURE_THRESHOLD = 3  # failures in window before a critical alert fires
_VALID_SEVERITIES = ("critical", "warning", "info")


def _cutoff_iso(days: int) -> str:
    """ISO timestamp `days` in the past, matching daemon.base.utcnow_iso()."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _percentile(sorted_vals: List[float], pct: float) -> float:
    """Nearest-rank percentile of an already-sorted list (pure Python)."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac)


def compute_reflex_health(days: int = DEFAULT_WINDOW_DAYS, conn=None) -> Dict[str, Any]:
    """Return per-reflex health metrics over the last `days` from genesis_audit.

    Metrics per reflex: attempts (started rows), failures (failed+tripped),
    successes (completed rows), failure_rate, and p50/p95 completed-run
    duration_ms. Reflexes are sorted by failure_rate descending so the
    "highest error rate" query is the head of the list.
    """
    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        cutoff = _cutoff_iso(days)
        rows = conn.execute(
            """
            SELECT reflex_name, event_type, success, duration_ms
            FROM genesis_audit
            WHERE created_at >= %s
              AND event_type IN (%s, %s, %s, %s)
              AND reflex_name IS NOT NULL
            """,
            [cutoff, EVENT_STARTED, EVENT_COMPLETED, EVENT_FAILED, EVENT_TRIPPED],
        ).fetchall()
    finally:
        if own_conn:
            conn.close()

    agg: Dict[str, Dict[str, Any]] = {}

    def _slot(name: str) -> Dict[str, Any]:
        return agg.setdefault(
            name,
            {"attempts": 0, "failures": 0, "successes": 0, "trips": 0, "_durations": []},
        )

    for r in rows:
        # Support both dict-like (RealDictCursor / sqlite3.Row) and tuple rows.
        if isinstance(r, dict):
            name, etype, dur = r.get("reflex_name"), r.get("event_type"), r.get("duration_ms")
        else:
            name, etype, dur = r[0], r[1], r[3]
        if not name:
            continue
        slot = _slot(name)
        if etype == EVENT_STARTED:
            slot["attempts"] += 1
        elif etype == EVENT_COMPLETED:
            slot["successes"] += 1
            if isinstance(dur, (int, float)):
                slot["_durations"].append(float(dur))
        elif etype == EVENT_FAILED:
            slot["failures"] += 1
        elif etype == EVENT_TRIPPED:
            slot["failures"] += 1
            slot["trips"] += 1

    reflexes: List[Dict[str, Any]] = []
    for name, slot in agg.items():
        attempts = slot["attempts"]
        failures = slot["failures"]
        # Denominator: prefer started rows; fall back to completed+failed when
        # started rows were pruned but outcome rows survive.
        denom = attempts if attempts else (slot["successes"] + failures)
        failure_rate = round(failures / denom, 4) if denom else 0.0
        durs = sorted(slot["_durations"])
        reflexes.append(
            {
                "reflex": name,
                "attempts": attempts,
                "successes": slot["successes"],
                "failures": failures,
                "circuit_breaker_trips": slot["trips"],
                "failure_rate": failure_rate,
                "p50_duration_ms": round(_percentile(durs, 50), 1),
                "p95_duration_ms": round(_percentile(durs, 95), 1),
            }
        )

    reflexes.sort(key=lambda d: (d["failure_rate"], d["failures"]), reverse=True)
    total_failures = sum(d["failures"] for d in reflexes)
    return {
        "window_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reflex_count": len(reflexes),
        "total_failures": total_failures,
        "reflexes": reflexes,
    }


def recent_failures(days: int = DEFAULT_WINDOW_DAYS, limit: int = 50, conn=None) -> List[Dict[str, Any]]:
    """Return the most recent reflex failure rows in the window (doc query:
    "all failures last 7 days")."""
    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        cutoff = _cutoff_iso(days)
        rows = conn.execute(
            """
            SELECT reflex_name, event_type, details, created_at
            FROM genesis_audit
            WHERE created_at >= %s
              AND event_type IN (%s, %s)
            ORDER BY created_at DESC
            LIMIT %s
            """,
            [cutoff, EVENT_FAILED, EVENT_TRIPPED, limit],
        ).fetchall()
    finally:
        if own_conn:
            conn.close()

    out: List[Dict[str, Any]] = []
    for r in rows:
        d = r if isinstance(r, dict) else {
            "reflex_name": r[0], "event_type": r[1], "details": r[2], "created_at": r[3],
        }
        error = None
        raw = d.get("details")
        if raw:
            try:
                error = json.loads(raw).get("error")
            except Exception:
                error = None
        out.append(
            {
                "reflex": d.get("reflex_name"),
                "event_type": d.get("event_type"),
                "tripped": d.get("event_type") == EVENT_TRIPPED,
                "error": error,
                "created_at": d.get("created_at"),
            }
        )
    return out


def _critical_reflex_map(config: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Resolve {reflex_name: severity} for reflexes to alert on.

    Sources (merged, later wins):
      * top-level ``reflex_health.critical_reflexes`` — list of names or
        {name: severity} mappings; bare names take reflex_health.default_severity.
      * any per-reflex ``reflexes.<name>.alert_severity`` override.
    """
    cfg = config or {}
    rh = cfg.get("reflex_health", {}) or {}
    default_sev = rh.get("default_severity", "critical")
    if default_sev not in _VALID_SEVERITIES:
        default_sev = "critical"

    result: Dict[str, str] = {}
    for item in rh.get("critical_reflexes", []) or []:
        if isinstance(item, dict):
            for name, sev in item.items():
                result[name] = sev if sev in _VALID_SEVERITIES else default_sev
        elif isinstance(item, str):
            result[item] = default_sev

    for name, rcfg in (cfg.get("reflexes", {}) or {}).items():
        if isinstance(rcfg, dict) and rcfg.get("alert_severity"):
            sev = rcfg["alert_severity"]
            result[name] = sev if sev in _VALID_SEVERITIES else default_sev
    return result


def open_critical_reflex_alerts(config: Optional[Dict[str, Any]] = None, conn=None) -> Dict[str, Any]:
    """Open/refresh/resolve alerts for critical reflexes based on recent failures.

    For each critical reflex, count failures in the cooldown window. If at or
    above the failure threshold, ensure a single firing ``alerts`` row exists
    (refreshed, not duplicated — the cooldown/once-per-window guarantee). When a
    reflex recovers (no failures in window), its firing alert is auto-resolved.
    Only rows this module owns (source prefix ``genesis-reflex-health:``) are
    touched.
    """
    if config is None:
        try:
            from tools.genesis.daemon import GenesisDaemon
            config = GenesisDaemon.load_config()
        except Exception:
            config = {}

    rh = (config or {}).get("reflex_health", {}) or {}
    if not rh.get("enabled", True):
        return {"enabled": False, "opened": 0, "refreshed": 0, "resolved": 0}

    cooldown_min = int(rh.get("cooldown_minutes", DEFAULT_COOLDOWN_MINUTES))
    threshold = int(rh.get("failure_threshold", DEFAULT_FAILURE_THRESHOLD))
    critical = _critical_reflex_map(config)
    if not critical:
        return {"enabled": True, "opened": 0, "refreshed": 0, "resolved": 0, "note": "no critical reflexes configured"}

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection
        conn = get_connection()

    opened = refreshed = resolved = 0
    now = datetime.now(timezone.utc).isoformat()
    window_days = max(cooldown_min / 1440.0, 1.0 / 24)  # at least 1h look-back
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cooldown_min)).isoformat()
    try:
        for name, severity in critical.items():
            source = f"{ALERT_SOURCE_PREFIX}:{name}"
            fail_rows = conn.execute(
                """
                SELECT details, created_at FROM genesis_audit
                WHERE reflex_name = %s AND created_at >= %s
                  AND event_type IN (%s, %s)
                ORDER BY created_at DESC
                """,
                [name, cutoff, EVENT_FAILED, EVENT_TRIPPED],
            ).fetchall()
            fail_count = len(fail_rows)

            existing = conn.execute(
                "SELECT id, created_at FROM alerts WHERE source = %s AND status = 'firing' "
                "ORDER BY created_at DESC LIMIT 1",
                [source],
            ).fetchone()
            existing_id = (existing["id"] if isinstance(existing, dict) else existing[0]) if existing else None

            if fail_count >= threshold:
                last_err = None
                if fail_rows:
                    raw = fail_rows[0]["details"] if isinstance(fail_rows[0], dict) else fail_rows[0][0]
                    if raw:
                        try:
                            last_err = json.loads(raw).get("error")
                        except Exception:
                            last_err = None
                title = f"Genesis reflex '{name}' failing ({fail_count} in {cooldown_min}m)"
                description = (
                    f"Reflex '{name}' recorded {fail_count} failures in the last "
                    f"{cooldown_min} minutes (threshold {threshold}). Last error: {last_err or 'n/a'}."
                )
                if existing_id is None:
                    conn.execute(
                        "INSERT INTO alerts (project_id, severity, source, title, description, status, auto_healed, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, 'firing', %s, %s)",
                        [None, severity, source, title, description, False, now],
                    )
                    opened += 1
                else:
                    conn.execute(
                        "UPDATE alerts SET title = %s, description = %s, severity = %s WHERE id = %s",
                        [title, description, severity, existing_id],
                    )
                    refreshed += 1
            elif existing_id is not None:
                # Recovered — auto-resolve our firing alert.
                conn.execute(
                    "UPDATE alerts SET status = 'resolved', resolved_at = %s WHERE id = %s",
                    [now, existing_id],
                )
                resolved += 1
        conn.commit()
    except Exception as exc:
        logger.warning("open_critical_reflex_alerts failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        if own_conn:
            conn.close()

    return {
        "enabled": True,
        "opened": opened,
        "refreshed": refreshed,
        "resolved": resolved,
        "critical_reflexes": list(critical.keys()),
        "cooldown_minutes": cooldown_min,
        "failure_threshold": threshold,
        "window_days": round(window_days, 3),
    }


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Genesis reflex health trends + critical-failure alerts")
    parser.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS, help="trend window in days")
    parser.add_argument("--failures", action="store_true", help="list recent failures instead of the trend table")
    parser.add_argument("--alert", action="store_true", help="open/refresh/resolve critical-reflex alerts")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    if args.alert:
        result = open_critical_reflex_alerts()
    elif args.failures:
        result = {"recent_failures": recent_failures(days=args.days)}
    else:
        result = compute_reflex_health(days=args.days)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
