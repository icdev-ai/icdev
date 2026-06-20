# CUI // SP-CTI
"""Tests for check_canvas_placeholder_style coherence check.

Builds synthetic tools/ subtrees under tmp_path, points PROJECT_ROOT at them,
runs the check, and asserts expected status + violations.
"""
from __future__ import annotations

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workflow import coherence_checker as cc  # noqa: E402


def _make_repo(tmp_path: pathlib.Path, files: dict) -> pathlib.Path:
    """Write files dict {rel_path: body} under tmp_path/repo and return root."""
    root = tmp_path / "repo"
    tools = root / "tools"
    tools.mkdir(parents=True)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Test 1: clean repo — no ? placeholders → pass
# ---------------------------------------------------------------------------

def test_clean_repo_passes(tmp_path, monkeypatch):
    """A canvas file that uses %s should pass cleanly."""
    repo = _make_repo(tmp_path, {
        "tools/ace/db/init_db.py": (
            "from tools.db.storage import get_canvas_connection\n"
            "def save(name, value):\n"
            "    with get_canvas_connection('ACE_DB') as conn:\n"
            "        conn.execute('INSERT INTO tbl (name, val) VALUES (%s, %s)', (name, value))\n"
        )
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_canvas_placeholder_style()
    assert result.status == "pass", f"Expected pass, got {result.status}: {result.message}"
    assert result.check_id == "canvas_placeholder_style"
    assert result.missing == []


# ---------------------------------------------------------------------------
# Test 2: violation detect — single ? in execute() → fail with file:line
# ---------------------------------------------------------------------------

def test_violation_detected_single_file(tmp_path, monkeypatch):
    """A canvas file with ? placeholder in execute() should fail, reporting file:line."""
    repo = _make_repo(tmp_path, {
        "tools/ace/canvas.py": (
            "from tools.db.storage import get_canvas_connection\n"
            "def lookup(worker_id):\n"
            "    with get_canvas_connection('ACE_DB') as conn:\n"
            "        return conn.execute('SELECT * FROM coworkers WHERE id = ?', (worker_id,))\n"
        )
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_canvas_placeholder_style()
    assert result.status == "fail", f"Expected fail, got {result.status}: {result.message}"
    assert len(result.missing) == 1
    violation = result.missing[0]
    assert "canvas.py" in violation
    assert ":4:" in violation or ":4 " in violation.replace(":4:", ":4 ")
    assert "?" in result.message or "%s" in result.message


# ---------------------------------------------------------------------------
# Test 3: multi-file — only files with ? violations are reported
# ---------------------------------------------------------------------------

def test_multi_file_only_violators_flagged(tmp_path, monkeypatch):
    """Multiple canvas files: only files containing ? execute() calls appear in missing."""
    repo = _make_repo(tmp_path, {
        # Clean file — uses %s
        "tools/ace/clean.py": (
            "from tools.db.storage import get_canvas_connection\n"
            "def insert(val):\n"
            "    with get_canvas_connection('ACE_DB') as conn:\n"
            "        conn.execute('INSERT INTO t VALUES (%s)', (val,))\n"
        ),
        # Violating file — uses ?
        "tools/ace/dirty.py": (
            "from tools.db.storage import get_canvas_connection\n"
            "def fetch(key):\n"
            "    with get_canvas_connection('ACE_DB') as conn:\n"
            "        return conn.execute('SELECT val FROM t WHERE k = ?', (key,))\n"
        ),
        # Unrelated file — no canvas connection import → ignored
        "tools/other/unrelated.py": (
            "import sqlite3\n"
            "def raw(key):\n"
            "    conn = sqlite3.connect(':memory:')\n"
            "    return conn.execute('SELECT * FROM t WHERE k = ?', (key,))\n"
        ),
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_canvas_placeholder_style()
    assert result.status == "fail"
    # Only dirty.py should appear
    assert len(result.missing) == 1
    assert "dirty.py" in result.missing[0]
    assert not any("clean.py" in v for v in result.missing)
    assert not any("unrelated.py" in v for v in result.missing)


# ---------------------------------------------------------------------------
# Test 4: changed_files scoping — restrict scan to changed file list
# ---------------------------------------------------------------------------

def test_scoped_to_changed_files(tmp_path, monkeypatch):
    """When changed_files is passed, only those files are scanned."""
    repo = _make_repo(tmp_path, {
        "tools/ace/a.py": (
            "from tools.db.storage import get_canvas_connection\n"
            "def q(x):\n"
            "    with get_canvas_connection('DB') as c:\n"
            "        return c.execute('SELECT * FROM t WHERE id = ?', (x,))\n"
        ),
        "tools/ace/b.py": (
            "from tools.db.storage import get_canvas_connection\n"
            "def q(x):\n"
            "    with get_canvas_connection('DB') as c:\n"
            "        return c.execute('SELECT * FROM t WHERE id = ?', (x,))\n"
        ),
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    # Restrict to only a.py — b.py should not appear
    result = cc.check_canvas_placeholder_style(
        changed_files=[repo / "tools" / "ace" / "a.py"]
    )
    assert result.status == "fail"
    assert all("a.py" in v for v in result.missing)
    assert not any("b.py" in v for v in result.missing)


# ---------------------------------------------------------------------------
# Test 5: f-string SQL with ? in constant fragment → flagged
# ---------------------------------------------------------------------------

def test_fstring_question_mark_flagged(tmp_path, monkeypatch):
    """An f-string whose constant SQL fragment contains ? should be flagged."""
    repo = _make_repo(tmp_path, {
        "tools/ace/fstr.py": (
            "from tools.db.storage import get_canvas_connection\n"
            "def q(tbl, val):\n"
            "    with get_canvas_connection('DB') as c:\n"
            "        return c.execute(f'SELECT * FROM {tbl} WHERE id = ?', (val,))\n"
        )
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_canvas_placeholder_style()
    assert result.status == "fail"
    assert any("fstr.py" in v for v in result.missing)


# ---------------------------------------------------------------------------
# Test 6: registry membership and fix tier
# ---------------------------------------------------------------------------

def test_registry_and_fix_tier():
    """canvas_placeholder_style must be in CHECK_REGISTRY with skip fix tier."""
    assert "canvas_placeholder_style" in cc.CHECK_REGISTRY
    assert cc._FIX_REGISTRY.get("canvas_placeholder_style") == "skip"
