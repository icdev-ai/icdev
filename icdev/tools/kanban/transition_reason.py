"""Make ``kanban_status_transitions.reason`` non-optional at the write boundary.

Why this module exists
----------------------
PR #1183 removed the reason-less ``_move_task(..., "backlog")`` call sites in
``tools/genesis/reflexes/kanban.py``, and every one of those sites is still
covered today. It did not close the hole, because it fixed *call sites* rather
than the *boundary*: ``_record_status_transition`` still accepts
``reason=None`` and writes the row anyway. So a blank row is produced by

  * any call site added later that forgets ``reason=`` — four such sites still
    exist for ``-> in_progress`` and were writing ~176 blank rows a day on
    2026-08-06/07; and
  * a scheduler process still holding pre-#1183 code in memory. That is what
    produced the 198 blank ``in_progress -> backlog`` rows measured between
    2026-08-02 and 2026-08-03: the five reason-less backlog sites were gone
    from disk at 855e1dd50..37e64009d, but the long-lived process was not
    re-imported, and nothing at the write boundary noticed.

Either way the row lands indistinguishable from the 2,152 historical blanks,
which is exactly the blindness #1183 set out to remove.

The rule
--------
A transition row may not be blank. When a caller supplies no reason, the row
records *who failed to supply one* — resolved from the call stack — so the row
is self-identifying instead of anonymous. That holds for call sites that do not
exist yet and for processes running code older than this module.

Fail-safe: every writer of this table is best-effort and must never let audit
bookkeeping break a status change, so nothing here raises. On any internal
error it degrades to a fixed non-empty string, never to ``None``.
"""

from __future__ import annotations

import traceback
from pathlib import Path

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Prefix that marks a reason this module synthesized rather than one a caller
# supplied. Queries can separate "we know why" from "nobody said why, and here
# is the code that did not say" without re-introducing a blank sentinel.
UNATTRIBUTED_PREFIX = "unattributed"

# Frames to name in the synthesized reason. Two is the useful number: the
# boundary wrapper (_move_task / transition / _set_task_status) plus the real
# call site above it. A third adds noise more often than signal.
_FRAMES_TO_NAME = 2

# Upper bound on the synthesized string. The column is free text and the
# scheduler's own reasons run to ~400 chars, so this is only a runaway guard.
_MAX_LEN = 400


def _describe_callers(skip: int) -> str:
    """Return ``file.py:line func < file.py:line func`` for the calling code.

    ``skip`` is the number of innermost frames to drop: this function, plus
    ``resolve_transition_reason`` itself, plus whatever the caller asks to hide.
    Frames belonging to this module are always dropped regardless of ``skip``,
    so an inlined or re-exported caller cannot accidentally name us.
    """
    try:
        stack = traceback.extract_stack()[:-skip]
        here = Path(__file__).name
        named = []
        for frame in reversed(stack):
            if Path(frame.filename).name == here:
                continue
            named.append(f"{Path(frame.filename).name}:{frame.lineno} {frame.name}")
            if len(named) >= _FRAMES_TO_NAME:
                break
        return " < ".join(named) if named else "unknown call site"
    except Exception:  # noqa: BLE001 - attribution is a nicety, never a failure
        return "unknown call site"


def resolve_transition_reason(
    reason: str | None,
    *,
    from_status: str | None = None,
    to_status: str | None = None,
    actor: str | None = None,
    skip_frames: int = 0,
) -> str:
    """Return a guaranteed non-empty reason for a status-transition row.

    Parameters
    ----------
    reason
        What the caller supplied. Returned unchanged (bar surrounding
        whitespace) whenever it carries any content — this never rewrites or
        truncates a real reason.
    from_status, to_status, actor
        Recorded in the synthesized text so a blank-origin row is readable on
        its own, without joining back to the task.
    skip_frames
        Extra innermost frames to hide, for writers that wrap this call in a
        helper of their own and want the synthesized text to name their
        caller rather than the helper.

    Returns
    -------
    str
        Either the caller's reason, or an ``unattributed: ...`` string naming
        the code that omitted one. Never ``None``, never empty.
    """
    try:
        if reason is not None:
            text = str(reason).strip()
            if text:
                return text
        # +2: this function and _describe_callers, which the caller never wants named.
        site = _describe_callers(skip=skip_frames + 2)
        pair = f"{from_status or '?'}->{to_status or '?'}"
        synthesized = (
            f"{UNATTRIBUTED_PREFIX}: caller passed no reason "
            f"({pair} by {actor or 'unknown actor'}) at {site}"
        )[:_MAX_LEN]
        logger.warning(
            "kanban transition recorded with no caller-supplied reason (%s, actor=%s) "
            "at %s — recording an attributed placeholder instead of a blank row",
            pair, actor or "unknown", site,
        )
        return synthesized
    except Exception:  # noqa: BLE001 - a blank row is the one outcome not allowed
        return f"{UNATTRIBUTED_PREFIX}: caller passed no reason"
