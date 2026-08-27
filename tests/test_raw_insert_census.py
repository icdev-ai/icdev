# CUI // SP-CTI
"""The raw-INSERT board-writer census and its coherence gate (rem-hyg-05).

What these assert, in the order that matters:

  1. the census is CLEAN on the tree as committed — the ratchet starts closed;
  2. a NEW raw INSERT fails BY NAME, which is the whole point;
  3. the ceiling is exactly today's count, because headroom is permission;
  4. prose about the pattern is not a finding, and SQL is — the distinction the
     first version of this tool got wrong, by flagging its own caller five times;
  5. the exclusions assert a raw INSERT is CORRECT there and each carries a
     written reason;
  6. a partial scan does not make a tree-wide claim.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    """Load a module BY PATH from this checkout.

    `from tools.kanban... import` resolves through sys.path, which in a worktree
    can land on the shared checkout and test a different tree — the exact failure
    mode the tool under test documents in its own sibling-import comment.
    """
    path = REPO_ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ric():
    return _load("_test_raw_insert_census", "tools/kanban/raw_insert_census.py")


@pytest.fixture(scope="module")
def report(ric):
    return ric.census(REPO_ROOT)


# --------------------------------------------------------------------------- #
# 1. The ratchet starts closed
# --------------------------------------------------------------------------- #
def test_census_is_clean_on_the_committed_tree(report):
    """Every raw board INSERT in the tree is registered or excluded.

    If this fails on `main`, someone added a writer without registering it —
    which is the gate doing its job, not a broken test.
    """
    assert report["errors"] == [], "\n".join(str(e) for e in report["errors"])
    assert report["ok"] is True
    assert report["unregistered"] == []


def test_the_census_is_not_empty(report):
    """A census that measured nothing would pass every assertion above.

    The adoption measurement was 219 sites in 199 files. This asserts the scan still SEES
    the bypassers rather than silently resolving to zero — a scanner whose scope quietly
    broke reports a perfectly clean board.

    THE FLOORS MOVE DOWN WHEN THE POPULATION REALLY SHRINKS, and only then. xit-rm-02
    removed the trading and FathomDesk trees from this domain, taking real raw-INSERT
    writers with them: 203 sites in 187 files now, measured. Lowering the floors by exactly
    that much keeps the guard doing its job — it still fails on a scanner that collapses to
    zero or near-zero — while not asserting a population this domain no longer has.
    """
    assert report["total_sites"] >= 200, report["total_sites"]
    assert report["total_files"] >= 187, report["total_files"]
    assert report["registered"] == report["total_sites"]


# --------------------------------------------------------------------------- #
# 2. A NEW writer fails BY NAME
# --------------------------------------------------------------------------- #
_NEW_WRITER = '''\
def seed_something(conn):
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, status) VALUES (?, ?, ?)",
        ("x-01", "t", "backlog"),
    )
'''


def test_a_new_raw_insert_is_unregistered_and_fails(ric, tmp_path):
    """The load-bearing assertion: an unregistered site fails, and is NAMED."""
    rel = "tools/_rem_hyg_05_probe.py"
    probe = REPO_ROOT / rel
    probe.write_text(_NEW_WRITER, encoding="utf-8", newline="\n")
    try:
        result = ric.census(REPO_ROOT, files=[rel])
    finally:
        probe.unlink()

    assert result["ok"] is False
    assert f"{rel}::seed_something[1]" in result["unregistered"]
    joined = " ".join(str(e) for e in result["errors"])
    assert "create_tasks" in joined, "the error must name the fix, not just the defect"
    assert "never raise it" in joined, "the error must say the ceiling only goes down"


def test_a_second_insert_in_a_registered_file_is_still_caught(ric, tmp_path):
    """Per SITE, not per file.

    A per-file census would grandfather a module once and let it grow a second
    and third raw INSERT unobserved. The ordinal is what stops that.
    """
    sites = ric.scan_source("tools/x.py", _NEW_WRITER + _NEW_WRITER.replace(
        "seed_something", "seed_another"
    ))
    assert {s.key for s in sites} == {
        "tools/x.py::seed_something[1]",
        "tools/x.py::seed_another[1]",
    }

    doubled = ric.scan_source(
        "tools/x.py",
        "def f(conn):\n"
        '    conn.execute("INSERT INTO kanban_tasks (id) VALUES (?)", ("a",))\n'
        '    conn.execute("INSERT INTO kanban_tasks (id) VALUES (?)", ("b",))\n',
    )
    assert [s.key for s in doubled] == ["tools/x.py::f[1]", "tools/x.py::f[2]"]


# --------------------------------------------------------------------------- #
# 3. The ceiling is a ratchet, not headroom
# --------------------------------------------------------------------------- #
def test_ceiling_equals_todays_count(ric, report):
    """`raw_insert_max` must not sit above the registered count.

    Headroom is permission: a ceiling of 230 against 219 registered sites is a
    standing licence to add eleven more writers with nothing going red.
    """
    assert report["raw_insert_max"] == report["registered"], (
        f"ceiling {report['raw_insert_max']} != {report['registered']} registered "
        "site(s). Lower raw_insert_census.raw_insert_max in "
        "args/board_writer_gate.yaml; never raise it."
    )


def test_no_stale_entries(report):
    """A census entry naming a site that no longer exists.

    Warned about rather than failed by the gate (deleting a raw INSERT must not
    fail the PR that deleted it), but on a merged tree it means the ceiling was
    not lowered when a writer was converted.
    """
    assert report["stale"] == [], (
        "run `python tools/kanban/raw_insert_census.py --prune` and lower the ceiling"
    )


# --------------------------------------------------------------------------- #
# 4. SQL vs prose about SQL
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "source, expected",
    [
        # Statement shapes that MUST be found.
        ('x = "INSERT INTO kanban_tasks (id) VALUES (?)"', 1),
        ('x = "INSERT OR IGNORE INTO kanban_tasks (id) VALUES (?)"', 1),
        ('x = "INSERT INTO kanban_tasks SELECT * FROM t"', 1),
        ('x = "insert into kanban_tasks(id) values (1)"', 1),
        # Adjacent literals: the parser folds them, so the column list is there.
        ('x = ("INSERT INTO kanban_tasks "\n     "(id) VALUES (?)")', 1),
        # f-string with an interpolated COLUMN list — the placeholder keeps shape.
        ('cols="id"\nx = f"INSERT INTO kanban_tasks ({cols}) VALUES (?)"', 1),
        # Prose that must NOT be found. Every one of these is real text from the
        # tree that the unqualified predicate flagged.
        ('logger.warning("best-effort INSERT into kanban_tasks failed: %s", e)', 0),
        ('"""_create_task() - insert into kanban_tasks"""', 0),
        ('X = ["a raw `INSERT INTO kanban_tasks` that bypasses task_factory"]', 0),
    ],
)
def test_statement_shape_separates_sql_from_prose(ric, source, expected):
    assert len(ric.scan_source("tools/x.py", source)) == expected, source


def test_interpolated_table_name_is_reported_not_swallowed(ric):
    """The documented non-goal must surface as an ERROR, never as a clean pass.

    A variable table name hides the write from the AST scan. The tool refuses to
    be silently narrower than a plain text search: the file lands in
    `text_only_files` and `--check` fails on it.
    """
    rel = "tools/_rem_hyg_05_probe.py"
    probe = REPO_ROOT / rel
    # The literal table name is present so the TEXT scan matches, while the AST
    # scan sees a statement whose shape is broken across a call boundary.
    probe.write_text(
        'SQL = "INSERT INTO kanban_tasks (id) VALUES (?)".replace("x", "y")\n'
        "def f(conn):\n"
        "    conn.execute(SQL)\n",
        encoding="utf-8",
        newline="\n",
    )
    try:
        # This one IS attributable — assert the mechanism, then the broken case.
        found = ric.census(REPO_ROOT, files=[rel])
        assert found["unregistered"], "a literal statement must still be attributed"

        probe.write_text(
            "def f(conn):\n"
            '    conn.execute("INSERT INTO kanban_tasks (id) VALUES (?)"\n',
            encoding="utf-8",
            newline="\n",
        )
        broken = ric.census(REPO_ROOT, files=[rel])
    finally:
        probe.unlink()

    assert rel in broken["text_only_files"]
    assert broken["ok"] is False


# --------------------------------------------------------------------------- #
# 5. Exclusions are claims, and claims need reasons
# --------------------------------------------------------------------------- #
def test_every_exclusion_carries_a_written_reason(ric):
    config = ric.load_config(REPO_ROOT)
    excluded = ric.exclusions(config)
    assert excluded, "an empty exclusion list would mean the seeder censuses itself"
    for entry in excluded:
        assert len(entry["reason"].strip()) >= 12, entry


def test_the_canonical_seeder_is_excluded_not_registered(ric, report):
    """task_factory holds the one raw INSERT that is CORRECT.

    It must not count against the ceiling we are trying to drive down — every
    census entry exists precisely because it is NOT the seeder.
    """
    scope = ric.in_scope(REPO_ROOT)
    assert "tools/kanban/task_factory.py" not in scope
    assert "icdev/tools/kanban/task_factory.py" not in scope
    assert not any(
        f.endswith("kanban/task_factory.py") for f in report["per_file"]
    ), report["per_file"]


def test_the_icdev_mirror_is_in_scope(ric):
    """CLAUDE.md directs NEW code at `icdev.tools.*`.

    Scanning only `tools/` would leave the growth path wide open in the tree the
    project tells people to write in.
    """
    scope = ric.in_scope(REPO_ROOT)
    assert any(f.startswith("icdev/tools/") for f in scope)
    assert any(f.startswith("tools/") for f in scope)


# --------------------------------------------------------------------------- #
# 6. A partial scan must not make a tree-wide claim
# --------------------------------------------------------------------------- #
def test_partial_scan_suppresses_the_ceiling_and_stale_halves(ric):
    """A subset cannot tell a deleted site from an unscanned one.

    Reporting "219 entries are stale" for a one-file commit is how a check earns
    a `|| true`.
    """
    result = ric.census(REPO_ROOT, files=["tools/kanban/gates.py"])
    assert result["partial"] is True
    assert result["stale"] == []
    assert result["ok"] is True


def test_coherence_check_partial_message_does_not_claim_the_tree():
    from tools.workflow.coherence_checker import check_board_writer_census

    result = check_board_writer_census([Path("tools/kanban/gates.py")])
    assert result.status == "pass"
    # The full-tier phrasing ("N known bypasser(s) enumerated ... against a
    # ceiling of M") must not appear after a one-file scan.
    assert "known bypasser" not in result.message, result.message
    assert "changed in-scope file" in result.message, result.message


def test_coherence_check_is_clean_on_the_committed_tree():
    from tools.workflow.coherence_checker import check_board_writer_census

    result = check_board_writer_census()
    assert result.status == "pass", result.message


def test_coherence_check_fails_on_a_new_writer():
    from tools.workflow.coherence_checker import check_board_writer_census

    rel = "tools/_rem_hyg_05_probe.py"
    probe = REPO_ROOT / rel
    probe.write_text(_NEW_WRITER, encoding="utf-8", newline="\n")
    try:
        result = check_board_writer_census([Path(rel)])
    finally:
        probe.unlink()

    assert result.status == "fail", result.message
    assert f"{rel}::seed_something[1]" in result.missing


def test_check_is_registered_and_not_auto_fixable():
    """An autofix here would be the gate widening its own allowlist.

    Registering a site is a decision with a written reason attached; a tool that
    appended the line itself would grandfather a writer nobody looked at.
    """
    from tools.workflow import coherence_checker as cc

    assert cc.CHECK_REGISTRY["board_writer_census"] is cc.check_board_writer_census
    assert cc._FIX_REGISTRY["board_writer_census"] == "skip"
