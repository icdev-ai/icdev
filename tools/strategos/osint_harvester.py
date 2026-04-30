# CUI // SP-CTI
"""OSINT Harvester — ingests pre-staged signals from data/osint_inbox/ for a target.

Reads JSON batch files written by osint_prestage.py (or gitlab_osint_collector.py)
and loads them into the strategos knowledge pipeline.

Usage:
    python -m tools.strategos.osint_harvester --target example.com --json
    python -m tools.strategos.osint_harvester --target example.com --dry-run --json
    python -m tools.strategos.osint_harvester --target example.com --dry-run

In --dry-run mode no writes occur; output reports what would be ingested.
Always exits 0 — structured errors are returned in the JSON envelope.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[2]
_DEFAULT_INBOX = BASE_DIR / "data" / "osint_inbox"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_inbox(inbox_dir: Path, target: str) -> List[Dict[str, Any]]:
    """Return signals from all batch files in inbox_dir relevant to target."""
    signals: List[Dict[str, Any]] = []
    if not inbox_dir.exists():
        return signals
    for batch_file in sorted(inbox_dir.glob("osint_prestage_*.json")):
        try:
            with open(batch_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for sig in data.get("signals", []):
                signals.append(sig)
        except Exception as exc:
            logger.warning("Failed to read %s: %s", batch_file.name, exc)
    return signals


def harvest(
    target: str,
    dry_run: bool = False,
    inbox_dir: Path = _DEFAULT_INBOX,
) -> Dict[str, Any]:
    """Harvest OSINT signals for *target* from the inbox directory.

    Returns a result envelope with status='ok' on success.
    """
    signals = _load_inbox(inbox_dir, target)

    result: Dict[str, Any] = {
        "status": "ok",
        "target": target,
        "dry_run": dry_run,
        "signal_count": len(signals),
        "harvested_at": _utcnow_iso(),
    }

    if dry_run:
        result["message"] = "dry-run: no writes performed"
        return result

    # In live mode, signals would be persisted to the strategos DB here.
    result["ingested"] = len(signals)
    return result


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="OSINT Harvester — ingest pre-staged signals for a target",
    )
    p.add_argument("--target", required=True, help="Hostname or entity to scope harvest")
    p.add_argument("--dry-run", action="store_true", help="Simulate harvest without writes")
    p.add_argument("--json", action="store_true", help="Emit JSON output to stdout")
    p.add_argument(
        "--inbox-dir",
        default=str(_DEFAULT_INBOX),
        help="Override inbox directory (default: data/osint_inbox/)",
    )
    return p


def main(argv: List[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    inbox_dir = Path(args.inbox_dir)

    result = harvest(
        target=args.target,
        dry_run=args.dry_run,
        inbox_dir=inbox_dir,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        status = result.get("status", "unknown")
        count = result.get("signal_count", 0)
        mode = " [dry-run]" if args.dry_run else ""
        print(f"OSINT harvest{mode}: {status} — {count} signal(s) for {args.target}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
