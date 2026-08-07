#!/usr/bin/env python3
# CUI // SP-CTI
"""obs-cov-05: a kanban_status_transitions row may never have a blank reason.

#1183 removed the reason-less ``_move_task(..., "backlog")`` call sites and
blank discards kept arriving, because it fixed call sites rather than the write
boundary. These tests hold the boundary itself, plus the structural rules that
stop the hole reopening:

  * no call site in the reflex may omit ``reason=`` (behavioural regressions
    are caught before they reach the board);
  * every writer of the table must route through
    ``tools.kanban.transition_reason`` (a sixth writer added later cannot
    quietly reintroduce blanks).
"""

import ast
import pathlib

import pytest

from tools.kanban.transition_reason import (
    UNATTRIBUTED_PREFIX,
    resolve_transition_reason,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Every module that INSERTs into kanban_status_transitions. Discovered by grep
# and asserted below, so this list cannot silently go stale.
WRITERS = [
    "tools/genesis/reflexes/kanban.py",
    "tools/dashboard/api/kanban.py",
    "tools/kanban/cli.py",
    "tools/kanban/state_machine.py",
    "tools/ci/pr_watcher.py",
    "tools/mcp/kanban_server.py",
]

# Not a production writer: this verification harness seeds synthetic 'done'
# transitions into a throwaway database to prove the board-throughput stall rule
# (kax-stall-01) on a live backend. It still routes through the boundary, and
# still belongs in the inventory — an INSERT the list does not know about is
# exactly what this file exists to prevent. It has no icdev/ mirror (neither
# does verify_govcon_audit_writes.py), so it is listed separately from WRITERS.
VERIFIER_WRITERS = [
    "tools/testing/verify_board_stall_rule.py",
]

# The same six under the canonical ``icdev.tools.*`` namespace. These are not
# redundant: ``tools.X`` and ``icdev.tools.X`` are separate module objects in a
# checkout, and a wheel ships only the ``icdev/`` copy. The first cut of this
# file scanned ``tools/`` alone, so ``icdev/tools/genesis/reflexes/kanban.py``
# and ``icdev/tools/ci/pr_watcher.py`` kept writing blank reasons with every
# root-namespace test passing — the mirror is where this hole reopens.
ICDEV_WRITERS = ["icdev/" + rel for rel in WRITERS]

ALL_WRITERS = WRITERS + ICDEV_WRITERS + VERIFIER_WRITERS


# ── the boundary ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("blank", [None, "", "   ", "\n\t "])
def test_blank_reason_is_replaced_not_passed_through(blank):
    """The one outcome the table may never receive is an empty reason."""
    out = resolve_transition_reason(
        blank, from_status="in_progress", to_status="backlog", actor="scheduler",
    )
    assert out
    assert out.strip()
    assert out.startswith(UNATTRIBUTED_PREFIX)


def test_synthesized_reason_names_the_transition_and_actor():
    """A blank-origin row must be readable without joining back to the task."""
    out = resolve_transition_reason(
        None, from_status="in_progress", to_status="backlog", actor="scheduler",
    )
    assert "in_progress->backlog" in out
    assert "scheduler" in out


def test_synthesized_reason_names_the_call_site():
    """Identifying the writer is the whole point — the row must say who."""
    def a_caller_that_forgot():
        return resolve_transition_reason(
            None, from_status="in_progress", to_status="backlog", actor="scheduler",
        )

    out = a_caller_that_forgot()
    assert "a_caller_that_forgot" in out
    assert "test_kanban_transition_reason.py" in out


def test_a_real_reason_is_returned_verbatim():
    """This must never rewrite, truncate, or decorate a reason a caller gave."""
    real = "claude CLI exited 1: Permission allow rule (…) is not matched"
    assert resolve_transition_reason(
        real, from_status="in_progress", to_status="backlog", actor="scheduler",
    ) == real


def test_whitespace_only_padding_is_stripped_from_a_real_reason():
    assert resolve_transition_reason("  timeout 3/5  ", to_status="backlog") == "timeout 3/5"


def test_never_raises_on_junk_input():
    """Every caller is best-effort; audit bookkeeping must not break a move."""

    class Explodes:
        def __str__(self):
            raise RuntimeError("boom")

    out = resolve_transition_reason(Explodes(), to_status="backlog")
    assert out
    assert out.startswith(UNATTRIBUTED_PREFIX)


def test_skip_frames_hides_the_writer_wrapper():
    """A writer that wraps this call wants its caller named, not itself."""

    def real_call_site():
        return a_writer_wrapper()

    def a_writer_wrapper():
        return resolve_transition_reason(None, to_status="backlog", skip_frames=1)

    out = real_call_site()
    # Match the frame token, not a bare substring — this test's own name would
    # otherwise satisfy the assertion by accident.
    named = out.split(" at ", 1)[1].split(" < ")
    assert not any(f.endswith(" a_writer_wrapper") for f in named)
    assert any(f.endswith(" real_call_site") for f in named)


# ── the boundary, actually persisting ─────────────────────────────────────


def test_reflex_writes_a_non_blank_reason_when_the_caller_omits_one(tmp_path, monkeypatch):
    """The end-to-end claim: a reason-less call cannot land a blank row.

    Exercises the real INSERT through the storage layer (so ``%s`` placeholder
    translation is applied) rather than a stub, because the blank rows on the
    live board were produced by this exact write path.
    """
    db = tmp_path / "kst.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))

    from tools.db.storage import get_connection

    conn = get_connection(str(db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kanban_status_transitions ("
        "id TEXT PRIMARY KEY, task_id TEXT, from_status TEXT, to_status TEXT, "
        "actor TEXT, reason TEXT, recorded_at TEXT)"
    )
    conn.commit()
    conn.close()

    import tools.genesis.reflexes.kanban as reflex

    monkeypatch.setattr(reflex, "get_connection", lambda *a, **kw: get_connection(str(db)))
    reflex._record_status_transition(
        "obs-cov-05-probe", "in_progress", "backlog", actor="scheduler", reason=None,
    )

    conn = get_connection(str(db))
    row = conn.execute(
        "SELECT reason FROM kanban_status_transitions WHERE task_id = %s",
        ("obs-cov-05-probe",),
    ).fetchone()
    conn.close()

    assert row is not None, "no row was written at all"
    reason = dict(row)["reason"]
    assert reason and reason.strip(), "a blank reason reached the table"
    assert reason.startswith(UNATTRIBUTED_PREFIX)
    # skip_frames=1 in the writer means the row names this test, not the writer.
    assert "_record_status_transition" not in reason


# ── structural: the call sites ────────────────────────────────────────────


def test_no_reflex_call_site_omits_a_reason():
    """The regression #1183 fixed for 'backlog' only — assert it for every target.

    Four ``-> in_progress`` sites still omitted ``reason=`` after #1183 and were
    writing ~176 blank rows a day when this was measured on 2026-08-07.
    """
    src = (REPO_ROOT / "tools/genesis/reflexes/kanban.py").read_text(encoding="utf-8")
    offenders = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "_move_task":
            continue
        if not any(kw.arg == "reason" for kw in node.keywords):
            offenders.append(node.lineno)
    assert not offenders, (
        "_move_task called without reason= at lines "
        f"{offenders} — these write a row the board cannot account for"
    )


# ── structural: the writers ───────────────────────────────────────────────


def _writers_on_disk():
    """Every module that INSERTs into the table, found rather than assumed.

    Scans BOTH namespaces. Scanning only ``tools/`` is what let the two
    ``icdev/`` mirrors write blank reasons unnoticed.
    """
    found = []
    for root in ("tools", "icdev/tools"):
        for path in REPO_ROOT.joinpath(root).rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "INSERT INTO kanban_status_transitions" in text:
                found.append(path.relative_to(REPO_ROOT).as_posix())
    return sorted(found)


def test_writer_inventory_is_current():
    """If someone adds a seventh writer, this fails and they must extend the list."""
    assert _writers_on_disk() == sorted(ALL_WRITERS)


@pytest.mark.parametrize("rel", ALL_WRITERS)
def test_every_writer_routes_through_the_boundary(rel):
    """A writer that builds its own INSERT can reintroduce blanks; forbid it."""
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "transition_reason" in text, (
        f"{rel} INSERTs into kanban_status_transitions without importing "
        "tools.kanban.transition_reason — it can write a blank reason"
    )


def test_mcp_move_records_a_transition_with_a_reason(tmp_path, monkeypatch):
    """It used to UPDATE and return: an MCP move left no audit row whatsoever.

    Worse than a blank reason — the status change was invisible to the timeline,
    so a move nobody could account for looked like it had never happened.
    """
    db = tmp_path / "mcp.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))

    from tools.db.storage import get_connection

    conn = get_connection(str(db))
    conn.execute("CREATE TABLE kanban_tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
    conn.execute(
        "CREATE TABLE kanban_status_transitions ("
        "id TEXT PRIMARY KEY, task_id TEXT, from_status TEXT, to_status TEXT, "
        "actor TEXT, reason TEXT, recorded_at TEXT)"
    )
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, status) VALUES (%s, %s, %s)",
        ("obs-cov-05-mcp", "T", "in_progress"),
    )
    conn.commit()
    conn.close()

    import tools.db.storage as storage_mod
    import tools.mcp.kanban_server as server

    real = storage_mod.get_connection
    monkeypatch.setattr(storage_mod, "get_connection", lambda *a, **kw: real(str(db)))

    out = server.handle_kanban_move_task(
        {"task_id": "obs-cov-05-mcp", "status": "backlog"}
    )
    assert out.get("moved") == "obs-cov-05-mcp", out

    conn = real(str(db))
    row = conn.execute(
        "SELECT from_status, to_status, actor, reason FROM kanban_status_transitions "
        "WHERE task_id = %s",
        ("obs-cov-05-mcp",),
    ).fetchone()
    conn.close()

    assert row is not None, "MCP move wrote no transition row — the change is invisible"
    d = dict(row)
    assert d["from_status"] == "in_progress", "prior status must be real, not None"
    assert d["to_status"] == "backlog"
    assert d["actor"] == "mcp"
    assert d["reason"] and d["reason"].strip(), "a blank reason reached the table"


def test_mcp_move_keeps_an_explicit_reason(tmp_path, monkeypatch):
    """A caller that does supply a cause must see it recorded verbatim."""
    db = tmp_path / "mcp2.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))

    from tools.db.storage import get_connection

    conn = get_connection(str(db))
    conn.execute("CREATE TABLE kanban_tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
    conn.execute(
        "CREATE TABLE kanban_status_transitions ("
        "id TEXT PRIMARY KEY, task_id TEXT, from_status TEXT, to_status TEXT, "
        "actor TEXT, reason TEXT, recorded_at TEXT)"
    )
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, status) VALUES (%s, %s, %s)",
        ("obs-cov-05-mcp2", "T", "in_progress"),
    )
    conn.commit()
    conn.close()

    import tools.db.storage as storage_mod
    import tools.mcp.kanban_server as server

    real = storage_mod.get_connection
    monkeypatch.setattr(storage_mod, "get_connection", lambda *a, **kw: real(str(db)))

    server.handle_kanban_move_task({
        "task_id": "obs-cov-05-mcp2", "status": "backlog",
        "reason": "operator requeued after a bad merge",
    })

    conn = real(str(db))
    reason = dict(conn.execute(
        "SELECT reason FROM kanban_status_transitions WHERE task_id = %s",
        ("obs-cov-05-mcp2",),
    ).fetchone())["reason"]
    conn.close()
    assert reason == "operator requeued after a bad merge"


# ── the canonical namespace ───────────────────────────────────────────────


def test_icdev_mirror_reflex_replaces_a_blank_reason(tmp_path, monkeypatch):
    """The grep guard above proves the import exists; this proves it fires.

    ``icdev.tools.genesis.reflexes.kanban`` is a distinct module object from
    ``tools.genesis.reflexes.kanban`` and is the only copy a wheel ships. It
    wrote ``reason`` straight through to the INSERT, so every blank this task
    set out to remove was still reachable through the canonical namespace with
    the whole root-namespace suite green.
    """
    db = tmp_path / "mirror.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))

    import tools.db.storage as storage_mod

    real = storage_mod.get_connection
    conn = real(str(db))
    conn.execute(
        "CREATE TABLE kanban_status_transitions ("
        "id TEXT PRIMARY KEY, task_id TEXT, from_status TEXT, to_status TEXT, "
        "actor TEXT, reason TEXT, recorded_at TEXT)"
    )
    conn.commit()
    conn.close()

    import importlib

    mirror = importlib.import_module("icdev.tools.genesis.reflexes.kanban")
    monkeypatch.setattr(mirror, "get_connection", lambda *a, **kw: real(str(db)))

    mirror._record_status_transition(
        "obs-cov-05-mirror", "in_progress", "backlog", actor="scheduler",
    )

    conn = real(str(db))
    row = conn.execute(
        "SELECT reason FROM kanban_status_transitions WHERE task_id = %s",
        ("obs-cov-05-mirror",),
    ).fetchone()
    conn.close()

    assert row is not None, "the mirror wrote no row at all"
    reason = dict(row)["reason"]
    assert reason and reason.strip(), "the mirror wrote a blank reason"
    assert reason.startswith(UNATTRIBUTED_PREFIX)
    assert "in_progress->backlog" in reason
    assert "scheduler" in reason


def test_icdev_mirror_reflex_keeps_an_explicit_reason(tmp_path, monkeypatch):
    """Attribution must never overwrite a cause the caller actually knew."""
    db = tmp_path / "mirror2.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))

    import tools.db.storage as storage_mod

    real = storage_mod.get_connection
    conn = real(str(db))
    conn.execute(
        "CREATE TABLE kanban_status_transitions ("
        "id TEXT PRIMARY KEY, task_id TEXT, from_status TEXT, to_status TEXT, "
        "actor TEXT, reason TEXT, recorded_at TEXT)"
    )
    conn.commit()
    conn.close()

    import importlib

    mirror = importlib.import_module("icdev.tools.genesis.reflexes.kanban")
    monkeypatch.setattr(mirror, "get_connection", lambda *a, **kw: real(str(db)))

    mirror._record_status_transition(
        "obs-cov-05-mirror2", "in_progress", "backlog",
        actor="scheduler", reason="claude CLI exited 1: timeout",
    )

    conn = real(str(db))
    reason = dict(conn.execute(
        "SELECT reason FROM kanban_status_transitions WHERE task_id = %s",
        ("obs-cov-05-mirror2",),
    ).fetchone())["reason"]
    conn.close()
    assert reason == "claude CLI exited 1: timeout"
