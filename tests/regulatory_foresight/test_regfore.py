# CUI // SP-CTI
"""Unit tests for the Regulatory Foresight engine (pint-regfore V&V gate).

7 tests:
  1. _content_hash produces 32-char hex string and is deterministic
  2. _ttm_days returns correct day count (past date → 0, future → positive)
  3. ImpactScorer.score time_to_mandate sub-score bounds
  4. ImpactScorer.score icdev_impact sub-score with empty vs non-empty frameworks
  5. ImpactScorer.score blast_radius sub-score stays in [0, 1]
  6. ForesightEngine.run quiet-hours path returns valid JSON with skipped key
  7. ForesightEngine.run full cycle with mocked scanner + DB persists signal

Run: pytest tests/regulatory_foresight/ -v
"""

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import os
os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ---------------------------------------------------------------------------
# Helpers: build a minimal in-memory DB with the required tables
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS regulatory_foresight_signals (
    id                     TEXT PRIMARY KEY,
    source                 TEXT,
    doc_id                 TEXT,
    title                  TEXT,
    url                    TEXT,
    proposed_at            TEXT,
    comment_deadline       TEXT,
    estimated_mandate_date TEXT,
    affected_frameworks    TEXT,
    icdev_impact_areas     TEXT,
    time_to_mandate_days   INTEGER,
    icdev_impact_score     REAL,
    blast_radius_score     REAL,
    composite_score        REAL,
    status                 TEXT DEFAULT 'new',
    innovation_signal_id   TEXT,
    scanned_at             TEXT NOT NULL,
    classification         TEXT DEFAULT 'CUI // SP-CTI'
);

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


def _make_conn(tmp_path):
    db = tmp_path / "test_regfore.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    return conn


def _raw_signal(**overrides):
    base = {
        "id": "aabbccddeeff00112233445566778899",
        "source": "federal_register",
        "doc_id": "FR-2026-001",
        "title": "Proposed NIST AI risk management rule",
        "url": "https://example.gov/doc/FR-2026-001",
        "proposed_at": "2026-01-01",
        "comment_deadline": None,
        "estimated_mandate_date": None,
        "affected_frameworks": json.dumps(["NIST AI RMF", "FedRAMP"]),
        "icdev_impact_areas": json.dumps(["AI Security", "Compliance"]),
        "time_to_mandate_days": 200,
        "icdev_impact_score": None,
        "blast_radius_score": None,
        "composite_score": None,
        "status": "new",
        "innovation_signal_id": None,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "classification": "CUI // SP-CTI",
    }
    base.update(overrides)
    return base


# ===========================================================================
# Test 1 — _content_hash
# ===========================================================================

def test_content_hash_deterministic():
    from tools.regulatory_foresight.foresight_engine import _content_hash
    h = _content_hash("some-signal-id")
    assert isinstance(h, str)
    assert len(h) == 32
    assert h == _content_hash("some-signal-id"), "hash must be deterministic"
    assert h != _content_hash("other-signal-id"), "different input → different hash"


# ===========================================================================
# Test 2 — _ttm_days
# ===========================================================================

def test_ttm_days_future_and_past():
    from tools.regulatory_foresight.foresight_engine import _ttm_days

    future = (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%d")
    past = "2020-01-01"

    days_future = _ttm_days({"estimated_mandate_date": future})
    assert days_future is not None and days_future > 0

    days_past = _ttm_days({"estimated_mandate_date": past})
    assert days_past == 0

    days_none = _ttm_days({"estimated_mandate_date": None})
    assert days_none is None


# ===========================================================================
# Test 3 — ImpactScorer: time_to_mandate sub-score
# ===========================================================================

def test_impact_scorer_time_to_mandate_bounds():
    from tools.regulatory_foresight.impact_scorer import ImpactScorer
    scorer = ImpactScorer()

    # < 180 days → 1.0
    sig_urgent = _raw_signal(time_to_mandate_days=90)
    scored = scorer.score(sig_urgent)
    assert scored["time_to_mandate_score"] == 1.0

    # > 730 days → 0.1
    sig_far = _raw_signal(time_to_mandate_days=1000)
    scored_far = scorer.score(sig_far)
    assert scored_far["time_to_mandate_score"] == 0.1

    # composite_score in [0, 1]
    assert 0.0 <= scored["composite_score"] <= 1.0


# ===========================================================================
# Test 4 — ImpactScorer: icdev_impact sub-score
# ===========================================================================

def test_impact_scorer_icdev_impact_empty_vs_nonempty():
    from tools.regulatory_foresight.impact_scorer import ImpactScorer
    scorer = ImpactScorer()

    sig_empty = _raw_signal(affected_frameworks=json.dumps([]))
    scored_empty = scorer.score(sig_empty)
    assert scored_empty["icdev_impact_score"] == 0.0

    # Non-empty frameworks — score must be >= 0
    sig_filled = _raw_signal(affected_frameworks=json.dumps(["NIST 800-53", "FedRAMP"]))
    scored_filled = scorer.score(sig_filled)
    assert scored_filled["icdev_impact_score"] >= 0.0


# ===========================================================================
# Test 5 — ImpactScorer: blast_radius sub-score
# ===========================================================================

def test_impact_scorer_blast_radius_in_bounds():
    from tools.regulatory_foresight.impact_scorer import ImpactScorer
    scorer = ImpactScorer()

    sig = _raw_signal(
        title="Proposed rule on AI security and compliance certification",
        affected_frameworks=json.dumps(["NIST AI RMF"]),
        icdev_impact_areas=json.dumps(["security", "compliance"]),
    )
    scored = scorer.score(sig)
    assert 0.0 <= scored["blast_radius_score"] <= 1.0


# ===========================================================================
# Test 6 — ForesightEngine.run quiet hours
# ===========================================================================

def test_foresight_engine_quiet_hours_returns_valid_json():
    from tools.regulatory_foresight.foresight_engine import ForesightEngine

    cfg_with_quiet = {
        "quiet_hours": {"enabled": True, "start": "00:00", "end": "23:59"}
    }
    with patch(
        "tools.regulatory_foresight.foresight_engine._load_config",
        return_value=cfg_with_quiet,
    ):
        engine = ForesightEngine()
        result = engine.run()

    assert isinstance(result, dict), "run() must return a dict"
    # must be JSON-serialisable
    json.dumps(result)
    assert result.get("skipped") == "quiet_hours"
    assert result["scanned"] == 0
    assert result["new"] == 0


# ===========================================================================
# Test 7 — ForesightEngine.run: full cycle persists new signal
# ===========================================================================

def test_foresight_engine_run_persists_signal(tmp_path):
    from tools.regulatory_foresight.foresight_engine import ForesightEngine

    sig = _raw_signal(id=f"test{uuid.uuid4().hex[:18]}", composite_score=0.30)

    db_path = tmp_path / "test_regfore.db"
    # Pre-create DB with schema
    setup_conn = sqlite3.connect(str(db_path))
    setup_conn.row_factory = sqlite3.Row
    setup_conn.executescript(_DDL)
    setup_conn.close()

    # foresight_engine authors %s placeholders for PostgreSQL and relies on
    # StorageConnection to rewrite them; a bare sqlite3 connection drops that
    # layer and every statement raises `near "%": syntax error`.
    from _sql_compat import connect as _tconnect

    def _open_conn():
        return _tconnect(db_path)

    def _fake_scanner():
        return [sig]

    fake_source_scanners = {"mock_source": _fake_scanner}

    with (
        patch(
            "tools.regulatory_foresight.foresight_engine._load_config",
            return_value={"auto_signal_threshold": 0.70, "quiet_hours": {"enabled": False}},
        ),
        patch(
            "tools.regulatory_foresight.source_scanner.SOURCE_SCANNERS",
            fake_source_scanners,
        ),
        patch(
            "tools.regulatory_foresight.impact_scorer.ImpactScorer.score",
            lambda self, s: {
                **s,
                "icdev_impact_score": 0.3,
                "blast_radius_score": 0.3,
                "composite_score": 0.30,
            },
        ),
        patch(
            "tools.db.storage.get_connection",
            side_effect=_open_conn,
        ),
    ):
        engine = ForesightEngine()
        result = engine.run()

    assert isinstance(result, dict)
    assert result["scanned"] >= 1

    verify_conn = _open_conn()
    row = verify_conn.execute(
        "SELECT id FROM regulatory_foresight_signals WHERE id = ?", (sig["id"],)
    ).fetchone()
    verify_conn.close()
    assert row is not None, "signal must be persisted in DB"
