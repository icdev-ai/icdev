# CUI // SP-CTI
"""Unit tests for tools/awareness/edge_deriver.py (idp-cat-02).

The self-awareness graph shipped with 2,432 nodes and ZERO edges, so it could
not answer "what breaks if this changes". These tests pin the two properties
that make the new relationship layer trustworthy:

  1. Edges are derived from evidence that is actually on disk (an import
     statement, a CREATE TABLE, a route decorator) — not from title similarity.
  2. EVERY edge records how it was derived, and flags whether that derivation
     is mechanical, so a consumer can weight or drop the guesses.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.awareness import component_indexer as ci  # noqa: E402
from tools.awareness import edge_deriver as ed  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


def _tool(rel: str, label: str) -> ci.Node:
    return ci.Node(
        id=ci._node_id("tool", rel), label=label, entity_type="tool", file_path=rel
    )


@pytest.fixture()
def fake_repo(tmp_path: Path):
    """A miniature repo with one instance of every kind of evidence."""
    base = tmp_path

    _write(
        base / "tools" / "db" / "storage.py",
        '''
        """Storage layer."""
        def get_connection(): ...
        ''',
    )
    _write(
        base / "tools" / "widgets" / "engine.py",
        '''
        """Widget engine."""
        from tools.db.storage import get_connection

        def load():
            conn = get_connection()
            return conn.execute("SELECT * FROM widget_records").fetchall()
        ''',
    )
    _write(
        base / "tools" / "widgets" / "blueprint.py",
        '''
        """Widget blueprint."""
        from flask import Blueprint
        import tools.widgets.engine as engine

        bp = Blueprint("widgets", __name__)

        @bp.route("/widgets")
        def index(): ...

        @bp.route("/widgets/api/list", methods=["GET"])
        def api_list(): ...
        ''',
    )
    _write(
        base / "tools" / "db" / "migrations" / "001_widgets" / "up.sql",
        """
        CREATE TABLE IF NOT EXISTS widget_records (
            id TEXT PRIMARY KEY
        );
        """,
    )
    _write(
        base / "goals" / "build_widgets.md",
        """
        # Build Widgets

        Run `python tools/widgets/engine.py --json` then verify.
        """,
    )
    _write(
        base / "args" / "component_registry.yaml",
        """
        components:
        - key: widgets
          kind: canvas
          display_name: Widgets Canvas
          description: The widget canvas.
          module: tools.widgets.blueprint
          iqe:
            adapter_module: tools.widgets.engine
            collections:
            - widgets.records
            - widgets.scores
        - key: widgets_demo
          kind: core_extension
          display_name: Widgets Demo
          depends_on:
          - widgets
        """,
    )

    nodes = [
        _tool("tools/db/storage.py", "Storage"),
        _tool("tools/widgets/engine.py", "Widget Engine"),
        _tool("tools/widgets/blueprint.py", "Widget Blueprint"),
        ci.Node(
            id=ci._node_id("goal", "goals/build_widgets.md"),
            label="Build Widgets",
            entity_type="goal",
            file_path="goals/build_widgets.md",
        ),
    ]
    return base, nodes


def _edges_by_derivation(edges, derivation):
    return [e for e in edges if e.properties.get("derivation") == derivation]


# ---------------------------------------------------------------------------
# Provenance — the acceptance criterion "every edge records how it was derived"
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_every_edge_records_its_derivation(self, fake_repo):
        base, nodes = fake_repo
        _, edges = ed.derive(nodes, base=base)
        assert edges, "fake repo produced no edges"
        for edge in edges:
            props = edge.properties
            assert props.get("derivation") in ed.DERIVATIONS, props
            assert isinstance(props.get("mechanical"), bool)
            assert props.get("evidence"), f"no evidence on {edge.relationship} edge"

    def test_derivation_registry_declares_mechanical_and_weight(self):
        for name, meta in ed.DERIVATIONS.items():
            assert isinstance(meta["mechanical"], bool), name
            assert 0.0 < float(meta["weight"]) <= 1.0, name

    def test_keyword_match_is_the_only_non_mechanical_derivation(self):
        inferred = [k for k, v in ed.DERIVATIONS.items() if not v["mechanical"]]
        assert inferred == ["title_keyword_match"]

    def test_mechanical_edges_outweigh_inferred_ones(self):
        """A parsed import must never be worth less than a keyword guess."""
        guess = ed.DERIVATIONS["title_keyword_match"]["weight"]
        for name, meta in ed.DERIVATIONS.items():
            if meta["mechanical"]:
                assert meta["weight"] > guess, name


# ---------------------------------------------------------------------------
# Individual derivations
# ---------------------------------------------------------------------------


class TestImportEdges:
    def test_import_produces_an_edge_with_the_source_line(self, fake_repo):
        base, nodes = fake_repo
        _, edges = ed.derive(nodes, base=base)
        imports = _edges_by_derivation(edges, "python_import_ast")
        engine = ci._node_id("tool", "tools/widgets/engine.py")
        storage = ci._node_id("tool", "tools/db/storage.py")
        match = [e for e in imports if e.source_id == engine and e.target_id == storage]
        assert len(match) == 1
        edge = match[0]
        assert edge.relationship == "imports"
        assert edge.weight == 1.0
        assert edge.properties["mechanical"] is True
        assert "tools/widgets/engine.py:" in edge.properties["evidence"]

    def test_plain_import_statement_also_resolves(self, fake_repo):
        base, nodes = fake_repo
        _, edges = ed.derive(nodes, base=base)
        blueprint = ci._node_id("tool", "tools/widgets/blueprint.py")
        engine = ci._node_id("tool", "tools/widgets/engine.py")
        assert any(
            e.source_id == blueprint
            and e.target_id == engine
            and e.relationship == "imports"
            for e in edges
        )

    def test_self_imports_are_not_emitted(self, fake_repo):
        base, nodes = fake_repo
        _, edges = ed.derive(nodes, base=base)
        assert not [e for e in edges if e.source_id == e.target_id]


class TestTableEdges:
    def test_migration_owns_the_table_it_creates(self, fake_repo):
        base, nodes = fake_repo
        all_nodes, edges = ed.derive(nodes, base=base)
        tables = [n for n in all_nodes if n.entity_type == "db_table"]
        assert [n.label for n in tables] == ["widget_records"]
        creates = _edges_by_derivation(edges, "ddl_create_table")
        assert len(creates) == 1
        assert creates[0].relationship == "creates_table"
        assert creates[0].target_id == tables[0].id
        migration = next(n for n in all_nodes if n.entity_type == "migration")
        assert creates[0].source_id == migration.id

    def test_module_querying_the_table_gets_a_uses_table_edge(self, fake_repo):
        base, nodes = fake_repo
        all_nodes, edges = ed.derive(nodes, base=base)
        table = next(n for n in all_nodes if n.entity_type == "db_table")
        engine = ci._node_id("tool", "tools/widgets/engine.py")
        uses = _edges_by_derivation(edges, "sql_table_reference")
        assert any(
            e.source_id == engine and e.target_id == table.id and e.relationship == "uses_table"
            for e in uses
        )

    def test_unknown_identifiers_after_from_are_not_treated_as_tables(self, tmp_path):
        """`SELECT ... FROM whatever` must not invent a table node."""
        base = tmp_path
        _write(
            base / "tools" / "x.py",
            '''
            """X."""
            SQL = "SELECT * FROM not_a_real_table"
            ''',
        )
        nodes = [_tool("tools/x.py", "X")]
        all_nodes, edges = ed.derive(nodes, base=base)
        assert not [n for n in all_nodes if n.entity_type == "db_table"]
        assert not _edges_by_derivation(edges, "sql_table_reference")


class TestRouteEdges:
    def test_route_decorators_become_route_nodes_and_edges(self, fake_repo):
        base, nodes = fake_repo
        all_nodes, edges = ed.derive(nodes, base=base)
        routes = sorted(n.label for n in all_nodes if n.entity_type == "route")
        assert routes == ["/widgets", "/widgets/api/list"]
        serves = _edges_by_derivation(edges, "flask_route_decorator")
        blueprint = ci._node_id("tool", "tools/widgets/blueprint.py")
        assert len(serves) == 2
        assert all(e.source_id == blueprint for e in serves)
        assert all(e.relationship == "serves_route" for e in serves)


class TestDocumentedCommands:
    def test_goal_that_documents_a_tool_invocation(self, fake_repo):
        base, nodes = fake_repo
        _, edges = ed.derive(nodes, base=base)
        invokes = _edges_by_derivation(edges, "documented_command")
        goal = ci._node_id("goal", "goals/build_widgets.md")
        engine = ci._node_id("tool", "tools/widgets/engine.py")
        assert any(
            e.source_id == goal and e.target_id == engine and e.relationship == "invokes"
            for e in invokes
        )

    def test_dash_m_module_form_resolves(self, tmp_path):
        base = tmp_path
        _write(base / "tools" / "y.py", '"""Y."""\n')
        _write(
            base / "goals" / "g.md",
            """
            # G

            Run `python -m tools.y --json`.
            """,
        )
        nodes = [
            _tool("tools/y.py", "Y"),
            ci.Node(
                id=ci._node_id("goal", "goals/g.md"),
                label="G",
                entity_type="goal",
                file_path="goals/g.md",
            ),
        ]
        _, edges = ed.derive(nodes, base=base)
        assert _edges_by_derivation(edges, "documented_command")


class TestRegistryEdges:
    def test_component_nodes_and_iqe_collection_bindings(self, fake_repo):
        base, nodes = fake_repo
        all_nodes, edges = ed.derive(nodes, base=base)
        components = {n.extra["component_key"] for n in all_nodes if n.entity_type == "component"}
        assert components == {"widgets", "widgets_demo"}
        collections = sorted(n.label for n in all_nodes if n.entity_type == "iqe_collection")
        assert collections == ["widgets.records", "widgets.scores"]
        provides = _edges_by_derivation(edges, "component_registry_iqe")
        assert len(provides) == 2
        assert all(e.relationship == "provides_collection" for e in provides)

    def test_declared_prerequisite_becomes_a_depends_on_edge(self, fake_repo):
        base, nodes = fake_repo
        all_nodes, edges = ed.derive(nodes, base=base)
        deps = _edges_by_derivation(edges, "component_registry_depends_on")
        assert len(deps) == 1
        src = next(n for n in all_nodes if n.id == deps[0].source_id)
        dst = next(n for n in all_nodes if n.id == deps[0].target_id)
        assert src.extra["component_key"] == "widgets_demo"
        assert dst.extra["component_key"] == "widgets"

    def test_component_links_to_the_module_that_implements_it(self, fake_repo):
        base, nodes = fake_repo
        all_nodes, edges = ed.derive(nodes, base=base)
        impl = _edges_by_derivation(edges, "component_registry_module")
        targets = {e.target_id for e in impl}
        assert ci._node_id("tool", "tools/widgets/blueprint.py") in targets
        assert all(e.relationship == "implemented_by" for e in impl)


class TestKeywordHeuristic:
    def test_canvas_to_goal_match_is_flagged_as_a_guess(self, tmp_path):
        base = tmp_path
        (base / "tools" / "network_canvas").mkdir(parents=True)
        _write(base / "goals" / "n.md", "# network_canvas workflow\n")
        nodes = [
            ci.Node(
                id=ci._node_id("canvas_module", "tools/network_canvas"),
                label="network_canvas",
                entity_type="canvas_module",
                file_path="tools/network_canvas",
            ),
            ci.Node(
                id=ci._node_id("goal", "goals/n.md"),
                label="network_canvas workflow",
                entity_type="goal",
                file_path="goals/n.md",
            ),
        ]
        _, edges = ed.derive(nodes, base=base)
        keyword = _edges_by_derivation(edges, "title_keyword_match")
        assert len(keyword) == 1
        assert keyword[0].relationship == "referenced_by_goal"
        assert keyword[0].properties["mechanical"] is False
        assert keyword[0].weight < 1.0


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_default_config_enables_every_derivation(self):
        cfg = ed.load_edge_config()
        assert set(cfg["derivations"]) == set(ed.DERIVATIONS)
        for key in ed.DERIVATIONS:
            assert cfg["derivations"][key] is True, key

    def test_missing_config_file_degrades_to_everything_on(self, tmp_path):
        cfg = ed.load_edge_config(tmp_path / "nope.yaml")
        assert cfg["enabled"] is True
        assert all(cfg["derivations"].values())
        assert cfg["limits"] == ed._DEFAULT_LIMITS

    def test_disabling_a_derivation_drops_only_its_edges(self, fake_repo):
        base, nodes = fake_repo
        cfg = ed.load_edge_config()
        cfg["derivations"]["python_import_ast"] = False
        _, edges = ed.derive(nodes, base=base, cfg=cfg)
        assert not _edges_by_derivation(edges, "python_import_ast")
        assert _edges_by_derivation(edges, "ddl_create_table")

    def test_globally_disabling_edges_returns_none(self, fake_repo):
        base, nodes = fake_repo
        cfg = ed.load_edge_config()
        cfg["enabled"] = False
        _, edges = ed.derive(nodes, base=base, cfg=cfg)
        assert edges == []

    def test_per_module_import_cap_is_enforced(self, tmp_path):
        base = tmp_path
        for i in range(6):
            _write(base / "tools" / f"dep{i}.py", f'"""Dep {i}."""\n')
        imports = "\n".join(f"from tools.dep{i} import thing" for i in range(6))
        _write(base / "tools" / "hub.py", f'"""Hub."""\n{imports}\n')
        nodes = [_tool("tools/hub.py", "Hub")] + [
            _tool(f"tools/dep{i}.py", f"Dep {i}") for i in range(6)
        ]
        cfg = ed.load_edge_config()
        cfg["limits"]["max_imports_per_module"] = 2
        _, edges = ed.derive(nodes, base=base, cfg=cfg)
        assert len(_edges_by_derivation(edges, "python_import_ast")) == 2


# ---------------------------------------------------------------------------
# Query API — "a component's direct dependents can be queried"
# ---------------------------------------------------------------------------


class _FakeCursorResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Minimal stand-in for a StorageConnection over kg_nodes / kg_edges."""

    def __init__(self, nodes, edges):
        self._nodes = nodes
        self._edges = edges

    def execute(self, sql, params=()):
        return _FakeCursorResult(self._edges if "kg_edges" in sql else self._nodes)

    def close(self):
        pass


def _graph_conn(fake_repo):
    base, nodes = fake_repo
    all_nodes, edges = ed.derive(nodes, base=base)
    node_rows = [
        {
            "id": n.id,
            "label": n.label,
            "entity_type": n.entity_type,
            "properties": n.to_properties_json(),
        }
        for n in all_nodes
    ]
    edge_rows = [
        {
            "id": e.id,
            "source_id": e.source_id,
            "target_id": e.target_id,
            "relationship": e.relationship,
            "weight": e.weight,
            "properties": e.to_properties_json(),
        }
        for e in edges
    ]
    return _FakeConn(node_rows, edge_rows)


class TestQueryAPI:
    def test_direct_dependents_of_a_file(self, fake_repo):
        conn = _graph_conn(fake_repo)
        result = ed.get_dependents("tools/db/storage.py", depth=1, conn=conn)
        assert not result.get("error")
        assert result["direction"] == "dependents"
        labels = {r["label"] for r in result["direct"]}
        assert "Widget Engine" in labels
        for row in result["direct"]:
            assert row["derivation"] in ed.DERIVATIONS
            assert row["depth"] == 1

    def test_transitive_depth_reaches_the_second_hop(self, fake_repo):
        conn = _graph_conn(fake_repo)
        direct = ed.get_dependents("tools/db/storage.py", depth=1, conn=conn)
        deep = ed.get_dependents("tools/db/storage.py", depth=2, conn=conn)
        assert deep["total_count"] > direct["total_count"]
        assert any(r["depth"] == 2 for r in deep["results"])
        # blueprint imports engine which imports storage
        assert "Widget Blueprint" in {r["label"] for r in deep["results"]}

    def test_dependencies_is_the_inverse_direction(self, fake_repo):
        conn = _graph_conn(fake_repo)
        result = ed.get_dependencies("tools/widgets/engine.py", depth=1, conn=conn)
        labels = {r["label"] for r in result["direct"]}
        assert "Storage" in labels
        assert "widget_records" in labels

    def test_lookup_by_node_id_and_by_label(self, fake_repo):
        conn = _graph_conn(fake_repo)
        by_id = ed.get_dependents(ci._node_id("tool", "tools/db/storage.py"), conn=conn)
        by_label = ed.get_dependents("Storage", conn=conn)
        assert by_id["node"]["id"] == by_label["node"]["id"]

    def test_unknown_reference_reports_an_error_not_an_empty_success(self, fake_repo):
        conn = _graph_conn(fake_repo)
        result = ed.get_dependents("tools/does/not/exist.py", conn=conn)
        assert "no node matches" in result["error"]
        assert result["results"] == []

    def test_mechanical_only_drops_the_keyword_edges(self, tmp_path):
        base = tmp_path
        (base / "tools" / "network_canvas").mkdir(parents=True)
        _write(base / "goals" / "n.md", "# network_canvas workflow\n")
        nodes = [
            ci.Node(
                id=ci._node_id("canvas_module", "tools/network_canvas"),
                label="network_canvas",
                entity_type="canvas_module",
                file_path="tools/network_canvas",
            ),
            ci.Node(
                id=ci._node_id("goal", "goals/n.md"),
                label="network_canvas workflow",
                entity_type="goal",
                file_path="goals/n.md",
            ),
        ]
        all_nodes, edges = ed.derive(nodes, base=base)
        conn = _FakeConn(
            [
                {
                    "id": n.id,
                    "label": n.label,
                    "entity_type": n.entity_type,
                    "properties": n.to_properties_json(),
                }
                for n in all_nodes
            ],
            [
                {
                    "id": e.id,
                    "source_id": e.source_id,
                    "target_id": e.target_id,
                    "relationship": e.relationship,
                    "weight": e.weight,
                    "properties": e.to_properties_json(),
                }
                for e in edges
            ],
        )
        loose = ed.get_dependents("goals/n.md", conn=conn)
        strict = ed.get_dependents("goals/n.md", mechanical_only=True, conn=conn)
        assert loose["direct_count"] == 1
        assert strict["direct_count"] == 0

    def test_edge_stats_break_down_by_derivation(self, fake_repo):
        conn = _graph_conn(fake_repo)
        stats = ed.get_edge_stats(conn=conn)
        assert stats["edges"] > 0
        assert stats["mechanical"] + stats["inferred"] == stats["edges"]
        assert "python_import_ast" in stats["by_derivation"]


# ---------------------------------------------------------------------------
# Live repo — the regression this task exists to prevent
# ---------------------------------------------------------------------------


@pytest.mark.timeout(600)
class TestLiveRepo:
    """The bug was 'the graph has 2,432 nodes and 0 edges'. Guard the floor.

    Deriving over the real tree parses every file-backed Python node (~2,300
    files) plus 380 migrations, which runs well past the 30s project-wide
    per-test timeout on a cold filesystem cache — hence the explicit override.
    The fixture is class-scoped so that cost is paid once, not per assertion.
    """

    @pytest.fixture(scope="class")
    def live(self):
        nodes = ci.collect_nodes(ci.BASE_DIR)
        return ed.derive(nodes, base=ci.BASE_DIR)

    def test_the_graph_is_not_edgeless(self, live):
        _, edges = live
        assert len(edges) > 1000, (
            "the self-awareness graph regressed to a node bag — "
            f"only {len(edges)} edges derived"
        )

    def test_every_mechanical_derivation_fires_on_the_real_repo(self, live):
        _, edges = live
        fired = set(ed.derivation_summary(edges))
        mechanical = {k for k, v in ed.DERIVATIONS.items() if v["mechanical"]}
        assert mechanical <= fired, f"silent derivations: {sorted(mechanical - fired)}"

    def test_every_live_edge_carries_provenance(self, live):
        _, edges = live
        for edge in edges:
            assert edge.properties.get("derivation") in ed.DERIVATIONS
            assert "mechanical" in edge.properties

    def test_edge_endpoints_all_exist_in_the_node_set(self, live):
        all_nodes, edges = live
        ids = {n.id for n in all_nodes}
        for edge in edges:
            assert edge.source_id in ids
            assert edge.target_id in ids

    def test_derived_endpoint_node_types_are_present(self, live):
        all_nodes, _ = live
        types = {n.entity_type for n in all_nodes}
        for expected in ("db_table", "route", "migration", "component", "iqe_collection"):
            assert expected in types, expected

    def test_storage_layer_has_a_large_blast_radius(self, live):
        """tools/db/storage.py is imported everywhere; if it shows 0 dependents
        the import resolver has broken."""
        all_nodes, edges = live
        storage = [
            n for n in all_nodes if ed._norm(n.file_path) == "tools/db/storage.py"
        ]
        assert storage, "tools/db/storage.py is not in the graph"
        inbound = [e for e in edges if e.target_id == storage[0].id]
        assert len(inbound) > 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
