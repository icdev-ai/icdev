# CUI // SP-CTI
"""Phase 1d unit tests for tools/awareness/component_indexer.py.

Tests each parser in isolation with synthetic fixtures, then
verifies the end-to-end scan produces nodes for every major entity
type present in the live ICDEV repo.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.awareness import component_indexer as ci  # noqa: E402
from tools.awareness import enablement as en  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_caches():
    en._reset_caches_for_test()
    yield
    en._reset_caches_for_test()


def _mk_repo(tmp_path: Path) -> Path:
    """Create a minimal fake repo layout for parser tests."""
    (tmp_path / ".agents" / "skills" / "demo-skill").mkdir(parents=True)
    (tmp_path / "tools" / "mcp").mkdir(parents=True)
    (tmp_path / "tools" / "network_canvas" / "db").mkdir(parents=True)
    (tmp_path / "tools" / "genesis" / "reflexes").mkdir(parents=True)
    (tmp_path / "goals").mkdir()
    return tmp_path


class TestNodeAndEdgeShapes:
    def test_node_properties_json_round_trip(self):
        n = ci.Node(
            id="id-1",
            label="Foo",
            entity_type="tool",
            description="A test tool",
            file_path="tools/foo.py",
            enabled=True,
            extra={"category": "Test"},
        )
        payload = json.loads(n.to_properties_json())
        assert payload["description"] == "A test tool"
        assert payload["file_path"] == "tools/foo.py"
        assert payload["enabled"] is True
        assert payload["category"] == "Test"

    def test_node_id_is_deterministic(self):
        a = ci._node_id("tool", "tools/foo.py")
        b = ci._node_id("tool", "tools/foo.py")
        c = ci._node_id("tool", "tools/bar.py")
        d = ci._node_id("skill", "tools/foo.py")
        assert a == b
        assert a != c
        assert a != d
        assert a.startswith("icdev-tool-")

    def test_truncate_shortens_long_text(self):
        long_text = "a" * 700
        result = ci._truncate(long_text, limit=100)
        assert len(result) == 100
        assert result.endswith("...")

    def test_edge_id_is_deterministic(self):
        e1 = ci._edge_id("s1", "t1", "uses_tool")
        e2 = ci._edge_id("s1", "t1", "uses_tool")
        e3 = ci._edge_id("s1", "t1", "executes")
        assert e1 == e2
        assert e1 != e3


class TestParseSkill:
    def test_parses_frontmatter_name_and_description(self, tmp_path):
        base = _mk_repo(tmp_path)
        skill_md = base / ".agents" / "skills" / "demo-skill" / "SKILL.md"
        skill_md.write_text(
            textwrap.dedent(
                """
                ---
                name: demo-skill
                description: Does demo things for ICDEV
                model: sonnet
                ---
                # Demo Skill

                This is the body of the skill.
                """
            ).lstrip("\n"),
            encoding="utf-8",
        )
        node = ci._parse_skill(skill_md, base)
        assert node is not None
        assert node.entity_type == "skill"
        assert node.label == "demo-skill"
        assert "demo things" in node.description
        assert node.file_path.endswith("SKILL.md")
        assert node.extra["model"] == "sonnet"

    def test_missing_frontmatter_falls_back_to_dir_name(self, tmp_path):
        base = _mk_repo(tmp_path)
        skill_md = base / ".agents" / "skills" / "demo-skill" / "SKILL.md"
        skill_md.write_text("# just a heading\nno frontmatter here\n", encoding="utf-8")
        node = ci._parse_skill(skill_md, base)
        assert node is not None
        assert node.label == "demo-skill"  # falls back to dir name
        assert "heading" in node.description or "frontmatter" in node.description


class TestParseMcpServer:
    def test_extracts_class_docstring_and_tool_count(self, tmp_path):
        base = _mk_repo(tmp_path)
        server = base / "tools" / "mcp" / "demo_server.py"
        server.write_text(
            textwrap.dedent(
                '''
                """Module docstring — demo MCP server."""

                class DemoServer:
                    """Serves demo tools via MCP."""

                    @mcp_tool
                    def tool_a(self): pass

                    @mcp_tool
                    def tool_b(self): pass
                '''
            ).lstrip("\n"),
            encoding="utf-8",
        )
        node = ci._parse_mcp_server(server, base)
        assert node is not None
        assert node.entity_type == "mcp_server"
        assert node.label == "DemoServer"
        assert "demo" in node.description.lower()
        assert node.extra["tool_count"] == 2

    def test_handles_empty_python_file(self, tmp_path):
        base = _mk_repo(tmp_path)
        server = base / "tools" / "mcp" / "empty_server.py"
        server.write_text("", encoding="utf-8")
        node = ci._parse_mcp_server(server, base)
        assert node is not None
        assert node.entity_type == "mcp_server"


class TestParseCanvasModule:
    def test_extracts_docstring_and_routes(self, tmp_path):
        base = _mk_repo(tmp_path)
        canvas = base / "tools" / "network_canvas"
        (canvas / "agent.py").write_text(
            '"""Network Design Canvas agent — NDC orchestration."""\n',
            encoding="utf-8",
        )
        (canvas / "blueprint.py").write_text(
            textwrap.dedent(
                """
                from flask import Blueprint
                bp = Blueprint("nc", __name__)

                @bp.route("/network/topologies")
                def list_topologies(): pass

                @bp.route("/network/api/stitch", methods=["POST"])
                def stitch(): pass
                """
            ).lstrip("\n"),
            encoding="utf-8",
        )
        (canvas / "db" / "init_db.py").write_text(
            "CREATE TABLE IF NOT EXISTS nc_projects (id TEXT);\n"
            "CREATE TABLE topologies (id TEXT);\n",
            encoding="utf-8",
        )
        node = ci._parse_canvas_module(canvas, base)
        assert node is not None
        assert node.entity_type == "canvas_module"
        assert node.label == "network_canvas"
        assert "Network Design Canvas" in node.description
        assert "/network/topologies" in node.extra["blueprint_routes"]
        assert "/network/api/stitch" in node.extra["blueprint_routes"]
        assert "nc_projects" in node.extra["db_tables"]
        assert "topologies" in node.extra["db_tables"]


class TestParseGoal:
    def test_parses_title_and_body(self, tmp_path):
        base = _mk_repo(tmp_path)
        goal_md = base / "goals" / "demo_goal.md"
        goal_md.write_text(
            textwrap.dedent(
                """
                # Demo Goal

                This is the first paragraph that should become the description.

                ## Details
                Lots of stuff here.
                """
            ).lstrip("\n"),
            encoding="utf-8",
        )
        node = ci._parse_goal(goal_md, base)
        assert node is not None
        assert node.entity_type == "goal"
        assert node.label == "Demo Goal"
        assert "first paragraph" in node.description


class TestParseToolsManifest:
    def test_parses_table_rows(self, tmp_path):
        base = _mk_repo(tmp_path)
        (base / "tools").mkdir(exist_ok=True)
        manifest = base / "tools" / "manifest.md"
        manifest.write_text(
            textwrap.dedent(
                """
                # Tools Manifest

                ## Memory System
                | Tool | File | Description | Input | Output |
                |------|------|-------------|-------|--------|
                | Foo Tool | tools/foo/foo.py | Does foo | --in | --out |
                | Bar Tool | tools/foo/bar.py | Does bar | x | y |

                ## Database
                | Tool | File | Description | Input | Output |
                |------|------|-------------|-------|--------|
                | Baz Tool | tools/db/baz.py | DB stuff | | |
                """
            ).lstrip("\n"),
            encoding="utf-8",
        )
        nodes = ci._parse_tools_manifest(base)
        labels = {n.label for n in nodes}
        assert "Foo Tool" in labels
        assert "Bar Tool" in labels
        assert "Baz Tool" in labels
        foo = next(n for n in nodes if n.label == "Foo Tool")
        assert foo.file_path == "tools/foo/foo.py"
        assert foo.description == "Does foo"
        assert foo.extra["category"] == "Memory System"
        baz = next(n for n in nodes if n.label == "Baz Tool")
        assert baz.extra["category"] == "Database"


class TestParseReflex:
    def test_parses_docstring_and_run_presence(self, tmp_path):
        base = _mk_repo(tmp_path)
        reflex = base / "tools" / "genesis" / "reflexes" / "demo.py"
        reflex.write_text(
            textwrap.dedent(
                '''
                """Demo reflex — fires on schedule to do demo work."""

                def run(config, trust):
                    return {"ok": True}
                '''
            ).lstrip("\n"),
            encoding="utf-8",
        )
        node = ci._parse_reflex(reflex, base)
        assert node is not None
        assert node.entity_type == "reflex"
        assert node.label == "demo"
        assert "Demo reflex" in node.description
        assert node.extra["has_run"] is True


class TestCollectNodesFakeRepo:
    """End-to-end collect_nodes against a fake repo."""

    def test_produces_multiple_entity_types(self, tmp_path):
        base = _mk_repo(tmp_path)

        # skill
        skill_md = base / ".agents" / "skills" / "demo-skill" / "SKILL.md"
        skill_md.write_text(
            "---\nname: demo\ndescription: Demo skill\n---\nBody.\n", encoding="utf-8"
        )
        # mcp server
        (base / "tools" / "mcp" / "demo_server.py").write_text(
            '"""Demo server."""\nclass DemoServer: """Demo."""\n', encoding="utf-8"
        )
        # goal
        (base / "goals" / "demo.md").write_text(
            "# Demo Goal\n\nIntro paragraph.\n", encoding="utf-8"
        )
        # reflex
        (base / "tools" / "genesis" / "reflexes" / "demo.py").write_text(
            '"""Demo reflex."""\ndef run(c, t): pass\n', encoding="utf-8"
        )
        # canvas
        canvas = base / "tools" / "network_canvas"
        (canvas / "agent.py").write_text('"""NDC agent."""\n', encoding="utf-8")
        # tools manifest
        (base / "tools" / "manifest.md").write_text(
            "# Tools Manifest\n\n## Group\n"
            "| Tool | File | Description | Input | Output |\n"
            "|------|------|-------------|-------|--------|\n"
            "| X | tools/x.py | does x | a | b |\n",
            encoding="utf-8",
        )

        nodes = ci.collect_nodes(base)
        types = {n.entity_type for n in nodes}
        assert "skill" in types
        assert "mcp_server" in types
        assert "goal" in types
        assert "reflex" in types
        assert "canvas_module" in types
        assert "tool" in types

        # Every node carries enablement state
        for n in nodes:
            assert isinstance(n.enabled, bool)

    def test_derive_edges_produces_canvas_to_goal(self, tmp_path):
        base = _mk_repo(tmp_path)
        (base / "tools" / "network_canvas" / "agent.py").write_text(
            '"""NDC agent."""\n', encoding="utf-8"
        )
        # Goal title that mentions "network_canvas" keyword
        (base / "goals" / "network.md").write_text(
            "# network_canvas workflow\n\nStuff.\n", encoding="utf-8"
        )
        nodes = ci.collect_nodes(base)
        edges = ci.derive_edges(nodes)
        # Should have at least one referenced_by_goal edge
        assert any(e.relationship == "referenced_by_goal" for e in edges), edges


class TestLiveRepoScan:
    """Parsing against the REAL ICDEV repo. Must find at least the
    expected minimum of each entity type."""

    def test_live_collect_has_all_types(self):
        nodes = ci.collect_nodes(ci.BASE_DIR)
        types = {n.entity_type for n in nodes}
        assert "skill" in types
        assert "mcp_server" in types
        assert "canvas_module" in types
        assert "goal" in types
        assert "tool" in types
        assert "reflex" in types

        counts = {}
        for n in nodes:
            counts[n.entity_type] = counts.get(n.entity_type, 0) + 1
        assert counts.get("skill", 0) >= 10
        assert counts.get("mcp_server", 0) >= 5
        assert counts.get("canvas_module", 0) >= 5
        assert counts.get("goal", 0) >= 20
        assert counts.get("tool", 0) >= 100
        assert counts.get("reflex", 0) >= 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
