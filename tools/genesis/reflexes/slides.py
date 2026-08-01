# CUI // SP-CTI
"""Genesis Slides Reflex — weekly auto-generated ICDEV status deck.

Runs every Friday at 17:00. Pulls from all ICDEV native source connectors,
generates a "ICDEV™ Weekly Status" deck, and persists it to slides_decks.

GREEN tier: read-only data access + file write. Zero destructive operations.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import sys
from datetime import datetime, timezone
from pathlib import Path
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

CADENCE_HOURS = 168  # weekly (7 * 24)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(config: dict, state) -> dict:
    """Generate weekly ICDEV status deck."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    title = f"ICDEV™ Weekly Status — {date_str}"

    logger.info("slides reflex: generating weekly deck: %s", title)

    try:
        from tools.slides.engine import DeckEngine, DeckRequest
        req = DeckRequest(
            title=title,
            deck_type="weekly_status",
            theme="midnight_executive",
            sources=["icdev_capabilities", "canvases", "kanban", "genesis"],
            max_slides=10,
            min_slides=6,
            enable_graphics=True,
        )
        result = DeckEngine().run(req)

        # A degraded/template deck is still a real, downloadable artifact — it is
        # honestly flagged, not a failure. Only a hard "failed" is a failure.
        if result.status in ("completed", "degraded", "template", "auto"):
            logger.info(
                "slides reflex: deck generated (%s) — %d slides, path: %s",
                result.status, len(result.slides), result.pptx_path,
            )
            # Emit genesis_outputs event for dashboard notification
            _emit_output_event(deck_id=result.deck_id, pptx_path=result.pptx_path, title=title)
            return {
                "status": "ok",
                "deck_status": result.status,
                "degraded": result.status in ("degraded", "template"),
                "deck_id": result.deck_id,
                "slide_count": len(result.slides),
                "pptx_path": result.pptx_path,
                "generated_at": _utcnow_iso(),
            }
        else:
            logger.warning("slides reflex: generation failed: %s", result.error)
            return {"status": "failed", "error": result.error}

    except Exception as exc:
        logger.error("slides reflex: unexpected error: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}


def _emit_output_event(deck_id, pptx_path: str, title: str) -> None:
    """Emit a genesis_outputs event so the dashboard shows the new deck."""
    import uuid
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO genesis_outputs (id, reflex_name, output_type, output_ref, summary, created_at) "
                "VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)",
                (str(uuid.uuid4()), "slides", "pptx", pptx_path, f"Weekly deck: {title}"),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # non-fatal if table not yet migrated on this installation
