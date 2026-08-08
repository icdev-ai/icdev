# CUI // SP-CTI
"""A card's SECOND gate must be recognised as a gate (hgx-gate-01).

``is_manual_gate`` matched on the literal suffix ``-gate-00``. Every gate on the
board was a card's first, so the predicate looked correct for fifteen of them and
was wrong for the sixteenth.

``hgx-gate-01`` was that one. It held six tasks that have an autonomous agent edit
its own guardrails: ``.claude/hooks/pre_tool_use.py`` (the hook gating every tool
call, including the runner's own), which tools are eligible for parallel dispatch,
the kanban dispatcher itself, and the genesis daemon reflex dispatch. Because the
predicate returned False, the gate was not a sentinel to any of the machinery that
consults it — ``promote_backlog_to_scheduled`` promoted it, the scheduler dispatched
it to an unattended session, and the dispatch prompt told that session to mark the
gate done. Doing so releases all six, and ``pr_watcher`` auto-merges green
``kanban/*`` branches, so the safety hook could have been rewritten and merged with
nobody watching.

The gate held anyway — but only because its dependents' ``depends_on_task_id``
pointed at it and it happened to still be ``in_progress``. It could not protect
itself, which is the part these tests pin.
"""
from __future__ import annotations

import pytest

from tools.kanban.gates import GATE_TITLE_MARKER, is_manual_gate


class TestASecondGateIsStillAGate:
    def test_the_gate_that_was_dispatched(self):
        """The concrete regression: hgx-gate-01, title carries no marker."""
        assert is_manual_gate(
            "hgx-gate-01", "HGX SELF-MODIFICATION HOLD - human-driven slices only"
        ), (
            "a card's second gate was not recognised, so the scheduler dispatched the "
            "gate itself and asked it to mark itself done — releasing six tasks that "
            "edit the runner's own safety hook and dispatcher"
        )

    @pytest.mark.parametrize("task_id", ["hgx-gate-00", "hgx-gate-01", "hgx-gate-02",
                                         "prem-gate-00", "idp-gate-12", "x-gate-9"])
    def test_any_gate_number_is_a_gate(self, task_id):
        assert is_manual_gate(task_id, "no marker in this title")

    def test_the_first_gate_still_matches(self):
        """The existing convention must keep working unchanged."""
        assert is_manual_gate("prem-gate-00", None)

    def test_the_title_marker_still_matches(self):
        assert is_manual_gate("some-other-id", f"{GATE_TITLE_MARKER} - held")


class TestOrdinaryWorkIsNotSweptUp:
    """Widening recognition holds MORE work, so the boundary has to be exact —
    a false positive here silently parks a real task forever."""

    @pytest.mark.parametrize("task_id", [
        "hgx-guard-01",        # the tasks the gate holds are not themselves gates
        "hgx-exec-03",
        "sme-gate-review",     # gate-shaped word, not a number
        "kpr-gateway-01",      # 'gate' as a substring of a real word
        "-gate-01",            # no card prefix
        "gate-01",
        "hgx-gate-",
        "hgx-gate-01a",
        "",
        None,
    ])
    def test_not_a_gate(self, task_id):
        assert not is_manual_gate(task_id, "Ordinary task")

    def test_a_task_merely_mentioning_a_gate_is_not_one(self):
        assert not is_manual_gate("hgx-obs-02", "Rework dispatch behind the gate-00 hold")


class TestSeedingConventionIsUnchanged:
    def test_gap_seeder_constant_still_names_the_first_gate(self):
        """`gap_seeder` validates seeded ids against this exact string. Recognition
        got wider; what a seeder should WRITE did not."""
        from tools.kanban.gates import GATE_ID_SUFFIX

        assert GATE_ID_SUFFIX == "-gate-00"
