# CUI // SP-CTI
"""Pytest tests for SDC demo runner, seed data, and ROI calculator (SDC-29)."""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ── Minimal canvas schema DDL ─────────────────────────────────────────────────

_CANVAS_DDL = """
CREATE TABLE IF NOT EXISTS security_designs (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
    graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    template_id TEXT, source_topology_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sc_threats (
    id TEXT PRIMARY KEY, design_id TEXT,
    threat_category TEXT NOT NULL, mitre_technique TEXT, mitre_tactic TEXT,
    title TEXT NOT NULL, description TEXT,
    likelihood TEXT DEFAULT 'medium', impact TEXT DEFAULT 'medium',
    risk_score REAL DEFAULT 0, affected_assets TEXT DEFAULT '[]',
    status TEXT DEFAULT 'open', is_stale INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sc_controls (
    id TEXT PRIMARY KEY, design_id TEXT,
    control_family TEXT, control_id TEXT, title TEXT, description TEXT,
    implementation_status TEXT DEFAULT 'planned',
    implementation_notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sc_assets (
    id TEXT PRIMARY KEY, design_id TEXT,
    asset_type TEXT, label TEXT, description TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sc_data_flows (
    id TEXT PRIMARY KEY, design_id TEXT,
    source_asset_id TEXT, target_asset_id TEXT, protocol TEXT,
    encrypted INTEGER DEFAULT 0, authenticated INTEGER DEFAULT 0,
    crosses_boundary INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sc_trust_boundaries (
    id TEXT PRIMARY KEY, design_id TEXT,
    label TEXT, boundary_type TEXT,
    classification TEXT DEFAULT 'CUI', il_level TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sdc_attack_snapshots (
    id TEXT PRIMARY KEY, component_id TEXT NOT NULL,
    nodes_json TEXT NOT NULL DEFAULT '[]',
    edges_json TEXT NOT NULL DEFAULT '[]',
    caldera_op_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sdc_sops (
    id TEXT PRIMARY KEY, title TEXT NOT NULL,
    sop_type TEXT NOT NULL, description TEXT,
    purpose TEXT, scope TEXT,
    steps TEXT DEFAULT '[]', nist_controls TEXT DEFAULT '[]',
    owner TEXT, reviewer TEXT,
    approval_status TEXT DEFAULT 'draft',
    approved_by TEXT, approved_at TEXT, rejected_reason TEXT,
    version TEXT DEFAULT '1.0', next_review_date TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sc_audit (
    id TEXT PRIMARY KEY, action TEXT, entity_type TEXT,
    entity_id TEXT, actor TEXT, details_json TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sdc_compliance_timeline (
    id TEXT PRIMARY KEY, design_id TEXT NOT NULL,
    snapshot_label TEXT NOT NULL DEFAULT 'baseline',
    cat1_count INTEGER NOT NULL DEFAULT 0,
    cat2_count INTEGER NOT NULL DEFAULT 0,
    cat3_count INTEGER NOT NULL DEFAULT 0,
    risk_score REAL NOT NULL DEFAULT 0.0,
    posture_grade TEXT NOT NULL DEFAULT 'F',
    controls_implemented INTEGER NOT NULL DEFAULT 0,
    controls_total INTEGER NOT NULL DEFAULT 0,
    remediation_hours REAL NOT NULL DEFAULT 0.0,
    snapshot_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    classification TEXT NOT NULL DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS sdc_roi_metrics (
    id TEXT PRIMARY KEY, design_id TEXT NOT NULL,
    manual_hours REAL NOT NULL DEFAULT 200.0,
    automated_hours REAL NOT NULL DEFAULT 4.0,
    cost_per_hour REAL NOT NULL DEFAULT 150.0,
    roi_multiplier REAL NOT NULL DEFAULT 0.0,
    engagement_type TEXT NOT NULL DEFAULT 'standard',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    classification TEXT NOT NULL DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS sdc_workflow_step_runs (
    id TEXT PRIMARY KEY, design_id TEXT NOT NULL,
    step_id TEXT NOT NULL, step_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    approved_by TEXT, approved_at TEXT,
    started_at TEXT, completed_at TEXT,
    output_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def canvas_conn(tmp_path, monkeypatch):
    """Temporary security_canvas.db with full schema for SDC demo tests."""
    from tools.db.storage import StorageConnection

    db_path = tmp_path / "security_canvas.db"
    raw = sqlite3.connect(str(db_path))
    raw.row_factory = sqlite3.Row
    raw.executescript(_CANVAS_DDL)
    raw.commit()

    # roi_calculator / isso_gate now obtain their connection from the Security
    # Canvas helper (they author PG-native %s). Point that helper at this temp DB
    # and return a StorageConnection so %s translates to ? on SQLite; hand out a
    # fresh connection per call so a callee's .close() doesn't poison the fixture.
    def _sc_conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return StorageConnection(c, "sqlite")

    # Patch the module attribute directly (string form resolves through the
    # icdev.tools shim and fails). roi_calculator/isso_gate import get_connection
    # from this module at call time, so they pick up the patched function.
    from tools.security_canvas.db import init_db as _sc_init

    monkeypatch.setattr(_sc_init, "get_connection", _sc_conn)

    # Tests execute %s SQL directly on the yielded conn — wrap it too.
    yield StorageConnection(raw, "sqlite")
    raw.close()


def _seed_all(conn) -> None:
    """Run all three seed phases against the given conn."""
    from tools.db.seeds.seed_sdc_demo import (
        _ensure_demo_tables,
        seed_before_state,
        seed_after_state,
        seed_isso_workflow,
    )
    _ensure_demo_tables(conn)
    seed_before_state(conn, reset=False)
    seed_after_state(conn)
    seed_isso_workflow(conn)


# ══════════════════════════════════════════════════════════════════════════════
# 1. seed_before_state — threat counts and CAT1 threshold
# ══════════════════════════════════════════════════════════════════════════════

def test_seed_before_state_threat_count(canvas_conn):
    from tools.db.seeds.seed_sdc_demo import _ensure_demo_tables, seed_before_state

    _ensure_demo_tables(canvas_conn)
    counts = seed_before_state(canvas_conn, reset=False)

    assert counts["threats_before"] >= 30, (
        f"Expected >=30 threats, got {counts['threats_before']}"
    )
    total = canvas_conn.execute(
        "SELECT COUNT(*) FROM sc_threats WHERE id LIKE 'demo-threat-%'"
    ).fetchone()[0]
    assert total >= 30


def test_seed_before_state_cat1_minimum(canvas_conn):
    from tools.db.seeds.seed_sdc_demo import _ensure_demo_tables, seed_before_state

    _ensure_demo_tables(canvas_conn)
    seed_before_state(canvas_conn, reset=False)

    cat1_count = canvas_conn.execute(
        "SELECT COUNT(*) FROM sc_threats "
        "WHERE id LIKE 'demo-threat-%' AND risk_score >= 8.0"
    ).fetchone()[0]
    assert cat1_count >= 15, f"Expected >=15 CAT1 (risk>=8.0) threats, got {cat1_count}"


def test_seed_before_state_designs(canvas_conn):
    from tools.db.seeds.seed_sdc_demo import _ensure_demo_tables, seed_before_state

    _ensure_demo_tables(canvas_conn)
    counts = seed_before_state(canvas_conn, reset=False)

    assert counts["designs"] == 8
    designs = canvas_conn.execute(
        "SELECT COUNT(*) FROM security_designs WHERE id LIKE 'demo-design-%'"
    ).fetchone()[0]
    assert designs == 8


def test_seed_before_state_idempotent(canvas_conn):
    """Running twice must not raise and count must remain the same."""
    from tools.db.seeds.seed_sdc_demo import _ensure_demo_tables, seed_before_state

    _ensure_demo_tables(canvas_conn)
    seed_before_state(canvas_conn, reset=False)
    counts2 = seed_before_state(canvas_conn, reset=False)

    assert counts2["threats_before"] >= 30


# ══════════════════════════════════════════════════════════════════════════════
# 2. seed_after_state — 0 CAT1 threats after mitigation
# ══════════════════════════════════════════════════════════════════════════════

def test_seed_after_state_cat1_zero(canvas_conn):
    from tools.db.seeds.seed_sdc_demo import (
        _ensure_demo_tables, seed_before_state, seed_after_state,
    )
    _ensure_demo_tables(canvas_conn)
    seed_before_state(canvas_conn, reset=False)
    seed_after_state(canvas_conn)

    # After mitigation, no demo-threat should remain open
    open_cat1 = canvas_conn.execute(
        "SELECT COUNT(*) FROM sc_threats "
        "WHERE id LIKE 'demo-threat-%' AND status='open' AND risk_score >= 8.0"
    ).fetchone()[0]
    assert open_cat1 == 0, f"Expected 0 open CAT1 after seed_after_state, got {open_cat1}"


def test_seed_after_state_compliance_timeline(canvas_conn):
    from tools.db.seeds.seed_sdc_demo import (
        _ensure_demo_tables, seed_before_state, seed_after_state,
    )
    _ensure_demo_tables(canvas_conn)
    seed_before_state(canvas_conn, reset=False)
    seed_after_state(canvas_conn)

    rows = canvas_conn.execute(
        "SELECT snapshot_label, cat1_count, posture_grade FROM sdc_compliance_timeline "
        "WHERE design_id='demo-design-001' ORDER BY snapshot_label"
    ).fetchall()
    assert len(rows) >= 2, f"Expected >=2 timeline rows, got {len(rows)}"

    labels = {r["snapshot_label"] for r in rows}
    assert "before" in labels
    assert "after" in labels

    after_row = next(r for r in rows if r["snapshot_label"] == "after")
    assert after_row["cat1_count"] == 0, f"after-state cat1_count should be 0, got {after_row['cat1_count']}"
    assert after_row["posture_grade"] == "A", f"after-state grade should be A, got {after_row['posture_grade']}"


def test_seed_after_state_controls_implemented(canvas_conn):
    from tools.db.seeds.seed_sdc_demo import (
        _ensure_demo_tables, seed_before_state, seed_after_state, _CONTROLS_AFTER,
    )
    _ensure_demo_tables(canvas_conn)
    seed_before_state(canvas_conn, reset=False)
    counts = seed_after_state(canvas_conn)

    assert counts["controls_implemented"] == len(_CONTROLS_AFTER)


# ══════════════════════════════════════════════════════════════════════════════
# 3. run_sdc_demo(['A']) — status=pass
# ══════════════════════════════════════════════════════════════════════════════

def test_run_sdc_demo_scenario_a_passes(canvas_conn):
    _seed_all(canvas_conn)

    from tools.sdc.demo_runner import run_sdc_demo

    result = run_sdc_demo(scenarios=["A"], audience="exec")
    assert result["status"] == "pass", (
        f"run_sdc_demo(['A']) returned status={result['status']!r}; "
        f"sA={result['results'].get('sA')}"
    )
    sa = result["results"]["sA"]
    assert sa["status"] == "pass"
    # Verify expected fields are present with valid values
    assert "cat1_open" in sa, "Scenario A must return cat1_open"
    assert sa.get("cat1_open", -1) >= 0, "cat1_open must be non-negative"
    assert "attack_paths" in sa, "Scenario A must return attack_paths"


def test_run_sdc_demo_scenario_c_posture_a(canvas_conn):
    _seed_all(canvas_conn)

    from tools.sdc.demo_runner import run_sdc_demo

    result = run_sdc_demo(scenarios=["C"], audience="exec")
    assert result["status"] == "pass"
    sc = result["results"]["sC"]
    assert sc.get("cat1_after") == 0
    assert sc.get("posture_grade") == "A"
    assert sc.get("cato_ready") is True


def test_run_sdc_demo_scenario_b_workflow_steps(canvas_conn):
    _seed_all(canvas_conn)

    from tools.sdc.demo_runner import run_sdc_demo

    result = run_sdc_demo(scenarios=["B"], audience="tech")
    assert result["status"] == "pass"
    sb = result["results"]["sB"]
    assert sb["steps_total"] == 12
    assert sb["steps_completed"] == 12
    assert sb["isso_gate_count"] >= 1


def test_run_sdc_demo_all_scenarios(canvas_conn):
    _seed_all(canvas_conn)

    from tools.sdc.demo_runner import run_sdc_demo

    result = run_sdc_demo(scenarios=["A", "B", "C"], audience="exec")
    assert result["scenarios_total"] == 3
    assert result["scenarios_passed"] == 3
    assert result["status"] == "pass"


# ══════════════════════════════════════════════════════════════════════════════
# 4. sdc_compliance_timeline — >=2 rows
# ══════════════════════════════════════════════════════════════════════════════

def test_compliance_timeline_has_two_rows(canvas_conn):
    from tools.db.seeds.seed_sdc_demo import (
        _ensure_demo_tables, seed_before_state, seed_after_state,
    )
    _ensure_demo_tables(canvas_conn)
    seed_before_state(canvas_conn, reset=False)
    seed_after_state(canvas_conn)

    count = canvas_conn.execute(
        "SELECT COUNT(*) FROM sdc_compliance_timeline WHERE design_id='demo-design-001'"
    ).fetchone()[0]
    assert count >= 2, f"Expected >=2 timeline rows, got {count}"


def test_compliance_timeline_risk_reduction(canvas_conn):
    from tools.db.seeds.seed_sdc_demo import (
        _ensure_demo_tables, seed_before_state, seed_after_state,
    )
    _ensure_demo_tables(canvas_conn)
    seed_before_state(canvas_conn, reset=False)
    seed_after_state(canvas_conn)

    before = canvas_conn.execute(
        "SELECT risk_score FROM sdc_compliance_timeline "
        "WHERE design_id='demo-design-001' AND snapshot_label='before'"
    ).fetchone()
    after = canvas_conn.execute(
        "SELECT risk_score FROM sdc_compliance_timeline "
        "WHERE design_id='demo-design-001' AND snapshot_label='after'"
    ).fetchone()

    assert before is not None and after is not None
    assert before["risk_score"] > after["risk_score"], (
        f"Before risk {before['risk_score']} should be > after risk {after['risk_score']}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 5. compute_roi() — roi_multiplier >= 10
# ══════════════════════════════════════════════════════════════════════════════

def test_compute_roi_multiplier(canvas_conn):
    from tools.db.seeds.seed_sdc_demo import (
        _ensure_demo_tables, seed_before_state, seed_after_state,
    )
    _ensure_demo_tables(canvas_conn)
    seed_before_state(canvas_conn, reset=False)
    seed_after_state(canvas_conn)

    from tools.sdc.roi_calculator import compute_roi

    roi = compute_roi("demo-design-001")
    assert roi["roi_multiplier"] >= 10, (
        f"Expected roi_multiplier>=10, got {roi['roi_multiplier']}"
    )


def test_compute_roi_cost_saved(canvas_conn):
    from tools.db.seeds.seed_sdc_demo import (
        _ensure_demo_tables, seed_before_state, seed_after_state,
    )
    _ensure_demo_tables(canvas_conn)
    seed_before_state(canvas_conn, reset=False)
    seed_after_state(canvas_conn)

    from tools.sdc.roi_calculator import compute_roi

    roi = compute_roi("demo-design-001")
    assert roi["cost_saved_usd"] > 0
    assert roi["time_saved_hours"] > 0
    assert roi["ftes_avoided"] > 0


def test_compute_roi_fallback_defaults(canvas_conn):
    """compute_roi with no seeded data must use fallback defaults."""
    from tools.sdc.roi_calculator import compute_roi

    roi = compute_roi("nonexistent-design-999")
    # Fallback: manual=200, automated=4, rate=150
    assert roi["roi_multiplier"] >= 10
    assert roi["cost_saved_usd"] == int((200 - 4) * 150)


# ══════════════════════════════════════════════════════════════════════════════
# 6. ISSO workflow — 12 steps seeded
# ══════════════════════════════════════════════════════════════════════════════

def test_isso_workflow_step_count(canvas_conn):
    from tools.db.seeds.seed_sdc_demo import (
        _ensure_demo_tables, seed_isso_workflow,
    )
    _ensure_demo_tables(canvas_conn)
    counts = seed_isso_workflow(canvas_conn)

    assert counts["workflow_steps"] == 12

    rows = canvas_conn.execute(
        "SELECT COUNT(*) FROM sdc_workflow_step_runs WHERE design_id='demo-design-001'"
    ).fetchone()[0]
    assert rows == 12


def test_isso_workflow_approvals(canvas_conn):
    from tools.db.seeds.seed_sdc_demo import (
        _ensure_demo_tables, seed_isso_workflow,
    )
    _ensure_demo_tables(canvas_conn)
    seed_isso_workflow(canvas_conn)

    approved = canvas_conn.execute(
        "SELECT step_id, approved_by FROM sdc_workflow_step_runs "
        "WHERE design_id='demo-design-001' AND approved_by IS NOT NULL"
    ).fetchall()
    approved_steps = {r["step_id"] for r in approved}

    assert "step-04" in approved_steps, "step-04 (ISSO Approval Gate) should have approved_by set"
    assert "step-09" in approved_steps, "step-09 (Ansible Remediation) should have approved_by set"


# ══════════════════════════════════════════════════════════════════════════════
# 7. verify_sdc_demo_data — all checks pass after full seed
# ══════════════════════════════════════════════════════════════════════════════

def test_verify_sdc_demo_data_passes(canvas_conn):
    _seed_all(canvas_conn)

    from tools.db.seeds.seed_sdc_demo import verify_sdc_demo_data

    result = verify_sdc_demo_data(canvas_conn)
    failed = [c for c in result["checks"] if not c["ok"]]
    assert result["status"] == "pass", (
        f"verify_sdc_demo_data failed checks: {failed}"
    )
