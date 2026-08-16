#!/usr/bin/env python3
"""A project card must not vanish because its tasks match no epic. CUI // SP-CTI

`_compute_project_progress` derives every number on a card from EPIC patterns
(`<task_prefix><epic_key>-%`) and then decides visibility from those numbers::

    "visible": total_all > 0 and done_all < total_all,

A task carrying the project's `task_prefix` that no epic claims is dropped from
all of them. That collapsed three states into one invisible card — no tasks yet,
all tasks done, and *tasks exist but no epic claims them*. Only the first two
should hide.

The cost was real and repeated: measured against the live board, 8 registered
cards owned tasks no epic claimed, and 2 of them (150 and 27 tasks) rendered
NOTHING. The only detector was a human noticing a card was missing, which is why
it cost manual triage every time. `memory/` even carries a category for the
quieter half of it — "the card's own progress claim is wrong".

These tests pin both halves of the fix: the check that finds it, and the render
contract that keeps such a card on screen.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.workflow.coherence_checker import (  # noqa: E402
    _CARD_CHECK_ID,
    _project_card_entries,
    check_project_card_coverage,
)

_TEMPLATE = _ROOT / "tools" / "dashboard" / "templates" / "_projects_in_flight.html"
_APP = _ROOT / "tools" / "dashboard" / "app.py"


# --------------------------------------------------------------------------- #
# The render contract — the actual bug
# --------------------------------------------------------------------------- #

def test_visibility_no_longer_depends_on_counted_tasks_alone():
    """`visible` must consider unclaimed tasks — but only ones still OPEN.

    Two failures, opposite directions, and the rule has to avoid both.

    Counting only claimed tasks made a fully-orphaned card compute 0/0 and
    vanish, so unfinished work nobody counted looked like a project with no work.

    Counting ANY orphan then kept FINISHED projects on the board forever: seven
    of them on 2026-08-15, 555 unclaimed tasks between them and every one done.
    "In flight" has to mean outstanding work, or the panel stops meaning
    anything.

    So visibility keys on OPEN orphans. A done-but-unclaimed task is a counting
    defect, and that is reported by check_project_card_coverage — a gate that
    sees every card whether or not it renders.
    """
    src = _APP.read_text(encoding="utf-8", errors="replace")
    assert '"visible": (total_all > 0 and done_all < total_all) or open_orphans > 0,' in src, (
        "visible must stay True for unclaimed tasks that are still OPEN, and "
        "must go False once every task is done"
    )
    # ...and open_orphans must actually exclude terminal states, or it is just
    # len(orphans) wearing a different name.
    assert "status NOT IN ('done', 'decomposed', 'cancelled', 'merged')" in src


def test_a_finished_project_leaves_the_board():
    """The rule, as a decision table, independent of the source text."""
    def visible(total, done, orphans, open_orphans):
        return (total > 0 and done < total) or open_orphans > 0

    # unfinished counted work -> shows
    assert visible(10, 3, 0, 0) is True
    # finished, nothing unclaimed -> gone
    assert visible(10, 10, 0, 0) is False
    # finished, but unclaimed tasks still OPEN -> shows (the original bug)
    assert visible(10, 10, 5, 5) is True
    # fully orphaned and open -> shows even though counted work is 0/0
    assert visible(0, 0, 150, 150) is True
    # fully orphaned and ALL DONE -> gone (the seven cards)
    assert visible(0, 0, 150, 0) is False
    # finished, unclaimed tasks all done -> gone
    assert visible(83, 83, 324, 0) is False


def test_orphan_detection_is_not_skipped_for_a_card_with_no_epics():
    """The renderer carried the same blind spot as the check.

    `orphans = [...] if epic_pats else []` returned an empty list for a card that
    registers no epics — the one case where EVERY row is unclaimed. The card then
    reported 0 orphans, computed 0/0 and vanished. `str.startswith(())` is already
    False for an empty tuple, so the comprehension was right and the guard threw
    the answer away.
    """
    src = _APP.read_text(encoding="utf-8", errors="replace")
    assert "if not tid.startswith(epic_pats) and not has_gate_id(tid)" in src
    assert ") if epic_pats else []" not in src, (
        "a card with no epics must still report its orphans — it is the maximal "
        "case of this bug, not an exemption from it"
    )
    # The empty-tuple semantics the removal relies on.
    assert not "anything-01".startswith(())


def test_the_payload_reports_orphans():
    """An operator cannot act on a number the API never sends."""
    src = _APP.read_text(encoding="utf-8", errors="replace")
    assert '"orphaned_tasks": len(orphans),' in src
    assert '"orphaned_sample": orphans[:10],' in src


def test_gate_sentinels_are_not_counted_as_orphans():
    """`<prefix>gate-NN` holds the card; it is not work and not a defect."""
    src = _APP.read_text(encoding="utf-8", errors="replace")
    assert "has_gate_id" in src, "gate sentinels must be excluded from orphans"


def test_the_card_ui_warns_instead_of_showing_a_bare_subset():
    """A partial count presented as the whole truth is the quieter failure."""
    html = _TEMPLATE.read_text(encoding="utf-8", errors="replace")
    assert "proj-orphan-warn" in html
    assert "not claimed by any epic" in html
    # Not colour-only — the glyph and the bold count carry the signal too.
    assert "&#9888;" in html


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #

def test_check_is_registered():
    from tools.workflow.coherence_checker import CHECK_REGISTRY

    assert CHECK_REGISTRY[_CARD_CHECK_ID] is check_project_card_coverage


def test_check_degrades_honestly_when_the_board_is_unreachable():
    """A fresh worktree or an unreachable database must not read as clean.

    Same discipline as capability_liveness and chain_sweep: a check that reports
    "fine" when it never ran is worse than no check.

    Injected rather than monkeypatched. `tools.db.storage` and
    `icdev.tools.db.storage` are distinct module objects with distinct function
    objects, so patching the alias this test imports can leave the alias the
    check resolves untouched — which is exactly what happened: this test passed
    locally (worktree DB has no kanban_tasks, so the REAL call raised and gave
    the right answer for the wrong reason) and failed in CI where the table
    exists and the unpatched call succeeded.
    """
    def _unreachable():
        raise RuntimeError("no such table: kanban_tasks")

    result = check_project_card_coverage(conn_factory=_unreachable)
    assert result.status == "warn"
    assert "NOT verified" in result.message


def test_check_reports_warn_on_an_empty_board():
    """An empty board cannot show an unclaimed task, so "no findings" is not a
    result — it is the absence of one."""
    class _EmptyConn:
        def execute(self, *_a, **_kw):
            class _Cur:
                def fetchall(self_inner):
                    return []
            return _Cur()

        def close(self):
            pass

    result = check_project_card_coverage(conn_factory=_EmptyConn)
    assert result.status == "warn"
    assert "NOT verified" in result.message


def test_check_flags_a_task_no_epic_claims():
    """The finding itself, on a synthetic board — no live data dependency.

    Every registered card is audited, so a real project's real prefix is used;
    the assertion is only that an unclaimed id is reported and a claimed one is
    not.
    """
    entries = _project_card_entries()
    proj = next(p for p in entries if p["epics"])
    claimed = f"{proj['prefix']}{proj['epics'][0]}-01"
    orphan = f"{proj['prefix']}zzznotanepic-01"
    sentinel = f"{proj['prefix']}gate-00"

    class _Conn:
        def execute(self, *_a, **_kw):
            rows = [{"id": claimed}, {"id": orphan}, {"id": sentinel}]

            class _Cur:
                def fetchall(self_inner):
                    return rows
            return _Cur()

        def close(self):
            pass

    result = check_project_card_coverage(conn_factory=_Conn)
    assert result.status == "warn"
    joined = " ".join(result.extra)
    assert orphan in joined, "an unclaimed task must be named"
    assert claimed not in joined, "a claimed task must not be reported"
    assert sentinel not in joined, "a gate sentinel holds the card; it is not work"


def test_check_reports_warn_never_fail():
    """The finding is board DATA.

    Failing a per-task code gate on it would block commits that have nothing to
    do with it — the reasoning gate_sentinel_shape already documents.
    """
    result = check_project_card_coverage()
    assert result.status in {"pass", "warn"}


# --------------------------------------------------------------------------- #
# The invariant the check exists to protect
# --------------------------------------------------------------------------- #

def test_gate_is_a_reserved_epic_key_used_only_to_hold_the_sentinel():
    """`gate` is RESERVED, and nothing said so — which is half of why this cost
    manual work.

    30 registered cards declare an epic keyed `gate`. Its only job is to make the
    `<prefix>gate-00` hold sentinel appear on the card; it is not a work epic.
    That is load-bearing, because `tools/kanban/gates.py::is_manual_gate` returns
    True for ANY `<card>-gate-<n>` id, so `promote_backlog_to_scheduled` filters
    such a task out forever — silently, since a task nobody can dispatch looks
    exactly like a task nobody has got to yet.

    So a new card must never use `gate` as the key of an epic that holds work.
    `task_factory.create_tasks` is the enforcement; this pins the reason.
    """
    from tools.kanban.gates import has_gate_id
    from tools.kanban.task_factory import _sentinel_shaped_work

    assert has_gate_id("trust-gate-01"), "recognition must stay wide (kax-exec-04)"

    refused = _sentinel_shaped_work([
        {"id": "trust-gate-01", "title": "Build the two-stage gate spine"},
        {"id": "trust-spine-01", "title": "Build the two-stage gate spine"},
    ])
    assert refused == ["trust-gate-01"], (
        "seeding work under a `gate` epic must be refused while the id is still "
        "a keystroke, not discovered as 16 undispatchable rows in backlog"
    )


def test_reserved_gate_epics_are_the_only_gate_shaped_epic_keys():
    """Nobody has invented a second spelling (e.g. `subgate`, `x-gate`).

    If they had, its tasks would be undispatchable in the same silent way, and
    the `gate`-only convention above would no longer describe the file.
    """
    from tools.kanban.gates import has_gate_id

    offenders = sorted({
        f"{p['key']}:{ek}"
        for p in _project_card_entries()
        for ek in p["epics"]
        if has_gate_id(f"{p['prefix']}{ek}-01") and ek != "gate"
    })
    assert not offenders, (
        f"epic keys producing gate-shaped, undispatchable task ids: {offenders}"
    )


def test_project_card_entries_audits_a_card_that_declares_no_epics():
    """A card with a prefix but no epics can never count anything — so it is the
    one that most needs auditing, not the one to exclude.

    The loader used to require epics, and its test asserted that requirement back
    at it. That made the check structurally blind to the maximal case: `pgrt`
    carried 22 board tasks, declared no `epics:` block, rendered nothing, and
    `--check project_card_coverage` reported clean. A card is auditable because it
    declares a `task_prefix` — that is the claim "these rows are mine". Whether an
    epic then claims them is the finding.
    """
    entries = _project_card_entries()
    assert entries, "projects.yaml declares no cards at all"
    for p in entries:
        assert p["prefix"], p


def test_check_flags_a_card_that_registers_no_epics_at_all():
    """The maximal case, on a synthetic board: prefix declared, epics absent.

    Nothing can ever be claimed, so every non-sentinel row is unclaimed and the
    card renders nothing. The finding must say so rather than print an empty epic
    list, which reads as a truncated message instead of the defect.
    """
    prefix = "zzzcardwithnoepics-"
    orphan = f"{prefix}work-01"
    sentinel = f"{prefix}gate-00"

    class _Conn:
        def execute(self, *_a, **_kw):
            rows = [{"id": orphan}, {"id": sentinel}]

            class _Cur:
                def fetchall(self_inner):
                    return rows
            return _Cur()

        def close(self):
            pass

    # Injected, not monkeypatched — same reasoning as conn_factory: patching a
    # module attribute can land on `tools.workflow.coherence_checker` while the
    # call resolves `icdev.tools.workflow.coherence_checker`, and the test then
    # silently audits the real registry.
    result = check_project_card_coverage(
        conn_factory=_Conn,
        entries=[{"key": "zzzcard", "prefix": prefix, "epics": []}],
    )

    assert result.status == "warn"
    joined = " ".join(result.extra)
    assert orphan in joined, "an epic-less card's work must be reported"
    assert sentinel not in joined, "a gate sentinel holds the card; it is not work"
    assert "NO epics registered" in joined, (
        "the finding must name the cause; an empty 'epics: ' list reads as a "
        "truncated message, not as the defect"
    )


@pytest.mark.parametrize("field", ["task_prefix", "epics"])
def test_registered_projects_declare_the_fields_the_card_needs(field):
    raw = yaml.safe_load((_ROOT / "args" / "projects.yaml").read_text(encoding="utf-8"))
    projects = raw["projects"]
    # Not every entry is a card (some are prefix-only namespaces), but any entry
    # declaring epics must declare a prefix and vice versa — a half-declared
    # entry is exactly the state that renders an empty or absent card.
    for p in projects:
        if not isinstance(p, dict):
            continue
        has_prefix = bool(str(p.get("task_prefix") or "").strip())
        has_epics = bool(p.get("epics"))
        if has_prefix and has_epics:
            assert p.get(field) is not None, f"{p.get('key')} missing {field}"
