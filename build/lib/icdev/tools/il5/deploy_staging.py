#!/usr/bin/env python3
# CUI // SP-CTI
"""IL5 staging deployment and latency validation.

Validates the IL5 ingestion-and-display pipeline against the 30-second SLA
requirement in a staging environment. Three checks:

  1. Module import health — key IL5 modules are importable
  2. SLA constant verification — SLA_SECONDS == 30
  3. End-to-end latency test — ingest a synthetic event and verify display
     latency is < 30 s

Exit 0 on pass, 1 on fail.

Usage:
    python tools/il5/deploy_staging.py
    python tools/il5/deploy_staging.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

SLA_LIMIT_S = 30


def _check_imports() -> dict:
    """Verify all IL5 modules import without error."""
    modules = [
        "tools.il5.ingestion",
        "tools.il5.sla_handler",
        "tools.il5.il5_ingestion_service",
    ]
    failures = []
    for mod in modules:
        try:
            __import__(mod)
        except Exception as exc:
            failures.append(f"{mod}: {exc}")
    return {
        "check": "import_health",
        "modules_checked": len(modules),
        "failures": failures,
        "passed": len(failures) == 0,
    }


def _check_sla_constant() -> dict:
    """Verify SLA_SECONDS constant is set to 30."""
    try:
        from tools.il5.ingestion import SLA_SECONDS  # noqa: PLC0415

        passed = SLA_SECONDS == SLA_LIMIT_S
        return {
            "check": "sla_constant",
            "sla_seconds": SLA_SECONDS,
            "passed": passed,
            "message": f"SLA_SECONDS = {SLA_SECONDS}" + ("" if passed else f" (expected {SLA_LIMIT_S})"),
        }
    except Exception as exc:
        return {"check": "sla_constant", "passed": False, "error": str(exc)}


def _check_end_to_end_latency() -> dict:
    """Run an end-to-end IL5 ingest+display timing test.

    Uses a temporary SQLite DB so the main store is not polluted.
    Source publication timestamp is set to 'now', so display_latency_s
    will be at most a few milliseconds — well within the 30 s SLA.
    """
    from tools.il5.ingestion import check_sla_compliance, ingest_il5_event  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "staging_test.db"

        source_published_at = datetime.now(timezone.utc)
        t0 = time.monotonic()

        event_id = ingest_il5_event(
            source_id="staging-validation",
            content="IL5 staging validation payload — synthetic test event",
            source_published_at=source_published_at,
            metadata={"test": True, "environment": "staging"},
            db_path=db_path,
        )

        pipeline_elapsed_s = time.monotonic() - t0
        sla_record = check_sla_compliance(event_id, db_path=db_path)

        latency_s = sla_record.get("latency_s") if sla_record else pipeline_elapsed_s
        sla_met_flag = sla_record.get("sla_met") if sla_record else None
        passed = (
            bool(sla_met_flag)
            and pipeline_elapsed_s < SLA_LIMIT_S
            and (latency_s is None or latency_s < SLA_LIMIT_S)
        )

        return {
            "check": "end_to_end_latency",
            "event_id": event_id,
            "pipeline_elapsed_s": round(pipeline_elapsed_s, 4),
            "display_latency_s": round(latency_s, 4) if latency_s is not None else None,
            "sla_limit_s": SLA_LIMIT_S,
            "sla_met": sla_met_flag,
            "passed": passed,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="IL5 staging deployment and latency validation")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    checks = [
        _check_imports(),
        _check_sla_constant(),
        _check_end_to_end_latency(),
    ]

    all_passed = all(c.get("passed", False) for c in checks)
    passed_count = sum(1 for c in checks if c.get("passed", False))

    result = {
        "deployment": "il5-staging",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "passed": all_passed,
        "sla_requirement_s": SLA_LIMIT_S,
        "checks": checks,
        "summary": f"{'PASS' if all_passed else 'FAIL'} — {passed_count}/{len(checks)} checks passed",
    }

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"IL5 Staging Validation — {result['summary']}")
        for check in checks:
            status = "PASS" if check.get("passed") else "FAIL"
            name = check.get("check", "?")
            print(f"  [{status}] {name}")
            if not check.get("passed"):
                for key in ("error", "failures", "message"):
                    val = check.get(key)
                    if val:
                        print(f"         {key}: {val}")
            if name == "end_to_end_latency":
                lat = check.get("display_latency_s")
                elapsed = check.get("pipeline_elapsed_s")
                limit = check.get("sla_limit_s", SLA_LIMIT_S)
                print(f"         display_latency={lat}s  pipeline_elapsed={elapsed}s  limit={limit}s")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
