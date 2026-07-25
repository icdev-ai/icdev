# CUI // SP-CTI — Twin Core cross-canvas observer
"""Cross-canvas twin health observer.

Aggregates health across every registered twin (via :class:`TwinRegistry`)
WITHOUT rewriting or heavily querying any canvas twin:

* **Snapshot freshness** — latest snapshot timestamp + age per canvas.
* **Verdict distribution (window)** — from canvases that persist simulations
  (currently PDC via ``pdc_simulations``); others honestly report ``{}``.
* **Violation counts by severity** — from canvases that persist violations
  (currently IDC via ``idc_twin_violations``).
* **Refresh-schedule adherence** — joins the registered twin reflexes against
  Genesis reflex run history (``genesis_reflex_state``); flags overdue reflexes.

Every query is best-effort: a missing canvas DB / table / reflex row degrades to
``None`` / ``unavailable`` rather than raising. This is the data source for the
Twin Observatory dashboard (twx-obs-01).

CLI: ``python -m tools.twin_core.observer --json``
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger
from tools.twin_core.registry import TwinRegistry
from tools.twin_core.schema import SEVERITIES, VERDICTS

logger = get_logger("icdev.twin_core.observer")

# Registered twin Genesis reflexes and their refresh cadence (seconds).
# Data-driven map of canvas_key -> (reflex_name, cadence_seconds). Only canvases
# whose twin has a scheduled refresh reflex appear here; absence = not scheduled.
CANVAS_TWIN_REFLEX: dict[str, tuple[str, int]] = {
    "idc": ("idc_cloud_drift", 6 * 3600),
}

# A twin is "stale" when its newest snapshot across all entities is older than this.
DEFAULT_STALE_AFTER_HOURS = 48
# Overdue = last_run older than cadence * this factor (grace band).
_OVERDUE_FACTOR = 1.5


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _reflex_state(reflex_name: str) -> dict | None:
    """Fetch a reflex's run-history row from ``genesis_reflex_state`` (best-effort)."""
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT reflex_name, enabled, last_run_at, next_run_at, total_runs, "
                "total_failures, circuit_breaker_open FROM genesis_reflex_state "
                "WHERE reflex_name=%s",
                (reflex_name,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return dict(row) if hasattr(row, "keys") else {
            "reflex_name": row[0], "enabled": row[1], "last_run_at": row[2],
            "next_run_at": row[3], "total_runs": row[4], "total_failures": row[5],
            "circuit_breaker_open": row[6],
        }
    except Exception as exc:  # noqa: BLE001 — table may not exist in a fresh DB
        logger.debug("genesis_reflex_state unavailable for %s: %s", reflex_name, exc)
        return None


def _reflex_adherence(canvas_key: str, now: datetime) -> dict | None:
    """Refresh-schedule adherence for a canvas's twin reflex, or None if unscheduled."""
    mapping = CANVAS_TWIN_REFLEX.get(canvas_key)
    if not mapping:
        return None
    reflex_name, cadence = mapping
    state = _reflex_state(reflex_name)
    info = {
        "reflex": reflex_name,
        "cadence_seconds": cadence,
        "scheduled": True,
        "known": state is not None,
        "last_run_at": None,
        "age_seconds": None,
        "overdue": None,
        "circuit_breaker_open": None,
    }
    if state is None:
        # Reflex is expected but never recorded a run → overdue by definition.
        info["overdue"] = True
        return info
    last = _parse_iso(state.get("last_run_at"))
    info["last_run_at"] = state.get("last_run_at")
    info["circuit_breaker_open"] = bool(state.get("circuit_breaker_open"))
    if last is None:
        info["overdue"] = True
    else:
        age = (now - last).total_seconds()
        info["age_seconds"] = round(age, 1)
        info["overdue"] = age > cadence * _OVERDUE_FACTOR
    return info


def observe(window_hours: int = 24, stale_after_hours: int = DEFAULT_STALE_AFTER_HOURS) -> dict:
    """Aggregate cross-canvas twin health into one JSON-serializable dict."""
    now = datetime.now(timezone.utc)
    TwinRegistry.discover()
    adapters = TwinRegistry.all()

    twins: list[dict] = []
    verdict_totals = {v: 0 for v in VERDICTS}
    verdict_totals["unknown"] = 0
    violation_totals = {s: 0 for s in SEVERITIES}
    stale: list[str] = []
    overdue_reflexes: list[str] = []

    for key in sorted(adapters):
        adapter = adapters[key]
        try:
            health = adapter.fleet_health(window_hours=window_hours)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fleet_health failed for %s: %s", key, exc)
            health = {"available": False, "snapshot_count": None, "latest_snapshot_at": None,
                      "verdicts": {}, "violation_counts": {}}

        latest_at = _parse_iso(health.get("latest_snapshot_at"))
        age_seconds = round((now - latest_at).total_seconds(), 1) if latest_at else None
        # Only twins that persist snapshots can be "stale". Such a twin is stale
        # when it has no snapshots yet OR its newest snapshot exceeds the threshold.
        if adapter.snapshot_table:
            is_stale = latest_at is None or age_seconds > stale_after_hours * 3600
        else:
            is_stale = False

        for v, c in (health.get("verdicts") or {}).items():
            nv = v if v in verdict_totals else "unknown"
            verdict_totals[nv] += c or 0
        for s, c in (health.get("violation_counts") or {}).items():
            if s in violation_totals:
                violation_totals[s] += c or 0

        reflex = _reflex_adherence(key, now)
        if reflex and reflex.get("overdue"):
            overdue_reflexes.append(key)
        if is_stale:
            stale.append(key)

        twins.append({
            **adapter.describe(),
            "snapshot_count": health.get("snapshot_count"),
            "latest_snapshot_at": health.get("latest_snapshot_at"),
            "latest_snapshot_age_seconds": age_seconds,
            "stale": is_stale,
            "verdicts": health.get("verdicts") or {},
            "violation_counts": health.get("violation_counts") or {},
            "fleet_health_available": health.get("available", False),
            "reflex": reflex,
        })

    return {
        "generated_at": now.isoformat(),
        "window_hours": window_hours,
        "stale_after_hours": stale_after_hours,
        "twin_count": len(twins),
        "twins": twins,
        "summary": {
            "verdict_distribution": verdict_totals,
            "violation_counts": violation_totals,
            "total_violations": sum(violation_totals.values()),
            "stale_twins": stale,
            "overdue_reflexes": overdue_reflexes,
            "snapshotting_twins": sum(1 for t in twins if t.get("snapshot_count")),
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cross-canvas twin health observer (twin_core).")
    ap.add_argument("--json", action="store_true", help="Emit JSON (default).")
    ap.add_argument("--window-hours", type=int, default=24, help="Verdict-distribution window.")
    ap.add_argument("--stale-after-hours", type=int, default=DEFAULT_STALE_AFTER_HOURS,
                    help="Snapshot age beyond which a twin is flagged stale.")
    args = ap.parse_args(argv)
    report = observe(window_hours=args.window_hours, stale_after_hours=args.stale_after_hours)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
