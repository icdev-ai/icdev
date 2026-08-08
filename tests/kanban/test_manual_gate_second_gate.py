#!/usr/bin/env python3
# CUI // SP-CTI
"""Regression — a card's SECOND gate must be recognised as a manual gate.

``is_manual_gate`` matched the literal id suffix ``-gate-00``. That is fine for
a card with one gate, but HGX seeded two: ``hgx-gate-00`` for the card as a
whole, and ``hgx-gate-01`` for six slices that have an autonomous agent edit its
own guardrails (``.claude/hooks/pre_tool_use.py``) or its own dispatch path.

``hgx-gate-01`` was invisible to every exemption that keys off the predicate, so
on 2026-08-08 the dispatcher promoted it, the stale-reaper bounced it back to
backlog after 112 minutes of silent dispatch, and the dispatcher promoted it
again — a churn loop with no terminating condition. Each of those dispatches
handed a session the boilerplate "POST {status: done}" closing steps, and
completing the gate releases all six self-modification slices into a pipeline
where ``pr_watcher`` auto-merges green ``kanban/*`` branches unattended.

The predicate now matches ``<prefix>-gate-<digits>``.
"""

from __future__ import annotations

import pytest

from tools.kanban.gates import GATE_TITLE_MARKER, is_manual_gate


@pytest.mark.parametrize(
    "task_id",
    [
        "prem-gate-00",
        "hgx-gate-00",
        "hgx-gate-01",
        "hgx-gate-02",
        "aadc-enh-gate-07",
        "x-gate-100",
    ],
)
def test_numeric_gate_suffixes_are_gates(task_id):
    assert is_manual_gate(task_id, None) is True


@pytest.mark.parametrize(
    "task_id",
    [
        "hgx-guard-01",
        "hgx-exec-03",
        "hgx-gate",
        "hgx-gate-",
        "hgx-gate-01a",
        "-gate-01",
        "",
        None,
    ],
)
def test_non_gate_ids_are_not_gates(task_id):
    assert is_manual_gate(task_id, None) is False


def test_title_marker_still_matches_without_a_gate_id():
    assert is_manual_gate("some-task-07", f"{GATE_TITLE_MARKER} - held") is True


def test_orphan_sweep_skips_a_second_gate_parent():
    """The orphan-done sweep must not roll back work gated behind ``-gate-01``.

    The sweep used to exclude gates with ``p.id NOT LIKE '%-gate-00'`` in raw
    SQL, bypassing the predicate entirely. A dependent of ``hgx-gate-01`` that
    legitimately reached ``done`` would have been rolled back to ``backlog`` on
    every scheduler cycle, because its parent gate is held ``in_progress``
    forever by design.
    """
    rows = [
        {"id": "hgx-guard-01", "parent_id": "hgx-gate-01",
         "parent_status": "in_progress", "parent_title": "HGX SELF-MODIFICATION HOLD"},
        {"id": "hgx-doc-02", "parent_id": "hgx-doc-01",
         "parent_status": "backlog", "parent_title": "Real unfinished parent"},
    ]
    kept = [r for r in rows
            if not is_manual_gate(r["parent_id"], r["parent_title"])]
    assert [r["id"] for r in kept] == ["hgx-doc-02"]
