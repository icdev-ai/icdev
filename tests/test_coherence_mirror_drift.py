# CUI // SP-CTI
"""Tests for check_mirror_drift coherence check (hcx-ctx-04).

Builds a synthetic tools/<pkg> + icdev/tools/<pkg> pair under tmp_path, points
PROJECT_ROOT at it, restricts the package list to one test package, then asserts
each drift classification: content-differs, tools/-only, icdev/-only, identical
(not flagged), re-export shim (not flagged), and __pycache__/roles exclusion.
"""
from __future__ import annotations

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workflow import coherence_checker as cc  # noqa: E402


SHIM_BODY = (
    "# CUI // SP-CTI\n"
    '"""Backward-compatibility shim — pure re-export of the icdev twin."""\n'
    "from __future__ import annotations\n"
    "from icdev.tools.llm.agent_loop import *  # noqa: F401,F403\n"
)


def _make_pair(tmp_path: pathlib.Path, tools_files: dict, icdev_files: dict) -> pathlib.Path:
    """Write tools/llm/* and icdev/tools/llm/* files, return repo root."""
    root = tmp_path / "repo"
    for rel, body in tools_files.items():
        p = root / "tools" / "llm" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    for rel, body in icdev_files.items():
        p = root / "icdev" / "tools" / "llm" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def _run(tmp_path, monkeypatch, tools_files, icdev_files):
    repo = _make_pair(tmp_path, tools_files, icdev_files)
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    monkeypatch.setattr(cc, "_DEFAULT_MIRROR_DRIFT_PKGS", ("llm",))
    return cc.check_mirror_drift()


# ---------------------------------------------------------------------------
# Identical mirror → pass, WARN-only severity contract
# ---------------------------------------------------------------------------

def test_identical_mirror_passes(tmp_path, monkeypatch):
    body = "def f():\n    return 1\n"
    result = _run(tmp_path, monkeypatch, {"a.py": body}, {"a.py": body})
    assert result.status == "pass", f"Expected pass, got {result.status}: {result.message}"
    assert result.check_id == "mirror_drift"
    assert result.missing == []


# ---------------------------------------------------------------------------
# Content-differs → WARN (never fail), reported with mtime hint
# ---------------------------------------------------------------------------

def test_content_differs_warns(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {"a.py": "def f():\n    return 1\n"},
        {"a.py": "def f():\n    return 2\n"},
    )
    assert result.status == "warn", f"Expected warn, got {result.status}"
    assert any("a.py !=" in m for m in result.missing), result.missing
    # WARN-only contract: this check must never fail the gate.
    assert result.status != "fail"


# ---------------------------------------------------------------------------
# Exists-only-in-one-tree
# ---------------------------------------------------------------------------

def test_tools_only_and_icdev_only(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {"only_tools.py": "x = 1\n"},
        {"only_icdev.py": "y = 2\n"},
    )
    assert result.status == "warn"
    assert any("only_tools.py: no icdev twin" in m for m in result.missing), result.missing
    assert any("only_icdev.py: no tools/ twin" in m for m in result.missing), result.missing


# ---------------------------------------------------------------------------
# Re-export shim → NOT flagged even when byte-content differs
# ---------------------------------------------------------------------------

def test_shim_not_flagged(tmp_path, monkeypatch):
    # tools/ side is a short re-export shim; icdev/ is the full implementation.
    icdev_full = "".join(f"# line {i}\n" for i in range(200)) + "def run():\n    return 1\n"
    result = _run(
        tmp_path,
        monkeypatch,
        {"agent_loop.py": SHIM_BODY},
        {"agent_loop.py": icdev_full},
    )
    assert result.status == "pass", f"Shim must not be flagged: {result.missing}"
    assert result.missing == []


def test_tools_only_shim_not_flagged(tmp_path, monkeypatch):
    # A shim that exists only on the tools/ side is still an intentional divergence.
    result = _run(tmp_path, monkeypatch, {"agent_loop.py": SHIM_BODY}, {})
    assert result.status == "pass", result.missing


# ---------------------------------------------------------------------------
# Exclusions: __pycache__ and roles/ are ignored
# ---------------------------------------------------------------------------

def test_pycache_and_roles_excluded(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {"__pycache__/a.cpython-314.py": "x = 1\n", "roles/persona.py": "p = 1\n"},
        {},  # icdev side missing both — but they must be excluded, so still pass
    )
    assert result.status == "pass", f"Excluded paths leaked: {result.missing}"


# ---------------------------------------------------------------------------
# Combined scene — counts summarized in actual[]
# ---------------------------------------------------------------------------

def test_combined_counts(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "same.py": "S = 1\n",
            "diff.py": "D = 1\n",
            "t_only.py": "T = 1\n",
            "agent_loop.py": SHIM_BODY,
        },
        {
            "same.py": "S = 1\n",
            "diff.py": "D = 2\n",
            "i_only.py": "I = 1\n",
        },
    )
    assert result.status == "warn"
    # 1 content-differ (diff.py), 1 tools/-only (t_only.py), 1 icdev/-only (i_only.py)
    # same.py identical → skipped; agent_loop.py shim → skipped.
    assert len(result.missing) == 3, result.missing
