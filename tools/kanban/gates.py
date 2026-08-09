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

#: The id a seeder gives a card's FIRST gate. Seeders still validate against this
#: exact string (``tools/idp/gap_seeder.py``) — it is the convention to write.
GATE_ID_SUFFIX = "-gate-00"

#: The separator that makes an id gate-shaped. Recognition is deliberately WIDER
#: than the seeding convention: a card may need a SECOND gate, and the natural id
#: for it is ``-gate-01``.
#:
#: That is not hypothetical. ``hgx-gate-01`` held six tasks that have an agent edit
#: its own guardrails — ``.claude/hooks/pre_tool_use.py``, parallel-dispatch
#: eligibility, the kanban dispatcher, the genesis daemon. Matching only on
#: ``-gate-00`` meant the predicate returned False for it, so the gate was promoted,
#: dispatched to an unattended session, and handed a prompt instructing it to mark
#: ITSELF done — which would have released all six. It was the only unrecognised
#: gate among fifteen on the board, because it was the only one that was not a
#: card's first.
#:
#: A gate that cannot protect itself is not a control, so recognition must not
#: depend on a gate being numbered 00.
GATE_ID_SEPARATOR = "-gate-"

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
#:
#: The second group states a CONSEQUENCE of dispatching rather than a property of
#: the work. Added 2026-08-09, after the advisor recommended releasing
#: agov-gate-00 — a gate whose description says in plain prose that "autonomous
#: dispatch of this card is not acceptable", because its 19 tasks edit
#: .claude/hooks/pre_tool_use.py and approval_gate.py while pr_watcher auto-merges
#: anything CI-green. None of the phrases above matched, so it scored risk=None,
#: took the +10000 unjustified penalty, and came out TOP of the release ranking.
#: A guard that recommends releasing the one gate whose text says not to is worse
#: than no guard: it is confidently wrong, and it was quoted to a human as a
#: recommendation.
#:
#: Still not an intent-detector, and still not procedure. "Do not move this to
#: done" says what to DO; these say what GOES WRONG if the runner builds the card.
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
    # consequence of dispatching it
    "is not acceptable",
    "auto-merge",
    "without review",
    "unattended",
    "no human",
)


def _has_gate_id(task_id: str | None) -> bool:
    """True when the id is ``<card>-gate-<number>`` for any number.

    Split from the right so a card prefix that itself contains the separator
    cannot confuse the number. The prefix must be non-empty and the tail must be
    all digits, so ``sme-gate-review`` and a bare ``-gate-01`` are not gates.
    """
    prefix, separator, number = str(task_id or "").rpartition(GATE_ID_SEPARATOR)
    return bool(prefix) and bool(separator) and number.isdigit()


def is_manual_gate(task_id: str | None, title: str | None) -> bool:
    """True when the task is a manual-mode gate sentinel.

    Matches on EITHER the id shape or the title marker, so a gate is still
    recognised if one of the two is renamed.
    """
    return _has_gate_id(task_id) or GATE_TITLE_MARKER in (title or "")


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
