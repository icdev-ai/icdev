#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for Network Migration COA (Courses of Action) recommendation engine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

import pytest

from tools.migration_canvas.db import init_db as init_db_mod
from tools.migration_canvas import network_migration as nm


@pytest.fixture
def mc_db_path(tmp_path, monkeypatch):
    """Point migration_canvas DB to a temp SQLite file."""
    db_path = tmp_path / "migration_canvas_coa.db"
    monkeypatch.setenv("MC_DB_PATH", str(db_path))
    monkeypatch.setattr(init_db_mod, "DB_PATH", db_path)
    monkeypatch.setattr(nm, "_MC_DB_PATH", db_path)
    init_db_mod.init_db()
    return db_path


@pytest.fixture
def session_id(mc_db_path):
    """Create a network migration session."""
    sid = f"nmig-coa-test-{uuid.uuid4().hex[:8]}"
    raw_config = """
set system host-name core-rtr-01
set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/30
set protocols bgp group external neighbor 10.0.0.2 peer-as 65001
"""
    with init_db_mod.get_connection() as conn:
        conn.execute(
            "INSERT INTO mc_net_sessions (id, src_model, tgt_model, src_config_raw) "
            "VALUES (?, ?, ?, ?)",
            (sid, "Juniper MX204", "Cisco ASR-9901", raw_config),
        )
        conn.commit()
    return sid


def test_seed_coa_questions_creates_defaults(session_id):
    """Default COA questions are seeded for a new session."""
    questions = nm.get_coa_questions(session_id)
    keys = {q["question_key"] for q in questions}
    expected = {
        "spare_ports_available",
        "same_mgmt_vlan_ok",
        "igp_controlled",
        "tight_maintenance_window",
        "l2_only_replacement",
        "rollback_familiar",
    }
    assert keys == expected
    assert all(q["user_answer"] in (0, 1) for q in questions)


def test_recommend_coa_defaults_to_side_by_side(session_id):
    """With default yes/no answers, the safe default COA-A is recommended."""
    result = nm.recommend_coa(session_id)
    assert result["recommended"] == "coa_a"
    scores = result["scores"]
    assert scores["coa_a"] >= scores["coa_b"]
    assert scores["coa_a"] >= scores["coa_c"]
    assert result["rationale"]


def test_context_and_answers_can_recommend_cold_cutover(session_id):
    """A constrained L2-only, no-IGP-control scenario can push recommendation to COA-C."""
    context = (
        "Replacement is layer 2 only. Downstream OSPF/BGP is not under my control. "
        "We have a very tight maintenance window with no time for parallel validation."
    )
    nm.get_coa_questions(session_id)  # seed default questions before saving answers
    nm.save_coa_answers(
        session_id,
        {
            "spare_ports_available": 0,
            "same_mgmt_vlan_ok": 0,
            "igp_controlled": 0,
            "tight_maintenance_window": 1,
            "l2_only_replacement": 1,
            "rollback_familiar": 0,
        },
    )
    with init_db_mod.get_connection() as conn:
        conn.execute(
            "UPDATE mc_net_sessions SET engineer_context=? WHERE id=?",
            (context, session_id),
        )
        conn.commit()

    result = nm.recommend_coa(session_id)
    assert result["recommended"] == "coa_c"
    assert result["context_signals"]["l2_only"]
    assert result["context_signals"]["no_igp_control"]
    assert result["context_signals"]["tight_window"]


def test_select_coa_persists_choice(session_id):
    """select_coa stores the engineer override and returns a fresh recommendation."""
    result = nm.select_coa(session_id, "coa_c", context="Engineer chose cold cutover")
    assert result["recommended"] in ("coa_a", "coa_b", "coa_c")

    with init_db_mod.get_connection() as conn:
        row = conn.execute(
            "SELECT selected_coa, engineer_context FROM mc_net_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
    assert row["selected_coa"] == "coa_c"
    assert "cold cutover" in row["engineer_context"].lower()


def test_detect_context_signals_layer2_downstream():
    """_detect_context_signals extracts L2-only and downstream-IGP signals."""
    signals = nm._detect_context_signals(
        "Replacement is layer-2 only; all IGP happens downstream, not under my control."
    )
    assert signals["l2_only"]
    assert signals["no_igp_control"]
    assert not signals["tight_window"]


def test_invalid_coa_raises(session_id):
    """select_coa refuses unknown COA keys."""
    with pytest.raises(ValueError):
        nm.select_coa(session_id, "coa_d")
