# CUI // SP-CTI
"""Tests: child apps must inherit anti-hallucination grounding (trust-cite-05)
and pass the hardened forge_validator gate (cvx-gen-01).

Covers:
    - child_app_generator.DIRECTORY_TREE includes tools/quality
    - forge_validator FORGE-03c grounding presence + API-freshness (pass/fail)
    - forge_validator FORGE-11 coherence: missing tools/workflow -> FAIL
    - forge_validator FORGE-12 banned DB patterns: sqlite3.connect -> FAIL,
      bare '?' placeholder -> WARN
"""

import importlib

cag = importlib.import_module("tools.builder.child_app_generator")
fv = importlib.import_module("tools.builder.forge_validator")

# Canonical API markers the child grounding modules must carry.
_CONTENT_GROUNDING_STUB = "def ground_content(*args, **kwargs):\n    return None\n"
_CITATION_GROUNDING_STUB = "def classify_confidence(score):\n    return 'include'\n"


def _grounding_check(checks):
    return next((c for c in checks if c.check_id == "FORGE-03c"), None)


def _write_current_grounding(tmp_path):
    q = tmp_path / "tools" / "quality"
    q.mkdir(parents=True)
    (q / "content_grounding.py").write_text(_CONTENT_GROUNDING_STUB, encoding="utf-8")
    (q / "citation_grounding.py").write_text(_CITATION_GROUNDING_STUB, encoding="utf-8")
    return q


def test_directory_tree_includes_quality():
    assert "tools/quality" in cag.DIRECTORY_TREE


def test_directory_tree_includes_workflow():
    assert "tools/workflow" in cag.DIRECTORY_TREE


# ── FORGE-03c: grounding presence + API freshness ──────────────────────────


def test_validator_passes_when_grounding_current(tmp_path):
    # (a) grounding files present AND carry the current public API -> pass
    _write_current_grounding(tmp_path)
    (tmp_path / "tools" / "db").mkdir(parents=True)
    check = _grounding_check(fv._check_tools(tmp_path))
    assert check is not None
    assert check.status == "pass"


def test_validator_fails_when_grounding_missing(tmp_path):
    # tools/ exists with some scripts but no grounding modules
    (tmp_path / "tools" / "db").mkdir(parents=True)
    (tmp_path / "tools" / "db" / "x.py").write_text("# stub\n", encoding="utf-8")
    check = _grounding_check(fv._check_tools(tmp_path))
    assert check is not None
    assert check.status == "fail"
    assert "citation_grounding.py" in check.actual


def test_validator_fails_when_grounding_stale(tmp_path):
    # (b) files present but content_grounding lacks ground_content() -> FAIL
    q = tmp_path / "tools" / "quality"
    q.mkdir(parents=True)
    (tmp_path / "tools" / "db").mkdir(parents=True)
    (q / "content_grounding.py").write_text("# stale pre-ground_content snapshot\n", encoding="utf-8")
    (q / "citation_grounding.py").write_text(_CITATION_GROUNDING_STUB, encoding="utf-8")
    check = _grounding_check(fv._check_tools(tmp_path))
    assert check is not None
    assert check.status == "fail"
    assert "stale" in check.actual
    assert "content_grounding.py" in check.actual


# ── FORGE-11: coherence checker must be present ────────────────────────────


def _coherence_check(checks):
    return next((c for c in checks if c.check_id == "FORGE-11"), None)


def test_coherence_fails_when_workflow_missing(tmp_path):
    # (c) no tools/workflow/coherence_checker.py -> explicit FAIL (not silent pass)
    (tmp_path / "tools" / "db").mkdir(parents=True)
    check = _coherence_check(fv._check_coherence(tmp_path))
    assert check is not None
    assert check.status == "fail"
    assert "missing" in check.message.lower()


# ── FORGE-12: banned DB access patterns ────────────────────────────────────


def _check(checks, check_id):
    return next((c for c in checks if c.check_id == check_id), None)


def test_db_patterns_fails_on_sqlite_connect(tmp_path):
    # (d) sqlite3.connect() in a runtime module -> FORGE-12 FAIL
    mod = tmp_path / "tools" / "svc"
    mod.mkdir(parents=True)
    (mod / "handler.py").write_text(
        "import sqlite3\n\ndef go():\n    return sqlite3.connect('x.db')\n", encoding="utf-8"
    )
    check = _check(fv._check_db_patterns(tmp_path), "FORGE-12")
    assert check is not None
    assert check.status == "fail"
    assert "handler.py" in check.actual


def test_db_patterns_allows_sqlite_connect_in_db_init(tmp_path):
    # sqlite3.connect() is allowed in db/init_*.py -> FORGE-12 pass
    db = tmp_path / "tools" / "db"
    db.mkdir(parents=True)
    (db / "init_db.py").write_text(
        "import sqlite3\n\ndef init():\n    return sqlite3.connect('x.db')\n", encoding="utf-8"
    )
    check = _check(fv._check_db_patterns(tmp_path), "FORGE-12")
    assert check is not None
    assert check.status == "pass"


def test_db_patterns_warns_on_bare_placeholder(tmp_path):
    # (e) bare '?' placeholder in a runtime module -> FORGE-12a WARN
    mod = tmp_path / "tools" / "svc"
    mod.mkdir(parents=True)
    (mod / "query.py").write_text(
        'def q(cur, x):\n    return cur.execute("SELECT 1 WHERE id = ?", (x,))\n', encoding="utf-8"
    )
    check = _check(fv._check_db_patterns(tmp_path), "FORGE-12a")
    assert check is not None
    assert check.status == "warn"
    assert "query.py" in check.actual
