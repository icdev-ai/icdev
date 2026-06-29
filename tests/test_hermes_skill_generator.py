# CUI // SP-CTI
"""Tests for NOVA Skill Generator (adapt-hermes-04).

Covers: importability, analyze_patterns, generate_skill_spec (dry-run + write),
list_queued, _llm_generate_spec fallback, and CLI JSON output.
"""
import json
import sqlite3
import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db():
    """In-memory SQLite with required tables (memory_entries + agent_improvement_artifacts)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE memory_entries (
            id TEXT PRIMARY KEY,
            type TEXT,
            content TEXT,
            tags TEXT DEFAULT '',
            importance INTEGER DEFAULT 5,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE agent_improvement_artifacts (
            artifact_id TEXT PRIMARY KEY,
            task_type TEXT NOT NULL,
            skill_used TEXT NOT NULL DEFAULT '',
            generation_n INTEGER NOT NULL DEFAULT 1,
            improvement_text TEXT NOT NULL DEFAULT '',
            composite_score REAL NOT NULL DEFAULT 0.0,
            baseline_score REAL NOT NULL DEFAULT 0.0,
            evidence_traces TEXT NOT NULL DEFAULT '[]',
            applied_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            applied_at TEXT
        )
    """)
    conn.commit()
    return conn


def _sqlite_conn(conn):
    """Return conn unchanged — test DB already uses ? placeholders via _ph()."""
    return conn


# ---------------------------------------------------------------------------
# Import / API surface
# ---------------------------------------------------------------------------

def test_skill_generator_importable():
    from tools.nova.skill_generator import analyze_patterns, generate_skill_spec, list_queued
    assert callable(analyze_patterns)
    assert callable(generate_skill_spec)
    assert callable(list_queued)


def test_icdev_mirror_importable():
    from icdev.tools.nova.skill_generator import analyze_patterns, generate_skill_spec, list_queued
    assert callable(analyze_patterns)
    assert callable(generate_skill_spec)
    assert callable(list_queued)


def test_gen_id_format():
    from tools.nova.skill_generator import _gen_id
    gid = _gen_id("sg")
    assert gid.startswith("sg-")
    assert len(gid) == 15  # "sg-" + 12 hex


def test_now_iso_format():
    from tools.nova.skill_generator import _now_iso
    ts = _now_iso()
    assert "T" in ts
    assert ts.endswith(("Z", "+00:00"))


# ---------------------------------------------------------------------------
# analyze_patterns
# ---------------------------------------------------------------------------

def test_analyze_patterns_returns_list_type():
    from tools.nova import skill_generator
    conn = _make_db()
    with patch.object(skill_generator, "_get_conn", return_value=conn):
        patterns = skill_generator.analyze_patterns(limit=10, min_count=1)
    assert isinstance(patterns, list)


def test_analyze_patterns_finds_tool_invocation():
    from tools.nova import skill_generator
    conn = _make_db()
    content = "python tools/memory/hybrid_search.py --query test"
    conn.execute(
        "INSERT INTO memory_entries VALUES (?,?,?,?,?,?,?)",
        ("s1", "session_user", content, "", 5, "CUI", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO memory_entries VALUES (?,?,?,?,?,?,?)",
        ("s2", "session_user", content, "", 5, "CUI", "2026-01-02"),
    )
    conn.commit()

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        patterns = skill_generator.analyze_patterns(limit=10, min_count=2)

    assert any("hybrid_search" in p["pattern"] for p in patterns)
    assert all(p["count"] >= 2 for p in patterns)


def test_analyze_patterns_respects_min_count():
    from tools.nova import skill_generator
    conn = _make_db()
    conn.execute(
        "INSERT INTO memory_entries VALUES (?,?,?,?,?,?,?)",
        ("s1", "session_user", "icdev status", "", 5, "CUI", "2026-01-01"),
    )
    conn.commit()

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        patterns = skill_generator.analyze_patterns(limit=10, min_count=2)

    # Single occurrence below min_count=2 → not surfaced
    assert patterns == []


def test_analyze_patterns_empty_on_no_session_rows():
    from tools.nova import skill_generator
    conn = _make_db()

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        patterns = skill_generator.analyze_patterns(limit=10, min_count=2)

    assert patterns == []


def test_analyze_patterns_graceful_on_db_error():
    from tools.nova import skill_generator

    class BrokenConn:
        def execute(self, *a, **k): raise RuntimeError("DB unavailable")
        def close(self): pass

    with patch.object(skill_generator, "_get_conn", return_value=BrokenConn()):
        patterns = skill_generator.analyze_patterns(limit=5, min_count=1)

    assert patterns == []


# ---------------------------------------------------------------------------
# generate_skill_spec
# ---------------------------------------------------------------------------

def test_generate_skill_spec_dry_run_no_db_write():
    from tools.nova import skill_generator
    conn = _make_db()

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        with patch.object(skill_generator, "_llm_generate_spec", return_value="# test-skill\n"):
            result = skill_generator.generate_skill_spec(
                "python tools/memory/hybrid_search.py", dry_run=True
            )

    assert result["dry_run"] is True
    assert result["queued"] is False
    row = conn.execute("SELECT COUNT(*) FROM agent_improvement_artifacts").fetchone()
    assert row[0] == 0


def test_generate_skill_spec_queues_on_write():
    from tools.nova import skill_generator
    conn = _make_db()

    # Wrap conn so _queue_for_harness's conn.close() is a no-op (in-memory DB would disappear).
    # Also guard params=None to avoid sqlite3 TypeError.
    class NoCloseWrapper:
        def __init__(self, c): self._c = c
        def execute(self, sql, params=None):
            return self._c.execute(sql) if params is None else self._c.execute(sql, params)
        def commit(self): self._c.commit()
        def close(self): pass

    def mock_get_conn():
        return NoCloseWrapper(conn)

    with patch.object(skill_generator, "_get_conn", side_effect=mock_get_conn):
        with patch.object(skill_generator, "_llm_generate_spec", return_value="# icdev-test\n## When\nTest"):
            result = skill_generator.generate_skill_spec("pytest tests/test_memory", dry_run=False)

    assert result["queued"] is True
    row = conn.execute("SELECT COUNT(*) FROM agent_improvement_artifacts").fetchone()
    assert row[0] == 1


def test_generate_skill_spec_returns_required_keys():
    from tools.nova import skill_generator
    conn = _make_db()

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        with patch.object(skill_generator, "_llm_generate_spec", return_value="# spec"):
            result = skill_generator.generate_skill_spec("icdev status", dry_run=True)

    for key in ("skill_id", "skill_name", "pattern", "category", "spec_preview", "queued", "dry_run"):
        assert key in result, f"Missing key: {key}"


def test_generate_skill_spec_slug_name():
    from tools.nova import skill_generator
    conn = _make_db()

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        with patch.object(skill_generator, "_llm_generate_spec", return_value="# s"):
            result = skill_generator.generate_skill_spec("python tools/db/init.py", dry_run=True)

    assert result["skill_name"].startswith("icdev-")
    # slug contains only alphanumeric + hyphens
    slug = result["skill_name"].removeprefix("icdev-")
    assert re.match(r"^[a-z0-9-]+$", slug), f"Bad slug: {slug}"


# ---------------------------------------------------------------------------
# list_queued
# ---------------------------------------------------------------------------

def test_list_queued_empty_db():
    from tools.nova import skill_generator
    conn = _make_db()

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        result = skill_generator.list_queued(limit=10)

    assert result == []


def test_list_queued_returns_pending_entries():
    from tools.nova import skill_generator
    conn = _make_db()
    conn.execute(
        "INSERT INTO agent_improvement_artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("sg-abc123", "skill_generation", "icdev-hybrid-search", 1,
         "# spec", 0.0, 0.0, "[]", 0, "pending", "2026-01-01", None),
    )
    conn.commit()

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        results = skill_generator.list_queued(limit=10)

    assert len(results) == 1
    assert results[0]["artifact_id"] == "sg-abc123"
    assert results[0]["status"] == "pending"


def test_list_queued_only_skill_generation_rows():
    from tools.nova import skill_generator
    conn = _make_db()
    conn.execute(
        "INSERT INTO agent_improvement_artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("sg-xxx", "skill_generation", "icdev-test", 1, "# s", 0.0, 0.0, "[]", 0, "pending", "2026-01-01", None),
    )
    conn.execute(
        "INSERT INTO agent_improvement_artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("sg-yyy", "task_improvement", "icdev-other", 1, "# o", 0.5, 0.3, "[]", 0, "applied", "2026-01-01", None),
    )
    conn.commit()

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        results = skill_generator.list_queued(limit=10)

    assert len(results) == 1
    assert results[0]["artifact_id"] == "sg-xxx"


def test_list_queued_graceful_on_missing_table():
    from tools.nova import skill_generator

    class EmptyConn:
        def execute(self, *a, **k): raise Exception("table not found")
        def close(self): pass

    with patch.object(skill_generator, "_get_conn", return_value=EmptyConn()):
        result = skill_generator.list_queued(limit=10)

    assert result == []


# ---------------------------------------------------------------------------
# _llm_generate_spec
# ---------------------------------------------------------------------------

def test_llm_generate_spec_fallback_on_import_error():
    from tools.nova import skill_generator

    with patch.dict(sys.modules, {"tools.llm.router": None, "tools.llm.provider": None}):
        spec = skill_generator._llm_generate_spec("python tools/db/init.py", "icdev-db-init")

    assert "icdev-db-init" in spec
    assert "python tools/db/init.py" in spec
    assert "## Steps" in spec


def test_llm_generate_spec_fallback_on_exception():
    from tools.nova import skill_generator

    mock_router = MagicMock()
    mock_router.invoke.side_effect = RuntimeError("LLM timeout")

    with patch("tools.nova.skill_generator.LLMRouter", return_value=mock_router, create=True):
        spec = skill_generator._llm_generate_spec("icdev status", "icdev-status")

    assert isinstance(spec, str)
    assert "icdev-status" in spec


def test_llm_generate_spec_returns_string():
    from tools.nova import skill_generator

    spec = skill_generator._llm_generate_spec("icdev deploy --env staging", "icdev-deploy")

    assert isinstance(spec, str)
    assert len(spec) > 10


# ---------------------------------------------------------------------------
# _queue_for_harness
# ---------------------------------------------------------------------------

def test_queue_for_harness_returns_true_on_success():
    from tools.nova import skill_generator
    conn = _make_db()

    # Suppress close() so the in-memory DB remains queryable after the call.
    # Guard params=None to avoid sqlite3 TypeError on no-arg execute calls.
    class NoCloseWrapper:
        def __init__(self, c): self._c = c
        def execute(self, sql, params=None):
            return self._c.execute(sql) if params is None else self._c.execute(sql, params)
        def commit(self): self._c.commit()
        def close(self): pass

    with patch.object(skill_generator, "_get_conn", return_value=NoCloseWrapper(conn)):
        ok = skill_generator._queue_for_harness(
            "sg-test001", "icdev-test", "# spec content", "icdev status"
        )

    assert ok is True
    row = conn.execute(
        "SELECT artifact_id, status FROM agent_improvement_artifacts WHERE artifact_id = ?",
        ("sg-test001",),
    ).fetchone()
    assert row is not None
    assert row["status"] == "pending"


def test_queue_for_harness_returns_false_on_db_error():
    from tools.nova import skill_generator

    class BrokenConn:
        def execute(self, *a, **k): raise RuntimeError("constraint")
        def commit(self): pass
        def close(self): pass

    # patch _ph to return "?" for the broken conn
    with patch.object(skill_generator, "_get_conn", return_value=BrokenConn()):
        ok = skill_generator._queue_for_harness("sg-bad", "icdev-x", "# s", "pattern")

    assert ok is False


# ---------------------------------------------------------------------------
# CLI JSON output
# ---------------------------------------------------------------------------

def test_cli_list_queued_json(capsys):
    from tools.nova import skill_generator
    conn = _make_db()

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        with patch.object(sys, "argv", ["skill_generator.py", "--list-queued", "--json"]):
            skill_generator._cli()

    data = json.loads(capsys.readouterr().out)
    assert data["classification"] == "CUI // SP-CTI"
    assert data["count"] == 0
    assert isinstance(data["queued"], list)


def test_cli_generate_dry_run_json(capsys):
    from tools.nova import skill_generator
    conn = _make_db()

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        with patch.object(skill_generator, "_llm_generate_spec", return_value="# spec"):
            with patch.object(sys, "argv",
                              ["skill_generator.py", "--generate", "icdev status", "--dry-run", "--json"]):
                skill_generator._cli()

    data = json.loads(capsys.readouterr().out)
    assert data["dry_run"] is True
    assert data["queued"] is False
    assert data["classification"] == "CUI // SP-CTI"
    assert "skill_id" in data


def test_cli_analyze_json(capsys):
    from tools.nova import skill_generator
    conn = _make_db()

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        with patch.object(sys, "argv", ["skill_generator.py", "--analyze", "--json"]):
            skill_generator._cli()

    data = json.loads(capsys.readouterr().out)
    assert "patterns" in data
    assert isinstance(data["patterns"], list)


def test_cli_no_args_exits(capsys):
    import pytest
    from tools.nova import skill_generator

    with patch.object(sys, "argv", ["skill_generator.py"]):
        with pytest.raises(SystemExit):
            skill_generator._cli()


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
import re  # noqa: E402  (used in test_generate_skill_spec_slug_name)
