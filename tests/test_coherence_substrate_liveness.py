# CUI // SP-CTI
"""Tests for coherence_checker.check_substrate_liveness (#trust-disc-04).

The check is the consumer half of the substrate probe: a capability that
measures and that nothing consults is the very defect this family of tools
exists to catch, so the measurement is wired to the per-commit gate.

Its scope was chosen from a fire-rate survey over the last 40-60 commits on
main, not from taste:

    every table mentioned in a changed file            68% of commits fire
    every table in the added lines of the diff         22%
    declared substrates mentioned anywhere             30%
    declared substrates READ by a changed .py module    2%  (one commit, and it
                                                             is the module that
                                                             actually reads the
                                                             empty table)

These tests pin each narrowing, because each one is a place a future session
could widen the check back into noise and switch it off.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

coherence = importlib.import_module("tools.workflow.coherence_checker")
capcon = importlib.import_module("tools.awareness.capability_consumption")

CONFIG = {
    "substrate_probe": {"history_witnesses": [{"table": "audit_trail", "min_rows": 1000}]},
    "substrates": [
        {"ref": "kg_ontology", "note": "declared signature legality"},
        {"ref": "kg_edges", "note": "the graph itself"},
    ],
}

READS_THE_EMPTY_ONE = '''"""A module designed against the declared ontology."""

def legal_signatures(conn):
    return conn.execute(
        "SELECT DISTINCT subject_type, predicate FROM kg_ontology"
    ).fetchall()
'''

READS_THE_FULL_ONE = '''def edges(conn):
    return conn.execute("SELECT id FROM kg_edges").fetchall()
'''

ONLY_WRITES_THE_EMPTY_ONE = '''def seed(conn, rows):
    for row in rows:
        conn.execute("INSERT INTO kg_ontology (subject_type) VALUES (%s)", (row,))
'''

MENTIONS_IT_IN_PROSE = '''"""Notes on kg_ontology and how it would be used one day."""

VALUE = 1
'''


@pytest.fixture
def board(tmp_path, monkeypatch):
    """A seeded SQLite board behind the real get_connection, plus a scratch module.

    The board is bound through ``ICDEV_DB_PATH`` rather than by patching
    ``get_connection``, for two reasons. The check opens a connection for the
    census, closes it, and opens another for the attribution pass, so a fixture
    handing back one shared object fails on the second open in a way that reads
    like a check bug. And the probe issues ``%s`` SQL that only translates
    through the storage wrapper — routing through the real factory keeps that
    translation in the loop instead of substituting a raw sqlite3 handle whose
    'near "%"' error would be swallowed and read as "no findings".
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)
    db_path = tmp_path / "board.db"
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    def _make(source: str, audit_rows: int = 2000, edge_rows: int = 1):
        raw = sqlite3.connect(str(db_path))
        try:
            raw.execute("CREATE TABLE IF NOT EXISTS kg_ontology (subject_type TEXT, predicate TEXT)")
            raw.execute("CREATE TABLE IF NOT EXISTS kg_edges (id TEXT PRIMARY KEY)")
            raw.execute(
                "CREATE TABLE IF NOT EXISTS audit_trail (id INTEGER PRIMARY KEY, event_type TEXT)"
            )
            raw.execute("DELETE FROM audit_trail")
            raw.execute("DELETE FROM kg_edges")
            for i in range(audit_rows):
                raw.execute("INSERT INTO audit_trail (event_type) VALUES (?)", (f"e{i}",))
            for i in range(edge_rows):
                raw.execute("INSERT INTO kg_edges (id) VALUES (?)", (f"edge{i}",))
            raw.commit()
        finally:
            raw.close()

        monkeypatch.setattr(capcon, "load_config", lambda *a, **k: CONFIG)

        module = tmp_path / "changed_module.py"
        module.write_text(source, encoding="utf-8")
        monkeypatch.setattr(
            coherence, "_scan_targets",
            lambda changed_files=None, subdir="tools": [module] if subdir == "tools" else [],
        )
        return module

    return _make


def test_warns_when_a_changed_module_reads_an_empty_declared_substrate(board):
    """The headline: the design is reported, with the query line that made it."""
    module = board(READS_THE_EMPTY_ONE)
    result = coherence.check_substrate_liveness(changed_files=[module])

    assert result.status == "warn"
    assert "kg_ontology" in result.message
    assert "empty" in result.message
    assert "changed_module.py:5" in result.message  # the SELECT, not the docstring
    # The standing census travels with the finding, so a reader sees the whole
    # declared list rather than only what this diff happened to touch.
    assert "kg_edges: populated (1 rows)" in result.actual


def test_passes_when_the_substrate_it_reads_holds_rows(board):
    module = board(READS_THE_FULL_ONE)
    result = coherence.check_substrate_liveness(changed_files=[module])
    assert result.status == "pass"
    assert "all populated" in result.message


def test_a_module_that_only_writes_the_empty_substrate_is_not_charged(board):
    """Adding `INSERT INTO x` is the fix for an empty x, not the defect."""
    module = board(ONLY_WRITES_THE_EMPTY_ONE)
    result = coherence.check_substrate_liveness(changed_files=[module])
    assert result.status == "pass"
    # The state is still reported — just not attributed to this change.
    assert "kg_ontology: empty (0 rows)" in result.actual


def test_a_prose_mention_is_not_a_design(board):
    """30% of commits fire if a manifest row or a docstring counts as a design."""
    module = board(MENTIONS_IT_IN_PROSE)
    result = coherence.check_substrate_liveness(changed_files=[module])
    assert result.status == "pass"
    assert "nothing in this diff depends on them" in result.message


def test_a_fresh_database_warns_instead_of_fabricating(board):
    """No operating history: every table looks inert, so nothing is asserted."""
    module = board(READS_THE_EMPTY_ONE, audit_rows=0)
    result = coherence.check_substrate_liveness(changed_files=[module])
    assert result.status == "warn"
    assert "no operating history" in result.message
    assert "NOT verified" in result.message
    # Crucially, it does NOT name kg_ontology as a finding.
    assert "read at" not in result.message


def test_no_diff_reports_the_census_without_going_yellow(board):
    """A nightly sweep that is permanently yellow is a sweep nobody reads."""
    board(READS_THE_EMPTY_ONE)
    result = coherence.check_substrate_liveness()
    assert result.status == "pass"
    assert "1/2 declared substrate(s) hold no rows (kg_ontology)" in result.message


def test_never_blocks_a_commit(board):
    """An empty substrate is a fact about the DATABASE, not about the diff.

    Failing a per-commit code gate on it would block commits that have nothing
    to do with it, which is how project_card_coverage settled on warn too.
    """
    for source in (READS_THE_EMPTY_ONE, ONLY_WRITES_THE_EMPTY_ONE, MENTIONS_IT_IN_PROSE):
        module = board(source)
        assert coherence.check_substrate_liveness(changed_files=[module]).status != "fail"


def test_registered_and_runs_in_the_fast_tier():
    """The per-task gate, not only the nightly sweep.

    A design built on an empty substrate should be reported while the session
    that wrote it is still in the room. The fast tier is a denylist, so this
    asserts the absence that keeps it there — and the check measures ~0.3s
    against the live PostgreSQL board, well inside that budget.
    """
    assert "substrate_liveness" in coherence.CHECK_REGISTRY
    assert "substrate_liveness" not in coherence.HEAVY_CHECKS
    assert "substrate_liveness" in coherence.select_checks("fast")
    assert "substrate_liveness" in coherence.select_checks("full")


def test_an_empty_declared_list_is_not_a_clean_board(monkeypatch):
    """Declaring nothing must not read as "nothing is wrong"."""
    monkeypatch.setattr(capcon, "load_config", lambda *a, **k: {"substrates": []})
    result = coherence.check_substrate_liveness(changed_files=[])
    assert result.status == "warn"
    assert "unasked question" in result.message
