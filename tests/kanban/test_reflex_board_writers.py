#!/usr/bin/env python3
"""The autonomous board writers seed through the canonical seeder. CUI // SP-CTI

rem-hyg-06, batch 1. Six raw ``INSERT INTO kanban_tasks`` sites in the Genesis
reflex tree — the ``scheduled_at`` cohort — now call
``tools.kanban.task_factory.create_tasks``. These are the writes nobody is at a
keyboard for, so a value the board rejects has nothing to surface it.

Three of the six wrote ``task_type='bug'``. There is no ``bug``:
``kanban_tasks_task_type_check`` allows exactly build/run/fix/research/deploy/
test/chore, so on PostgreSQL — the primary backend — every one of those INSERTs
raised the moment the reflex actually had something to file, and the live board
carries 0 ``bug`` rows against 3,273 tasks. SQLite does not enforce CHECK
constraints, which is why no test ever caught it. ``create_tasks`` validates in
Python, so the one backend that would not tell you no longer has to.

The seeder had to grow ``scheduled_at`` to take these callers at all: that
column is what makes a row dispatchable without waiting for
``promote_backlog_to_scheduled``, so dropping it on the way in would have
silently parked every converted card in backlog — a lossy migration that looks
exactly like a clean one.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.genesis.reflexes import coherence_to_kanban_reflex as coh  # noqa: E402
from tools.genesis.reflexes import e2e_runner as e2e  # noqa: E402
from tools.genesis.reflexes import qa_agent_reflex as qa  # noqa: E402
from tools.genesis.reflexes import route_perf_reflex as perf  # noqa: E402
from tools.kanban import task_factory  # noqa: E402
from tools.kanban.task_factory import VALID_TASK_TYPES  # noqa: E402


#: (module, callable name, kwargs) for every writer converted in batch 1.
#: The kwargs are the shape each helper is called with by its own ``run()``.
_CONVERTED = [
    (coh, "_insert_coherence_task", {
        "check_id": "canvas_rls_bypass",
        "violations": ["tools/x/db/init_db.py"],
        "check_message": "1 canvas bypasses get_canvas_connection",
    }),
    (qa, "_insert_sweep_task", {}),
    (qa, "_insert_gap_task", {
        "canvas_key": "zta_canvas", "display_name": "ZTA", "route": "/security/zta",
    }),
    (qa, "_insert_smoke_bug_task", {
        "route": "/dic", "status": 500, "error": "UndefinedColumn",
    }),
    (perf, "_insert_perf_task", {
        "route": "/dic", "baseline_ms": 120, "current_ms": 900, "ratio": 7.5,
    }),
    (e2e, "_create_run_task", {}),
]

#: The three that carried a task_type the board rejects.
_WAS_BUG_TYPED = [
    (coh, "_insert_coherence_task"),
    (qa, "_insert_smoke_bug_task"),
    (perf, "_insert_perf_task"),
]

_MODULES = [coh, qa, perf, e2e]


def _short(module) -> str:
    return module.__name__.rsplit(".", 1)[-1]


_CONVERTED_IDS = [_short(m) + "." + f for m, f, _ in _CONVERTED]
_BUG_IDS = [_short(m) + "." + f for m, f in _WAS_BUG_TYPED]


def _seed(monkeypatch, module, fn_name, kwargs) -> list:
    """Call the helper with the seeder stubbed; return the specs it built."""
    captured: list = []

    def _fake_create_tasks(specs):
        captured.extend(specs)
        return [s["id"] for s in specs]

    monkeypatch.setattr(module, "create_tasks", _fake_create_tasks)
    getattr(module, fn_name)(**kwargs)
    return captured


# --------------------------------------------------------------------------- #
# the conversion itself
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("module,fn_name,kwargs", _CONVERTED, ids=_CONVERTED_IDS)
def test_converted_writer_seeds_through_the_canonical_seeder(
    monkeypatch, module, fn_name, kwargs
):
    specs = _seed(monkeypatch, module, fn_name, kwargs)
    assert len(specs) == 1, f"{fn_name} should seed exactly one card, got {len(specs)}"
    assert specs[0].get("id"), "create_tasks skips a spec with no id"


@pytest.mark.parametrize("module,fn_name,kwargs", _CONVERTED, ids=_CONVERTED_IDS)
def test_converted_writer_uses_a_task_type_the_board_allows(
    monkeypatch, module, fn_name, kwargs
):
    """The defect this batch exists to surface.

    SQLite does not enforce the CHECK constraint, so this cannot be caught by
    writing a row and reading it back on the test backend — it has to be
    asserted against the allowed set directly.
    """
    spec = _seed(monkeypatch, module, fn_name, kwargs)[0]
    task_type = spec.get("task_type", "build")
    assert task_type in VALID_TASK_TYPES, (
        f"{module.__name__}.{fn_name} seeds task_type={task_type!r}, which "
        f"kanban_tasks_task_type_check rejects; allowed: {sorted(VALID_TASK_TYPES)}"
    )


@pytest.mark.parametrize("module,fn_name", _WAS_BUG_TYPED, ids=_BUG_IDS)
def test_the_bug_typed_writers_now_seed_fix(monkeypatch, module, fn_name):
    kwargs = next(k for m, f, k in _CONVERTED if (m, f) == (module, fn_name))
    spec = _seed(monkeypatch, module, fn_name, kwargs)[0]
    assert spec["task_type"] == "fix", (
        "'bug' is not a task type — the constraint spells the repair 'fix'"
    )


@pytest.mark.parametrize("module,fn_name,kwargs", _CONVERTED, ids=_CONVERTED_IDS)
def test_converted_writer_keeps_its_scheduled_at(monkeypatch, module, fn_name, kwargs):
    """Every writer in this cohort set scheduled_at, and losing it is invisible.

    A card seeded with status='backlog' and no scheduled_at waits on
    promote_backlog_to_scheduled instead of being dispatchable now. Nothing goes
    red when that happens; the card simply takes longer, forever.
    """
    spec = _seed(monkeypatch, module, fn_name, kwargs)[0]
    assert spec.get("scheduled_at"), f"{fn_name} dropped scheduled_at in the conversion"


@pytest.mark.parametrize("module", _MODULES, ids=[_short(m) for m in _MODULES])
def test_converted_module_carries_no_raw_board_insert(module):
    """Both copies — the tools/ tree and the icdev/ package mirror."""
    rel = pathlib.Path(module.__file__).resolve().relative_to(_ROOT)
    for path in (_ROOT / rel, _ROOT / "icdev" / rel):
        assert path.exists(), f"missing mirror: {path}"
        body = path.read_text(encoding="utf-8")
        # The docstrings name the pattern; only executable SQL counts.
        offenders = [
            ln for ln in body.splitlines()
            if "INSERT INTO kanban_tasks" in ln and "``" not in ln
        ]
        assert not offenders, f"{path} still writes the board raw: {offenders}"


# --------------------------------------------------------------------------- #
# the seeder change that made the conversion possible
# --------------------------------------------------------------------------- #

@pytest.fixture
def _isolated_board(icdev_db, monkeypatch):
    """Point ``get_connection()`` at the per-test board conftest builds.

    The two tests below said in their own docstring that they ran "against the
    isolated SQLite board conftest provides" and NEVER REQUESTED IT. Nothing
    redirected ``get_connection()``, so both seeded through the canonical
    seeder into the AMBIENT ``data/icdev.db`` — the live board — and then ran
    ``DELETE FROM kanban_tasks`` against it in their teardown. A skipped
    teardown (``-x``, an interrupt) stranded the rows on the real board.

    That is also why they were RED: the ambient board is an old table, and
    ``CREATE TABLE IF NOT EXISTS`` never alters one, so it never gained
    ``loop_type`` / ``adversarial_enabled`` and the seeder's INSERT — which
    names both — died there. The failure was reported as a missing column when
    the defect was a test writing somewhere it never meant to.

    ``get_connection()`` resolves ``ICDEV_DB_PATH`` at CALL time, so this one
    redirect covers the seeder's own connection as well as the assertions'
    (opx-kan-02, the pattern tests/kanban/test_done_verification.py uses).
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    return icdev_db


def test_create_tasks_persists_scheduled_at(_isolated_board):
    """Round-trip against the isolated SQLite board conftest provides."""
    from tools.db.storage import get_connection

    task_id = "remhyg06-sched-roundtrip"
    stamp = "2026-08-17T12:00:00+00:00"
    task_factory.create_tasks([{
        "id": task_id, "title": "scheduled_at round-trip",
        "description": "rem-hyg-06", "task_type": "chore",
        "status": "backlog", "scheduled_at": stamp,
        "dispatch_source": "test_reflex_board_writers",
    }])
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT scheduled_at FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
        assert row is not None, "the seeder reported success and wrote nothing"
        assert dict(row)["scheduled_at"] == stamp
    finally:
        conn.execute("DELETE FROM kanban_tasks WHERE id = %s", (task_id,))
        conn.commit()
        conn.close()


def test_create_tasks_leaves_scheduled_at_null_when_unset(_isolated_board):
    """The new field must not invent a schedule for the callers that had none."""
    from tools.db.storage import get_connection

    task_id = "remhyg06-sched-absent"
    task_factory.create_tasks([{
        "id": task_id, "title": "no scheduled_at",
        "description": "rem-hyg-06", "task_type": "chore",
        "dispatch_source": "test_reflex_board_writers",
    }])
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT scheduled_at FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
        assert row is not None
        assert dict(row)["scheduled_at"] is None
    finally:
        conn.execute("DELETE FROM kanban_tasks WHERE id = %s", (task_id,))
        conn.commit()
        conn.close()
