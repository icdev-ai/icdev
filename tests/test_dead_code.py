#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the dead-code & dependency-graph lens (CodeLens CL-1 + CL-2).

Covers: file discovery, module-name resolution, AST fact extraction,
dead-symbol detection (incl. decorator/__all__/entrypoint exclusions),
orphan-file detection, unused-dependency detection, import-graph build,
Tarjan cycle detection, and the run_scan orchestrator + output contract.
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.code_intelligence.dead_code import (  # noqa: E402
    analyze_file,
    build_import_graph,
    find_cycles,
    find_dead_symbols,
    find_orphan_files,
    find_unused_dependencies,
    iter_python_files,
    module_name_for,
    run_scan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(root: Path, rel: str, body: str) -> Path:
    fp = root / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(textwrap.dedent(body), encoding="utf-8")
    return fp


def _facts(root: Path):
    return [a for a in (analyze_file(fp, root) for fp in iter_python_files(root)) if a]


# ---------------------------------------------------------------------------
# File discovery + module naming
# ---------------------------------------------------------------------------


def test_iter_skips_excluded_dirs(tmp_path):
    _write(tmp_path, "tools/a.py", "x = 1\n")
    _write(tmp_path, "node_modules/b.py", "y = 2\n")
    _write(tmp_path, "__pycache__/c.py", "z = 3\n")
    found = {p.name for p in iter_python_files(tmp_path)}
    assert found == {"a.py"}


def test_module_name_for_regular_and_init(tmp_path):
    f1 = _write(tmp_path, "tools/foo/bar.py", "")
    f2 = _write(tmp_path, "tools/foo/__init__.py", "")
    assert module_name_for(f1, tmp_path) == "tools.foo.bar"
    assert module_name_for(f2, tmp_path) == "tools.foo"


# ---------------------------------------------------------------------------
# Dead symbol detection
# ---------------------------------------------------------------------------


def test_dead_function_flagged(tmp_path):
    _write(tmp_path, "tools/m.py", """
        def used():
            return 1

        def dead_one():
            return 2

        print(used())
    """)
    findings = find_dead_symbols(_facts(tmp_path))
    names = {f["name"] for f in findings}
    assert "dead_one" in names
    assert "used" not in names


def test_cross_file_reference_is_not_dead(tmp_path):
    _write(tmp_path, "tools/a.py", """
        def helper():
            return 1
    """)
    _write(tmp_path, "tools/b.py", """
        from tools.a import helper
        print(helper())
    """)
    findings = find_dead_symbols(_facts(tmp_path))
    assert "helper" not in {f["name"] for f in findings}


def test_decorated_def_excluded(tmp_path):
    _write(tmp_path, "tools/routes.py", """
        bp = object()

        @bp.route("/x")
        def view():
            return "ok"
    """)
    findings = find_dead_symbols(_facts(tmp_path))
    assert "view" not in {f["name"] for f in findings}


def test_all_exports_and_entrypoints_excluded(tmp_path):
    _write(tmp_path, "tools/pub.py", """
        __all__ = ["api"]

        def api():
            return 1

        def main():
            return api()
    """)
    findings = find_dead_symbols(_facts(tmp_path))
    assert {f["name"] for f in findings} == set()


def test_dynamic_string_reference_lowers_confidence(tmp_path):
    _write(tmp_path, "tools/dyn.py", """
        def handler():
            return 1

        name = "handler"
        getattr_target = name
    """)
    findings = find_dead_symbols(_facts(tmp_path))
    hit = [f for f in findings if f["name"] == "handler"]
    assert hit and hit[0]["confidence"] == "low"


def test_dunder_methods_not_flagged(tmp_path):
    _write(tmp_path, "tools/c.py", """
        def __getattr__(name):
            return None
    """)
    findings = find_dead_symbols(_facts(tmp_path))
    assert "__getattr__" not in {f["name"] for f in findings}


# ---------------------------------------------------------------------------
# Orphan files
# ---------------------------------------------------------------------------


def test_orphan_file_flagged(tmp_path):
    _write(tmp_path, "tools/island.py", "VALUE = 1\n")
    _write(tmp_path, "tools/hub.py", "import os\nprint(os.getcwd())\n")
    facts = _facts(tmp_path)
    _, adj = build_import_graph(facts)
    orphans = {f["name"] for f in find_orphan_files(facts, adj)}
    assert "tools.island" in orphans
    assert "tools.hub" in orphans  # also orphan: nobody imports it either


def test_imported_file_not_orphan(tmp_path):
    _write(tmp_path, "tools/lib.py", "def f():\n    return 1\n")
    _write(tmp_path, "tools/app.py", "from tools.lib import f\nprint(f())\n")
    facts = _facts(tmp_path)
    _, adj = build_import_graph(facts)
    orphans = {f["name"] for f in find_orphan_files(facts, adj)}
    assert "tools.lib" not in orphans


def test_main_guard_excludes_orphan(tmp_path):
    _write(tmp_path, "tools/script.py", """
        def go():
            return 1

        if __name__ == "__main__":
            go()
    """)
    facts = _facts(tmp_path)
    _, adj = build_import_graph(facts)
    assert "tools.script" not in {f["name"] for f in find_orphan_files(facts, adj)}


def test_init_and_test_files_excluded_from_orphans(tmp_path):
    _write(tmp_path, "tools/pkg/__init__.py", "X = 1\n")
    _write(tmp_path, "tools/test_thing.py", "def test_x():\n    assert True\n")
    facts = _facts(tmp_path)
    _, adj = build_import_graph(facts)
    names = {f["name"] for f in find_orphan_files(facts, adj)}
    assert "tools.pkg" not in names
    assert "tools.test_thing" not in names


# ---------------------------------------------------------------------------
# Unused dependencies
# ---------------------------------------------------------------------------


def test_unused_dependency_flagged(tmp_path):
    _write(tmp_path, "tools/u.py", "import yaml\nprint(yaml)\n")
    req = tmp_path / "requirements.txt"
    req.write_text("pyyaml>=6.0\nflask>=3.0\n", encoding="utf-8")
    findings = find_unused_dependencies(req, _facts(tmp_path))
    names = {f["name"] for f in findings}
    assert "flask" in names      # never imported
    assert "pyyaml" not in names  # maps to imported `yaml`


def test_tool_dep_is_low_confidence(tmp_path):
    _write(tmp_path, "tools/u.py", "x = 1\n")
    req = tmp_path / "requirements.txt"
    req.write_text("ruff>=0.12\n", encoding="utf-8")
    findings = find_unused_dependencies(req, _facts(tmp_path))
    hit = [f for f in findings if f["name"] == "ruff"]
    assert hit and hit[0]["confidence"] == "low"


# ---------------------------------------------------------------------------
# Import graph + cycles
# ---------------------------------------------------------------------------


def test_build_graph_edges(tmp_path):
    _write(tmp_path, "tools/a.py", "from tools.b import g\n")
    _write(tmp_path, "tools/b.py", "def g():\n    return 1\n")
    _, adj = build_import_graph(_facts(tmp_path))
    assert "tools.b" in adj["tools.a"]
    assert adj["tools.b"] == set()


def test_shim_import_normalized(tmp_path):
    # `icdev.tools.b` should resolve to the same node as `tools.b`
    _write(tmp_path, "tools/a.py", "from icdev.tools.b import g\n")
    _write(tmp_path, "tools/b.py", "def g():\n    return 1\n")
    _, adj = build_import_graph(_facts(tmp_path))
    assert "tools.b" in adj["tools.a"]


def test_find_cycles_detects_two_node_cycle():
    adj = {"a": {"b"}, "b": {"a"}, "c": {"a"}}
    cycles = find_cycles(adj)
    assert cycles == [["a", "b"]]


def test_find_cycles_none_when_acyclic():
    adj = {"a": {"b"}, "b": {"c"}, "c": set()}
    assert find_cycles(adj) == []


def test_circular_dependency_end_to_end(tmp_path):
    _write(tmp_path, "tools/x.py", "from tools.y import h\n")
    _write(tmp_path, "tools/y.py", "from tools.x import g\n")
    report = run_scan(project_dir=str(tmp_path / "tools"), base=tmp_path,
                      checks=["circular"])
    circ = [f for f in report["findings"] if f["kind"] == "circular_dependency"]
    assert len(circ) == 1
    assert circ[0]["confidence"] == "high"
    assert report["summary"]["graph"]["cycles"] == 1


# ---------------------------------------------------------------------------
# Orchestrator + output contract
# ---------------------------------------------------------------------------


def test_run_scan_contract(tmp_path):
    _write(tmp_path, "tools/a.py", "def dead():\n    return 1\n")
    req = tmp_path / "requirements.txt"
    req.write_text("flask>=3.0\n", encoding="utf-8")
    report = run_scan(project_dir=str(tmp_path / "tools"), base=tmp_path,
                      req_path=req)
    assert report["tool"] == "dead_code"
    assert set(report["summary"]) >= {"files_scanned", "findings", "by_kind", "graph"}
    for f in report["findings"]:
        assert set(f) == {
            "kind", "name", "file", "line", "confidence",
            "explanation", "suggested_action",
        }
        assert f["confidence"] in {"high", "medium", "low"}


def test_run_scan_deterministic(tmp_path):
    _write(tmp_path, "tools/a.py", "def dead_a():\n    return 1\n")
    _write(tmp_path, "tools/b.py", "def dead_b():\n    return 1\n")
    r1 = run_scan(project_dir=str(tmp_path / "tools"), base=tmp_path, checks=["dead-code"])
    r2 = run_scan(project_dir=str(tmp_path / "tools"), base=tmp_path, checks=["dead-code"])
    assert r1["findings"] == r2["findings"]


def test_check_filter_limits_kinds(tmp_path):
    _write(tmp_path, "tools/a.py", "def dead():\n    return 1\n")
    report = run_scan(project_dir=str(tmp_path / "tools"), base=tmp_path,
                      checks=["circular"])
    assert all(f["kind"] == "circular_dependency" for f in report["findings"])
