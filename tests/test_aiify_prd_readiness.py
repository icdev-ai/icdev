# CUI // SP-CTI
"""Tests for the PRD AI-readiness assessor (EQO-PRD-01).

Covers the two acceptance cases from the task:
  - a PRD that says "classify and route documents" yields inferred AI
    opportunities + a meaningful score;
  - a CRUD-only PRD yields no AI-ready opportunities (overall not_suitable),
    i.e. "don't AI-ify for the sake of it".

The deterministic path is exercised end-to-end against a temp SQLite DB; no
LLM and no network are touched.
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.aiify.prd_readiness_assessor import assess_prd_for_ai_readiness


def _init_db(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE intake_sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            customer_name TEXT,
            customer_org TEXT,
            session_status TEXT DEFAULT 'active',
            classification TEXT DEFAULT 'CUI',
            impact_level TEXT DEFAULT 'IL4',
            context_summary TEXT,
            source_documents TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE intake_requirements (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            project_id TEXT,
            source_turn INTEGER,
            raw_text TEXT NOT NULL,
            refined_text TEXT,
            requirement_type TEXT DEFAULT 'functional',
            priority TEXT DEFAULT 'medium',
            acceptance_criteria TEXT,
            compliance_impact TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    return conn


def _new_session(conn, impact="IL4", project_id=None) -> str:
    sid = f"sess-{uuid.uuid4().hex[:10]}"
    conn.execute(
        "INSERT INTO intake_sessions (id, project_id, customer_name, impact_level) "
        "VALUES (?, ?, ?, ?)",
        (sid, project_id, "Tester", impact),
    )
    conn.commit()
    return sid


def _add_req(conn, sid, turn, raw, ac=None):
    conn.execute(
        "INSERT INTO intake_requirements (id, session_id, source_turn, raw_text, "
        "acceptance_criteria) VALUES (?, ?, ?, ?, ?)",
        (f"req-{uuid.uuid4().hex[:8]}", sid, turn, raw, ac),
    )
    conn.commit()


def test_classify_and_route_doc_yields_opportunities(tmp_path):
    conn = _init_db(tmp_path / "icdev.db")
    sid = _new_session(conn)
    _add_req(conn, sid, 1,
             "The system shall automatically classify and route incoming documents "
             "to the correct review queue using machine learning.",
             ac="Classification accuracy of at least 90% on 1000 sample documents.")
    _add_req(conn, sid, 2,
             "The system shall extract the vendor, invoice number, and amount from "
             "each uploaded document.",
             ac="Extracted fields match ground truth within 95% on the labeled dataset.")
    _add_req(conn, sid, 3,
             "The system shall send an email alert when a document remains unrouted "
             "for more than 24 hours.")

    result = assess_prd_for_ai_readiness(sid, conn=conn)

    assert 0.0 <= result["score"] <= 1.0
    assert result["score"] > 0.0
    assert result["opportunity_count"] >= 2
    ptypes = {o["pattern_type"] for o in result["opportunities"]}
    assert "manual_classification_ui" in ptypes  # "classify"
    assert "document_routing" in ptypes           # "route ... documents"
    # At least one opportunity should be a real AI candidate (not all disqualified).
    assert any(o["ai_readiness"] in ("ai_ready", "partial") for o in result["opportunities"])
    assert result["overall_ai_readiness"] in ("ai_ready", "partial")
    # Every opportunity carries a verdict + composite score from score_and_assess.
    for o in result["opportunities"]:
        assert "composite_score" in o
        assert o["ai_readiness"] in ("ai_ready", "partial", "not_suitable")
    conn.close()


def test_crud_only_doc_is_not_suitable(tmp_path):
    conn = _init_db(tmp_path / "icdev.db")
    sid = _new_session(conn)
    _add_req(conn, sid, 1,
             "The system shall allow users to create a customer record with name and address.")
    _add_req(conn, sid, 2,
             "The system shall allow users to update and delete existing customer records.")
    _add_req(conn, sid, 3,
             "The system shall display a paginated list of all customer records.")

    result = assess_prd_for_ai_readiness(sid, conn=conn)

    # Pure CRUD: no AI opportunities inferred -> overall not suitable.
    assert result["overall_ai_readiness"] == "not_suitable"
    assert not any(o["ai_readiness"] == "ai_ready" for o in result["opportunities"])
    # AI-mention density should be low for a CRUD PRD.
    assert result["components"]["ai_mention_density"]["score"] < 0.5
    conn.close()


def test_components_and_weights_present(tmp_path):
    conn = _init_db(tmp_path / "icdev.db")
    sid = _new_session(conn)
    _add_req(conn, sid, 1, "Extract data from PDF invoices and generate a summary report.")

    result = assess_prd_for_ai_readiness(sid, conn=conn)

    expected = {
        "ai_mention_density", "data_requirement_specificity", "integration_clarity",
        "acceptance_criteria_quantifiability", "governance_declarations",
        "il_model_appropriateness",
    }
    assert set(result["components"]) == expected
    total_weight = sum(c["weight"] for c in result["components"].values())
    assert abs(total_weight - 1.0) < 1e-6
    assert isinstance(result["recommendations"], list) and result["recommendations"]
    conn.close()


def test_il6_without_local_model_lowers_il_component(tmp_path):
    conn = _init_db(tmp_path / "icdev.db")
    sid = _new_session(conn, impact="IL6")
    _add_req(conn, sid, 1, "Classify documents using AI in an air-gapped environment.")

    result = assess_prd_for_ai_readiness(sid, conn=conn)
    assert result["components"]["il_model_appropriateness"]["score"] < 0.8

    # Declaring a local model should restore appropriateness.
    sid2 = _new_session(conn, impact="IL6")
    _add_req(conn, sid2, 1,
             "Classify documents using a local on-prem model (Ollama) in an "
             "air-gapped environment.")
    result2 = assess_prd_for_ai_readiness(sid2, conn=conn)
    assert result2["components"]["il_model_appropriateness"]["score"] >= 0.8
    conn.close()
