# CUI // SP-CTI
"""xit-cut-01 -- the FathomDesk export tool: DDL-enumerated, read-only, manifest-verified."""
from __future__ import annotations

from pathlib import Path

from tools.db import export_ft_data as x

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_enumeration_is_by_declaring_source_not_by_prefix(tmp_path):
    (tmp_path / "tools" / "trading").mkdir(parents=True)
    (tmp_path / "tools" / "knowledge_graph").mkdir(parents=True)
    (tmp_path / "tools" / "db" / "migrations" / "057_ad_backtest_runs").mkdir(parents=True)
    (tmp_path / "tools" / "db" / "migrations" / "010_other").mkdir(parents=True)
    (tmp_path / "tools" / "trading" / "db.py").write_text(
        'A = "CREATE TABLE IF NOT EXISTS ad_positions (id TEXT)"\n'
        'B = "CREATE TABLE IF NOT EXISTS trading_daemon_audit (id TEXT)"\n'
        'C = "CREATE TABLE IF NOT EXISTS kg_nodes (id TEXT)"\n', encoding="utf-8")
    (tmp_path / "tools" / "knowledge_graph" / "schema.py").write_text(
        'C = "CREATE TABLE IF NOT EXISTS kg_nodes (id TEXT)"\n', encoding="utf-8")
    (tmp_path / "tools" / "db" / "migrations" / "057_ad_backtest_runs" / "up.sql").write_text(
        "CREATE TABLE IF NOT EXISTS ad_backtest_runs (id TEXT);\n", encoding="utf-8")
    (tmp_path / "tools" / "db" / "migrations" / "010_other" / "up.sql").write_text(
        "CREATE TABLE IF NOT EXISTS kanban_tasks (id TEXT);\n", encoding="utf-8")
    enum = x.enumerate_tables(tmp_path)
    assert enum["tables"] == ["ad_backtest_runs", "ad_positions", "trading_daemon_audit"]
    assert list(enum["excluded_shared"]) == ["kg_nodes"]  # declared by trading AND by the core
    assert "kanban_tasks" not in enum["tables"]


def test_the_real_tree_enumerates_fathomdesk_from_the_migrations():
    """The ad_ namespace still enumerates AFTER the sources were removed (xit-rm-02).

    The FathomDesk and trading trees are gone from this domain; the ad_* MIGRATIONS stay as
    history and are never deleted or renumbered, and they are what keeps this enumerable.
    """
    enum = x.enumerate_tables(REPO_ROOT)
    assert len(enum["tables"]) >= 137, len(enum["tables"])
    assert all(t.startswith("ad_") for t in enum["tables"]), [t for t in enum["tables"] if not t.startswith("ad_")]
    for noise in ("is", "from", "ad_"):
        assert noise not in enum["tables"]


def test_the_shared_graph_can_no_longer_enter_the_export_SET():
    """The protection that mattered, restated for a tree with no FathomDesk sources.

    `excluded_shared` USED to name kg_nodes/kg_edges/kg_graphs/trading_daemon_audit -- tables
    declared by a FathomDesk source AND by the core, which the exporter had to subtract so a
    cutover could not carry the shared knowledge graph into ICDEV[FT]. After xit-rm-02 there
    is no FathomDesk source left to co-declare them, so that set is empty STRUCTURALLY rather
    than by measurement, and asserting the four old names would assert the removal never
    happened.

    What is asserted instead is the invariant those exclusions existed to serve: nothing
    outside the ad_ namespace can reach the export set, whatever the tree declares.
    """
    enum = x.enumerate_tables(REPO_ROOT)
    shared = {"kg_nodes", "kg_edges", "kg_graphs", "trading_daemon_audit"}
    assert not (shared & set(enum["tables"])), "the shared graph must never be exportable"
    assert all(t.startswith("ad_") for t in enum["tables"])


def test_diff_counts_is_exact_and_names_every_difference():
    expected = {"ad_a": 10, "ad_b": 0, "ad_c": 5}
    rep = x.diff_counts(expected, {"ad_a": 10, "ad_b": 0, "ad_c": 5})
    assert rep["ok"] is True and rep["matched"] == 3
    rep = x.diff_counts(expected, {"ad_a": 9, "ad_b": None, "ad_c": 5})
    assert rep["ok"] is False
    assert rep["missing_on_target"] == ["ad_b"]
    assert rep["row_count_mismatch"] == {"ad_a": {"expected": 10, "observed": 9}}


def test_reconstructed_ddl_is_labelled_and_valid_shape():
    ddl = x.reconstruct_ddl("ad_x", [
        {"name": "id", "type": "text", "length": None, "nullable": False, "default": None},
        {"name": "qty", "type": "numeric", "length": None, "nullable": True, "default": "0"},
        {"name": "sym", "type": "character varying", "length": 16, "nullable": True, "default": None},
    ], ["id"])
    assert ddl.startswith('CREATE TABLE IF NOT EXISTS "ad_x" (')
    assert '"id" text NOT NULL' in ddl and '"qty" numeric DEFAULT 0' in ddl
    assert '"sym" character varying(16)' in ddl and 'PRIMARY KEY ("id")' in ddl


def test_dsn_is_redacted_in_every_report():
    assert x.redact_dsn("postgresql://icdev:s3cret@localhost:5432/icdev") == "postgresql://icdev:***@localhost:5432/icdev"
    assert "s3cret" not in x.redact_dsn("postgresql://icdev:s3cret@h/icdev")


def test_cli_refuses_without_a_dsn_and_lists_without_one(monkeypatch, capsys):
    monkeypatch.delenv(x.DSN_ENV, raising=False)
    assert x.main(["--dry-run"]) == 2
    assert x.main(["--list"]) == 0
    out = capsys.readouterr().out
    # `kg_nodes` used to appear here as an EXCLUDED shared table. After xit-rm-02 no
    # FathomDesk source co-declares it, so it is absent from both lists -- and it must not
    # appear as an exported table either, which is the half that matters.
    assert "FathomDesk table(s) declared" in out
    assert "ad_" in out
    assert "\n  kg_nodes" not in out, "the shared graph must never be listed for export"


def test_tool_never_mutates():
    """Every SQL string the tool hands to the driver is a SELECT/COPY-OUT/catalog read."""
    import ast

    src = (REPO_ROOT / "tools" / "db" / "export_ft_data.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    sql_literals = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and any(k in node.value.upper() for k in ("SELECT", "COPY", "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE"))
    ]
    # the module docstring describes what it never does; everything else must be a read
    docstring_node = tree.body[0].value if isinstance(tree.body[0], ast.Expr) else None
    docstring_raw = docstring_node.value if isinstance(docstring_node, ast.Constant) else None
    body_literals = [s for s in sql_literals if s != docstring_raw]
    for lit in body_literals:
        up = lit.upper()
        assert not any(v in up for v in ("DROP TABLE", "DELETE FROM", "TRUNCATE", "INSERT INTO", "UPDATE ")), lit
    assert "readonly=True" in src
