# CUI // SP-CTI
"""Does this task id belong to a card, and does an epic claim it? (rem-hyg-02)

THE QUESTION NOTHING ASKS AT SEED TIME
======================================
The contract is ``<task_prefix><epic_key>-<N>``. Every number on a project card
comes from EPIC patterns — ``<task_prefix><epic_key>-%`` — never from
``task_prefix`` alone, so a row no epic key matches is counted by NOTHING, and
when every row of a card is unclaimed the card vanishes entirely, which is
indistinguishable from a project with no work.

``coherence_checker.py::check_project_card_coverage`` already detects the damage
AFTER the fact, and its own docstring names the gap::

    Both are invisible at seed time, because task_factory does not consult the
    project registry.

Measured 2026-08-16, seeding the HCX card by hand: ``hcx-`` was absent from all
150+ ``task_prefix`` values in ``args/projects.yaml`` while the BOARD already
held 25 rows under it. Reading the YAML proved nothing, because the YAML is only
half the state. Nothing warned; the collision surfaced only from a manual board
query afterwards, by which time the scheduler had dispatched three of the new
tasks.

This module is the shared answer, so the seeder, the survey (rem-hyg-03), the
autonomous writers (rem-hyg-06) and the coherence check all ask it the same way
and cannot drift apart.

WHAT IT IS NOT
==============
Pure functions. No database, no Flask, no environment, no LLM, no writes — the
only I/O is reading ``args/projects.yaml``, and that is confined to
:func:`load_cards`, which fails OPEN (returns ``[]``) on anything it cannot
parse. Nothing here refuses anything: arming is rem-hyg-04, and only after
rem-hyg-03 has measured the fire rate.

THE THREE ZEROES THAT ARE NOT FINDINGS
======================================
* **an unreadable / absent registry** — every id would resolve to "no card",
  which is 3,000 fabricated findings rather than a measurement. Reported as
  :data:`REASON_NO_REGISTRY`, which is deliberately NOT in
  :data:`ACTIONABLE_REASONS`.
* **a gate sentinel** — ``<prefix>gate-<n>`` holds the card, it is not work, and
  it must never appear in a progress figure. ``gate`` is a RESERVED epic key.
* **an id that is not id-shaped** — a blank id is ``task_factory``'s existing
  skip-with-a-warning, not a card-coverage defect.

Ask :data:`ACTIONABLE_REASONS` rather than re-deriving "is this a finding?" at
each call site; a second copy of that set is how the two halves drift.

Usage::

    from tools.kanban.task_identity import check_batch, load_cards, resolve

    cards = load_cards()                    # read the registry ONCE
    resolve("rem-hyg-02", cards)            # -> {..., 'claimed': True}
    check_batch([{"id": "hcx-live-01"}])    # -> [finding]
    unregistered_prefixes(board_ids, cards) # -> {'hcx-': ['hcx-live-01', ...]}
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. parents[N] is whatever holds this file's `tools` package: the
# repo root under tools/, and <repo>/icdev in the icdev/ mirror. Resolved from
# __file__ and NEVER from os.getcwd(), because seeding happens from worktrees.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.kanban.gates import has_gate_id  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402
from tools.project.prefix_scope import child_prefixes  # noqa: E402

logger = get_logger(__name__)


def _resolve_config(name: str) -> Path:
    """Find ``args/<name>`` from either tree.

    ``icdev/args/`` carries only a fraction of ``args/``, so the mirrored copy of
    this module cannot assume its own sibling exists. Prefer the local one (a
    wheel install has only that), fall back to the parent, and name the local
    path when neither is there.
    """
    local = _REPO_ROOT / "args" / name
    if local.is_file():
        return local
    parent = _REPO_ROOT.parent / "args" / name
    return parent if parent.is_file() else local


PROJECTS_YAML = _resolve_config("projects.yaml")

#: An epic claims ``<prefix><epic_key>-<N>``; this is the separator in the middle.
EPIC_SEPARATOR = "-"

#: Reserved epic key. 30 cards declare it, and its only job is to make the
#: ``<prefix>gate-<n>`` hold sentinel appear on the card. Never a work epic.
RESERVED_EPIC_KEY = "gate"

# --- resolution outcomes ----------------------------------------------------
#: An epic key claims this row. The card counts it.
REASON_CLAIMED = "claimed"
#: A manual-mode gate sentinel: holds the card, is not work, is not an orphan.
REASON_GATE = "gate_sentinel"
#: A card owns the id's prefix, but no epic key claims it — the row is counted
#: by nothing and the card's percentage silently describes a subset.
REASON_NO_EPIC = "no_epic"
#: No registered ``task_prefix`` owns this id at all — the HCX case.
REASON_NO_CARD = "no_card"
#: Blank / unusable id. ``task_factory`` already skips these with a warning.
REASON_NOT_ID_SHAPED = "not_id_shaped"
#: The registry could not be read, so NOTHING was measured. Never a finding.
REASON_NO_REGISTRY = "no_registry"

#: The reasons a consumer may act on. The other four are states in which the
#: honest answer is "not measured" or "not applicable", and folding them in is
#: how a check that cannot run comes to read as a check that found something.
ACTIONABLE_REASONS = (REASON_NO_CARD, REASON_NO_EPIC)


@dataclass(frozen=True)
class Card:
    """A project card: its id prefix and the epic keys that claim rows."""

    key: str
    prefix: str
    epics: tuple


def load_cards(path: Optional[Path] = None) -> list:
    """Read ``args/projects.yaml`` into ``[Card]`` — the registry half of the state.

    Entries without a ``task_prefix`` are skipped, matching the
    Projects-in-Flight renderer. Entries with a prefix but NO epics are kept:
    such a card claims nothing, and that is exactly the misconfiguration
    :func:`resolve` has to be able to report.

    An EXACT duplicate ``task_prefix`` is unresolvable — no predicate assigns a
    row to one card over the other — so the renderer skips the later entry and
    so does this: first wins, and the collision is logged.

    Returns ``[]`` (loudly) rather than raising when the file is absent or
    unparseable. Callers must treat an empty registry as UNMEASURABLE, not as
    "no card claims anything" — see :data:`REASON_NO_REGISTRY`.
    """
    try:
        import yaml

        src = Path(path) if path else PROJECTS_YAML
        raw = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — fail OPEN, every caller is advisory
        logger.warning(
            "task_identity: cannot read %s (%s) — no cards loaded, identity NOT verified",
            path or PROJECTS_YAML, exc,
        )
        return []

    cards: list = []
    seen: dict = {}
    for entry in raw.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        prefix = str(entry.get("task_prefix") or "").strip()
        if not prefix:
            continue
        key = str(entry.get("key") or "").strip() or prefix.rstrip("-")
        if prefix in seen:
            logger.warning(
                "task_identity: duplicate task_prefix %r (%s and %s) — keeping the "
                "first, as the card renderer does", prefix, seen[prefix], key,
            )
            continue
        epics = tuple(
            str(e.get("key") or "").strip()
            for e in (entry.get("epics") or [])
            if isinstance(e, dict) and str(e.get("key") or "").strip()
        )
        seen[prefix] = key
        cards.append(Card(key=key, prefix=prefix, epics=epics))
    return cards


def _owning_card(task_id: str, cards: Sequence) -> Optional[Card]:
    """The card whose ``task_prefix`` owns this id — longest match wins.

    ``aadc-`` alongside ``aadc-enh-`` is a legitimate parent/child namespace, so
    a row is owned by the most specific prefix that matches it. The nesting rule
    itself is not re-derived here: ``prefix_scope.child_prefixes`` is the single
    source, and it is the same function the dashboard subtracts children with.
    """
    all_prefixes = [c.prefix for c in cards if c.prefix]
    for card in cards:
        if not card.prefix or not task_id.startswith(card.prefix):
            continue
        if any(task_id.startswith(child)
               for child in child_prefixes(card.prefix, all_prefixes)):
            continue  # a nested child owns this row instead
        return card
    return None


def _matching_epic(task_id: str, card: Card) -> Optional[str]:
    """The longest epic key claiming ``task_id``, or None.

    Longest rather than first because a card whose epic keys nest under the
    ``-`` separator is a misconfiguration the registry forbids but the YAML can
    still contain; resolving to the more specific key is the reading that
    matches how a human names the epic.
    """
    matched = [
        ek for ek in card.epics
        if ek and task_id.startswith(f"{card.prefix}{ek}{EPIC_SEPARATOR}")
    ]
    return max(matched, key=len) if matched else None


def resolve(task_id: str, cards: Optional[Sequence] = None) -> dict:
    """Answer, for one task id: which card, which epic, and is it counted?

    Returns ``{card, epic, prefix, claimed, reason}`` where ``claimed`` is True
    only when a progress figure will actually include this row — an epic claims
    it, or it is a gate sentinel that deliberately stays out of the figures.

    ``claimed=False`` alone is NOT a defect: check ``reason in
    ACTIONABLE_REASONS``, because an unreadable registry and a blank id both
    land here and neither is a card-coverage problem.

    ``cards`` is the registry, loaded once by the caller. Pass it when resolving
    many ids — omitting it re-reads and re-parses ``args/projects.yaml`` per
    call, which over a 3,000-row board is thousands of parses of the same file.
    """
    tid = str(task_id or "").strip()
    blank = {"card": None, "epic": None, "prefix": None, "claimed": False}
    if not tid:
        return dict(blank, reason=REASON_NOT_ID_SHAPED)

    registry = list(cards) if cards is not None else load_cards()
    if not registry:
        # Nothing was measured. Saying "no card owns it" here would turn a
        # missing config file into a finding against every id on the board.
        return dict(blank, reason=REASON_NO_REGISTRY)

    card = _owning_card(tid, registry)
    if card is None:
        return dict(blank, reason=REASON_NO_CARD)

    base = {"card": card.key, "prefix": card.prefix}
    if has_gate_id(tid):
        # Holds the card; never counted, so never an orphan. Recognised with the
        # dispatcher's own predicate so the two cannot disagree about what a
        # gate is.
        return dict(base, epic=RESERVED_EPIC_KEY, claimed=True, reason=REASON_GATE)

    epic = _matching_epic(tid, card)
    if epic is None:
        return dict(base, epic=None, claimed=False, reason=REASON_NO_EPIC)
    return dict(base, epic=epic, claimed=True, reason=REASON_CLAIMED)


def _candidate_prefix(task_id: str) -> str:
    """The prefix an unregistered id APPEARS to want — ``hcx-live-01`` -> ``hcx-``.

    A heuristic, and named as one: with no registry entry there is nothing that
    says where the prefix ends, so this takes everything up to and including the
    first separator. It groups a batch of orphans under one heading for a human
    to read; it is never used to decide whether an id is claimed.
    """
    head, sep, _ = task_id.partition(EPIC_SEPARATOR)
    return f"{head}{sep}" if sep else task_id


def unregistered_prefixes(board_ids: Iterable, cards: Optional[Sequence] = None) -> dict:
    """``{apparent_prefix: [ids]}`` for live rows that no registered card owns.

    The half of the state that reading ``args/projects.yaml`` cannot show. A
    prefix absent from the YAML proves nothing about the BOARD, and the board is
    where the collision was found: 25 ``hcx-`` rows existed under a prefix no
    card had ever declared.

    Ids are returned sorted, and gate sentinels are INCLUDED — a gate is not an
    epic-coverage orphan, but a gate under an unregistered prefix means the
    whole card is missing, which is the finding.

    Returns ``{}`` when the registry is unreadable: with no cards every id looks
    unregistered, and that is a fabrication rather than a measurement.
    """
    registry = list(cards) if cards is not None else load_cards()
    if not registry:
        logger.warning(
            "task_identity: registry empty — unregistered_prefixes NOT verified "
            "(every id would look unowned)"
        )
        return {}

    out: dict = {}
    for raw in board_ids:
        tid = str(raw or "").strip()
        if not tid or _owning_card(tid, registry) is not None:
            continue
        out.setdefault(_candidate_prefix(tid), []).append(tid)
    return {p: sorted(ids) for p, ids in sorted(out.items())}


def suggested_id(task_id: str, card: Optional[Card] = None) -> str:
    """The id this task should have carried — ``pgrt-perf-02`` -> ``pgrt-<epic>-02``.

    Named in the report so it ends in an edit rather than a puzzle, following
    ``task_factory._work_id_suggestion``. ``<epic>`` is left for the seeder to
    fill because only it knows which epic the work belongs to.
    """
    prefix = card.prefix if card else _candidate_prefix(task_id)
    _, sep, number = task_id.rpartition(EPIC_SEPARATOR)
    tail = number if sep and number else "<N>"
    return f"{prefix}<epic>-{tail}"


def check_batch(task_specs: Iterable, cards: Optional[Sequence] = None) -> list:
    """Findings for a batch about to be seeded — the seeder-facing entry point.

    ``task_specs`` are ``create_tasks``-shaped dicts (only ``id`` is read); bare
    id strings are accepted too, for the survey.

    Returns one finding per ACTIONABLE id::

        {task_id, reason, card, prefix, epic, suggestion, detail}

    An empty list means either "everything is claimed" or "nothing could be
    measured" — the two are distinguished in the log, never by an empty return
    value, because the caller of a batch check cannot tell them apart from the
    outside. Raises nothing: every caller is advisory until rem-hyg-04.
    """
    registry = list(cards) if cards is not None else load_cards()
    if not registry:
        return []

    findings: list = []
    for spec in task_specs:
        tid = str((spec.get("id") if isinstance(spec, dict) else spec) or "").strip()
        if not tid:
            continue
        report = resolve(tid, registry)
        if report["reason"] not in ACTIONABLE_REASONS:
            continue

        owner = next((c for c in registry if c.prefix == report["prefix"]), None)
        if report["reason"] == REASON_NO_EPIC:
            epics = ", ".join(owner.epics) if owner and owner.epics else "(none declared)"
            detail = (
                f"{tid}: card {report['card']!r} owns the prefix "
                f"{report['prefix']!r} but no epic key claims the id, so no card "
                f"figure counts this row. Use {suggested_id(tid, owner)!r} — "
                f"registered epics: {epics} — or register the epic in "
                f"args/projects.yaml."
            )
        else:
            detail = (
                f"{tid}: no registered task_prefix owns this id (nearest guess "
                f"{_candidate_prefix(tid)!r}). Every card figure comes from "
                f"<task_prefix><epic_key>-%, so this row would be counted by "
                f"nothing and no card would show it. Register the card in "
                f"args/projects.yaml, or reuse an existing card's prefix."
            )
        findings.append({
            "task_id": tid,
            "reason": report["reason"],
            "card": report["card"],
            "prefix": report["prefix"],
            "epic": report["epic"],
            "suggestion": suggested_id(tid, owner),
            "detail": detail,
        })
    return findings
