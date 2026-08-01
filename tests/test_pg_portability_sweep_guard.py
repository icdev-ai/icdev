# CUI // SP-CTI
"""Repo-wide guard for the sqlite_master / PRAGMA introspection sweep (pgrt-sweep-07).

The pgrt sweep replaced ad-hoc, SQLite-only schema-introspection probes in
runtime modules with the backend-aware helpers
``tools.db.storage.table_exists / list_tables / column_exists`` (or with an
explicit ``is_pg()`` branch whose SQLite arm is annotated).  This test is the
closure guard: it fails if anyone re-introduces an UNANNOTATED introspection
probe that would raise or silently mis-answer on PostgreSQL.

What counts as a flagged probe
------------------------------
* ``FROM sqlite_master`` in a shape that translate_sql rule-14 does NOT rewrite
  (anything other than the three exact ``WHERE type='table'`` shapes) — a
  bypassing system-catalogue query.
* An introspection ``PRAGMA`` other than ``PRAGMA table_info(X)`` (the only shape
  translate_sql rule-1 rewrites): ``index_list``, ``index_info``, ``index_xinfo``,
  ``foreign_key_list``, ``foreign_key_check``, ``table_xinfo``, ``database_list``,
  ``collation_list``.  Config PRAGMAs (journal_mode, foreign_keys, busy_timeout,
  synchronous, cache_size, …) are NOT introspection and are ignored.

A flagged probe is allowed only when it carries a ``# pg-portability:
sqlite-only path`` annotation on the same line or within the small contiguous
comment/continuation block immediately above it (the SQL text frequently lands
on a continuation line of a multi-line ``conn.execute( ... )`` call).

Exclusions
----------
Files the linter already exempts (``pg_portability_linter._is_exempt`` —
storage.py, init_icdev_db.py, migration_runner.py, init_db.py, seed_*/migrate_*,
/tests/, /lint/, /schema/, …) plus everything under ``tools/db/migrations/``.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_ANNOTATION = "# pg-portability: sqlite-only path"

# translate_sql rule-14 sqlite_master shapes that ARE rewritten to PG
# information_schema (kept identical to the linter's shape set).  Escaped quotes
# in Python source (``type=\'table\'``) are normalised before matching so a
# validly-escaped rule-14 query is recognised as safe.
_RULE14_SHAPES: tuple = (
    re.compile(
        r"select\s+(?:1|name|count\(\s*\*\s*\))\s+from\s+sqlite_master\s+"
        r"where\s+type\s*=\s*'table'\s+and\s+name\s*=\s*(?:\?|%s)",
        re.IGNORECASE,
    ),
    re.compile(
        r"select\s+name\s+from\s+sqlite_master\s+where\s+type\s*=\s*'table'",
        re.IGNORECASE,
    ),
    re.compile(
        r"select\s+count\(\s*\*\s*\)\s+from\s+sqlite_master\s+where\s+type\s*=\s*'table'",
        re.IGNORECASE,
    ),
)

_FROM_MASTER = re.compile(r"\bfrom\s+sqlite_master\b", re.IGNORECASE)
_PRAGMA_TABLE_INFO = re.compile(r"pragma\s+table_info\s*\(", re.IGNORECASE)
_PRAGMA_INTROSPECTION = re.compile(
    r"pragma\s+(?:index_list|index_info|index_xinfo|foreign_key_list|"
    r"foreign_key_check|table_xinfo|database_list|collation_list)\s*\(",
    re.IGNORECASE,
)


def _load_linter():
    path = PROJECT_ROOT / "tools" / "lint" / "pg_portability_linter.py"
    spec = importlib.util.spec_from_file_location("_pgp_linter_guard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_LINTER = _load_linter()


def _is_excluded(path: Path) -> bool:
    if _LINTER._is_exempt(path):
        return True
    return "/db/migrations/" in str(path).replace("\\", "/")


def _normalise_escapes(line: str) -> str:
    return line.replace("\\'", "'").replace('\\"', '"')


def _is_annotated(lines: list[str], idx: int) -> bool:
    """Annotation on the same line or within the contiguous (blank-terminated)
    comment/continuation block immediately above the probe line."""
    if _ANNOTATION in lines[idx]:
        return True
    for j in range(idx - 1, max(-1, idx - 4), -1):
        if not lines[j].strip():
            break
        if _ANNOTATION in lines[j]:
            return True
    return False


def _scan_source(lines: list[str], filename: str = "<mem>") -> list[tuple]:
    """Return [(line_no, pattern, text), …] for unannotated bypassing probes."""
    findings: list[tuple] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        norm = _normalise_escapes(line)
        # sqlite_master query that bypasses rule-14
        if _FROM_MASTER.search(norm) and not any(rx.search(norm) for rx in _RULE14_SHAPES):
            if not _is_annotated(lines, i):
                findings.append((i + 1, "sqlite_master", stripped[:100]))
                continue
        # introspection PRAGMA other than table_info
        if _PRAGMA_INTROSPECTION.search(norm) and not _PRAGMA_TABLE_INFO.search(norm):
            if not _is_annotated(lines, i):
                findings.append((i + 1, "PRAGMA", stripped[:100]))
    return findings


def _scan_file(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rel = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    return [
        {"file": rel, "line": ln, "pattern": pat, "text": txt}
        for ln, pat, txt in _scan_source(lines, rel)
    ]


def _scan_runtime_tree() -> list[dict]:
    tools_root = PROJECT_ROOT / "tools"
    findings: list[dict] = []
    for py in sorted(tools_root.rglob("*.py")):
        if _is_excluded(py):
            continue
        findings.extend(_scan_file(py))
    return findings


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_no_unannotated_introspection_probes_in_runtime_tools():
    findings = _scan_runtime_tree()
    if findings:
        lines = "\n".join(
            f"  {f['file']}:{f['line']} [{f['pattern']}] {f['text']}" for f in findings
        )
        pytest.fail(
            f"{len(findings)} unannotated sqlite_master / introspection-PRAGMA "
            "probe(s) found in runtime tools/. Replace with "
            "tools.db.storage.table_exists()/list_tables()/column_exists() or add "
            f"an explicit is_pg() branch annotated `{_ANNOTATION}`:\n{lines}"
        )


# ---------------------------------------------------------------------------
# Positive / negative controls — the detector must actually detect
# ---------------------------------------------------------------------------


def test_detects_unannotated_bypassing_sqlite_master():
    src = [
        "def probe(conn):",
        "    return conn.execute(\"SELECT sql FROM sqlite_master WHERE name='foo'\").fetchone()",
    ]
    findings = _scan_source(src)
    assert any(p == "sqlite_master" for _, p, _ in findings)


def test_detects_unannotated_introspection_pragma():
    src = [
        "def cols(conn):",
        '    return conn.execute("PRAGMA index_list(users)").fetchall()',
    ]
    findings = _scan_source(src)
    assert any(p == "PRAGMA" for _, p, _ in findings)


def test_rule14_shapes_are_allowed():
    src = [
        "def a(conn):",
        "    conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")",
        "    conn.execute(\"SELECT count(*) FROM sqlite_master WHERE type='table'\")",
        "    conn.execute(\"SELECT 1 FROM sqlite_master WHERE type='table' AND name=?\", (t,))",
    ]
    assert _scan_source(src) == []


def test_pragma_table_info_is_allowed():
    src = ["def a(conn):", '    conn.execute(f"PRAGMA table_info({t})")']
    assert _scan_source(src) == []


def test_config_pragma_is_ignored():
    src = [
        "def a(conn):",
        '    conn.execute("PRAGMA journal_mode=WAL")',
        '    conn.execute("PRAGMA foreign_keys=ON")',
        '    conn.execute("PRAGMA busy_timeout=5000")',
    ]
    assert _scan_source(src) == []


def test_same_line_annotation_allows():
    src = [
        "def a(conn):",
        "    conn.execute(\"SELECT sql FROM sqlite_master WHERE name='x'\")  # pg-portability: sqlite-only path",
    ]
    assert _scan_source(src) == []


def test_annotation_above_multiline_execute_allows():
    src = [
        "def a(conn):",
        "    # pg-portability: sqlite-only path — SQLite branch of an is_pg() guard",
        "    rows = conn.execute(",
        "        \"SELECT name FROM sqlite_master\"",
        "        \" WHERE type='table' AND name LIKE 'ad_%'\"",
        "    ).fetchall()",
    ]
    assert _scan_source(src) == []
