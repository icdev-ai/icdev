# CUI // SP-CTI
"""Genesis reflex: wf_ext_poller — 15-min reflex to poll external step status."""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"
from tools.logging.icdev_logger import get_logger


logger = get_logger(__name__)


def run(config: dict, trust) -> dict:
    """Poll all waiting external steps; mark complete if done; escalate on timeout."""
    try:
        from tools.workflow_hitl.external_steps import check_all_pending
        resolved = check_all_pending()
    except Exception as exc:
        logger.warning("wf_ext_poller error: %s", exc)
        return {"status": "error", "error": str(exc)}

    logger.info("wf_ext_poller: resolved %d external steps", resolved)
    return {"status": "ok", "resolved": resolved}
