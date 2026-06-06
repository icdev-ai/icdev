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

# Captured before any test patches it, so a telemetry test can restore the real
# implementation over the fixture's default stub (see ``conn`` fixture below).
_REAL_HARVEST_TELEMETRY = harvester._harvest_telemetry

# Minimal slices of the engine store tables — only the columns the harvester
# reads. Mirrors tools/db/init_icdev_db.py column names exactly. oracle_predictions
# backs the genesis source (Oracle lens predictions + Internal Awareness gap nodes).
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
CREATE TABLE oracle_predictions (
    id TEXT PRIMARY KEY, prediction_text TEXT, confidence REAL,
    prediction_type TEXT, severity TEXT
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
    # Telemetry harvests by invoking the introspective analyzer, which opens its
    # OWN real DB connection — stub it off by default so table-store tests stay
    # hermetic. The telemetry test restores the real implementation explicitly.
    monkeypatch.setattr(harvester, "_harvest_telemetry", lambda cap: [])
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


# =========================================================================
# acf-harvest-02 — genesis + telemetry sources + cross-source dedup
# =========================================================================
def test_harvest_genesis_from_oracle_predictions(conn):
    """An Oracle prediction / awareness gap node becomes a genesis signal."""
    conn.execute(
        "INSERT INTO oracle_predictions VALUES (?,?,?,?,?)",
        ("pred-1", "Route /foo not listed in start.md", 0.90,
         "gap::route_not_listed", "low"),
    )
    conn.commit()
    signals = harvester.harvest("run-G", conn=conn)
    gen = next(s for s in signals if s["source_engine"] == "genesis")
    assert gen["source_ref"] == "oracle_predictions:pred-1"
    assert gen["theme"] == "Route /foo not listed in start.md"
    assert gen["raw_score"] == pytest.approx(0.90)
    # prediction_type + severity become the clustering keywords.
    assert gen["keywords"] == ["gap::route_not_listed", "low"]

    row = next(r for r in _rows(conn, "run-G") if r["source_engine"] == "genesis")
    assert json.loads(row["keywords"]) == ["gap::route_not_listed", "low"]


def test_harvest_telemetry_gap_becomes_signal(conn, monkeypatch):
    """A telemetry gap surfaced by the introspective analyzer becomes a signal.

    The analyzer's read-only analyses are stubbed (one finding from
    gate_failures; the rest skipped) so the test never touches a real DB but
    still exercises the genuine normalization path (_signal_title/_signal_score).
    """
    import tools.innovation.introspective_analyzer as ia

    monkeypatch.setattr(
        ia, "analyze_gate_failures",
        lambda *a, **k: {
            "analysis_type": "gate_failures",
            "skipped": False,
            "findings": [
                {
                    "gate_event_type": "test_failed",
                    "project_id": "proj-x",
                    "failure_count": 8,
                    "first_failure": "2026-01-01T00:00:00Z",
                    "last_failure": "2026-02-01T00:00:00Z",
                }
            ],
            "recommendations": [],
        },
    )
    for other in ("analyze_unused_tools", "analyze_slow_pipelines", "analyze_knowledge_gaps"):
        monkeypatch.setattr(ia, other, lambda *a, **k: {"skipped": True, "findings": []})
    # Restore the real telemetry harvester over the fixture's default stub.
    monkeypatch.setattr(harvester, "_harvest_telemetry", _REAL_HARVEST_TELEMETRY)

    signals = harvester.harvest("run-T", conn=conn)
    tel = next(s for s in signals if s["source_engine"] == "telemetry")
    assert tel["theme"] == "[Gate Failure] test_failed failed 8x in proj-x"
    assert tel["raw_score"] == pytest.approx(8 / 20.0)
    # Analysis type leads the keywords; the salient tag field follows.
    assert tel["keywords"] == ["gate_failures", "test_failed"]
    assert tel["source_ref"].startswith("introspective:gate_failures:")

    rows = [r for r in _rows(conn, "run-T") if r["source_engine"] == "telemetry"]
    assert len(rows) == 1


def test_harvest_dedupes_same_signal_across_sources(conn):
    """The same capability surfaced by two engines is stored once (highest score)."""
    # innovation: tags -> keywords ["automation"], score 0.9
    conn.execute(
        "INSERT INTO innovation_signals VALUES (?,?,?,?,?)",
        ("sig-1", "Shared Capability", 0.9, "automation", None),
    )
    # research: explicit keywords ["automation"], same theme, lower score 0.5
    conn.execute(
        "INSERT INTO research_challenges VALUES (?,?,?,?)",
        ("chal-1", "Shared Capability", 0.5, json.dumps(["automation"])),
    )
    conn.commit()

    signals = harvester.harvest("run-DD", conn=conn)
    shared = [s for s in signals if s["theme"] == "Shared Capability"]
    assert len(shared) == 1
    # Highest-scoring representative wins -> the innovation signal.
    assert shared[0]["source_engine"] == "innovation"
    assert shared[0]["raw_score"] == pytest.approx(0.9)

    rows = [r for r in _rows(conn, "run-DD") if r["theme"] == "Shared Capability"]
    assert len(rows) == 1


def test_dedupe_key_normalizes_theme_and_keyword_order():
    """Dedup key is invariant to case, whitespace and keyword order."""
    a = {"theme": " Edge AI ", "keywords": ["B", "a"], "raw_score": 0.1}
    b = {"theme": "edge ai", "keywords": ["a", "b"], "raw_score": 0.2}
    assert harvester._dedupe_key(a) == harvester._dedupe_key(b)
    # A distinct theme yields a distinct key.
    c = {"theme": "other", "keywords": ["a", "b"], "raw_score": 0.2}
    assert harvester._dedupe_key(c) != harvester._dedupe_key(a)
