#!/usr/bin/env python3
# CUI // SP-CTI
"""pytest unit tests for tools.strategos.wargame_orbat."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.strategos.wargame_orbat import load_orbat_strengths

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_ORBAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS sg_wargames (
    id            TEXT PRIMARY KEY,
    conflict_id   TEXT,
    blue_strength INTEGER DEFAULT 0,
    red_strength  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sg_orbat_units (
    id             TEXT PRIMARY KEY,
    unit_name      TEXT,
    conflict_id    TEXT,
    side           TEXT,
    strength_value INTEGER DEFAULT 0
);
"""

_WARGAME_ID = "test-wg-orbat-01"
_CONFLICT_ID = "conflict-alpha-01"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path, name, wargame_id, conflict_id=_CONFLICT_ID, units=None):
    """Create a temp SQLite DB and optionally seed ORBAT units.

    units: list of (unit_id, side, strength_value) tuples
    """
    db_path = tmp_path / name
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_ORBAT_SCHEMA)
    conn.execute(
        "INSERT INTO sg_wargames (id, conflict_id, blue_strength, red_strength) "
        "VALUES (?, ?, 0, 0)",
        (wargame_id, conflict_id),
    )
    for uid, side, sv in (units or []):
        conn.execute(
            "INSERT INTO sg_orbat_units (id, unit_name, conflict_id, side, strength_value) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, uid, conflict_id, side, sv),
        )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def orbat_db(tmp_path, monkeypatch):
    """DB with two blue units (300+200=500) and two red units (400+100=500)."""
    units = [
        ("blue-1", "blue", 300),
        ("blue-2", "blue", 200),
        ("red-1",  "red",  400),
        ("red-2",  "red",  100),
    ]
    db_path = _make_db(tmp_path, "icdev.db", _WARGAME_ID, units=units)
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    return db_path


@pytest.fixture
def empty_orbat_db(tmp_path, monkeypatch):
    """DB with a wargame but no ORBAT units for its conflict_id."""
    db_path = _make_db(tmp_path, "icdev.db", _WARGAME_ID, units=[])
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    return db_path


@pytest.fixture
def null_conflict_db(tmp_path, monkeypatch):
    """DB with a wargame whose conflict_id is NULL."""
    db_path = _make_db(tmp_path, "icdev.db", _WARGAME_ID, conflict_id=None, units=[])
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    return db_path


# ---------------------------------------------------------------------------
# TestReturnShape — load_orbat_strengths returns required keys and types
# ---------------------------------------------------------------------------

class TestReturnShape:
    """load_orbat_strengths returns a dict with the three expected keys."""

    def test_has_blue_strength_key(self, orbat_db):
        assert "blue_strength" in load_orbat_strengths(_WARGAME_ID)

    def test_has_red_strength_key(self, orbat_db):
        assert "red_strength" in load_orbat_strengths(_WARGAME_ID)

    def test_has_unit_count_key(self, orbat_db):
        assert "unit_count" in load_orbat_strengths(_WARGAME_ID)

    def test_values_are_integers(self, orbat_db):
        result = load_orbat_strengths(_WARGAME_ID)
        assert isinstance(result["blue_strength"], int)
        assert isinstance(result["red_strength"], int)
        assert isinstance(result["unit_count"], int)


# ---------------------------------------------------------------------------
# TestCorrectTotals — sums are accurate across multiple units per side
# ---------------------------------------------------------------------------

class TestCorrectTotals:
    """load_orbat_strengths returns correct sums when ORBAT units exist."""

    def test_blue_strength_is_correct(self, orbat_db):
        # blue-1 (300) + blue-2 (200) = 500
        assert load_orbat_strengths(_WARGAME_ID)["blue_strength"] == 500

    def test_red_strength_is_correct(self, orbat_db):
        # red-1 (400) + red-2 (100) = 500
        assert load_orbat_strengths(_WARGAME_ID)["red_strength"] == 500

    def test_unit_count_is_correct(self, orbat_db):
        # 4 units total (2 blue + 2 red)
        assert load_orbat_strengths(_WARGAME_ID)["unit_count"] == 4

    def test_uneven_sides(self, tmp_path, monkeypatch):
        """Blue dominance: 900 vs 150."""
        units = [
            ("b1", "blue", 500),
            ("b2", "blue", 400),
            ("r1", "red",  150),
        ]
        db_path = _make_db(tmp_path, "icdev.db", "wg-uneven", units=units)
        monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        result = load_orbat_strengths("wg-uneven")
        assert result["blue_strength"] == 900
        assert result["red_strength"] == 150
        assert result["unit_count"] == 3

    def test_neutral_units_not_counted_in_blue_or_red(self, tmp_path, monkeypatch):
        """Units with side='neutral' must not inflate blue or red totals."""
        units = [
            ("b1",  "blue",    200),
            ("r1",  "red",     100),
            ("n1",  "neutral", 999),
        ]
        db_path = _make_db(tmp_path, "icdev.db", "wg-neutral", units=units)
        monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        result = load_orbat_strengths("wg-neutral")
        assert result["blue_strength"] == 200
        assert result["red_strength"] == 100

    def test_zero_strength_units_are_included_in_count(self, tmp_path, monkeypatch):
        """Units with strength_value=0 still increment unit_count."""
        units = [
            ("b1", "blue", 0),
            ("r1", "red",  0),
        ]
        db_path = _make_db(tmp_path, "icdev.db", "wg-zero", units=units)
        monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        result = load_orbat_strengths("wg-zero")
        assert result["unit_count"] == 2
        assert result["blue_strength"] == 0
        assert result["red_strength"] == 0


# ---------------------------------------------------------------------------
# TestNoUnits — zeros returned gracefully when no ORBAT units exist
# ---------------------------------------------------------------------------

class TestNoUnits:
    """load_orbat_strengths returns all zeros when no matching ORBAT units."""

    def test_blue_strength_is_zero_when_no_units(self, empty_orbat_db):
        assert load_orbat_strengths(_WARGAME_ID)["blue_strength"] == 0

    def test_red_strength_is_zero_when_no_units(self, empty_orbat_db):
        assert load_orbat_strengths(_WARGAME_ID)["red_strength"] == 0

    def test_unit_count_is_zero_when_no_units(self, empty_orbat_db):
        assert load_orbat_strengths(_WARGAME_ID)["unit_count"] == 0

    def test_null_conflict_id_yields_zero_totals(self, null_conflict_db):
        """Wargame with NULL conflict_id: no units to sum → all zeros."""
        result = load_orbat_strengths(_WARGAME_ID)
        assert result["blue_strength"] == 0
        assert result["red_strength"] == 0
        assert result["unit_count"] == 0


# ---------------------------------------------------------------------------
# TestDatabaseUpdate — sg_wargames columns are persisted after the call
# ---------------------------------------------------------------------------

class TestDatabaseUpdate:
    """load_orbat_strengths writes blue_strength and red_strength to sg_wargames."""

    def _read_wargame(self, db_path, wargame_id):
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT blue_strength, red_strength FROM sg_wargames WHERE id=?",
            (wargame_id,),
        ).fetchone()
        conn.close()
        return row

    def test_blue_strength_persisted(self, orbat_db):
        load_orbat_strengths(_WARGAME_ID)
        row = self._read_wargame(orbat_db, _WARGAME_ID)
        assert row[0] == 500

    def test_red_strength_persisted(self, orbat_db):
        load_orbat_strengths(_WARGAME_ID)
        row = self._read_wargame(orbat_db, _WARGAME_ID)
        assert row[1] == 500

    def test_db_values_match_return_value(self, orbat_db):
        result = load_orbat_strengths(_WARGAME_ID)
        row = self._read_wargame(orbat_db, _WARGAME_ID)
        assert result["blue_strength"] == row[0]
        assert result["red_strength"] == row[1]

    def test_db_reset_to_zero_when_no_units(self, empty_orbat_db):
        """Even when units are absent the UPDATE still fires (zeroes persist)."""
        conn = sqlite3.connect(str(empty_orbat_db))
        conn.execute(
            "UPDATE sg_wargames SET blue_strength=999, red_strength=999 WHERE id=?",
            (_WARGAME_ID,),
        )
        conn.commit()
        conn.close()

        load_orbat_strengths(_WARGAME_ID)
        row = self._read_wargame(empty_orbat_db, _WARGAME_ID)
        assert row[0] == 0
        assert row[1] == 0

    def test_second_call_reflects_updated_units(self, tmp_path, monkeypatch):
        """A second call after adding units in the DB should reflect new totals."""
        units = [("b1", "blue", 100)]
        db_path = _make_db(tmp_path, "icdev.db", "wg-update", units=units)
        monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

        first = load_orbat_strengths("wg-update")
        assert first["blue_strength"] == 100

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO sg_orbat_units (id, unit_name, conflict_id, side, strength_value) "
            "VALUES (?, ?, ?, ?, ?)",
            ("b2", "b2", _CONFLICT_ID, "blue", 150),
        )
        conn.commit()
        conn.close()

        second = load_orbat_strengths("wg-update")
        assert second["blue_strength"] == 250


# ---------------------------------------------------------------------------
# TestErrorHandling — ValueError for missing wargame row
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """load_orbat_strengths raises ValueError when the wargame does not exist."""

    def test_missing_wargame_raises_value_error(self, orbat_db):
        with pytest.raises(ValueError, match="not found"):
            load_orbat_strengths("nonexistent-wargame-id")

    def test_error_message_contains_wargame_id(self, orbat_db):
        bad_id = "bad-id-xyz"
        with pytest.raises(ValueError, match=bad_id):
            load_orbat_strengths(bad_id)
