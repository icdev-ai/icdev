# CUI // SP-CTI
"""Tests for the substrate half of capability_consumption.py (#trust-disc-04).

The headline test is :func:`test_the_trust_disc_04_plan_is_reported_before_the_code`:
an approved implementation plan described ``kg_ontology`` as a working
SHACL-lite supplying declared (subject_type, predicate, object_type) legality,
and on the live board the whole declared-ontology chain held nothing while the
graph beneath it held 25,000 rows. Probing the PLAN — not the finished code —
has to surface that.

Its twin is :func:`test_a_database_with_no_operating_history_reports_unmeasurable`.
A prober that cannot tell "this platform has never been run" from "this writer
was never wired" fabricates a finding for every empty table on a fresh
worktree, and 1,320 of the 1,775 tables on the live board are empty. Both
tests are asserted against a seeded fixture rather than the live database, so
they pin the MEASUREMENT; the CLI reports the state.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

capcon = importlib.import_module("tools.awareness.capability_consumption")


# The live shape, in miniature: a rich graph, an inert ontology chain beside it,
# a witness table proving the database has been run, and a reserved-word table
# that only a quoted identifier can count.
SCHEMA = [
    "CREATE TABLE kg_nodes (id TEXT PRIMARY KEY, label TEXT, ontology_id TEXT)",
    "CREATE TABLE kg_edges (id TEXT PRIMARY KEY, subject TEXT, object TEXT)",
    "CREATE TABLE kg_ontology (subject_type TEXT, predicate TEXT, object_type TEXT)",
    "CREATE TABLE ontology_subclass_closure (subclass TEXT, superclass TEXT)",
    "CREATE TABLE audit_trail (id INTEGER PRIMARY KEY, event_type TEXT)",
    'CREATE TABLE "order" (id INTEGER PRIMARY KEY, name TEXT)',
]

CONFIG = {
    "substrate_probe": {
        "history_witnesses": [{"table": "audit_trail", "min_rows": 1000}],
        "ignore_names": [],
    },
    "substrates": [
        {"ref": "kg_ontology", "note": "declared signature legality"},
        {"ref": "ontology_subclass_closure", "note": "the source that feeds it"},
        {"ref": "kg_nodes.ontology_id", "note": "the per-node link"},
        {"ref": "kg_edges", "note": "the graph itself"},
    ],
}

PLAN = """
# Plan: KG signature validator

Validate every edge against the declared ontology. kg_ontology supplies the
legal (subject_type, predicate, object_type) triples, seeded from
ontology_subclass_closure, and each node resolves its type through
kg_nodes.ontology_id. The graph itself is kg_nodes and kg_edges.

    SELECT subject_type FROM kg_ontology WHERE predicate = %s

The agents will review the tasks and the users can read the documents.
"""


def _seed(db_path, rows=(), skip_tables=(), audit_rows=2000):
    raw = sqlite3.connect(str(db_path))
    try:
        for ddl in SCHEMA:
            if any(f"TABLE {t} " in ddl or f'TABLE "{t}" ' in ddl for t in skip_tables):
                continue
            raw.execute(ddl)
        # The operating-history witness. Without it every zero below is an
        # artifact of a fresh database and the prober must refuse to read it.
        for i in range(audit_rows):
            raw.execute("INSERT INTO audit_trail (event_type) VALUES (?)", (f"e{i}",))
        for sql, params in rows:
            raw.execute(sql, params)
        raw.commit()
    finally:
        raw.close()


@pytest.fixture
def conn_factory(tmp_path, monkeypatch):
    """A StorageConnection over a seeded temp SQLite database.

    A real ``get_connection`` rather than a bare ``sqlite3.connect``, so the
    %s -> ? translation the production code relies on stays in the loop.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)
    made = []

    def _make(rows=(), skip_tables=(), audit_rows=2000, name="substrate.db"):
        db_path = tmp_path / name
        _seed(db_path, rows, skip_tables, audit_rows)
        from tools.db.storage import get_connection

        conn = get_connection(db_path=str(db_path))
        made.append(conn)
        return conn

    yield _make
    for conn in made:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


POPULATED_GRAPH = [
    ("INSERT INTO kg_nodes (id, label, ontology_id) VALUES (?, ?, NULL)", ("n1", "Alpha")),
    ("INSERT INTO kg_nodes (id, label, ontology_id) VALUES (?, ?, NULL)", ("n2", "Beta")),
    ("INSERT INTO kg_edges (id, subject, object) VALUES (?, ?, ?)", ("e1", "n1", "n2")),
]


def _by_ref(report):
    return {s["ref"]: s for s in report["substrates"]}


# ---------------------------------------------------------------------------
# The acceptance test — a PLAN, probed before any code exists
# ---------------------------------------------------------------------------


def test_the_trust_disc_04_plan_is_reported_before_the_code(conn_factory):
    """The plan that shipped the bug is reported against the substrate it named."""
    conn = conn_factory(POPULATED_GRAPH)
    catalogue = capcon._known_tables(conn)
    refs = capcon.extract_substrate_refs(PLAN, catalogue, source="plan.md")
    report = capcon.probe_substrates(refs, conn=conn, config=CONFIG)

    found = _by_ref(report)
    # The half that is real.
    assert found["kg_nodes"]["status"] == "populated"
    assert found["kg_edges"]["status"] == "populated"
    # The half that is not — and each with its own distinct verdict.
    assert found["kg_ontology"]["status"] == "empty"
    assert found["ontology_subclass_closure"]["status"] == "empty"
    assert found["kg_nodes.ontology_id"]["status"] == "column_unpopulated"
    assert found["kg_nodes.ontology_id"]["rows"] == 2
    assert found["kg_nodes.ontology_id"]["populated"] == 0

    findings = {f["ref"] for f in report["findings"]}
    assert findings == {"kg_ontology", "ontology_subclass_closure", "kg_nodes.ontology_id"}
    # And it points at the line that designed against it.
    assert any("plan.md:" in ref for ref in found["kg_ontology"]["references"])


def test_prose_nouns_that_happen_to_be_tables_are_not_findings(conn_factory):
    """`agents`, `tasks`, `users`, `documents` are real tables AND English.

    The bare-name matcher requires an underscore for exactly this reason: a
    plan is prose, and a prober that reads every sentence as a schema reference
    reports nothing anybody will act on.
    """
    conn = conn_factory(POPULATED_GRAPH)
    catalogue = set(capcon._known_tables(conn)) | {"agents", "tasks", "users", "documents"}
    refs = capcon.extract_substrate_refs(PLAN, catalogue, source="plan.md")
    assert not {"agents", "tasks", "users", "documents"} & set(refs)


# ---------------------------------------------------------------------------
# The rule that keeps the measurement honest
# ---------------------------------------------------------------------------


def test_a_database_with_no_operating_history_reports_unmeasurable(conn_factory):
    """A fresh worktree must WARN, never fabricate a finding."""
    conn = conn_factory(POPULATED_GRAPH, audit_rows=0)
    refs = {r: {"match_kinds": ["explicit"], "references": []} for r in
            ("kg_ontology", "ontology_subclass_closure", "kg_nodes.ontology_id")}
    report = capcon.probe_substrates(refs, conn=conn, config=CONFIG)

    assert report["measurable"] is False
    assert report["totals"]["findings"] == 0
    assert report["totals"]["unmeasurable"] == 3
    for entry in report["substrates"]:
        assert entry["status"] == "unmeasurable"
        assert entry["measurable"] is False
        assert "no operating history" in (entry["unmeasured_reason"] or "")
    # The witness evidence is reported, not just the verdict.
    witness = report["operating_history"]["witnesses"][0]
    assert witness["table"] == "audit_trail"
    assert witness["rows"] == 0
    assert witness["satisfied"] is False


def test_operating_history_is_satisfied_by_any_witness(conn_factory):
    conn = conn_factory(POPULATED_GRAPH, audit_rows=1200)
    history = capcon.operating_history(conn, CONFIG)
    assert history["has_history"] is True
    assert history["reason"] is None


def test_an_absent_table_is_never_reported_as_empty(conn_factory):
    """A missing migration and a missing writer are different answers."""
    conn = conn_factory(POPULATED_GRAPH, skip_tables=("kg_ontology",))
    report = capcon.probe_substrates(
        {"kg_ontology": {"match_kinds": ["explicit"], "references": []}},
        conn=conn, config=CONFIG,
    )
    entry = report["substrates"][0]
    assert entry["status"] == "absent"
    assert entry["is_finding"] is False
    assert "migration" in entry["note"]


# ---------------------------------------------------------------------------
# What counts as "designed against"
# ---------------------------------------------------------------------------


def test_a_write_only_reference_is_not_a_finding(conn_factory):
    """A change that adds `INSERT INTO x` is the fix, not the defect."""
    conn = conn_factory(POPULATED_GRAPH)
    catalogue = capcon._known_tables(conn)
    writer = "cur.execute('INSERT INTO kg_ontology (subject_type) VALUES (%s)', (t,))"
    refs = capcon.extract_substrate_refs(writer, catalogue, source="writer.py")
    assert refs["kg_ontology"]["match_kinds"] == ["write_sql"]

    report = capcon.probe_substrates(refs, conn=conn, config=CONFIG)
    entry = report["substrates"][0]
    assert entry["status"] == "empty"        # the state is reported truthfully
    assert entry["is_finding"] is False      # but it is not charged to this change
    assert report["totals"]["findings"] == 0


def test_delete_from_is_a_write_not_a_read(conn_factory):
    conn = conn_factory(POPULATED_GRAPH)
    catalogue = capcon._known_tables(conn)
    refs = capcon.extract_substrate_refs(
        "DELETE FROM kg_ontology WHERE predicate = %s", catalogue, source="x.py"
    )
    assert refs["kg_ontology"]["match_kinds"] == ["write_sql"]


def test_a_read_points_at_the_query_not_the_docstring(conn_factory):
    conn = conn_factory(POPULATED_GRAPH)
    catalogue = capcon._known_tables(conn)
    module = '"""We read kg_ontology here."""\n\nx = 1\nSQL = "SELECT * FROM kg_ontology"\n'
    refs = capcon.extract_substrate_refs(module, catalogue, source="m.py")
    assert refs["kg_ontology"]["read_references"] == ["m.py:4"]
    assert "m.py:1" in refs["kg_ontology"]["references"]


def test_a_column_of_an_empty_table_is_superseded_by_the_table(conn_factory):
    """Otherwise every empty table is reported once per column anybody named."""
    conn = conn_factory(POPULATED_GRAPH)
    refs = {
        "kg_ontology": {"match_kinds": ["read_sql"], "references": []},
        "kg_ontology.predicate": {"match_kinds": ["read_sql"], "references": []},
    }
    report = capcon.probe_substrates(refs, conn=conn, config=CONFIG)
    found = _by_ref(report)
    assert found["kg_ontology"]["is_finding"] is True
    assert found["kg_ontology.predicate"]["superseded_by"] == "kg_ontology"
    assert found["kg_ontology.predicate"]["is_finding"] is False
    assert report["totals"]["findings"] == 1


def test_a_module_path_is_not_read_as_a_column(conn_factory):
    conn = conn_factory(POPULATED_GRAPH)
    catalogue = capcon._known_tables(conn)
    refs = capcon.extract_substrate_refs(
        "see tools/graph/kg_nodes.py and kg_nodes.md", catalogue, source="d.md"
    )
    assert "kg_nodes" in refs
    assert "kg_nodes.py" not in refs
    assert "kg_nodes.md" not in refs


def test_a_reserved_word_table_is_counted_not_reported_unmeasurable(conn_factory):
    """`order` is a real table here and a reserved word in both backends."""
    conn = conn_factory(POPULATED_GRAPH)
    result = capcon.probe_substrate(conn, "order")
    assert result.measurable is True
    assert result.status == "empty"


def test_a_ref_that_is_not_an_identifier_is_refused(conn_factory):
    """The table name is interpolated into SQL, so it is asserted to be a word."""
    conn = conn_factory(POPULATED_GRAPH)
    result = capcon.probe_substrate(conn, "kg_nodes; DROP TABLE kg_nodes")
    assert result.measurable is False
    assert "not a plain identifier" in (result.unmeasured_reason or "")


# ---------------------------------------------------------------------------
# Config substrates
# ---------------------------------------------------------------------------


def test_config_substrate_states(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "args"
    cfg_dir.mkdir()
    (cfg_dir / "demo.yaml").write_text(
        "filled:\n  - one\nempty_list: []\nnothing:\n", encoding="utf-8"
    )
    monkeypatch.setattr(capcon, "_repo_file", lambda rel: tmp_path / rel)

    def _probe(ref):
        return capcon._probe_config_substrate(capcon.parse_substrate_ref(ref))

    assert _probe("args/demo.yaml::filled").status == "config_populated"
    assert _probe("args/demo.yaml::empty_list").status == "config_empty"
    assert _probe("args/demo.yaml::nothing").status == "config_empty"
    assert _probe("args/demo.yaml::missing").status == "config_absent"
    assert _probe("args/gone.yaml::any").status == "config_absent"
    # A missing config KEY is a fact about the tree under review, so unlike an
    # absent TABLE it is a finding.
    assert _probe("args/demo.yaml::missing").is_finding is True


# ---------------------------------------------------------------------------
# Diff parsing and the CLI contract
# ---------------------------------------------------------------------------


def test_added_lines_carry_real_file_line_numbers():
    diff = (
        "diff --git a/tools/x.py b/tools/x.py\n"
        "--- a/tools/x.py\n"
        "+++ b/tools/x.py\n"
        "@@ -0,0 +41,2 @@\n"
        "+SQL = 'SELECT 1 FROM kg_ontology'\n"
        "+OTHER = 2\n"
        "@@ -60,1 +90,1 @@\n"
        "-gone = 1\n"
        "+kept = 1\n"
    )
    parsed = capcon._added_lines_by_file(diff)
    assert parsed["tools/x.py"][0] == (41, "SQL = 'SELECT 1 FROM kg_ontology'")
    assert parsed["tools/x.py"][1] == (42, "OTHER = 2")
    assert parsed["tools/x.py"][2] == (90, "kept = 1")


def test_collect_omits_substrates_unless_asked(conn_factory, monkeypatch):
    """check_capability_liveness calls collect() twice per commit.

    Probing substrates it never reads would put a COUNT(*) fan-out on every
    commit for nothing, so the substrate half is opt-in at the API and on at
    the CLI.
    """
    conn = conn_factory(POPULATED_GRAPH)
    monkeypatch.setattr(capcon, "load_config", lambda *a, **k: dict(CONFIG, classes={}))
    report = capcon.collect(conn=conn, config=dict(CONFIG, classes={}))
    assert "substrates" not in report


def test_gate_exit_codes(conn_factory, monkeypatch, capsys):
    """--substrate-gate fails on an empty substrate and never on a fresh DB."""
    monkeypatch.setattr(capcon, "load_config", lambda *a, **k: CONFIG)
    real_probe = capcon.probe_substrates

    live = conn_factory(POPULATED_GRAPH, name="live.db")
    monkeypatch.setattr(capcon, "probe_substrates", _pinned(real_probe, live))
    assert capcon.main(["--probe-substrate", "kg_ontology", "--substrate-gate"]) == 1
    assert capcon.main(["--probe-substrate", "kg_edges", "--substrate-gate"]) == 0

    fresh = conn_factory(POPULATED_GRAPH, audit_rows=0, name="fresh.db")
    monkeypatch.setattr(capcon, "probe_substrates", _pinned(real_probe, fresh))
    assert capcon.main(["--probe-substrate", "kg_ontology", "--substrate-gate"]) == 0
    assert "GATE WARN" in capsys.readouterr().err


def _pinned(func, conn):
    """Bind a probe to a fixture connection without touching the real database."""

    def _wrapped(refs=None, conn_=None, config=None, include_declared=False, **kwargs):
        return func(refs, conn=conn, config=config, include_declared=include_declared)

    return _wrapped
