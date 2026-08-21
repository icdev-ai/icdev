#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Detector-Findings Reflex (autonomy-act-02) — runs the three detectors
nobody else runs (status_churn, born_red_survey, recovery_summary) and turns
each finding into ONE kanban card carrying its evidence and its derivation.

Delegates all logic to tools/kanban/detector_findings.py. Dedupes on the
FINDING (one projection row per detector/subject/fingerprint, ``seen_count``
bumped on re-observation), never on the run; seeds through
task_factory.create_tasks, never a raw INSERT.
GREEN tier — reads the board, the audit trail and the ungated-test baseline;
writes the projection and files `suggested` cards.
"""
IMPLEMENTATION_STATUS = "full"

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("detector_findings_reflex")


def run(config: dict, state: object) -> dict:
    """Entry point called by the Genesis daemon. ``config`` is this reflex's
    block from args/genesis_config.yaml (max_cards_per_run, seed_status,
    detectors.<name>.*)."""
    try:
        from tools.kanban.detector_findings import consume

        report = consume(config or {})
        ok = report.get("state") in ("ok", "partial")
        if report.get("state") == "unmigrated":
            # Loud, and a FAILURE: a reflex that runs and can write nothing is
            # the declared-but-inert shape this card exists to remove.
            logger.error("detector_findings_reflex: %s", "; ".join(report.get("errors") or []))
        return {
            "success": ok,
            "metric_value": float(report.get("findings_seen") or 0),
            "details": report,
            **({"error": "; ".join(report.get("errors") or [])} if not ok else {}),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("detector_findings_reflex failed: %s", exc)
        return {"success": False, "error": str(exc), "metric_value": 0.0}


if __name__ == "__main__":
    import json
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env", override=True)
    except ImportError:
        pass
    print(json.dumps(run({}, None), indent=2, default=str))
