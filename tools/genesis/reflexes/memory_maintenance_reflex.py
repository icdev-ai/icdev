# CUI // SP-CTI
"""Genesis reflex — scheduled memory-tier upkeep (embedding backfill + buffer flush).

The memory tier's upkeep (tools/memory/maintenance_cron.py) was fully built but
NOT scheduled — nothing invoked it from the daemon, a reflex, or a cron. The
measured consequence (oss2-meas-01): 0% of memory_entries carried an embedding, so
hybrid_search's semantic half was inert and recall was keyword-only. This reflex
closes that scheduling gap.

Deliberately NON-DESTRUCTIVE: it flushes the capture buffer and backfills embeddings
for entries missing them, both of which only add data. It does NOT auto-prune
(deletes rows) or auto-consolidate (merges rows) — those change existing memory and
warrant their own explicit opt-in, so they stay on the manual maintenance_cron path.

Air-gap safe: embedding uses the LLM provider abstraction and returns
``status: no_provider`` (embedded 0) when none is reachable, so the reflex degrades
to a flush-only no-op rather than failing. Bounded per run so a large embedding
backlog is caught up over several cycles instead of stalling one.
"""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Read by the daemon's stub-detector; this is a real implementation.
IMPLEMENTATION_STATUS = "full"

#: Cap unembedded entries processed per cycle so one run can't stall the daemon.
EMBED_MAX_PER_RUN = 200


def run(args: dict, _ctx=None) -> dict:
    """Flush the memory buffer, then backfill up to EMBED_MAX_PER_RUN embeddings.

    Best-effort and self-contained: a missing maintenance module or an unreachable
    embedding provider never aborts the reflex, matching every other reflex's
    degrade-not-crash posture.
    """
    try:
        from tools.memory import maintenance_cron as mc
    except ImportError as exc:
        logger.warning("[memory_maintenance_reflex] maintenance_cron unavailable: %s", exc)
        return {"skipped": True, "reason": str(exc)}

    result: dict = {}

    # Flush any buffered captures into memory_entries (non-destructive).
    try:
        result["flush"] = mc.flush_buffer()
    except Exception as exc:  # noqa: BLE001 - one step's failure must not abort the reflex
        logger.warning("[memory_maintenance_reflex] flush_buffer failed: %s", exc)
        result["flush"] = {"error": str(exc)}

    # Backfill embeddings for entries missing them, bounded per run.
    try:
        embed = mc.embed_unembedded(limit=EMBED_MAX_PER_RUN)
        result["embed"] = embed
        if embed.get("status") == "no_provider":
            logger.info("[memory_maintenance_reflex] no embedding provider reachable; flush-only this cycle")
        else:
            logger.info(
                "[memory_maintenance_reflex] embedded %s (errors %s) of %s unembedded",
                embed.get("embedded"), embed.get("errors"), embed.get("total_unembedded"),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[memory_maintenance_reflex] embed_unembedded failed: %s", exc)
        result["embed"] = {"error": str(exc)}

    return result
