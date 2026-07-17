#!/usr/bin/env python3
# CUI // SP-CTI
"""SDC ROI Calculator — compute automation savings per security design.

Reads sdc_roi_metrics from security_canvas.db. Falls back to default
(manual=200h, automated=4h) if no row exists for the design.

Usage:
    python tools/sdc/roi_calculator.py --design demo-design-001
    python tools/sdc/roi_calculator.py --design demo-design-001 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CANVAS_DB = _ROOT / "data" / "security_canvas.db"

_DEFAULTS = {
    "manual_hours": 200.0,
    "automated_hours": 4.0,
    "cost_per_hour": 150.0,
}


def _canvas_conn():
    # PG-primary via the Security Canvas helper (SC_STORAGE_BACKEND); SQLite is a
    # guarded fallback. Returns a StorageConnection so %s placeholders translate.
    from tools.security_canvas.db.init_db import get_connection

    return get_connection()


def compute_roi(design_id: str, hourly_rate: float = 150.0) -> dict:
    """Compute ROI metrics for a security design.

    Returns dict with: manual_hours, automated_hours, time_saved_hours,
    cost_saved_usd, roi_multiplier, ftes_avoided, engagement_type.
    Falls back to defaults if no sdc_roi_metrics row found.
    """
    conn = _canvas_conn()
    try:
        row = conn.execute(
            "SELECT * FROM sdc_roi_metrics WHERE design_id=%s LIMIT 1",
            (design_id,),
        ).fetchone()
    finally:
        conn.close()

    if row:
        manual = row["manual_hours"]
        automated = row["automated_hours"]
        rate = row["cost_per_hour"] or hourly_rate
        engagement_type = row["engagement_type"] or "standard"
    else:
        manual = _DEFAULTS["manual_hours"]
        automated = _DEFAULTS["automated_hours"]
        rate = hourly_rate
        engagement_type = "standard"

    time_saved = manual - automated
    cost_saved = time_saved * rate
    roi_mult = round(manual / max(automated, 0.1), 1)
    ftes_avoided = round(time_saved / 160, 2)  # ~1 FTE = 160h/month

    return {
        "design_id": design_id,
        "manual_hours": manual,
        "automated_hours": automated,
        "time_saved_hours": time_saved,
        "cost_saved_usd": int(cost_saved),
        "roi_multiplier": roi_mult,
        "ftes_avoided": ftes_avoided,
        "hourly_rate": rate,
        "engagement_type": engagement_type,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="SDC ROI Calculator")
    parser.add_argument("--design", default="demo-design-001")
    parser.add_argument("--hourly-rate", type=float, default=150.0)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    sys.path.insert(0, str(_ROOT))
    result = compute_roi(args.design, hourly_rate=args.hourly_rate)

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"Design: {result['design_id']}")
        print(f"Manual hours:    {result['manual_hours']:.0f}h")
        print(f"Automated hours: {result['automated_hours']:.0f}h")
        print(f"Time saved:      {result['time_saved_hours']:.0f}h")
        print(f"Cost saved:      ${result['cost_saved_usd']:,}")
        print(f"ROI multiplier:  {result['roi_multiplier']}x")
        print(f"FTEs avoided:    {result['ftes_avoided']}")


if __name__ == "__main__":
    main()
