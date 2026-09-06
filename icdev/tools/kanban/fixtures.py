#!/usr/bin/env python3
# CUI // SP-CTI
"""Test-fixture card predicate — the single source of truth.

A fixture card is a board row an E2E spec creates, drives, and deletes again. It
is NOT work, and it must never be promoted or dispatched.

`tests/e2e/kanban_pipeline.spec.ts` and `kanban_api.spec.ts` POST a task to the
REAL board on purpose: a pipeline proof against a throwaway table would prove
nothing about the pipeline. They delete it in `afterAll`, and that cleanup
works. It is not enough, because the row is visible to the scheduler for as long
as the spec runs, and the scheduler polls faster than the spec finishes.

MEASURED on the live board 2026-09-06, from `kanban_status_transitions`:

    11:37:41  the nightly [AUTO-RUN] Playwright suite starts
    11:59:22  kanban_pipeline.spec POSTs `task-444e9c3f6c` as `backlog`
    11:59:24  the scheduler has ALREADY promoted it: `scheduled -> in_progress`
    11:59:38  scheduler: "dispatched: agent subprocess launched"
    (later)   the spec's afterAll DELETEs the card, successfully

The window was ~2 seconds. A sibling fixture (`task-e69f3b0e42`) was dispatched
12 seconds earlier in the same run, and the scheduler recorded its outcome as
"No git commits found on task branch - agent produced no committed file-level
output" — a whole worker session spent on a row that no longer existed.
`task-7054abe88d` (2026-08-28) was parked `token_exhausted, retry 2/60` against
a deleted row. Of 619 distinct dispatched task ids, 4 went to a card that no
longer exists (0.65%), and 2 of the 4 are from that single run.

So the SPEC cannot close this — only the board can, the same way it already
declines to dispatch a manual gate (`tools/kanban/gates.py`).

SURVEYED BEFORE ARMING, as CLAUDE.md requires: over all 3,950 lifetime board
rows, ZERO carry the `[E2E ` prefix, so this predicate refuses nothing that has
ever been real work. Re-derive with:

    SELECT COUNT(*) FROM kanban_tasks WHERE title LIKE '[E2E %';

Recognition is a TITLE PREFIX and deliberately not a substring search. Real
cards mention e2e and Playwright constantly — "[AUTO-RUN] Playwright E2E Suite",
"Survey whether E2E (Playwright) can become a required check" — and every one of
them is work that must stay dispatchable. It is keyed on the title rather than
the id because the id is server-generated and opaque (`task-<hex>`), so a spec
cannot choose one that says what the row is.

This module imports NOTHING, so any caller — CLI, scheduler, reflex, Flask
route — can use it without dragging in a dependency tree. Keep it import-free.
"""

from __future__ import annotations

#: The prefix an E2E spec puts on a board row it intends to delete again.
#:
#: A spec that seeds a card MUST use it. There is no second convention and no
#: allowlist of spec names: an allowlist is a claim a reviewer has to check,
#: while a prefix is one the predicate re-derives from the row itself.
FIXTURE_TITLE_PREFIX = "[E2E "


def is_test_fixture(task_id: str | None, title: str | None) -> bool:
    """True when this board row is an E2E spec's throwaway fixture.

    Args:
        task_id: the row's id. Accepted for call-site symmetry with
            ``gates.is_manual_gate`` and deliberately NOT consulted — the id is
            server-generated and opaque, so it carries no evidence either way.
        title: the row's title. ``None`` or empty is NOT a fixture: an
            unreadable title is an unknown, and freezing a card we cannot
            identify is a worse failure than dispatching one we can.

    Returns:
        bool: True only on a positive title match.
    """
    if not title:
        return False
    return title.startswith(FIXTURE_TITLE_PREFIX)
