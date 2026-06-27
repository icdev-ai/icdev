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
            "INSERT INTO mc_net_sessions (id, src_model, tgt_model, src_config_raw) VALUES (?,?,?,?)",
            (sid, "Juniper MX204", "Cisco ASR-9901", raw_config),
        )
        conn.commit()
    return sid


def test_seed_coa_questions_creates_defaults(session_id):
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


def test_recommend_coa_defaults_to_side_by_side(session_id):
    result = nm.recommend_coa(session_id)
    assert result["recommended"] == "coa_a"


def test_context_and_answers_can_recommend_cold_cutover(session_id):
    context = (
        "Replacement is layer 2 only. Downstream OSPF/BGP is not under my control. "
        "We have a very tight maintenance window with no time for parallel validation."
    )
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
    qs = nm.get_coa_questions(session_id)
    print("AFTER", [(q["question_key"], q["user_answer"]) for q in qs])
    result = nm.recommend_coa(session_id)
    print("REC", result["recommended"], result["scores"])
    assert result["recommended"] == "coa_c"
