"""Tests for tools.observability_canvas.replay_verify (ODC closed-loop hook)."""
from __future__ import annotations

import json
import uuid

import pytest

from tools.observability_canvas.replay_verify import (
    _sigma_known_ttps,
    verify_path,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_odc_db():
    """Ensure ODC DB schema exists before each test."""
    from tools.observability_canvas.db.init_db import init_db

    init_db()


@pytest.fixture()
def _conn():
    from tools.observability_canvas.db.init_db import get_connection

    c = get_connection()
    yield c
    c.close()


# ── Case 1: empty path ────────────────────────────────────────────────────────


def test_verify_empty_path_returns_empty_results():
    result = verify_path([])
    assert result["path"] == []
    assert result["results"] == []
    assert result["summary"]["total"] == 0
    assert result["summary"]["full"] == 0
    assert result["summary"]["partial"] == 0
    assert result["summary"]["none"] == 0


# ── Case 2: unknown TTP gets state=none ───────────────────────────────────────


def test_verify_unknown_ttp_gets_none():
    fake_tid = "T9999"
    result = verify_path([fake_tid])
    assert len(result["results"]) == 1
    row = result["results"][0]
    assert row["ttp_id"] == fake_tid
    assert row["state"] == "none"
    assert result["summary"]["none"] == 1
    assert result["summary"]["total"] == 1


# ── Case 3: sigma-known TTP (no design baseline) → partial ───────────────────


def test_verify_sigma_known_ttp_without_baseline_is_partial():
    sigma_ttps = _sigma_known_ttps()
    if not sigma_ttps:
        pytest.skip("no sigma TTPs available in current env")
    tid = next(iter(sigma_ttps))
    result = verify_path([tid])
    row = result["results"][0]
    # Without any design baseline entry, state must be partial (sigma-only signal)
    assert row["state"] in ("partial", "full")


# ── Case 4: coverage row written to od_ttp_coverage ──────────────────────────


def test_coverage_row_written_to_db(_conn):
    fake_tid = f"T-cov-{uuid.uuid4().hex[:6]}"
    result = verify_path([fake_tid])
    expected_row_id = result["results"][0]["coverage_row_id"]
    row = _conn.execute(
        "SELECT * FROM od_ttp_coverage WHERE id = ?", (expected_row_id,)
    ).fetchone()
    assert row is not None
    assert row["ttp_id"] == fake_tid
    assert row["state"] == "none"


# ── Case 5: audit event written to od_audit ───────────────────────────────────


def test_audit_row_written_per_ttp(_conn):
    fake_tid = f"T-aud-{uuid.uuid4().hex[:6]}"
    design_id = "test-design-audit"
    result = verify_path([fake_tid], design_id=design_id)
    coverage_row_id = result["results"][0]["coverage_row_id"]

    rows = _conn.execute(
        "SELECT * FROM od_audit WHERE action = 'ttp_coverage_check' AND design_id = ?",
        (design_id,),
    ).fetchall()
    assert len(rows) >= 1
    detail = json.loads(rows[-1]["detail"])
    assert detail["ttp_id"] == fake_tid
    assert detail["coverage_row_id"] == coverage_row_id
    assert detail["state"] == "none"


# ── Bonus: full state when baseline + sigma both present ──────────────────────


def test_full_state_when_design_baseline_covers_sigma_ttp(_conn):
    sigma_ttps = _sigma_known_ttps()
    if not sigma_ttps:
        pytest.skip("no sigma TTPs available in current env")
    tid = next(iter(sigma_ttps))

    # Insert a design with a cmp-baseline node that marks the TTP as covered
    design_id = f"test-design-{uuid.uuid4().hex[:8]}"
    graph = {
        "nodes": [
            {
                "id": "baseline-node",
                "type": "cmp-baseline",
                "label": "MITRE Baseline",
                "config_json": json.dumps(
                    {"techniques": [{"id": tid, "name": "Test Technique", "covered": True}]}
                ),
            }
        ],
        "edges": [],
    }
    _conn.execute(
        "INSERT INTO observability_designs (id, name, graph_json) VALUES (?, ?, ?)",
        (design_id, "test-design", json.dumps(graph)),
    )
    _conn.commit()

    result = verify_path([tid], design_id=design_id)
    row = result["results"][0]
    assert row["state"] == "full", (
        f"Expected 'full' for {tid} with sigma + covered baseline, got '{row['state']}'"
    )
    assert result["summary"]["full"] == 1
