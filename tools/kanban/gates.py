#!/usr/bin/env python3
# CUI // SP-CTI
"""Manual-mode gate predicate — the single source of truth.

A manual-mode gate (e.g. ``prem-gate-00``) is a SENTINEL, not work. It is held
``in_progress`` FOREVER by design: it exists only to block its dependents from
auto-dispatch while a human implements them (they target private external repos
the runner cannot build in). It must never be promoted, dispatched, reaped,
startup-recovered, auto-completed, or moved by a board arrow.

This predicate used to be copy-pasted into three modules, each with a comment
explaining that it was duplicated "rather than imported because this module is a
standalone CLI that must not drag in the whole reflex". That reason is real, so
the fix is this module: it imports NOTHING, so any caller — CLI, scheduler,
reflex, Flask route — can import it without pulling in a dependency tree.

Keep it import-free.
"""

from __future__ import annotations

GATE_ID_SUFFIX = "-gate-00"
GATE_TITLE_MARKER = "MANUAL-MODE GATE"


def is_manual_gate(task_id: str | None, title: str | None) -> bool:
    """True when the task is a manual-mode gate sentinel.

    Matches on EITHER the id suffix or the title marker, so a gate is still
    recognised if one of the two is renamed.
    """
    return str(task_id or "").endswith(GATE_ID_SUFFIX) or GATE_TITLE_MARKER in (title or "")
