#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Sim Training Export Reflex — Phase 4 pipeline.

Runs on a 6-hour cadence. Scans all canvas training directories and bulk-exports
training_pair.json files into ft_datasets / ft_dataset_examples for fine-tuning.

One dataset per canvas named ``icdev-<canvas>-sim`` is created or reused.
Deduplication is handled by content hash in ``add_example()``.

Return value:
  {"canvases": N, "total_added": N, "total_skipped": N, "total_errors": N}

Risk tier: GREEN (DB writes to ft_datasets / ft_dataset_examples only; no code mutation).
Cadence: every 6h (configurable via genesis_config.yaml).
"""
from __future__ import annotations
IMPLEMENTATION_STATUS = "full"

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]


def run(config: dict, trust_kernel) -> dict:
    """Main entry point called by GenesisDaemon."""
    promote_ready = config.get("promote_ready", True)

    try:
        from tools.studio.sim.training_exporter import export_all
        results = export_all(promote_ready=promote_ready)
    except Exception as exc:
        _log_audit("sim_training_export", "error", {"error": str(exc)})
        return {"canvases": 0, "total_added": 0, "total_skipped": 0,
                "total_errors": 1, "error": str(exc)}

    total_added   = sum(r.get("examples_added", 0) for r in results)
    total_skipped = sum(r.get("skipped_duplicate", 0) for r in results)
    total_errors  = sum(r.get("errors", 0) for r in results)

    _log_audit("sim_training_export", "success", {
        "canvases": len(results),
        "total_added": total_added,
        "total_skipped": total_skipped,
        "total_errors": total_errors,
        "detail": results,
    })

    return {
        "canvases":      len(results),
        "total_added":   total_added,
        "total_skipped": total_skipped,
        "total_errors":  total_errors,
        "detail":        results,
    }


def _log_audit(reflex: str, status: str, payload: dict) -> None:
    if get_connection is None:
        return
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO genesis_audit_log
               (reflex_name, status, details_json, created_at)
               VALUES (%s, %s, %s, %s)""",
            (reflex, status, json.dumps(payload),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Standalone CLI ─────────────────────────────────────────────────────────────

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
    import argparse

    parser = argparse.ArgumentParser(description="Genesis Sim Training Export Reflex")
    parser.add_argument("--json",           action="store_true")
    parser.add_argument("--promote-ready",  action="store_true", default=True)
    args = parser.parse_args()

    cfg = {"promote_ready": args.promote_ready}
    result = run(cfg, None)
    print(json.dumps(result, indent=2))
