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

#: The line a gate uses to say WHY it is held (kpr-idle-02).
#:
#: A gate stops work, so it owes a reason. Holding is only justified by a
#: specific risk of letting the runner build the card unattended — "these are
#: design decisions, not agent work", "the target is a private repo the runner
#: cannot reach", "the tasks need a human in the loop for classification".
#: Without one, the gate is not a control; it is just work nobody has looked at,
#: and the board cannot tell the two apart.
#:
#: Measured on the live board 2026-08-03: of four held gates, two stated a
#: reason and two did not. One of the unjustified pair had been holding 19
#: tasks — three of them critical — for 39 hours.
RISK_MARKER = "RISK:"

#: Prose that states a risk without using the marker. Deliberately narrow: it
#: recognises the phrasings already on the board so existing gates are not all
#: declared unjustified overnight, but it is not a general intent-detector. A
#: gate that matches only by prose is reported as `implicit`, so the difference
#: between "someone wrote a reason" and "someone wrote the reason down properly"
#: stays visible.
_IMPLICIT_RISK_PHRASES = (
    "not agent work",
    "not on an agent",
    "waiting on decisions",
    "human in the loop",
    "cannot build",
    "cannot reach",
    "private repo",
    "by a cli session",
    "needs a human",
)


def is_manual_gate(task_id: str | None, title: str | None) -> bool:
    """True when the task is a manual-mode gate sentinel.

    Matches on EITHER the id suffix or the title marker, so a gate is still
    recognised if one of the two is renamed.
    """
    return str(task_id or "").endswith(GATE_ID_SUFFIX) or GATE_TITLE_MARKER in (title or "")


def declared_risk(description: str | None) -> tuple[str | None, str]:
    """The risk this gate states, and how plainly it states it (kpr-idle-02).

    Returns ``(risk_text, confidence)`` where confidence is one of:

    ``explicit``
        a ``RISK:`` line — the intended form
    ``implicit``
        risk-shaped prose, recognised so existing gates are not all condemned
        at once, but reported differently so it can be tidied
    ``none``
        nothing. The gate is holding work for no recorded reason.

    Procedure is not risk. Several gates on this board say "do not move this to
    done without an explicit decision" or "re-hold after /start" — those tell a
    reader what to DO, not what goes wrong if the runner builds the card, so
    they do not count. That distinction is the whole point: a gate whose only
    justification is "someone decided to hold it" cannot be reviewed, because
    there is nothing to review.
    """
    text = (description or "").strip()
    if not text:
        return None, "none"

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(RISK_MARKER):
            stated = stripped[len(RISK_MARKER):].strip()
            if stated:
                return stated, "explicit"

    lowered = text.lower()
    for phrase in _IMPLICIT_RISK_PHRASES:
        if phrase in lowered:
            for line in text.splitlines():
                if phrase in line.lower():
                    return line.strip(), "implicit"
    return None, "none"
