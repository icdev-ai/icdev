#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Redaction Scan Reflex — scheduled at-rest PII/CUI sweep (trust-mask-03).

Runs the DB PII scanner, builds a remediation plan for columns whose PII density
meets the threshold, and files deduped [PII-SCAN] kanban remediation cards so
unmasked sensitive data at rest is surfaced and actioned (not just detected).

GREEN tier — reads sampled DB rows, files kanban tasks. Detect-only scanner is
extended here into a detect-plan-remediate loop.
"""
IMPLEMENTATION_STATUS = "full"

import hashlib
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("redaction_scan_reflex")

_DEFAULT_THRESHOLD = 0.3   # pii_density at/above which a column is remediated
_DEFAULT_MAX_CARDS = 20    # cap remediation cards filed per run (avoid flooding)


def _card_id(table: str, column: str) -> str:
    """Deterministic id per (table, column) so re-runs dedup via task_factory."""
    # usedforsecurity=False: this is a dedup id, not a security digest.
    h = hashlib.sha1(f"{table}:{column}".encode(), usedforsecurity=False).hexdigest()[:10]
    return f"task-piiscan-{h}"


def _file_remediation_cards(plan: list, max_cards: int) -> list:
    """File deduped [PII-SCAN] kanban tasks for the plan. Returns filed ids."""
    if not plan:
        return []
    capped = plan[:max_cards]
    if len(plan) > max_cards:
        logger.warning(
            "redaction_scan_reflex: %d columns flagged, filing top %d by density (%d not carded this run)",
            len(plan), max_cards, len(plan) - max_cards,
        )
    specs = []
    for item in capped:
        table, col = item["table"], item["column"]
        ents = ", ".join(sorted(item.get("entity_types") or {})) or "PII"
        specs.append({
            "id": _card_id(table, col),
            "title": f"[PII-SCAN] Unmasked {ents} in {table}.{col}",
            "description": (
                f"Column {table}.{col} has PII density {item['pii_density']} "
                f"(entities: {item.get('entity_types')}). "
                f"Recommended treatment: {item['recommended_treatment']}. "
                f"Remediate by anonymizing at rest (tools/redaction/anonymizer.py) "
                f"and/or enabling redaction.mask_at_ingestion for its source."
            ),
            "task_type": "bug",
            "priority": "high" if item["pii_density"] >= 0.6 else "medium",
            "status": "backlog",
            "dispatch_source": "redaction_scan_reflex",
        })
    try:
        from tools.kanban.task_factory import create_tasks
        return create_tasks(specs)
    except Exception as exc:
        logger.warning("redaction_scan_reflex: kanban filing failed: %s", exc)
        return []


def run(config: dict, state: object) -> dict:
    """Entry point called by the Genesis daemon."""
    config = config or {}
    try:
        from tools.redaction.db_scanner import DBScanner, remediation_plan

        threshold = float(config.get("pii_density_threshold", _DEFAULT_THRESHOLD))
        max_cards = int(config.get("max_cards", _DEFAULT_MAX_CARDS))

        scanner = DBScanner(sample_size=int(config.get("sample_size", 20)))
        scan = scanner.scan()
        plan = remediation_plan(scan, threshold=threshold)
        filed = _file_remediation_cards(plan, max_cards)

        logger.info(
            "redaction_scan_reflex: %d columns with PII, %d flagged >= %.2f, %d cards filed",
            scan.get("columns_with_pii", 0), len(plan), threshold, len(filed),
        )
        return {
            "success": True,
            "metric_value": float(len(plan)),
            "details": {
                "tables_scanned": scan.get("tables_scanned", 0),
                "columns_with_pii": scan.get("columns_with_pii", 0),
                "flagged": len(plan),
                "cards_filed": len(filed),
                "filed_ids": filed,
                "threshold": threshold,
            },
        }
    except Exception as exc:
        logger.exception("redaction_scan_reflex failed: %s", exc)
        return {"success": False, "error": str(exc), "metric_value": 0.0}


if __name__ == "__main__":
    import json
    print(json.dumps(run({}, None), indent=2))
