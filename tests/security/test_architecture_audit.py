"""OPT-54 — tests for tools/analysis/architecture_audit.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.analysis import architecture_audit as aa  # noqa: E402


def _write_module(dir_path: Path, name: str, src: str) -> Path:
    p = dir_path / name
    p.write_text(src, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Depth classification
# ---------------------------------------------------------------------------
def test_analyze_deep_module(tmp_path):
    """Single public function, 50 impl lines -> deep."""
    body = "\n".join(f"    x = {i}" for i in range(50))
    src = f"def solve(n):\n{body}\n    return x\n"
    p = _write_module(tmp_path, "deep.py", src)
    r = aa._analyze_module(p)
    assert r is not None
    assert r.public_symbols == 1
    assert r.classification == "deep"


def test_analyze_shallow_module(tmp_path):
    """Many public functions each with tiny body -> shallow."""
    src = "\n".join(f"def f{i}():\n    return {i}" for i in range(8))
    p = _write_module(tmp_path, "shallow.py", src)
    r = aa._analyze_module(p)
    assert r is not None
    assert r.public_symbols == 8
    assert r.classification == "shallow"


def test_analyze_private_ignored_in_public_count(tmp_path):
    src = """\
def public_api():
    for i in range(50):
        x = i * 2
    return x


def _helper():
    return 1


def _another_helper():
    return 2
"""
    p = _write_module(tmp_path, "mixed.py", src)
    r = aa._analyze_module(p)
    assert r is not None
    assert r.public_symbols == 1
    assert r.private_symbols == 2


def test_analyze_ignores_comments_and_blank_lines(tmp_path):
    src = """\
# just a comment

# another
def foo():
    return 1
"""
    p = _write_module(tmp_path, "commented.py", src)
    r = aa._analyze_module(p)
    assert r is not None
    assert r.total_lines == 2  # 'def foo' + 'return 1' only


def test_analyze_parse_error_returns_none(tmp_path):
    p = _write_module(tmp_path, "broken.py", "def f(: x\n")
    r = aa._analyze_module(p)
    assert r is None


# ---------------------------------------------------------------------------
# Coupling clusters
# ---------------------------------------------------------------------------
def test_coupling_cluster_requires_mutual_edges(tmp_path, monkeypatch):
    """One-way import should NOT create a cluster."""
    # Simulate a tools/ tree
    tools = tmp_path / "tools"
    (tools / "a").mkdir(parents=True)
    (tools / "b").mkdir(parents=True)
    _write_module(tools / "a", "x.py", "from tools.b.y import thing\n")
    _write_module(tools / "b", "y.py", "thing = 1\n")
    # Reset BASE_DIR to our sandbox
    monkeypatch.setattr(aa, "BASE_DIR", tmp_path)
    paths = list(tools.rglob("*.py"))
    clusters = aa._find_coupling_clusters(paths, min_edges=1)
    assert clusters == []


def test_coupling_cluster_detects_mutual(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    (tools / "a").mkdir(parents=True)
    (tools / "b").mkdir(parents=True)
    _write_module(tools / "a", "x.py", "from tools.b.y import thing\n")
    _write_module(tools / "b", "y.py", "from tools.a.x import gizmo\n")
    monkeypatch.setattr(aa, "BASE_DIR", tmp_path)
    paths = list(tools.rglob("*.py"))
    clusters = aa._find_coupling_clusters(paths, min_edges=2)
    assert len(clusters) == 1
    assert sorted(clusters[0].modules) == ["tools.a", "tools.b"]
    assert clusters[0].edges == 2


# ---------------------------------------------------------------------------
# End-to-end scan + rendering
# ---------------------------------------------------------------------------
def test_scan_path_on_self(monkeypatch):
    """Scanning tools/analysis should find architecture_audit.py as deep."""
    report = aa.scan_path(BASE_DIR / "tools" / "analysis")
    assert report.module_count >= 1
    paths = {r.path for r in report.deep + report.balanced + report.shallow}
    assert any("architecture_audit.py" in p for p in paths)


def test_render_markdown_contains_sections():
    """Shape check on markdown output."""
    report = aa.scan_path(BASE_DIR / "tools" / "compat")
    md = aa.render_markdown(report, top=5)
    assert "# Architecture Audit" in md
    assert "## Summary" in md
    assert "## Shallow-module candidates" in md
    assert "## Deep modules" in md
    assert "## Coupling clusters" in md


def test_render_json_parses_and_has_keys():
    report = aa.scan_path(BASE_DIR / "tools" / "compat")
    out = aa.render_json(report, top=5)
    parsed = json.loads(out)
    for k in ("root", "summary", "shallow_top", "deep_top", "clusters_top"):
        assert k in parsed


def test_summary_counts_are_consistent():
    report = aa.scan_path(BASE_DIR / "tools" / "compat")
    s = report.summary
    assert s["module_count"] == (s["deep_count"] + s["balanced_count"]
                                  + s["shallow_count"])
