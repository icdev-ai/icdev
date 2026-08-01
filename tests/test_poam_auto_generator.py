# CUI // SP-CTI
"""Unit tests for tools/compliance/poam_auto_generator.py — dt-bdc-08.

POA&M auto-generator from violations in compliance_snapshots.

Tests (RED → GREEN → REFACTOR):
  1. generate_poam_items returns empty list when no violations exist
  2. generate_poam_items returns items for not_satisfied rows
  3. generate_poam_items returns items for partially_satisfied rows
  4. generate_poam_items excludes satisfied rows
  5. generate_poam_items excludes not_applicable rows
  6. severity_for_status maps not_satisfied → high, partially_satisfied → moderate
  7. milestone_date_for_severity returns ISO-8601 date string
  8. build_poam_item produces required keys including snapshot_id, framework_id, control_id
  9. run_auto_generator writes items to poam_items table (DB integration)
 10. run_auto_generator is idempotent — re-running does not duplicate items
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from tools.compliance.poam_auto_generator import (
    build_poam_item,
    generate_poam_items,
    milestone_date_for_severity,
    run_auto_generator,
    severity_for_status,
)

# ---------------------------------------------------------------------------
# Shared schema helpers
# ---------------------------------------------------------------------------

_SNAPSHOTS_DDL = """
CREATE TABLE compliance_snapshots (
    snapshot_id   TEXT NOT NULL PRIMARY KEY,
    project_id    TEXT NOT NULL,
    framework_id  TEXT NOT NULL,
    control_id    TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN (
                      'satisfied',
                      'not_satisfied',
                      'partially_satisfied',
                      'not_applicable',
                      'planned'
                  )),
    evidence_ref  TEXT,
    taken_at      TEXT NOT NULL
);
"""

_POAM_DDL = """
CREATE TABLE IF NOT EXISTS poam_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id          TEXT NOT NULL,
    weakness_id         TEXT,
    weakness_description TEXT,
    severity            TEXT,
    source              TEXT,
    control_id          TEXT,
    status              TEXT DEFAULT 'open',
    corrective_action   TEXT,
    milestone_date      TEXT,
    completion_date     TEXT,
    responsible_party   TEXT,
    resources_required  TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);
"""


def _make_conn(snapshots: list[dict]) -> sqlite3.Connection:
    """Create an in-memory DB pre-loaded with provided snapshot rows."""
    # Translating wrapper — poam_auto_generator authors %s for PostgreSQL.
    from _sql_compat import connect as _tconnect

    conn = _tconnect(":memory:")
    conn.executescript(_SNAPSHOTS_DDL + _POAM_DDL)
    for s in snapshots:
        conn.execute(
            "INSERT INTO compliance_snapshots "
            "(snapshot_id, project_id, framework_id, control_id, status, evidence_ref, taken_at) "
            "VALUES (:snapshot_id, :project_id, :framework_id, :control_id, :status, :evidence_ref, :taken_at)",
            s,
        )
    conn.commit()
    return conn


def _snap(
    status: str,
    project_id: str = "proj-1",
    framework_id: str = "fedramp-moderate",
    control_id: str = "AC-2",
    evidence_ref: str | None = None,
) -> dict:
    return {
        "snapshot_id": str(uuid.uuid4()),
        "project_id": project_id,
        "framework_id": framework_id,
        "control_id": control_id,
        "status": status,
        "evidence_ref": evidence_ref,
        "taken_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Test 1 — empty result when no violations
# ---------------------------------------------------------------------------

def test_generate_poam_items_empty_when_no_violations() -> None:
    conn = _make_conn([_snap("satisfied"), _snap("not_applicable")])
    items = generate_poam_items(conn)
    assert items == []
    conn.close()


# ---------------------------------------------------------------------------
# Test 2 — not_satisfied rows produce items
# ---------------------------------------------------------------------------

def test_generate_poam_items_includes_not_satisfied() -> None:
    conn = _make_conn([_snap("not_satisfied", control_id="IA-5")])
    items = generate_poam_items(conn)
    assert len(items) == 1
    assert items[0]["control_id"] == "IA-5"
    assert items[0]["snapshot_status"] == "not_satisfied"
    conn.close()


# ---------------------------------------------------------------------------
# Test 3 — partially_satisfied rows produce items
# ---------------------------------------------------------------------------

def test_generate_poam_items_includes_partially_satisfied() -> None:
    conn = _make_conn([_snap("partially_satisfied", control_id="AU-12")])
    items = generate_poam_items(conn)
    assert len(items) == 1
    assert items[0]["control_id"] == "AU-12"
    assert items[0]["snapshot_status"] == "partially_satisfied"
    conn.close()


# ---------------------------------------------------------------------------
# Test 4 — satisfied rows are excluded
# ---------------------------------------------------------------------------

def test_generate_poam_items_excludes_satisfied() -> None:
    conn = _make_conn([
        _snap("satisfied", control_id="CM-6"),
        _snap("not_satisfied", control_id="CM-7"),
    ])
    items = generate_poam_items(conn)
    assert len(items) == 1
    assert items[0]["control_id"] == "CM-7"
    conn.close()


# ---------------------------------------------------------------------------
# Test 5 — not_applicable rows are excluded
# ---------------------------------------------------------------------------

def test_generate_poam_items_excludes_not_applicable() -> None:
    conn = _make_conn([
        _snap("not_applicable", control_id="PE-1"),
        _snap("partially_satisfied", control_id="PE-2"),
    ])
    items = generate_poam_items(conn)
    assert len(items) == 1
    assert items[0]["control_id"] == "PE-2"
    conn.close()


# ---------------------------------------------------------------------------
# Test 6 — severity_for_status mapping
# ---------------------------------------------------------------------------

def test_severity_for_status_not_satisfied() -> None:
    assert severity_for_status("not_satisfied") == "high"


def test_severity_for_status_partially_satisfied() -> None:
    assert severity_for_status("partially_satisfied") == "moderate"


def test_severity_for_status_planned() -> None:
    assert severity_for_status("planned") == "low"


# ---------------------------------------------------------------------------
# Test 7 — milestone_date_for_severity returns ISO date
# ---------------------------------------------------------------------------

def test_milestone_date_for_severity_format() -> None:
    date_str = milestone_date_for_severity("high")
    datetime.strptime(date_str, "%Y-%m-%d")  # must not raise


def test_milestone_date_high_sooner_than_low() -> None:
    from datetime import date
    d_high = date.fromisoformat(milestone_date_for_severity("high"))
    d_low = date.fromisoformat(milestone_date_for_severity("low"))
    assert d_high < d_low


# ---------------------------------------------------------------------------
# Test 8 — build_poam_item produces required keys
# ---------------------------------------------------------------------------

def test_build_poam_item_required_keys() -> None:
    row = _snap("not_satisfied", control_id="SI-2")
    item = build_poam_item(row)
    required = {
        "snapshot_id", "project_id", "framework_id", "control_id",
        "weakness_id", "weakness_description", "severity", "source",
        "status", "corrective_action", "milestone_date", "responsible_party",
    }
    assert required.issubset(item.keys()), f"Missing keys: {required - item.keys()}"
    assert item["snapshot_id"] == row["snapshot_id"]
    assert item["control_id"] == "SI-2"
    assert item["severity"] == "high"


# ---------------------------------------------------------------------------
# Test 9 — run_auto_generator writes items to poam_items table
# ---------------------------------------------------------------------------

def test_run_auto_generator_writes_to_db() -> None:
    snapshots = [
        _snap("not_satisfied", control_id="AC-2", framework_id="fedramp-moderate"),
        _snap("partially_satisfied", control_id="IA-5", framework_id="fedramp-moderate"),
        _snap("satisfied", control_id="CM-6", framework_id="fedramp-moderate"),
    ]
    conn = _make_conn(snapshots)
    result = run_auto_generator(conn)
    assert result["items_generated"] == 2
    rows = conn.execute("SELECT * FROM poam_items").fetchall()
    assert len(rows) == 2
    sources = {r["source"] for r in rows}
    assert all("compliance_snapshots" in s for s in sources)
    conn.close()


# ---------------------------------------------------------------------------
# Test 10 — run_auto_generator is idempotent (no duplicates on re-run)
# ---------------------------------------------------------------------------

def test_run_auto_generator_idempotent() -> None:
    snapshots = [_snap("not_satisfied", control_id="SC-7")]
    conn = _make_conn(snapshots)
    r1 = run_auto_generator(conn)
    r2 = run_auto_generator(conn)
    assert r1["items_generated"] == 1
    assert r2["items_generated"] == 0  # already exists; not duplicated
    rows = conn.execute("SELECT * FROM poam_items").fetchall()
    assert len(rows) == 1
    conn.close()


# ---------------------------------------------------------------------------
# Test 11 — dedup on (project_id, control_id): two snapshots same control → 1 item
# ---------------------------------------------------------------------------

def test_run_auto_generator_dedup_on_project_control() -> None:
    # Two different snapshot_ids for the same project+control — should produce only 1 POA&M.
    snap_a = _snap("not_satisfied", project_id="proj-x", control_id="AC-2")
    snap_b = _snap("partially_satisfied", project_id="proj-x", control_id="AC-2")
    conn = _make_conn([snap_a, snap_b])
    result = run_auto_generator(conn)
    assert result["items_generated"] == 1
    assert result["items_skipped"] == 1
    rows = conn.execute("SELECT * FROM poam_items").fetchall()
    assert len(rows) == 1
    conn.close()


# ---------------------------------------------------------------------------
# Test 12 — different projects with same control_id both get items
# ---------------------------------------------------------------------------

def test_run_auto_generator_different_projects_same_control() -> None:
    snap_a = _snap("not_satisfied", project_id="proj-a", control_id="SC-7")
    snap_b = _snap("not_satisfied", project_id="proj-b", control_id="SC-7")
    conn = _make_conn([snap_a, snap_b])
    result = run_auto_generator(conn)
    assert result["items_generated"] == 2
    conn.close()
