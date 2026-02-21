# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.mosa.modular_design_analyzer -- static modularity metrics."""

import pytest

from tools.mosa.modular_design_analyzer import (
    _build_dependency_graph,
    _count_cycles,
    _detect_circular_deps,
    _extract_python_imports,
    _iter_source_files,
    _module_name,
    analyze_modularity,
    evaluate_thresholds,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_py(path: Path, content: str):
    """Write a Python source file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# TestSourceFileIteration
# ---------------------------------------------------------------------------

class TestSourceFileIteration:
    """_iter_source_files: recursive file collection with exclusions."""

    def test_finds_py_files(self, tmp_path):
        _write_py(tmp_path / "a.py", "# a")
        _write_py(tmp_path / "sub" / "b.py", "# b")
        result = _iter_source_files(tmp_path, (".py",))
        names = {p.name for p in result}
        assert "a.py" in names
        assert "b.py" in names

    def test_excludes_venv(self, tmp_path):
        _write_py(tmp_path / "venv" / "pkg.py", "# venv")
        _write_py(tmp_path / "real.py", "# real")
        result = _iter_source_files(tmp_path, (".py",))
        names = {p.name for p in result}
        assert "pkg.py" not in names
        assert "real.py" in names

    def test_excludes_node_modules(self, tmp_path):
        _write_py(tmp_path / "node_modules" / "x.py", "# nm")
        result = _iter_source_files(tmp_path, (".py",))
        assert len(result) == 0

    def test_filters_by_extension(self, tmp_path):
        _write_py(tmp_path / "mod.py", "# py")
        (tmp_path / "data.txt").write_text("text", encoding="utf-8")
        result = _iter_source_files(tmp_path, (".py",))
        assert all(p.suffix == ".py" for p in result)


# ---------------------------------------------------------------------------
# TestPythonImportExtraction
# ---------------------------------------------------------------------------

class TestPythonImportExtraction:
    """_extract_python_imports: AST-based import + ABC detection."""

    def test_simple_import(self, tmp_path):
        f = tmp_path / "mod.py"
        _write_py(f, "import os\nimport json\n")
        imports, has_abstract = _extract_python_imports(f)
        assert "os" in imports
        assert "json" in imports
        assert has_abstract is False

    def test_from_import(self, tmp_path):
        f = tmp_path / "mod.py"
        _write_py(f, "from pathlib import Path\n")
        imports, _ = _extract_python_imports(f)
        assert "pathlib" in imports

    def test_detects_abc_import(self, tmp_path):
        f = tmp_path / "iface.py"
        _write_py(f, "from abc import ABC, abstractmethod\nclass Base(ABC): pass\n")
        _, has_abstract = _extract_python_imports(f)
        assert has_abstract is True

    def test_detects_protocol(self, tmp_path):
        f = tmp_path / "proto.py"
        _write_py(f, "from typing import Protocol\nclass MyProto(Protocol): ...\n")
        _, has_abstract = _extract_python_imports(f)
        assert has_abstract is True

    def test_syntax_error_returns_empty(self, tmp_path):
        f = tmp_path / "bad.py"
        _write_py(f, "def broken(\n")
        imports, has_abstract = _extract_python_imports(f)
        assert imports == set()
        assert has_abstract is False


# ---------------------------------------------------------------------------
# TestDependencyGraph
# ---------------------------------------------------------------------------

class TestDependencyGraph:
    """_build_dependency_graph: module-level graph construction."""

    def test_single_module(self, tmp_path):
        _write_py(tmp_path / "alpha" / "main.py", "import os\n")
        graph, modules, iface_count, total = _build_dependency_graph(tmp_path)
        assert "alpha" in modules
        assert total == 1

    def test_cross_module_edge(self, tmp_path):
        _write_py(tmp_path / "alpha" / "a.py", "import beta\n")
        _write_py(tmp_path / "beta" / "b.py", "x = 1\n")
        graph, modules, _, _ = _build_dependency_graph(tmp_path)
        assert "alpha" in modules
        assert "beta" in modules
        assert "beta" in graph.get("alpha", set())


# ---------------------------------------------------------------------------
# TestCycleDetection
# ---------------------------------------------------------------------------

class TestCycleDetection:
    """_detect_circular_deps / _count_cycles: cycle detection via TopologicalSorter."""

    def test_no_cycles(self):
        graph = {"a": {"b"}, "b": set()}
        modules = {"a", "b"}
        assert _detect_circular_deps(graph, modules) == 0

    def test_self_loop_excluded(self):
        # Self-references are stripped before topological sort
        graph = {"a": {"a"}}
        modules = {"a"}
        assert _detect_circular_deps(graph, modules) == 0

    def test_mutual_cycle_detected(self):
        graph = {"a": {"b"}, "b": {"a"}}
        modules = {"a", "b"}
        result = _detect_circular_deps(graph, modules)
        assert result >= 1


# ---------------------------------------------------------------------------
# TestModularityAnalysis
# ---------------------------------------------------------------------------

class TestModularityAnalysis:
    """analyze_modularity: full metric computation."""

    def test_nonexistent_dir_returns_error(self):
        result = analyze_modularity("/nonexistent/dir/xyz")
        assert "error" in result

    def test_empty_project(self, tmp_path):
        result = analyze_modularity(str(tmp_path))
        assert result["module_count"] == 0
        assert result["total_files_scanned"] == 0

    def test_well_structured_project(self, tmp_path):
        _write_py(tmp_path / "svc" / "api.py",
                   "from abc import ABC\nclass Base(ABC): ...\nimport os\n")
        _write_py(tmp_path / "svc" / "handler.py", "import json\n")
        result = analyze_modularity(str(tmp_path))
        assert result["module_count"] >= 1
        assert result["total_files_scanned"] >= 2
        assert "overall_modularity_score" in result
        assert 0.0 <= result["overall_modularity_score"] <= 1.0
        assert isinstance(result["modules_discovered"], list)

    def test_evaluate_thresholds_pass(self):
        metrics = {
            "coupling_score": 0.1,
            "cohesion_score": 0.9,
            "interface_coverage_pct": 90.0,
            "circular_deps": 0,
            "overall_modularity_score": 0.8,
        }
        gate = evaluate_thresholds(metrics)
        assert gate["passed"] is True
        assert all(c["passed"] for c in gate["checks"])

    def test_evaluate_thresholds_fail_on_coupling(self):
        metrics = {
            "coupling_score": 0.9,
            "cohesion_score": 0.9,
            "interface_coverage_pct": 90.0,
            "circular_deps": 0,
            "overall_modularity_score": 0.8,
        }
        gate = evaluate_thresholds(metrics)
        assert gate["passed"] is False
        coupling_check = [c for c in gate["checks"] if c["metric"] == "coupling_score"][0]
        assert coupling_check["passed"] is False


# CUI // SP-CTI
