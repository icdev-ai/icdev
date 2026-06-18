# CUI // SP-CTI
"""Conflict intelligence pipeline — Ingest → Filter → Visualize.

Wires the federated data mesh, conflict timeline, and intelligence report engine
into a single end-to-end pipeline. Intended as the application entry point for
the conflict monitoring system.

Usage:
    python -m icdev.tools.strategos.conflict_pipeline --dry-run --json
    python -m icdev.tools.strategos.conflict_pipeline --theater IL --json
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from icdev.tools.strategos.federated_mesh import run as mesh_run  # noqa: E402
from icdev.tools.strategos.conflict_timeline import fetch_timeline, render_timeline_html  # noqa: E402
from icdev.tools.strategos.intel_report_engine import generate_leadership_brief  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.strategos.conflict_pipeline")


def run_pipeline(
    theater_id: str | None = None,
    dry_run: bool = False,
    render_html: bool = False,
) -> dict[str, Any]:
    """Execute full pipeline: Ingest → Filter → Visualize.

    Returns a structured report with timing, signal counts, and leadership brief.
    Target: completes within 10 minutes (enforced by internal timer).
    """
    t_start = time.monotonic()
    result: dict[str, Any] = {
        "pipeline": "conflict_intelligence",
        "theater_id": theater_id or "GLOBAL",
        "dry_run": dry_run,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "stages": {},
    }

    # ── Stage 1: Ingest ───────────────────────────────────────────────────────
    logger.info("[Stage 1] Federated mesh ingest")
    if not dry_run:
        ingest_result = mesh_run(theater_id)
    else:
        ingest_result = {"ingest": {"total_pulled": 0, "inserted": 0}, "patterns": []}
    result["stages"]["ingest"] = ingest_result
    elapsed = time.monotonic() - t_start
    logger.info("[Stage 1] Done in %.1fs — %s signals", elapsed, ingest_result.get("ingest", {}).get("inserted", 0))

    # Guard: abort if taking too long (safety gate)
    if elapsed > 540:  # 9 minutes
        result["error"] = "pipeline_timeout_ingest_exceeded_9min"
        return result

    # ── Stage 2: Filter / Timeline ────────────────────────────────────────────
    logger.info("[Stage 2] Conflict timeline filtering")
    timeline = fetch_timeline(theater_id=theater_id)
    result["stages"]["filter"] = {
        "markers_found": len(timeline.get("markers", [])),
        "summary": timeline.get("summary", {}),
    }
    elapsed = time.monotonic() - t_start
    logger.info("[Stage 2] Done in %.1fs — %s markers", elapsed, len(timeline.get("markers", [])))

    if elapsed > 570:
        result["error"] = "pipeline_timeout_filter_exceeded_9.5min"
        return result

    # ── Stage 3: Visualize / Report ───────────────────────────────────────────
    logger.info("[Stage 3] Generating intelligence brief")
    if not dry_run:
        brief = generate_leadership_brief(theater_id)
    else:
        brief = {"dry_run": True, "message": "Leadership brief skipped in dry-run mode"}
    result["stages"]["visualize"] = brief

    if render_html:
        result["timeline_html"] = render_timeline_html(timeline)

    elapsed = time.monotonic() - t_start
    result["elapsed_seconds"] = round(elapsed, 2)
    result["completed_at"] = datetime.now(timezone.utc).isoformat()
    result["status"] = "success"

    logger.info("Pipeline complete in %.1fs", elapsed)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Conflict intelligence pipeline")
    ap.add_argument("--theater", default=None, help="Theater ID filter (e.g. IL, UA)")
    ap.add_argument("--dry-run", action="store_true", help="Skip DB writes")
    ap.add_argument("--html", action="store_true", help="Include HTML timeline fragment")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    output = run_pipeline(args.theater, args.dry_run, args.html)
    print(json.dumps(output, indent=2))
