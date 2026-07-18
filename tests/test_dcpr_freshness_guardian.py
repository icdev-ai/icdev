# CUI // SP-CTI
"""Tests for Data Canvas Freshness Guardian (dcpr-fix-02).

Regression coverage for the "always fresh" bug in
``tools.data_canvas.freshness_guardian.check_profile_freshness``:

  * data_profiler emits the table name under ``name`` (not ``table_name``),
    so the guardian used to report every table as ``?``.
  * The guardian used to fall back to ``profiled_at`` (always "now") for the
    last-modified signal, so every table looked "fresh" regardless of data age.

These tests feed profiler-shaped dicts (the exact shape emitted by
``data_profiler.profile_table``) and assert that a stale table reports stale
and a fresh one reports fresh — using the storage-layer-independent pure
functions, so no raw sqlite3 is involved.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

fg = importlib.import_module("tools.data_canvas.freshness_guardian")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _profiler_table(name: str, *, newest_ts: str | None = None, row_count: int = 100,
                    extra: dict | None = None) -> dict:
    """Build a table dict shaped like data_profiler.profile_table output.

    When ``newest_ts`` is given, it becomes the ``max`` of a datetime column —
    the only real last-modified signal the profiler surfaces.
    """
    columns = [
        {"name": "id", "type_str": "INTEGER", "inferred_type": "numeric",
         "min": "1", "max": str(row_count)},
        {"name": "label", "type_str": "TEXT", "inferred_type": "string",
         "min": None, "max": None},
    ]
    if newest_ts is not None:
        columns.append({
            "name": "created_at", "type_str": "TIMESTAMP",
            "inferred_type": "datetime", "min": newest_ts, "max": newest_ts,
        })
    tbl = {
        "name": name,
        "row_count": row_count,
        "columns": columns,
        "classification": "CUI // SP-CTI",
        # profiled_at is always "now" — the guardian must NOT use it as the signal.
        "profiled_at": _iso(datetime.now(timezone.utc)),
    }
    if extra:
        tbl.update(extra)
    return tbl


def _profile(*tables: dict) -> dict:
    return {"db_name": "test.db", "tables": list(tables)}


# ── fresh case ────────────────────────────────────────────────────────────────

def test_fresh_table_reports_fresh():
    now = datetime.now(timezone.utc)
    profile = _profile(_profiler_table("orders", newest_ts=_iso(now - timedelta(hours=1))))
    result = fg.check_profile_freshness(profile, stale_hours=24, critical_hours=168)

    assert result["overall_status"] == "fresh"
    assert result["summary"]["fresh"] == 1
    tbl = result["tables"][0]
    assert tbl["table"] == "orders"          # name read from "name", not "?"
    assert tbl["status"] == "fresh"


# ── stale case ────────────────────────────────────────────────────────────────

def test_stale_table_reports_stale():
    now = datetime.now(timezone.utc)
    profile = _profile(_profiler_table("orders", newest_ts=_iso(now - timedelta(hours=48))))
    result = fg.check_profile_freshness(profile, stale_hours=24, critical_hours=168)

    assert result["overall_status"] == "stale"
    assert result["summary"]["stale"] == 1
    assert result["tables"][0]["status"] == "stale"
    assert result["tables"][0]["table"] == "orders"


def test_critical_table_reports_critical():
    now = datetime.now(timezone.utc)
    profile = _profile(_profiler_table("orders", newest_ts=_iso(now - timedelta(days=10))))
    result = fg.check_profile_freshness(profile, stale_hours=24, critical_hours=168)

    assert result["overall_status"] == "critical"
    assert result["tables"][0]["status"] == "critical"


# ── the always-fresh regression ───────────────────────────────────────────────

def test_no_datetime_column_reports_unknown_not_fresh():
    # A table whose only timestamp is profiled_at (always now) has no real
    # freshness signal — it must be "unknown", never "fresh".
    profile = _profile(_profiler_table("dim_lookup", newest_ts=None))
    result = fg.check_profile_freshness(profile, stale_hours=24, critical_hours=168)

    tbl = result["tables"][0]
    assert tbl["status"] == "unknown"
    assert tbl["table"] == "dim_lookup"
    assert result["overall_status"] == "unknown"


def test_profiled_at_is_not_used_as_freshness_signal():
    # Even with a recent profiled_at, an old data timestamp must win => stale.
    now = datetime.now(timezone.utc)
    tbl = _profiler_table("orders", newest_ts=_iso(now - timedelta(hours=100)))
    assert tbl["profiled_at"]  # present and recent
    result = fg.check_profile_freshness(_profile(tbl), stale_hours=24, critical_hours=168)
    assert result["tables"][0]["status"] == "stale"


# ── explicit last_modified precedence ─────────────────────────────────────────

def test_explicit_last_modified_takes_precedence():
    now = datetime.now(timezone.utc)
    # Old datetime column, but a fresh explicit last_modified => fresh.
    tbl = _profiler_table("orders", newest_ts=_iso(now - timedelta(days=30)))
    tbl["last_modified"] = _iso(now - timedelta(hours=2))
    result = fg.check_profile_freshness(_profile(tbl), stale_hours=24, critical_hours=168)
    assert result["tables"][0]["status"] == "fresh"


# ── derivation helper unit coverage ───────────────────────────────────────────

def test_derive_last_modified_picks_newest_datetime_column():
    now = datetime.now(timezone.utc)
    older = _iso(now - timedelta(days=5))
    newer = _iso(now - timedelta(hours=1))
    tbl = {
        "name": "events",
        "columns": [
            {"name": "created_at", "inferred_type": "datetime", "max": older},
            {"name": "updated_at", "inferred_type": "datetime", "max": newer},
        ],
    }
    assert fg._derive_last_modified(tbl) == newer


def test_derive_last_modified_none_when_no_datetime():
    tbl = {"name": "t", "columns": [{"name": "id", "inferred_type": "numeric", "max": "9"}]}
    assert fg._derive_last_modified(tbl) is None


def test_mixed_profile_summary_and_overall():
    now = datetime.now(timezone.utc)
    profile = _profile(
        _profiler_table("fresh_tbl", newest_ts=_iso(now - timedelta(hours=1))),
        _profiler_table("stale_tbl", newest_ts=_iso(now - timedelta(hours=48))),
        _profiler_table("no_ts_tbl", newest_ts=None),
    )
    result = fg.check_profile_freshness(profile, stale_hours=24, critical_hours=168)
    assert result["summary"]["fresh"] == 1
    assert result["summary"]["stale"] == 1
    assert result["summary"]["unknown"] == 1
    assert result["overall_status"] == "stale"
