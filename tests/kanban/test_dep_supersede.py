# CUI // SP-CTI
"""Seeding order must not gate dispatch as if it were a dependency (kpr-fix-02).

The board carries two dependency mechanisms. ``kanban_task_deps`` is the real
fan-in graph; ``kanban_tasks.depends_on_task_id`` is mostly the linear chain a
seeder writes as it walks its batch. Every dispatch decision used to require
BOTH, so the accidental declaration overrode the deliberate one — measured on
the live board 2026-08-18, ``cef-di-03/04/05/06`` had all three real
prerequisites done and were blocked solely by seeding order.

What is asserted here, in the order it matters:

  * junction rows supersede the scalar          — the four CEF tasks are freed
  * a scalar with NO junction rows still gates  — the ordinary chain is intact
  * a scalar pointing at a MANUAL GATE always gates, junction rows or not
  * the SQL and Python renderings of the rule agree, including the two cases
    they used to disagree on (a dangling junction dep; gate recognition)
  * the bulk form answers exactly what the per-task form answers
  * the done-guard asks the same question, so released work can be completed
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.kanban import deps as D  # noqa: E402

GATE_TITLE = "MANUAL-MODE GATE — hold KPR merge-watch PRs for human review"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE kanban_tasks (
            id                 TEXT PRIMARY KEY,
            title              TEXT NOT NULL DEFAULT '',
            status             TEXT DEFAULT 'backlog',
            depends_on_task_id TEXT,
            last_failure_reason TEXT
        );
        CREATE TABLE kanban_task_deps (
            task_id       TEXT NOT NULL,
            depends_on_id TEXT NOT NULL,
            created_at    TEXT
        );
        """
    )
    # Production SQL is authored for PostgreSQL (%s placeholders, per CLAUDE.md);
    # StorageConnection is what translates them for SQLite. A raw sqlite3
    # connection turns every %s into `near "%": syntax error`.
    from tools.db.storage import StorageConnection

    return StorageConnection(c, "sqlite")


def _add(conn, tid, *, status="backlog", dep=None, title="t", junction=()):
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, status, depends_on_task_id) "
        "VALUES (?,?,?,?)",
        (tid, title, status, dep),
    )
    for j in junction:
        conn.execute(
            "INSERT INTO kanban_task_deps (task_id, depends_on_id, created_at) "
            "VALUES (?,?,?)",
            (tid, j, "2026-08-18T00:00:00+00:00"),
        )
    conn.commit()


def _sql_dispatchable(conn) -> set[str]:
    """Ids the dispatcher's own WHERE fragment admits."""
    clause, params = D.dep_clause_sql("kt")
    rows = conn.execute(
        f"SELECT kt.id FROM kanban_tasks kt WHERE {clause}",  # nosec B608
        params,
    ).fetchall()
    return {dict(r)["id"] for r in rows}


# ────────────────────────────────────────────────────────────────────────────
# The rule
# ────────────────────────────────────────────────────────────────────────────

def test_junction_rows_supersede_the_seeding_chain(conn):
    """The measured case: every real prerequisite done, blocked by batch order."""
    for rsv in ("cef-rsv-01", "cef-rsv-02", "cef-rsv-03"):
        _add(conn, rsv, status="done")
    _add(conn, "cef-di-02", status="pr_opened")
    _add(conn, "cef-di-03", dep="cef-di-02",
         junction=("cef-rsv-01", "cef-rsv-02", "cef-rsv-03"))
    _add(conn, "cef-di-04", dep="cef-di-03",
         junction=("cef-rsv-01", "cef-rsv-02", "cef-rsv-03"))

    assert D.blocking_deps("cef-di-03", conn) == []
    assert D.deps_satisfied("cef-di-03", conn)
    # cef-di-04 too: they are independent migrations onto one built API, so they
    # run at the same time rather than one per ~90 minutes.
    assert D.deps_satisfied("cef-di-04", conn)
    assert {"cef-di-03", "cef-di-04"} <= _sql_dispatchable(conn)


def test_a_scalar_with_no_junction_rows_still_gates(conn):
    """The scalar is the ONLY declaration there, so it is honoured in full."""
    _add(conn, "a-01", status="in_progress")
    _add(conn, "a-02", dep="a-01")

    assert D.blocking_deps("a-02", conn) == ["a-01"]
    assert not D.deps_satisfied("a-02", conn)
    assert "a-02" not in _sql_dispatchable(conn)


def test_an_unsatisfied_junction_dep_still_blocks(conn):
    _add(conn, "b-01", status="done")
    _add(conn, "b-02", status="backlog")
    _add(conn, "b-03", dep="b-01", junction=("b-02",))

    assert D.blocking_deps("b-03", conn) == ["b-02"]
    assert "b-03" not in _sql_dispatchable(conn)


def test_a_manual_gate_holds_even_when_junction_rows_exist(conn):
    """A gate is a HOLD, not seeding order — the one scalar that must survive.

    ``kpr-watch-01 -> kpr-gate-02`` has no junction rows and would hold under any
    reading of the rule. The case that needs asserting is the one that would have
    been released by accident: a gate-held task that also carries junction rows.
    """
    _add(conn, "kpr-gate-02", status="in_progress", title=GATE_TITLE)
    _add(conn, "c-01", status="done")
    _add(conn, "c-02", dep="kpr-gate-02", junction=("c-01",))
    _add(conn, "kpr-watch-01", dep="kpr-gate-02")

    assert D.blocking_deps("c-02", conn) == ["kpr-gate-02"]
    assert D.blocking_deps("kpr-watch-01", conn) == ["kpr-gate-02"]
    assert _sql_dispatchable(conn).isdisjoint({"c-02", "kpr-watch-01"})


def test_a_gate_is_recognised_by_id_shape_too(conn):
    """``hgx-gate-01`` was missed once by a predicate that matched only ``-gate-00``."""
    _add(conn, "hgx-gate-01", status="in_progress", title="hold the HGX card")
    _add(conn, "d-01", status="done")
    _add(conn, "d-02", dep="hgx-gate-01", junction=("d-01",))

    assert D.blocking_deps("d-02", conn) == ["hgx-gate-01"]
    assert "d-02" not in _sql_dispatchable(conn)


# ────────────────────────────────────────────────────────────────────────────
# One rule, three renderings
# ────────────────────────────────────────────────────────────────────────────

def test_a_dangling_junction_dep_blocks_in_both_renderings(conn):
    """The old SQL's inner JOIN dropped it silently while the Python blocked."""
    _add(conn, "e-01", junction=("does-not-exist",))

    assert D.blocking_deps("e-01", conn) == ["does-not-exist (missing)"]
    assert "e-01" not in _sql_dispatchable(conn)


def test_sql_gate_recognition_is_a_superset_of_the_python_predicate():
    """SQL may only ever hold MORE than ``is_manual_gate``, never release more."""
    from tools.kanban.gates import is_manual_gate

    cases = [
        ("kpr-gate-02", ""),
        ("hgx-gate-01", ""),
        ("sme-gate-review", ""),          # not a gate to Python; SQL holds it anyway
        ("cef-di-03", ""),
        ("anything", GATE_TITLE),
        ("-gate-01", ""),                 # no card prefix — not a gate to Python
    ]
    for tid, title in cases:
        if is_manual_gate(tid, title):
            assert D.sql_recognises_gate(tid, title), tid
    # And the direction that is allowed to differ actually does, so the
    # relationship is a stated superset rather than an untested equality.
    assert D.sql_recognises_gate("sme-gate-review", "")
    assert not is_manual_gate("sme-gate-review", "")


def test_the_two_renderings_agree_over_a_whole_board(conn):
    _add(conn, "g-gate-00", status="in_progress", title=GATE_TITLE)
    _add(conn, "g-01", status="done")
    _add(conn, "g-02", status="in_progress")
    _add(conn, "g-03", dep="g-01")
    _add(conn, "g-04", dep="g-02")
    _add(conn, "g-05", dep="g-02", junction=("g-01",))
    _add(conn, "g-06", dep="g-gate-00", junction=("g-01",))
    _add(conn, "g-07", junction=("g-01", "g-02"))
    _add(conn, "g-08", dep="missing-parent")
    _add(conn, "g-09", dep="missing-parent", junction=("g-01",))

    ids = [dict(r)["id"] for r in conn.execute("SELECT id FROM kanban_tasks").fetchall()]
    python = {t for t in ids if D.deps_satisfied(t, conn)}
    assert python == _sql_dispatchable(conn)
    # Spot-check the interesting rows so an agreeing-but-wrong pair cannot pass.
    assert "g-05" in python          # junction satisfied, scalar superseded
    assert "g-06" not in python      # gate scalar survives the junction
    assert "g-09" in python          # a superseded scalar is not even resolved


def test_bulk_form_answers_what_the_per_task_form_answers(conn):
    _add(conn, "h-gate-00", status="in_progress", title=GATE_TITLE)
    _add(conn, "h-01", status="done")
    _add(conn, "h-02", status="backlog")
    _add(conn, "h-03", dep="h-01", junction=("h-02",))
    _add(conn, "h-04", dep="h-02", junction=("h-01",))
    _add(conn, "h-05", dep="h-gate-00", junction=("h-01",))
    _add(conn, "h-06", junction=("nope",))

    ids = [dict(r)["id"] for r in conn.execute("SELECT id FROM kanban_tasks").fetchall()]
    bulk = D.blocking_deps_bulk(ids, conn)
    assert bulk == {t: D.blocking_deps(t, conn) for t in ids}


# ────────────────────────────────────────────────────────────────────────────
# The pure rule, without a database
# ────────────────────────────────────────────────────────────────────────────

def test_effective_dep_ids_is_the_whole_rule():
    assert D.effective_dep_ids("s", ["j1", "j2"]) == ("j1", "j2")
    assert D.effective_dep_ids("s", []) == ("s",)
    assert D.effective_dep_ids(None, ["j1"]) == ("j1",)
    assert D.effective_dep_ids(None, []) == ()
    # A gate scalar is kept AND the junction still applies — both, not either.
    assert D.effective_dep_ids("s", ["j1"], scalar_is_gate=True) == ("s", "j1")
    # A scalar that is also a junction row is not listed twice.
    assert D.effective_dep_ids("j1", ["j1"], scalar_is_gate=True) == ("j1",)


def test_scalar_is_superseded_says_so_out_loud():
    assert D.scalar_is_superseded("s", ["j1"])
    assert not D.scalar_is_superseded("s", [])
    assert not D.scalar_is_superseded("s", ["j1"], scalar_is_gate=True)
    assert not D.scalar_is_superseded(None, ["j1"])


# ────────────────────────────────────────────────────────────────────────────
# Downstream: a released task has to be completable
# ────────────────────────────────────────────────────────────────────────────

def test_the_done_guard_asks_the_same_question(conn):
    """Refusing to complete FINISHED work on a superseded scalar is unclearable.

    ``cef-di-04`` can now be dispatched while ``cef-di-03`` is still open. If the
    done-transition guard still ANDed the scalar, the session that finished it
    could never mark it done and nothing on the board could release it.
    """
    _add(conn, "cef-rsv-01", status="done")
    _add(conn, "cef-di-03", status="in_progress")
    _add(conn, "cef-di-04", status="in_progress", dep="cef-di-03",
         junction=("cef-rsv-01",))

    ok, reason = D.parent_holds_done("cef-di-04", conn)
    assert ok and reason is None


def test_the_done_guard_still_refuses_a_real_prerequisite(conn):
    """It is defense-in-depth against the E-gate incident; narrowed, not disarmed."""
    _add(conn, "i-01", status="in_progress")
    _add(conn, "i-02", status="in_progress", junction=("i-01",))

    ok, reason = D.parent_holds_done("i-02", conn)
    assert not ok
    assert "i-01" in (reason or "")
