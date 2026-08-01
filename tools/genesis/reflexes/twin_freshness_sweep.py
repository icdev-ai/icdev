# CUI // SP-CTI
"""Genesis Reflex — Cross-Canvas Twin Freshness Sweep (twx-cov-02, 6h cadence).

The system-twin payoff of the twin_core unification: a SINGLE reflex that keeps
every registered canvas twin fresh, instead of one near-duplicate refresh reflex
per canvas. It runs the twin_core observer and, for each twin whose newest
snapshot is stale (or which has never been snapshotted), publishes a
``twin.snapshot.stale`` event on the cross-canvas bus so the owning canvas (or a
HITL surface) can re-snapshot.

This fills the residual twin-refresh gap for twins that lack a dedicated
refresh reflex (e.g. AADC, Mission) and is future-proof: any twin added to the
registry later is covered automatically, with no new reflex.

Read-only (green tier): it never writes twin snapshots itself — it only observes
and nudges. Air-gap safe (no LLM). Respects the bus's security-context
propagation via the default CUI context.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

from typing import Any, Dict

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

CADENCE_HOURS = 6


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Sweep every registered twin for snapshot staleness and nudge stale ones.

    Returns:
        twins_checked:   number of registered twins observed
        stale_twins:     list of canvas keys with a stale/absent newest snapshot
        events_published: count of twin.snapshot.stale events emitted
    """
    dry_run = ctx.get("dry_run", False)
    stale_after_hours = int(ctx.get("stale_after_hours", 48))
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "twins_checked": 0,
        "stale_twins": [],
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }
    try:
        from tools.twin_core.observer import observe

        report = observe(stale_after_hours=stale_after_hours)
        result["twins_checked"] = report.get("twin_count", 0)
        stale = report.get("summary", {}).get("stale_twins", []) or []
        result["stale_twins"] = stale

        # Index per-twin detail so the event carries useful context.
        detail = {t.get("canvas"): t for t in report.get("twins", [])}

        if stale and not dry_run:
            try:
                from tools.canvas.event_bus import publish

                for canvas in stale:
                    t = detail.get(canvas, {})
                    publish("twin_core", "twin.snapshot.stale", {
                        "canvas": canvas,
                        "snapshot_count": t.get("snapshot_count"),
                        "latest_snapshot_at": t.get("latest_snapshot_at"),
                        "latest_snapshot_age_seconds": t.get("latest_snapshot_age_seconds"),
                        "stale_after_hours": stale_after_hours,
                    }, target_canvas=canvas)
                    result["events_published"] += 1
            except Exception as exc:  # noqa: BLE001 — bus optional/air-gap
                result["errors"].append(f"event_bus: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.error("twin_freshness_sweep reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    return result


if __name__ == "__main__":
    import json as _json

    print(_json.dumps(run({"dry_run": True}), indent=2))
