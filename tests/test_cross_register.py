# CUI // SP-CTI
"""Unit tests for cross_register pull_from_innovation (acf-ada-04-d3).

Each test seeds a fixture row in ``innovation_signals`` and asserts that
``pull_from_innovation()`` produces a correctly normalized ``foundry_signals``
row with explicit hash-based dedup. Hermetic: a throwaway in-memory SQLite
connection is passed directly so the test never touches the repo database.
"""
import json
import sqlite3

import pytest

from tools import cross_register
from tools.foundry.db.init_db import _SCHEMA_SQLITE

# Minimal ``innovation_signals`` slice — only columns the pull function reads.
_INNOVATION_STORE = """
CREATE TABLE innovation_signals (
    id TEXT PRIMARY KEY,
    title TEXT,
    composite_score REAL,
    innovation_score REAL,
    category TEXT,
    source TEXT,
    source_type TEXT,
    content_hash TEXT,
    status TEXT DEFAULT 'new'
);
"""


@pytest.fixture
def conn(monkeypatch):
    """In-memory SQLite DB with foundry_* + innovation_signals; init_db stubbed."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA_SQLITE)
    c.executescript(_INNOVATION_STORE)
    c.commit()
    monkeypatch.setattr(cross_register.init_db, "__call__", lambda *a, **k: True)
    yield c
    c.close()


def _rows(conn, run_id):
    cur = conn.execute(
        "SELECT source_engine, source_ref, theme, raw_score, keywords, "
        "tenant_id, classification, content_hash FROM foundry_signals WHERE run_id = ? "
        "ORDER BY id",
        (run_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def test_pull_normalizes_one_row(conn):
    conn.execute(
        "INSERT INTO innovation_signals VALUES (?,?,?,?,?,?,?,?,?)",
        ("sig-1", "Edge AI inference", 0.85, None, "automation", "github", "repo", "hash-a", "new"),
    )
    conn.commit()

    result = cross_register.pull_from_innovation(run_id="run-A", conn=conn)

    assert result["run_id"] == "run-A"
    assert result["inserted"] == 1
    assert result["skipped"] == 0
    assert len(result["signals"]) == 1

    sig = result["signals"][0]
    assert sig["source_engine"] == "innovation"
    assert sig["source_ref"] == "innovation_signals:sig-1"
    assert sig["theme"] == "Edge AI inference"
    assert sig["raw_score"] == pytest.approx(0.85)
    assert sig["keywords"] == ["automation", "github", "repo"]

    rows = _rows(conn, "run-A")
    assert len(rows) == 1
    assert rows[0]["content_hash"] == "hash-a"
    assert json.loads(rows[0]["keywords"]) == ["automation", "github", "repo"]


def test_pull_dedup_skips_existing_hash(conn):
    conn.execute(
        "INSERT INTO innovation_signals VALUES (?,?,?,?,?,?,?,?,?)",
        ("sig-1", "Edge AI inference", 0.85, None, "automation", "github", "repo", "hash-a", "new"),
    )
    conn.commit()

    # First pull inserts.
    r1 = cross_register.pull_from_innovation(run_id="run-1", conn=conn)
    assert r1["inserted"] == 1

    # Second pull sees the same content_hash and skips.
    r2 = cross_register.pull_from_innovation(run_id="run-2", conn=conn)
    assert r2["inserted"] == 0
    assert r2["skipped"] == 1


def test_pull_dedup_uses_synthesised_hash_when_upstream_null(conn):
    conn.execute(
        "INSERT INTO innovation_signals VALUES (?,?,?,?,?,?,?,?,?)",
        ("sig-1", "Quantum crypto", 0.92, None, "security", None, None, None, "new"),
    )
    conn.commit()

    r1 = cross_register.pull_from_innovation(run_id="run-1", conn=conn)
    assert r1["inserted"] == 1

    r2 = cross_register.pull_from_innovation(run_id="run-2", conn=conn)
    assert r2["inserted"] == 0
    assert r2["skipped"] == 1


def test_pull_filters_by_min_score(conn):
    conn.execute(
        "INSERT INTO innovation_signals VALUES (?,?,?,?,?,?,?,?,?)",
        ("sig-low", "Low Score", 0.40, None, "cat", "src", "type", "hash-low", "new"),
    )
    conn.execute(
        "INSERT INTO innovation_signals VALUES (?,?,?,?,?,?,?,?,?)",
        ("sig-high", "High Score", 0.90, None, "cat", "src", "type", "hash-high", "new"),
    )
    conn.commit()

    result = cross_register.pull_from_innovation(run_id="run-F", min_score=0.7, conn=conn)
    themes = {s["theme"] for s in result["signals"]}
    assert "High Score" in themes
    assert "Low Score" not in themes


def test_pull_uses_innovation_score_fallback(conn):
    conn.execute(
        "INSERT INTO innovation_signals VALUES (?,?,?,?,?,?,?,?,?)",
        ("sig-fb", "Fallback", None, 0.78, "cat", "src", "type", "hash-fb", "new"),
    )
    conn.commit()

    result = cross_register.pull_from_innovation(run_id="run-FB", conn=conn)
    assert result["inserted"] == 1
    assert result["signals"][0]["raw_score"] == pytest.approx(0.78)


def test_pull_excludes_blocked_status(conn):
    conn.execute(
        "INSERT INTO innovation_signals VALUES (?,?,?,?,?,?,?,?,?)",
        ("sig-block", "Blocked", 0.95, None, "cat", "src", "type", "hash-block", "blocked"),
    )
    conn.commit()

    result = cross_register.pull_from_innovation(run_id="run-B", conn=conn)
    assert result["inserted"] == 0
    assert result["skipped"] == 0


def test_pull_respects_limit(conn):
    for i in range(5):
        conn.execute(
            "INSERT INTO innovation_signals VALUES (?,?,?,?,?,?,?,?,?)",
            (f"sig-{i}", f"Title {i}", 0.5 + 0.1 * i, None, "cat", "src", "type", f"hash-{i}", "new"),
        )
    conn.commit()

    result = cross_register.pull_from_innovation(run_id="run-L", limit=2, conn=conn)
    assert result["inserted"] == 2


def test_pull_returns_empty_when_store_empty(conn):
    result = cross_register.pull_from_innovation(run_id="run-empty", conn=conn)
    assert result["inserted"] == 0
    assert result["skipped"] == 0
    assert result["signals"] == []
    assert _rows(conn, "run-empty") == []
