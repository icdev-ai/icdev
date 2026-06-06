# CUI // SP-CTI
"""Unit tests for the ACF signal harvester (acf-harvest-01).

Each test seeds a fixture row in an existing engine store (innovation / creative /
research) and asserts that ``harvest()`` produces a correctly normalized
``foundry_signals`` row under the given run_id. Hermetic: a throwaway in-memory
SQLite connection is passed directly to ``harvest`` and ``init_db`` is stubbed so
the test never touches the repo database.
"""
import json
import sqlite3

import pytest

from tools.foundry import harvester
from tools.foundry.db.init_db import _SCHEMA_SQLITE

# Minimal slices of the three engine store tables — only the columns the
# harvester reads. Mirrors tools/db/init_icdev_db.py column names exactly.
_ENGINE_STORES = """
CREATE TABLE innovation_signals (
    id TEXT PRIMARY KEY, title TEXT, innovation_score REAL,
    category TEXT, source TEXT
);
CREATE TABLE creative_pain_points (
    id TEXT PRIMARY KEY, title TEXT, composite_score REAL, keywords TEXT
);
CREATE TABLE research_challenges (
    id TEXT PRIMARY KEY, title TEXT, composite_score REAL, keywords TEXT
);
"""


@pytest.fixture
def conn(monkeypatch):
    """In-memory SQLite DB with foundry_* + engine store tables; init_db stubbed."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA_SQLITE)
    c.executescript(_ENGINE_STORES)
    c.commit()
    # init_db() inside harvest() would open its own get_connection(); stub it so
    # the test stays isolated to this in-memory connection.
    monkeypatch.setattr(harvester, "init_db", lambda *a, **k: True)
    yield c
    c.close()


def _rows(conn, run_id):
    cur = conn.execute(
        "SELECT source_engine, source_ref, theme, raw_score, keywords, "
        "tenant_id, classification FROM foundry_signals WHERE run_id = ? "
        "ORDER BY source_engine",
        (run_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def test_harvest_normalizes_one_row_per_engine(conn):
    conn.execute(
        "INSERT INTO innovation_signals VALUES (?,?,?,?,?)",
        ("sig-1", "Edge AI inference", 0.81, "automation", "github"),
    )
    conn.execute(
        "INSERT INTO creative_pain_points VALUES (?,?,?,?)",
        ("pain-1", "Slow onboarding", 0.72, json.dumps(["onboarding", "ux"])),
    )
    conn.execute(
        "INSERT INTO research_challenges VALUES (?,?,?,?)",
        ("chal-1", "Zero-trust gaps", 0.66, json.dumps(["zta", "compliance"])),
    )
    conn.commit()

    signals = harvester.harvest("run-A", conn=conn)

    assert len(signals) == 3
    engines = {s["source_engine"] for s in signals}
    assert engines == {"innovation", "creative", "research"}

    rows = _rows(conn, "run-A")
    assert len(rows) == 3

    by_engine = {r["source_engine"]: r for r in rows}

    inn = by_engine["innovation"]
    assert inn["source_ref"] == "innovation_signals:sig-1"
    assert inn["theme"] == "Edge AI inference"
    assert inn["raw_score"] == pytest.approx(0.81)
    assert json.loads(inn["keywords"]) == ["automation", "github"]
    assert inn["tenant_id"] == "default"
    assert inn["classification"] == "CUI"

    cre = by_engine["creative"]
    assert cre["source_ref"] == "creative_pain_points:pain-1"
    assert cre["theme"] == "Slow onboarding"
    assert json.loads(cre["keywords"]) == ["onboarding", "ux"]

    res = by_engine["research"]
    assert res["source_ref"] == "research_challenges:chal-1"
    assert json.loads(res["keywords"]) == ["zta", "compliance"]


def test_harvest_returns_empty_when_stores_empty(conn):
    assert harvester.harvest("run-empty", conn=conn) == []
    assert _rows(conn, "run-empty") == []


def test_harvest_respects_disabled_source(conn):
    conn.execute(
        "INSERT INTO innovation_signals VALUES (?,?,?,?,?)",
        ("sig-1", "X", 0.9, "automation", "github"),
    )
    conn.execute(
        "INSERT INTO creative_pain_points VALUES (?,?,?,?)",
        ("pain-1", "Y", 0.5, "[]"),
    )
    conn.commit()
    cfg = {"sources": {"innovation": {"enabled": False, "max_signals": 50}}}
    signals = harvester.harvest("run-B", config=cfg, conn=conn)
    engines = {s["source_engine"] for s in signals}
    assert "innovation" not in engines
    assert "creative" in engines


def test_harvest_respects_per_source_cap(conn):
    for i in range(5):
        conn.execute(
            "INSERT INTO innovation_signals VALUES (?,?,?,?,?)",
            (f"sig-{i}", f"S{i}", 0.1 * i, "automation", "github"),
        )
    conn.commit()
    cfg = {"sources": {"innovation": {"enabled": True, "max_signals": 2}}}
    signals = harvester.harvest("run-C", config=cfg, conn=conn)
    inn = [s for s in signals if s["source_engine"] == "innovation"]
    assert len(inn) == 2
    # Cap keeps the highest-scoring signals.
    assert all(s["raw_score"] >= 0.3 for s in inn)


def test_harvest_keyword_fallback_to_tags(conn):
    """innovation_signals has no JSON keyword column — tags become keywords."""
    conn.execute(
        "INSERT INTO innovation_signals VALUES (?,?,?,?,?)",
        ("sig-1", "X", 0.5, "compliance", None),
    )
    conn.commit()
    signals = harvester.harvest("run-D", conn=conn)
    inn = next(s for s in signals if s["source_engine"] == "innovation")
    # None source dropped, category retained.
    assert inn["keywords"] == ["compliance"]
