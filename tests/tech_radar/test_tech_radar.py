# CUI // SP-CTI
"""Unit tests for the Tech Radar engine (pint-techrad V&V gate).

6 tests:
  1. _ring_from_score maps composite scores to correct rings
  2. _clamp keeps values within [0, 1] bounds
  3. RadarEngine._recompute_score applies ICDEV keyword boost and airgap penalty
  4. RadarEngine._index_signals normalizes entry names to lowercase keys
  5. RadarEngine.run dry_run does not write to DB
  6. RadarEngine.run full cycle persists ring change and history row

Run: pytest tests/tech_radar/ -v
"""

import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import os
os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")

# RadarEngine authors %s placeholders for PostgreSQL and expects the connection
# handed back by get_connection to rewrite them; bare sqlite3 does not.
from _sql_compat import connect as _tconnect  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal in-memory DB with tech_radar tables + innovation_signals
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS tech_radar_entries (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    category            TEXT,
    current_ring        TEXT CHECK(current_ring IN ('adopt','trial','assess','hold')),
    previous_ring       TEXT,
    ecosystem_maturity  REAL,
    icdev_fit           REAL,
    airgap_compat       REAL,
    il_compliance       REAL,
    composite_score     REAL,
    rationale           TEXT,
    last_assessed       TEXT,
    classification      TEXT DEFAULT 'CUI // SP-CTI'
);

CREATE TABLE IF NOT EXISTS tech_radar_history (
    id                   TEXT PRIMARY KEY,
    entry_id             TEXT NOT NULL,
    from_ring            TEXT,
    to_ring              TEXT,
    composite_score      REAL,
    innovation_signal_id TEXT,
    changed_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_techrad_ring ON tech_radar_entries(current_ring);
CREATE INDEX IF NOT EXISTS idx_techrad_history_entry ON tech_radar_history(entry_id);

CREATE TABLE IF NOT EXISTS innovation_signals (
    id               TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    source_type      TEXT NOT NULL,
    title            TEXT NOT NULL,
    description      TEXT,
    url              TEXT,
    metadata         TEXT,
    community_score  REAL DEFAULT 0.0,
    content_hash     TEXT NOT NULL,
    discovered_at    TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'new',
    category         TEXT,
    innovation_score REAL,
    classification   TEXT DEFAULT 'CUI // SP-CTI'
);
"""


def _make_conn(tmp_path, seed_entries=None):
    db = tmp_path / "test_techrad.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    if seed_entries:
        now = datetime.now(timezone.utc).isoformat()
        for e in seed_entries:
            conn.execute(
                """
                INSERT INTO tech_radar_entries
                    (id, name, category, current_ring, previous_ring,
                     ecosystem_maturity, icdev_fit, airgap_compat, il_compliance,
                     composite_score, rationale, last_assessed, classification)
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, '', ?, 'CUI // SP-CTI')
                """,
                (
                    e.get("id", str(uuid.uuid4())),
                    e["name"],
                    e.get("category", "testing"),
                    e["current_ring"],
                    e.get("ecosystem_maturity", 0.5),
                    e.get("icdev_fit", 0.5),
                    e.get("airgap_compat", 0.8),
                    e.get("il_compliance", 0.5),
                    e.get("composite_score", 0.5),
                    now,
                ),
            )
        conn.commit()
    return conn


# ===========================================================================
# Test 1 — _ring_from_score maps scores to rings
# ===========================================================================

def test_ring_from_score_thresholds():
    from tools.tech_radar.radar_engine import _ring_from_score

    assert _ring_from_score(1.0) == "adopt"
    assert _ring_from_score(0.75) == "adopt"
    assert _ring_from_score(0.74) == "trial"
    assert _ring_from_score(0.60) == "trial"
    assert _ring_from_score(0.59) == "assess"
    assert _ring_from_score(0.45) == "assess"
    assert _ring_from_score(0.44) == "hold"
    assert _ring_from_score(0.00) == "hold"


# ===========================================================================
# Test 2 — _clamp keeps values in [0, 1]
# ===========================================================================

def test_clamp_bounds():
    from tools.tech_radar.radar_engine import _clamp

    assert _clamp(1.5) == 1.0
    assert _clamp(-0.1) == 0.0
    assert _clamp(0.5) == 0.5
    assert _clamp(0.0) == 0.0
    assert _clamp(1.0) == 1.0


# ===========================================================================
# Test 3 — _recompute_score: keyword boost and airgap penalty
# ===========================================================================

def test_recompute_score_keyword_boost_and_airgap_penalty():
    from tools.tech_radar.radar_engine import RadarEngine

    engine = RadarEngine(enabled_sources=[], dry_run=True)

    # "ruff" matches keyword → fit boost; airgap_compat >= 0.70 → no penalty
    entry_ruff = {
        "id": "x1",
        "name": "ruff",
        "ecosystem_maturity": 0.80,
        "icdev_fit": 0.50,
        "airgap_compat": 0.90,
        "il_compliance": 0.50,
        "composite_score": 0.70,
        "current_ring": "adopt",
    }
    score_ruff = engine._recompute_score(entry_ruff, {})
    # With keyword boost icdev_fit rises → composite > baseline without boost
    baseline = 0.35 * 0.80 + 0.30 * 0.50 + 0.25 * 0.90 + 0.10 * 0.50
    assert score_ruff > baseline, "keyword boost must increase composite"
    assert 0.0 <= score_ruff <= 1.0

    # airgap_compat below threshold → penalty applied
    entry_low_compat = {
        "id": "x2",
        "name": "some-obscure-tool",
        "ecosystem_maturity": 0.50,
        "icdev_fit": 0.50,
        "airgap_compat": 0.60,  # below 0.70 threshold
        "il_compliance": 0.50,
        "composite_score": 0.55,
        "current_ring": "trial",
    }
    score_penalty = engine._recompute_score(entry_low_compat, {})
    # compat is penalised → effective compat = 0.55 → composite should reflect penalty
    assert 0.0 <= score_penalty <= 1.0
    no_penalty_composite = 0.35 * 0.50 + 0.30 * 0.50 + 0.25 * 0.60 + 0.10 * 0.50
    assert score_penalty < no_penalty_composite, "airgap penalty must lower composite"


# ===========================================================================
# Test 4 — _index_signals normalizes names to lowercase keys
# ===========================================================================

def test_index_signals_normalization():
    from tools.tech_radar.radar_engine import RadarEngine

    engine = RadarEngine(enabled_sources=[], dry_run=True)
    signals = [
        {"name": "Terraform", "ecosystem_maturity_signal": "ADOPT"},
        {"name": "  ANSIBLE  ", "ecosystem_maturity_signal": "TRIAL"},
        {"name": "", "ecosystem_maturity_signal": "ADOPT"},  # empty → skipped
    ]
    index = engine._index_signals(signals)

    assert "terraform" in index
    assert "ansible" in index
    assert "" not in index
    assert len(index["terraform"]) == 1
    assert len(index["ansible"]) == 1


# ===========================================================================
# Test 5 — RadarEngine.run dry_run skips DB writes
# ===========================================================================

def test_run_dry_run_does_not_write(tmp_path):
    from tools.tech_radar.radar_engine import RadarEngine

    entry_id = str(uuid.uuid4())
    conn = _make_conn(
        tmp_path,
        seed_entries=[
            {
                "id": entry_id,
                "name": "ruff",
                "current_ring": "hold",  # will compute to adopt → ring change
                "ecosystem_maturity": 0.82,
                "icdev_fit": 0.88,
                "airgap_compat": 0.90,
                "il_compliance": 0.68,
                "composite_score": 0.10,  # stale low score → delta will exceed min
            }
        ],
    )
    db_path = tmp_path / "test_techrad.db"
    conn.close()

    def _open_conn():
        return _tconnect(db_path)

    with (
        patch("tools.db.storage.get_connection", side_effect=_open_conn),
        patch("tools.tech_radar.radar_engine.RadarEngine._gather_signals", return_value=[]),
    ):
        engine = RadarEngine(dry_run=True)
        result = engine.run()

    assert result["entries_assessed"] == 1

    verify = _open_conn()
    row = verify.execute(
        "SELECT current_ring FROM tech_radar_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    verify.close()
    # dry_run → ring must remain 'hold' (no DB update)
    assert row["current_ring"] == "hold"


# ===========================================================================
# Test 6 — RadarEngine.run full cycle persists ring change + history
# ===========================================================================

def test_run_full_cycle_persists_ring_change(tmp_path):
    from tools.tech_radar.radar_engine import RadarEngine

    entry_id = str(uuid.uuid4())
    conn = _make_conn(
        tmp_path,
        seed_entries=[
            {
                "id": entry_id,
                "name": "trivy",
                "current_ring": "assess",
                "ecosystem_maturity": 0.84,
                "icdev_fit": 0.86,
                "airgap_compat": 0.85,
                "il_compliance": 0.80,
                "composite_score": 0.10,  # stale → forces delta
            }
        ],
    )
    db_path = tmp_path / "test_techrad.db"
    conn.close()

    def _open_conn():
        return _tconnect(db_path)

    with (
        patch("tools.db.storage.get_connection", side_effect=_open_conn),
        patch("tools.tech_radar.radar_engine.RadarEngine._gather_signals", return_value=[]),
    ):
        engine = RadarEngine(dry_run=False)
        result = engine.run()

    assert result["entries_assessed"] == 1

    verify = _open_conn()
    updated = verify.execute(
        "SELECT current_ring, composite_score FROM tech_radar_entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    history = verify.execute(
        "SELECT from_ring, to_ring FROM tech_radar_history WHERE entry_id = ?",
        (entry_id,),
    ).fetchone()
    verify.close()

    # Ring must have moved from assess → adopt (trivy baseline + keyword boost → ≥ 0.75)
    assert updated["current_ring"] == "adopt"
    assert updated["composite_score"] > 0.10
    assert history is not None
    assert history["from_ring"] == "assess"
    assert history["to_ring"] == "adopt"
