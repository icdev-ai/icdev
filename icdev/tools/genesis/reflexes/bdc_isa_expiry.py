# CUI // SP-CTI
"""BDC ISA Expiry Alerting Reflex — 4-hour cadence.

Runs as a Genesis reflex every 4 hours.  Delegates to
tools.boundary_canvas.isa_expiry.check_isa_expiry() which:
  - Queries bd_isa_tracker for ISAs expiring within 90 days
  - Publishes 'bdc' / 'isa_expiring_soon' canvas events
  - Sends Telegram notifications for ISAs expiring within 30 days

Reflex contract:
  - run(ctx, conn) → dict with keys: isas_checked, events_published,
                                     notifications_sent, dry_run
  - CADENCE_HOURS = 4 (read by Genesis scheduler)
  - Must be idempotent — canvas event bus is append-only; Telegram is fire-and-forget
  - Must not raise — catches all exceptions and returns them in errors[]
"""
IMPLEMENTATION_STATUS = "full"
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from typing import Any, Dict

logger = get_logger(__name__)

CADENCE_HOURS = 4


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Execute one BDC ISA expiry alert cycle.

    Args:
        ctx:  Genesis context dict (may contain 'dry_run').
        conn: Unused — BDC uses its own DB connection via get_connection().

    Returns:
        Summary dict with isas_checked, events_published, notifications_sent, errors.
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "errors": [],
        "status": "ok",
    }
    try:
        from tools.boundary_canvas.isa_expiry import check_isa_expiry
        check_result = check_isa_expiry(dry_run=dry_run)
        result.update(check_result)
    except Exception as exc:
        logger.error("bdc_isa_expiry reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    return result


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run({}), indent=2))
