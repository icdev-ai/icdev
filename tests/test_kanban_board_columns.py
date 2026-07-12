# CUI // SP-CTI
"""The kanban board must only ever show columns a task can actually reach.

A 'Triage' column shipped on both boards for a status that exists nowhere — not in
KanbanState, not in the kanban_tasks CHECK constraint. It could never populate, and
worse, the ◀ ▶ arrows routed through it (scheduled -> triage -> in_progress), so
every move on a scheduled or in-progress task POSTed a status the DB rejects and the
card silently snapped back.

Nothing caught that, because nothing checked the UI's vocabulary against the schema's.
These tests do.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.kanban.state_machine import KanbanState

_ROOT = Path(__file__).resolve().parents[1]
_HOME = _ROOT / "tools" / "dashboard" / "templates" / "index.html"
_KANBAN = _ROOT / "tools" / "dashboard" / "templates" / "kanban.html"
_JS = _ROOT / "tools" / "dashboard" / "static" / "js" / "task_pipeline.js"

#: Statuses kanban_tasks.status actually permits (the PG CHECK constraint).
#: KanbanState plus the two decomposer/pipeline states that live only in the DB.
PERSISTABLE = {s.value for s in KanbanState} | {"validating", "needs_decomposition"}

#: Columns that are NOT a stored status but a DERIVED bucket the JS folds several
#: real statuses into. A derived column is legitimate — it just must never be a
#: move TARGET, because there is no such status to write.
DERIVED_COLUMNS = {"blocked"}


def _columns(template: Path) -> list[str]:
    block = re.search(r"\{% set columns = \[(.*?)\] %\}",
                      template.read_text(encoding="utf-8"), re.S)
    assert block, f"no columns block in {template.name}"
    return [m[0] for m in re.findall(r'\("([a-z_]+)",\s*"([^"]+)"\)', block.group(1))]


def _js_map(name: str) -> dict[str, str]:
    block = re.search(rf"var {name} = \{{(.*?)\}};", _JS.read_text(encoding="utf-8"), re.S)
    assert block, f"no {name} in task_pipeline.js"
    return dict(re.findall(r"(\w+): '(\w+)'", block.group(1)))


def test_every_board_column_is_reachable():
    """A column is either a real persistable status or a documented derived bucket.
    Anything else is a column no task can ever appear in."""
    for template in (_HOME, _KANBAN):
        for column in _columns(template):
            assert column in PERSISTABLE or column in DERIVED_COLUMNS, (
                f"{template.name}: column '{column}' is neither a persistable status "
                f"nor a declared derived bucket — no task can ever reach it"
            )


def test_home_and_kanban_boards_are_aligned():
    """The two boards render the same lifecycle. They drifted before."""
    assert _columns(_HOME) == _columns(_KANBAN)


def test_js_buckets_match_the_rendered_columns():
    """A bucket with no column silently drops tasks; a column with no bucket is
    permanently empty."""
    block = re.search(r"var columns = \{(.*?)\};", _JS.read_text(encoding="utf-8"), re.S)
    buckets = re.findall(r"(\w+):\s*\[\]", block.group(1))
    assert buckets == _columns(_HOME)


@pytest.mark.parametrize("chain", ["nextStatus", "prevStatus"])
def test_move_arrows_never_target_a_status_the_db_rejects(chain):
    """This is the bug that shipped: the arrows POSTed 'triage', the CHECK constraint
    rejected it, and the card just snapped back with no error surfaced to the user."""
    for source, target in _js_map(chain).items():
        assert target in PERSISTABLE, (
            f"{chain}: '{source}' -> '{target}' would be rejected by the "
            f"kanban_tasks status CHECK"
        )


@pytest.mark.parametrize("chain", ["nextStatus", "prevStatus"])
def test_move_arrows_never_target_a_derived_bucket(chain):
    """'blocked' is a display aggregate, not a status. Moving INTO it is not a thing."""
    for source, target in _js_map(chain).items():
        assert target not in DERIVED_COLUMNS, (
            f"{chain}: '{source}' -> '{target}' targets a derived bucket, which is "
            f"not a storable status"
        )


def test_status_dropdowns_only_offer_persistable_statuses():
    """The /kanban modal offered <option value='triage'>, which the DB rejects on save."""
    for template in (_HOME, _KANBAN):
        html = template.read_text(encoding="utf-8")
        select = re.search(r'<select[^>]*id="task-status".*?</select>', html, re.S)
        if not select:
            continue
        for value in re.findall(r'<option value="([a-z_]*)"', select.group(0)):
            if not value:
                continue
            assert value in PERSISTABLE, (
                f"{template.name}: status dropdown offers '{value}', which the "
                f"kanban_tasks CHECK constraint rejects"
            )
