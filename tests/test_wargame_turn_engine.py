#!/usr/bin/env python3
# CUI // SP-CTI
"""pytest unit tests for tools.strategos.wargame_turn_engine."""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.strategos.wargame_turn_engine import _SMALL_FORCE_THRESHOLD, advance_turn

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_WARGAME_SCHEMA = """
CREATE TABLE IF NOT EXISTS sg_wargames (
    id          TEXT PRIMARY KEY,
    blue_strength REAL,
    red_strength  REAL,
    attrition_coefficients_json TEXT,
    blue_force  TEXT,
    red_force   TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS sg_wargame_turns (
    id             TEXT PRIMARY KEY,
    wargame_id     TEXT NOT NULL,
    turn_number    INTEGER NOT NULL,
    blue_losses    INTEGER,
    red_losses     INTEGER,
    blue_remaining INTEGER,
    red_remaining  INTEGER,
    tempo_delta    REAL,
    notes          TEXT,
    created_at     TEXT
);

CREATE TABLE IF NOT EXISTS sg_ooda_events (
    id         TEXT PRIMARY KEY,
    wargame_id TEXT,
    side       TEXT,
    domain     TEXT,
    phase      TEXT,
    latency_s  REAL,
    created_at TEXT
);
"""

_WARGAME_ID = "test-wg-001"
# beta=0.01, rho=0.01, b0=1000, r0=800, dt=1:
#   new_b = 1000 - 0.01*800 = 992  →  blue_losses = 8
#   new_r =  800 - 0.01*1000 = 790  →  red_losses  = 10
_B0 = 1000.0
_R0 = 800.0
_BETA = 0.01
_RHO  = 0.01


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_db(tmp_path, name, wargame_id, blue, red, beta=_BETA, rho=_RHO):
    db_path = tmp_path / name
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_WARGAME_SCHEMA)
    conn.execute(
        "INSERT INTO sg_wargames "
        "(id, blue_strength, red_strength, attrition_coefficients_json, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (wargame_id, blue, red, json.dumps({"beta": beta, "rho": rho})),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def wargame_db(tmp_path, monkeypatch):
    """Temp SQLite DB — standard wargame with large forces (square-law path)."""
    db_path = _make_db(tmp_path, "icdev.db", _WARGAME_ID, _B0, _R0)
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    return db_path


# ---------------------------------------------------------------------------
# TestReturnShape — advance_turn returns correct dict keys
# ---------------------------------------------------------------------------

class TestReturnShape:
    """advance_turn returns a dict containing all sg_wargame_turns columns
    plus 'lanchester' and 'blotto' summary sub-dicts."""

    def test_top_level_keys(self, wargame_db):
        result = advance_turn(_WARGAME_ID)
        expected = {
            "id", "wargame_id", "turn_number",
            "blue_losses", "red_losses",
            "blue_remaining", "red_remaining",
            "tempo_delta", "notes", "created_at",
            "lanchester", "blotto",
        }
        assert expected.issubset(result.keys())

    def test_wargame_id_matches(self, wargame_db):
        assert advance_turn(_WARGAME_ID)["wargame_id"] == _WARGAME_ID

    def test_lanchester_subdict_has_required_keys(self, wargame_db):
        lanch = advance_turn(_WARGAME_ID)["lanchester"]
        assert {"model", "final_b", "final_r", "winner"}.issubset(lanch.keys())

    def test_blotto_subdict_is_dict(self, wargame_db):
        assert isinstance(advance_turn(_WARGAME_ID)["blotto"], dict)

    def test_notes_is_valid_json(self, wargame_db):
        notes = advance_turn(_WARGAME_ID)["notes"]
        parsed = json.loads(notes)
        assert "lanchester_model" in parsed
        assert "blotto_winner" in parsed


# ---------------------------------------------------------------------------
# TestLanchesterDecay — strength reduction follows Lanchester square law
# ---------------------------------------------------------------------------

class TestLanchesterDecay:
    """advance_turn applies Lanchester attrition correctly."""

    def test_square_model_selected_for_large_forces(self, wargame_db):
        assert _B0 >= _SMALL_FORCE_THRESHOLD
        assert _R0 >= _SMALL_FORCE_THRESHOLD
        result = advance_turn(_WARGAME_ID)
        assert result["lanchester"]["model"] == "lanchester_square"

    def test_blue_remaining_square_decay(self, wargame_db):
        # new_b = b0 - rho*r0*dt = 1000 - 0.01*800 = 992
        assert advance_turn(_WARGAME_ID)["blue_remaining"] == 992

    def test_red_remaining_square_decay(self, wargame_db):
        # new_r = r0 - beta*b0*dt = 800 - 0.01*1000 = 790
        assert advance_turn(_WARGAME_ID)["red_remaining"] == 790

    def test_blue_losses_correct(self, wargame_db):
        # blue_losses = b0 - new_b = 1000 - 992 = 8
        assert advance_turn(_WARGAME_ID)["blue_losses"] == 8

    def test_red_losses_correct(self, wargame_db):
        # red_losses = r0 - new_r = 800 - 790 = 10
        assert advance_turn(_WARGAME_ID)["red_losses"] == 10

    def test_sg_wargames_updated_after_turn(self, wargame_db):
        """DB row for blue/red strength must reflect the attrition."""
        advance_turn(_WARGAME_ID)
        conn = sqlite3.connect(str(wargame_db))
        row = conn.execute(
            "SELECT blue_strength, red_strength FROM sg_wargames WHERE id=?",
            (_WARGAME_ID,),
        ).fetchone()
        conn.close()
        assert row[0] == 992
        assert row[1] == 790

    def test_remaining_in_result_matches_db(self, wargame_db):
        result = advance_turn(_WARGAME_ID)
        conn = sqlite3.connect(str(wargame_db))
        row = conn.execute(
            "SELECT blue_strength, red_strength FROM sg_wargames WHERE id=?",
            (_WARGAME_ID,),
        ).fetchone()
        conn.close()
        assert result["blue_remaining"] == row[0]
        assert result["red_remaining"] == row[1]

    def test_linear_model_selected_for_small_forces(self, tmp_path, monkeypatch):
        """When either side is below _SMALL_FORCE_THRESHOLD, linear law is used."""
        db_path = _make_db(tmp_path, "small.db", "small-001", 30.0, 20.0,
                           beta=0.05, rho=0.05)
        monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        result = advance_turn("small-001")
        assert result["lanchester"]["model"] == "lanchester_linear"

    def test_losses_are_nonnegative(self, wargame_db):
        result = advance_turn(_WARGAME_ID)
        assert result["blue_losses"] >= 0
        assert result["red_losses"] >= 0


# ---------------------------------------------------------------------------
# TestTurnNumberIncrement — turn_number increments on each advance_turn call
# ---------------------------------------------------------------------------

class TestTurnNumberIncrement:
    """turn_number starts at 1 and increments by 1 on every call."""

    def test_first_turn_number_is_one(self, wargame_db):
        assert advance_turn(_WARGAME_ID)["turn_number"] == 1

    def test_second_call_is_two(self, wargame_db):
        advance_turn(_WARGAME_ID)
        assert advance_turn(_WARGAME_ID)["turn_number"] == 2

    def test_three_successive_turns(self, wargame_db):
        for expected_turn in (1, 2, 3):
            result = advance_turn(_WARGAME_ID)
            assert result["turn_number"] == expected_turn

    def test_turn_rows_persisted_to_db(self, wargame_db):
        advance_turn(_WARGAME_ID)
        advance_turn(_WARGAME_ID)
        conn = sqlite3.connect(str(wargame_db))
        count = conn.execute(
            "SELECT COUNT(*) FROM sg_wargame_turns WHERE wargame_id=?",
            (_WARGAME_ID,),
        ).fetchone()[0]
        conn.close()
        assert count == 2

    def test_independent_wargames_have_separate_counters(self, tmp_path, monkeypatch):
        db_path = _make_db(tmp_path, "multi.db", "wg-A", _B0, _R0)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO sg_wargames "
            "(id, blue_strength, red_strength, attrition_coefficients_json, updated_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            ("wg-B", _B0, _R0, json.dumps({"beta": _BETA, "rho": _RHO})),
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

        advance_turn("wg-A")
        advance_turn("wg-A")
        r_b = advance_turn("wg-B")
        assert r_b["turn_number"] == 1  # wg-B untouched by wg-A advances


# ---------------------------------------------------------------------------
# TestErrorHandling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """advance_turn raises ValueError when wargame does not exist."""

    def test_missing_wargame_raises_value_error(self, wargame_db):
        with pytest.raises(ValueError, match="not found"):
            advance_turn("nonexistent-id")
