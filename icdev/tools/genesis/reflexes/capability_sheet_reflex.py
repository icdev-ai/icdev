#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — Capability Sheet Weekly Update (capability_sheet).

Triggered by Genesis on a weekly schedule.

Cycle:
  delta    -> detect new feat: commits since last_commit_sha
  draft    -> LLM drafts new/updated rows with status=draft
  generate -> regenerate Excel from approved rows only
"""
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

REFLEX_NAME = "capability_sheet"
SCHEDULE    = "weekly"   # Genesis reads this to set the cadence


def run(ctx: dict, session) -> dict:
    root   = Path(__file__).resolve().parents[4]
    python = sys.executable
    runner = root / "icdev" / "tools" / "showcase" / "capability_sheet_runner.py"

    try:
        result = subprocess.run(
            [python, str(runner)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=180,
        )
        output = (result.stdout or "") + (result.stderr or "")
        log.info("[capability_sheet_reflex] %s", output.strip())
        return {
            "status":     "ok" if result.returncode == 0 else "error",
            "output":     output,
            "returncode": result.returncode,
        }
    except Exception as exc:
        log.error("[capability_sheet_reflex] failed: %s", exc)
        return {"status": "error", "error": str(exc)}
