#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/sg/frontline_importer.py — bulk_import function.

Acceptance criteria:
  - Imports 3+ dates of test data without duplicates.
  - Re-running the same range inserts 0 additional rows (idempotent).
  - force=True replaces existing rows.
"""
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sg_conflict_events (
    id              TEXT NOT NULL PRIMARY KEY,
    event_type      TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'low',
    description     TEXT NOT NULL DEFAULT '',
    event_ts        TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    source          TEXT,
    external_id     TEXT,
    snapshot_date   TEXT,
    geometry_json   TEXT,
    properties_json TEXT
);
"""


def _make_feature(name: str, idx: int) -> dict:
    return {
        "type": "Feature",
        "id": f"feat-{idx}",
        "geometry": {
            "type": "LineString",
            "coordinates": [[30.0 + idx * 0.1, 50.0], [30.1 + idx * 0.1, 50.1]],
        },
        "properties": {"name": name},
    }


def _make_fc(n: int = 3) -> list:
    return [_make_feature(f"line-{i}", i) for i in range(n)]


def _count(db_path: str, where: str = "", params: tuple = ()) -> int:
    conn = sqlite3.connect(db_path)
    sql = "SELECT COUNT(*) FROM sg_conflict_events"
    if where:
        sql += f" WHERE {where}"
    count = conn.execute(sql, params).fetchone()[0]
    conn.close()
    return count


@pytest.fixture()
def db_path(tmp_path):
    """Path to a fresh SQLite DB with the sg_conflict_events schema."""
    path = str(tmp_path / "test_sg.db")
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def patched_importer(db_path, monkeypatch):
    """Patch get_connection() and is_pg() so bulk_import hits the test DB."""
    import tools.sg.frontline_importer as fi

    # Translating wrapper: the importer authors %s for PostgreSQL, so a bare
    # sqlite3 connection turned every INSERT into a syntax error.
    from _sql_compat import connect as _tconnect

    monkeypatch.setattr(fi, "get_connection", lambda: _tconnect(db_path, row_factory=False))
    monkeypatch.setattr(fi, "is_pg", lambda: False)
    return db_path


def _mock_fetch(dates_data: dict):
    def _fetch(snapshot_date: str) -> list:
        return dates_data.get(snapshot_date, [])
    return _fetch


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_bulk_import_three_dates(patched_importer):
    """Imports 3 dates; row count equals the sum of all features."""
    db = patched_importer
    dates_data = {
        "2024-01-01": _make_fc(5),
        "2024-01-02": _make_fc(4),
        "2024-01-03": _make_fc(3),
    }
    import tools.sg.frontline_importer as fi

    with patch.object(fi, "fetch_deepstate_geojson", side_effect=_mock_fetch(dates_data)):
        result = fi.bulk_import("2024-01-01", "2024-01-03")

    assert result["fetch_errors"] == 0
    assert result["total_failed"] == 0
    assert result["dates_requested"] == 3
    assert _count(db) == 5 + 4 + 3


def test_bulk_import_no_duplicates_on_rerun(patched_importer):
    """Re-running the same range with force=False inserts 0 additional rows."""
    db = patched_importer
    dates_data = {
        "2024-02-01": _make_fc(6),
        "2024-02-02": _make_fc(6),
        "2024-02-03": _make_fc(6),
    }
    import tools.sg.frontline_importer as fi

    fetch_mock = _mock_fetch(dates_data)
    with patch.object(fi, "fetch_deepstate_geojson", side_effect=fetch_mock):
        fi.bulk_import("2024-02-01", "2024-02-03")

    assert _count(db) == 18

    with patch.object(fi, "fetch_deepstate_geojson", side_effect=fetch_mock):
        result2 = fi.bulk_import("2024-02-01", "2024-02-03")

    assert _count(db) == 18, "Re-run must not insert duplicates"
    assert result2["total_skipped"] == 3


def test_bulk_import_force_replaces(patched_importer):
    """force=True deletes existing rows before re-inserting."""
    db = patched_importer
    dates_data = {
        "2024-03-01": _make_fc(4),
        "2024-03-02": _make_fc(4),
        "2024-03-03": _make_fc(4),
    }
    import tools.sg.frontline_importer as fi

    with patch.object(fi, "fetch_deepstate_geojson", side_effect=_mock_fetch(dates_data)):
        fi.bulk_import("2024-03-01", "2024-03-03")

    with patch.object(fi, "fetch_deepstate_geojson", side_effect=_mock_fetch(dates_data)):
        result = fi.bulk_import("2024-03-01", "2024-03-03", force=True)

    assert _count(db) == 12
    assert result["total_skipped"] == 0


def test_bulk_import_dry_run_no_writes(patched_importer):
    """dry_run=True fetches but writes nothing to the DB."""
    db = patched_importer
    dates_data = {
        "2024-04-01": _make_fc(5),
        "2024-04-02": _make_fc(5),
        "2024-04-03": _make_fc(5),
    }
    import tools.sg.frontline_importer as fi

    with patch.object(fi, "fetch_deepstate_geojson", side_effect=_mock_fetch(dates_data)):
        result = fi.bulk_import("2024-04-01", "2024-04-03", dry_run=True)

    assert _count(db) == 0
    assert result["dry_run"] is True


def test_bulk_import_snapshot_date_stored(patched_importer):
    """Each row carries the correct snapshot_date."""
    db = patched_importer
    import tools.sg.frontline_importer as fi

    dates_data = {
        "2024-05-01": _make_fc(2),
        "2024-05-02": _make_fc(2),
        "2024-05-03": _make_fc(2),
    }
    with patch.object(fi, "fetch_deepstate_geojson", side_effect=_mock_fetch(dates_data)):
        fi.bulk_import("2024-05-01", "2024-05-03")

    for d in ["2024-05-01", "2024-05-02", "2024-05-03"]:
        count = _count(db, "snapshot_date = ?", (d,))
        assert count == 2, f"Expected 2 rows for {d}, got {count}"


def test_bulk_import_invalid_date_order():
    """Raises ValueError when end_date < start_date."""
    import tools.sg.frontline_importer as fi

    with pytest.raises(ValueError, match="end_date must be >= start_date"):
        fi.bulk_import("2024-01-31", "2024-01-01")
