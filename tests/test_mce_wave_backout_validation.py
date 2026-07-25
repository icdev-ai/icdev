# CUI // SP-CTI
"""Tests for crx-mig-01: per-wave backout section + post-migration workload
validation with an audited wave-close gate.

The migration-canvas tables carry no tenant_id/classification columns, so these
tests pin the canvas backend to a temp SQLite file and self-create the schema
(conftest's MINIMAL_ICDEV_SCHEMA is not applied to a bare canvas connection).
"""
import uuid

import pytest


@pytest.fixture()
def canvas_db(tmp_path, monkeypatch):
    """Point the migration-canvas connection at an isolated temp SQLite DB."""
    db_file = tmp_path / "mc_test.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("MC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_CANVAS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("MC_DB_PATH", str(db_file))

    # Create the mc_migration_waves table the gate updates on close.
    from tools.migration_canvas import wave_planner as wp
    conn = wp._conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS mc_migration_waves (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, wave_number INTEGER,
                name TEXT, cutover_date TEXT, status TEXT DEFAULT 'planned',
                server_ids_json TEXT DEFAULT '[]', notes TEXT, created_at TEXT,
                classification TEXT DEFAULT 'CUI', app_count INTEGER DEFAULT 0,
                app_names TEXT DEFAULT '[]'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()
    return db_file


@pytest.fixture()
def session_id():
    return "sess-" + uuid.uuid4().hex[:8]


# ── Backout section (gap #2) ──────────────────────────────────────────────────

def test_generate_backout_section_has_all_parts(canvas_db):
    from tools.migration_canvas import wave_planner as wp
    section = wp.generate_backout_section({"id": "wave-1", "server_ids": ["s1", "s2"]})
    for key in ("snapshot_prerequisites", "decision_points",
                "go_no_go_criteria", "recovery_steps"):
        assert key in section
        assert isinstance(section[key], list) and section[key], f"{key} should be non-empty"
    # Wave scope reflected in the template output.
    assert any("2 server" in s for s in section["snapshot_prerequisites"])


def test_upsert_and_get_backout_section_roundtrip(canvas_db, session_id):
    from tools.migration_canvas import wave_planner as wp
    wave_id = "wave-abc"
    created = wp.upsert_backout_section(session_id, wave_id)  # template defaults
    assert created is not None
    assert created["approved"] is False
    assert created["go_no_go_criteria"]

    fetched = wp.get_backout_section(session_id, wave_id)
    assert fetched is not None
    assert fetched["snapshot_prerequisites"] == created["snapshot_prerequisites"]


def test_backout_edit_resets_approval(canvas_db, session_id):
    from tools.migration_canvas import wave_planner as wp
    wave_id = "wave-approve"
    wp.upsert_backout_section(session_id, wave_id)
    approved = wp.approve_backout_section(session_id, wave_id, user="reviewer")
    assert approved["approved"] is True
    assert approved["approved_by"] == "reviewer"

    # An edit must reset the approved flag (HITL re-approval required).
    edited = wp.upsert_backout_section(
        session_id, wave_id,
        {"snapshot_prerequisites": ["custom step"], "decision_points": [],
         "go_no_go_criteria": [], "recovery_steps": []},
    )
    assert edited["approved"] is False
    assert edited["snapshot_prerequisites"] == ["custom step"]


# ── Workload validation + wave-close gate (gap #3) ────────────────────────────

def test_passing_wave_closes_without_override(canvas_db, session_id):
    from tools.migration_canvas import workload_validator as wv
    wave_id = "wave-pass"
    # Workload with no targets -> all checks 'skip' (no failures).
    result = wv.run_workload_validation(session_id, wave_id, {"id": "wl-1", "name": "App A"})
    assert result["summary"]["fail"] == 0

    closeable, failing = wv.can_close_wave(session_id, wave_id)
    assert closeable is True
    assert failing == []

    closed = wv.close_wave(session_id, wave_id, user="op")
    assert closed["ok"] is True
    assert closed["status"] == "complete"


def test_failing_validation_blocks_close_until_override(canvas_db, session_id):
    from tools.migration_canvas import workload_validator as wv
    wave_id = "wave-fail"
    workload_id = "wl-bad"

    # Run the checklist (skips), then inject a failing check for the workload.
    wv.run_workload_validation(session_id, wave_id, {"id": workload_id, "name": "App B"})
    conn = wv._conn()
    try:
        conn.execute(
            "UPDATE mc_workload_validations SET status='fail', detail='injected failure' "
            "WHERE session_id=%s AND wave_id=%s AND workload_id=%s AND check_type=%s",
            (session_id, wave_id, workload_id, "security_scan"),
        )
        conn.commit()
    finally:
        conn.close()

    closeable, failing = wv.can_close_wave(session_id, wave_id)
    assert closeable is False
    assert any(f["check_type"] == "security_scan" for f in failing)

    # Close without force -> refused.
    blocked = wv.close_wave(session_id, wave_id, user="op")
    assert blocked["ok"] is False
    assert blocked["status"] == "blocked"

    # Force without a reason -> refused.
    no_reason = wv.close_wave(session_id, wave_id, user="op", force=True)
    assert no_reason["ok"] is False
    assert no_reason["status"] == "override_reason_required"

    # Force with a reason -> closes AND writes an append-only audit row.
    forced = wv.close_wave(
        session_id, wave_id, user="approver", force=True,
        override_reason="Accepted risk; remediation tracked in POAM-42.",
    )
    assert forced["ok"] is True
    assert forced["status"] == "complete"
    assert forced.get("override_id")

    audit = wv.get_close_overrides(session_id, wave_id)
    assert len(audit) == 1
    assert audit[0]["override_user"] == "approver"
    assert "POAM-42" in audit[0]["reason"]


def test_twin_diff_reports_resource_deltas(canvas_db, session_id):
    from tools.migration_canvas import workload_validator as wv
    wave_id = "wave-twin"
    workload = {
        "id": "wl-twin", "name": "App C",
        "twin_before": {"cpu": 4, "mem_gb": 16},
        "twin_after": {"cpu": 8, "mem_gb": 16},
    }
    result = wv.run_workload_validation(session_id, wave_id, workload)
    twin = [r for r in result["results"] if r["check_type"] == "twin_resource_diff"][0]
    assert twin["status"] == "pass"
    assert "cpu: 4 -> 8" in twin["detail"]
