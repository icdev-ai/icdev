# CUI // SP-CTI
"""ICDEV(TM) Pulse — publish gate on the LLM-judge verdict (nav-intel-09).

The Pulse blog's LLM judge (Prometheus-2, ``tools/writing/llm_judge.py``) scores
each post and maps the composite to a FAR 15.305 color rating stored on the post
row as ``judge_color`` — one of ``blue`` / ``purple`` / ``green`` / ``yellow`` /
``red``. Historically the verdict was advisory only: publishing succeeded
regardless of the color.

Product decision ("block red"): a **RED** verdict HARD-BLOCKS publishing. This
module is the single source of truth for that gate, shared by the publish
endpoint (``tools/dashboard/api/pulse.py``) and the batch / scheduler
auto-publish paths (``publish_all_approved`` in the WordPress and Hostinger
publishers).

Fail-closed semantics (matches the fail-closed quality check, PR #597):
  * ``red``                     → blocked (unacceptable quality).
  * missing / empty verdict     → blocked ("run the judge first"); a judge that
                                  never ran or errored leaves ``judge_color``
                                  empty and is treated as *not cleared*, never
                                  as an implicit pass.
  * ``green`` / ``yellow`` /
    ``blue`` / ``purple``       → cleared, publishes normally.

An audited HITL override (``force_publish`` + ``force_reason``, admin only) can
bypass the block; every override writes one immutable row to
``pulse_publish_audit`` (append-only, NIST AU — migration 281), mirroring the
OPORD/INTSUM grounding force-override pattern in ``tools/strategos/opord.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# The one blocking verdict. All other colors (and only actual colors) clear.
RED_VERDICT = "red"

# The full FAR 15.305 vocabulary the judge can emit (lowest → highest quality is
# red → blue). Kept for validation / documentation; only ``red`` blocks.
JUDGE_COLORS = ("red", "yellow", "green", "purple", "blue")

_REASON_RED = (
    "LLM judge verdict is RED (unacceptable quality) — publishing is blocked. "
    "Rewrite and re-run the judge, or apply an admin force override."
)
_REASON_ABSENT = (
    "The LLM judge has not cleared this post (no verdict on record) — run the "
    "judge before publishing."
)


def _verdict_of(post: dict[str, Any]) -> str:
    """Return the post's latest judge verdict, normalized to lowercase."""
    return (post.get("judge_color") or "").strip().lower()


def evaluate_publish_gate(post: dict[str, Any]) -> dict[str, Any]:
    """Decide whether ``post`` may be published based on its latest judge verdict.

    Args:
        post: A Pulse post row (dict). Only ``judge_color`` is consulted.

    Returns:
        dict with keys:
          * ``blocked`` (bool)  — True if publishing must be refused.
          * ``cleared`` (bool)  — convenience inverse of ``blocked``.
          * ``verdict`` (str|None) — the normalized color, or None if absent.
          * ``reason`` (str)    — human-readable explanation (empty when cleared).
    """
    verdict = _verdict_of(post)
    if not verdict:
        return {"blocked": True, "cleared": False, "verdict": None, "reason": _REASON_ABSENT}
    if verdict == RED_VERDICT:
        return {"blocked": True, "cleared": False, "verdict": RED_VERDICT, "reason": _REASON_RED}
    return {"blocked": False, "cleared": True, "verdict": verdict, "reason": ""}


def record_publish_override(
    post_id: str,
    reviewer: str,
    verdict: str | None,
    reason: str,
    tenant_id: str | None = None,
) -> None:
    """Append an immutable audit row for a HITL force-publish override (NIST AU).

    Best-effort: the append-only ``pulse_publish_audit`` table may be absent
    before migration 281 runs; a failure here must never break the publish flow.
    """
    from tools.db.storage import get_connection, is_pg

    ph = "%s" if is_pg() else "?"
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO pulse_publish_audit "
                f"(id, post_id, action, verdict, reviewer, reason, tenant_id, created_at) "  # nosec B608 -- constant columns
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
                (
                    str(uuid.uuid4()),
                    post_id,
                    "force_publish",
                    (verdict or "absent"),
                    reviewer,
                    reason,
                    tenant_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
    except Exception as exc:  # pragma: no cover - audit table may be absent pre-migration
        logger.warning("pulse publish override audit write skipped: %s", exc)
