"""Tests for tools/lint/pg_portability_linter.py.

Core acceptance (pgp-tx-03):
  * catches a seeded ``json_each`` in a runtime module
  * ignores a seeded ``init_db.py`` SQLite branch

Plus coverage for the other detected patterns, comment/docstring immunity,
inline exemptions, path exclusions, and the baseline allowlist + gate exit code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.lint import pg_portability_linter as lint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project(tmp_path: Path) -> Path:
    """Create a clean fake project root with a tools/ tree."""
    root = tmp_path / "proj"
    (root / "tools" / "foo").mkdir(parents=True)
    return root


def _write(root: Path, rel: str, body: str) -> Path:
    fp = root / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(body, encoding="utf-8")
    return fp


def _patterns(findings: list[dict]) -> set[str]:
    return {f["pattern"] for f in findings}


# ---------------------------------------------------------------------------
# Acceptance: catch json_each, ignore init_db SQLite branch
# ---------------------------------------------------------------------------

def test_catches_seeded_json_each(tmp_path: Path):
    root = _project(tmp_path)
    _write(
        root,
        "tools/foo/runtime_mod.py",
        'def q(conn):\n'
        '    return conn.execute(\n'
        '        "SELECT value FROM json_each(t.tags) WHERE x = ?"\n'
        '    ).fetchall()\n',
    )
    findings = lint.scan_tree(root)
    lint._relativize(findings, root)
    highs = [f for f in findings if f["severity"] == "high"]
    assert any(f["pattern"] == "json_each" for f in highs)
    je = next(f for f in highs if f["pattern"] == "json_each")
    assert je["file"] == "tools/foo/runtime_mod.py"
    assert je["severity"] == "high"
    assert "json_each" in je["fix"]


def test_ignores_init_db_sqlite_branch(tmp_path: Path):
    """A SQLite branch in init_db.py is infra — never flagged."""
    root = _project(tmp_path)
    _write(
        root,
        "tools/foo/init_db.py",
        'def init(conn, is_pg):\n'
        '    if not is_pg:\n'
        '        conn.execute("SELECT json_each(tags) FROM t")\n'
        '        conn.execute("PRAGMA foreign_keys=ON")\n',
    )
    assert lint.is_excluded(root / "tools" / "foo" / "init_db.py") is True
    findings = lint.scan_tree(root)
    assert findings == []


def test_init_db_alongside_runtime_only_runtime_flagged(tmp_path: Path):
    """Mixed tree: init_db.py skipped, sibling runtime module still scanned."""
    root = _project(tmp_path)
    _write(root, "tools/foo/init_db.py", 'x = "SELECT json_each(a) FROM t"\n')
    _write(root, "tools/foo/service.py", 'y = "SELECT json_each(a) FROM t"\n')
    findings = lint._relativize(lint.scan_tree(root), root)
    files = {f["file"] for f in findings}
    assert "tools/foo/service.py" in files
    assert "tools/foo/init_db.py" not in files


# ---------------------------------------------------------------------------
# Other high-severity patterns
# ---------------------------------------------------------------------------

def test_nested_json_array_length_extract_is_high(tmp_path: Path):
    root = _project(tmp_path)
    fp = _write(
        root,
        "tools/foo/net.py",
        'sql = "SELECT json_array_length(json_extract(g,\'$.nodes\')) FROM t"\n',
    )
    findings = lint.scan_file(fp)
    pats = _patterns(findings)
    assert "json_array_length(json_extract(...))" in pats
    nested = next(
        f for f in findings if f["pattern"] == "json_array_length(json_extract(...))"
    )
    assert nested["severity"] == "high"
    # The standalone json_extract / json_array_length matches that are *part of*
    # the nested form must be suppressed (no double-reporting).
    assert "json_extract" not in pats
    assert "json_array_length" not in pats


def test_sqlite3_connect_runtime_is_high(tmp_path: Path):
    root = _project(tmp_path)
    fp = _write(
        root,
        "tools/foo/store.py",
        "import sqlite3\n"
        "def get():\n"
        "    return sqlite3.connect('/tmp/x.db')\n",
    )
    findings = lint.scan_file(fp)
    s3 = [f for f in findings if f["pattern"] == "sqlite3.connect"]
    assert s3 and s3[0]["severity"] == "high"


# ---------------------------------------------------------------------------
# Medium-severity patterns
# ---------------------------------------------------------------------------

def test_standalone_json_extract_is_medium(tmp_path: Path):
    root = _project(tmp_path)
    fp = _write(
        root,
        "tools/foo/m.py",
        'sql = "SELECT json_extract(meta, \'$.k\') FROM t"\n',
    )
    findings = lint.scan_file(fp)
    je = [f for f in findings if f["pattern"] == "json_extract"]
    assert je and je[0]["severity"] == "medium"


def test_pragma_is_medium(tmp_path: Path):
    root = _project(tmp_path)
    fp = _write(root, "tools/foo/p.py", 'conn.execute("PRAGMA table_info(widgets)")\n')
    findings = lint.scan_file(fp)
    pr = [f for f in findings if f["pattern"] == "pragma"]
    assert pr and pr[0]["severity"] == "medium"


# ---------------------------------------------------------------------------
# Comments / docstrings are not flagged (AST-based scan)
# ---------------------------------------------------------------------------

def test_comments_and_docstrings_not_flagged(tmp_path: Path):
    root = _project(tmp_path)
    fp = _write(
        root,
        "tools/foo/doc.py",
        '"""Replaces MAX(json_extract(metadata, \'$.x\')) with a Python helper.\n'
        '\n'
        'Also avoids json_each(col) and PRAGMA table_info().\n'
        '"""\n'
        '# legacy used json_array_length(json_extract(g, \'$.nodes\'))\n'
        'def f():\n'
        '    return 1  # no real SQL here\n',
    )
    findings = lint.scan_file(fp)
    assert findings == []


# ---------------------------------------------------------------------------
# Inline exemption
# ---------------------------------------------------------------------------

def test_inline_exemption_comment_skips(tmp_path: Path):
    root = _project(tmp_path)
    fp = _write(
        root,
        "tools/foo/guarded.py",
        'def q(conn, is_pg):\n'
        '    if is_pg:\n'
        '        return conn.execute("SELECT g::jsonb->>\'node_id\' FROM t")\n'
        '    return conn.execute("SELECT json_extract(g, \'$.node_id\') FROM t")  # pg-ok\n',
    )
    findings = lint.scan_file(fp)
    assert findings == []


# ---------------------------------------------------------------------------
# Path exclusions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rel",
    [
        "tools/db/seeds/seed_widgets.py",
        "tools/db/migrations/099_add_thing/up.py",
        "tools/db/migrate_add_cols.py",
        "tools/db/schema/loader.py",
        "tools/db/storage.py",
        "tools/lint/pg_portability_linter.py",
        "tests/test_something.py",
        "tools/foo/init_db.py",
    ],
)
def test_excluded_paths(tmp_path: Path, rel: str):
    root = _project(tmp_path)
    fp = root / rel
    assert lint.is_excluded(fp) is True


def test_runtime_path_not_excluded(tmp_path: Path):
    root = _project(tmp_path)
    assert lint.is_excluded(root / "tools" / "network" / "blueprint.py") is False


# ---------------------------------------------------------------------------
# Baseline allowlist + gate exit code
# ---------------------------------------------------------------------------

def test_baseline_roundtrip_suppresses_high(tmp_path: Path):
    root = _project(tmp_path)
    _write(root, "tools/foo/a.py", 'x = "SELECT json_each(t) FROM w"\n')
    findings = lint._relativize(lint.scan_tree(root), root)

    # No baseline -> the json_each is a NEW high finding.
    new_high, baselined = lint.partition(findings, set())
    assert len(new_high) == 1 and not baselined

    # Snapshot to a baseline, reload, and confirm it is now suppressed.
    baseline_path = tmp_path / "baseline.json"
    n = lint.write_baseline(baseline_path, findings)
    assert n == 1
    loaded = lint.load_baseline(baseline_path)
    new_high2, baselined2 = lint.partition(findings, loaded)
    assert not new_high2 and len(baselined2) == 1


def test_main_exit_nonzero_on_new_high(tmp_path: Path, capsys):
    root = _project(tmp_path)
    _write(root, "tools/foo/a.py", 'x = "SELECT json_each(t) FROM w"\n')
    baseline_path = tmp_path / "bl.json"

    # New high, empty baseline -> exit 1.
    rc = lint.main(["--path", str(root), "--baseline", str(baseline_path), "--json"])
    assert rc == 1

    # Snapshot, then re-run -> exit 0.
    rc_write = lint.main(["--path", str(root), "--baseline", str(baseline_path), "--write-baseline"])
    assert rc_write == 0
    capsys.readouterr()
    rc2 = lint.main(["--path", str(root), "--baseline", str(baseline_path), "--json"])
    assert rc2 == 0


def test_main_passes_clean_tree(tmp_path: Path):
    root = _project(tmp_path)
    _write(root, "tools/foo/clean.py", 'x = "SELECT id FROM widgets WHERE id = ?"\n')
    rc = lint.main(["--path", str(root), "--baseline", str(tmp_path / "none.json"), "--json"])
    assert rc == 0


def test_fingerprint_is_line_drift_stable(tmp_path: Path):
    """Adding a blank line above a finding must not change its baseline key."""
    root = _project(tmp_path)
    fp = _write(root, "tools/foo/d.py", 'x = "SELECT json_each(t) FROM w"\n')
    f1 = lint._relativize(lint.scan_file(fp), root)[0]
    fp.write_text('\n\nx = "SELECT json_each(t) FROM w"\n', encoding="utf-8")
    f2 = lint._relativize(lint.scan_file(fp), root)[0]
    assert f1["line"] != f2["line"]
    assert lint.fingerprint(f1) == lint.fingerprint(f2)
