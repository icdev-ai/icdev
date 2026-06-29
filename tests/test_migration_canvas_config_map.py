#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for network device configuration mapping (AI-assisted, HITL-reviewed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

import pytest

SAMPLE_JUNIPER_CONFIG = """
# Sample Juniper MX config
system {
    host-name core-router-01;
    domain-name example.com;
    name-server 8.8.8.8;
}
interfaces {
    ge-0/0/0 {
        unit 0 {
            family inet {
                address 192.0.2.1/30;
            }
        }
        description "Uplink to dist-sw-01";
    }
    ge-0/0/1 {
        unit 0 {
            family inet {
                address 198.51.100.1/30;
            }
        }
        description "Downlink to access-sw-01";
    }
}
routing-options {
    static {
        route 0.0.0.0/0 next-hop 192.0.2.2;
    }
}
protocols {
    bgp {
        group upstream {
            type external;
            peer-as 64512;
            neighbor 192.0.2.2;
        }
    }
}
"""


@pytest.fixture
def mc_db_path(tmp_path, monkeypatch):
    """Point migration_canvas DB to a temp SQLite file."""
    db_path = tmp_path / "migration_canvas.db"
    from tools.migration_canvas.db import init_db as init_db_mod
    from tools.migration_canvas import network_migration as nm

    monkeypatch.setattr(init_db_mod, "DB_PATH", db_path)
    monkeypatch.setattr(nm, "_MC_DB_PATH", db_path)
    init_db_mod.init_db()
    return db_path


@pytest.fixture
def session_id(mc_db_path):
    """Create a network migration session with a parsed Juniper config."""
    from tools.migration_canvas.db.init_db import get_connection

    sid = f"nmig-test-{uuid.uuid4().hex[:8]}"
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO mc_net_sessions (id, src_model, tgt_model, src_config_raw, config_parsed) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, "MX10003", "MX304", SAMPLE_JUNIPER_CONFIG, 1),
        )
        conn.commit()
    return sid


def test_generate_config_map_questions(session_id):
    """Questions should be seeded from parsed source/target config."""
    from tools.migration_canvas.network_migration import generate_config_map_questions

    result = generate_config_map_questions(session_id)
    assert "questions" in result
    questions = result["questions"]
    assert isinstance(questions, list)
    # At minimum we expect vendor-difference question when src != tgt vendor.
    keys = {q["question_key"] for q in questions}
    assert "same_vendor_syntax" in keys or "cross_vendor_translate" in keys or len(questions) >= 1


def test_propose_config_mapping_rule_based(session_id):
    """Rule-based proposal should split config into sections and persist rows."""
    from tools.migration_canvas.network_migration import (
        propose_config_mapping,
        get_config_map,
    )

    result = propose_config_mapping(session_id, use_llm=False)
    assert "error" not in result, result
    assert result["model"] == "rule-based"
    assert result["count"] > 0
    proposals = result["proposals"]
    section_types = {p["src_section_type"] for p in proposals}
    assert "interfaces" in section_types
    assert "system" in section_types

    # Verify persistence
    loaded = get_config_map(session_id)
    assert loaded["proposals"]
    assert len(loaded["proposals"]) == result["count"]


def test_decide_config_map_row(session_id):
    """HITL decision updates row status and note."""
    from tools.migration_canvas.network_migration import (
        propose_config_mapping,
        decide_config_map_row,
        get_config_map,
    )

    propose_config_mapping(session_id, use_llm=False)
    row = get_config_map(session_id)["proposals"][0]
    rid = row["id"]

    res = decide_config_map_row(session_id, rid, "approved", "looks good")
    assert res.get("ok") is True
    assert res.get("updated") == 1

    updated = get_config_map(session_id)["proposals"]
    match = next(r for r in updated if r["id"] == rid)
    assert match["status"] == "approved"
    assert match["reviewer_note"] == "looks good"


def test_apply_approved_config_map(session_id):
    """Approved rows assemble into a target config and update session."""
    from tools.migration_canvas.network_migration import (
        propose_config_mapping,
        decide_config_map_row,
        apply_approved_config_map,
    )
    from tools.migration_canvas.db.init_db import get_connection

    propose_config_mapping(session_id, use_llm=False)
    rows = propose_config_mapping(session_id, use_llm=False)["proposals"]
    for row in rows:
        decide_config_map_row(session_id, row["id"], "approved")

    result = apply_approved_config_map(session_id)
    assert "error" not in result, result
    assert result["approved_count"] == len(rows)
    assert result["pending_count"] == 0
    assert isinstance(result["target_config"], str)
    assert result["target_config"].strip()

    with get_connection() as conn:
        sess = conn.execute(
            "SELECT target_config FROM mc_net_sessions WHERE id=?", (session_id,)
        ).fetchone()
    assert sess and sess["target_config"] == result["target_config"]


def test_get_config_map_empty_session(session_id):
    """Empty config map returns empty list without error."""
    from tools.migration_canvas.network_migration import get_config_map

    result = get_config_map(session_id)
    assert result["proposals"] == []
    assert "questions" in result
