# CUI // SP-CTI
"""Tests for tools/foundry/spec_generator.py — concept -> spec_md + canvas_contract.

Covers the task's acceptance criterion (a concept yields a spec_md plus a
canvas_contract with >=1 table and >=1 route) plus the deterministic derivation
(slug/prefix/env_flag), the mcp/reflex heuristics, persistence to foundry_specs,
and the LLM-assist fallback.
"""
from __future__ import annotations

import json

from tools.foundry import spec_generator as sg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _concept(**overrides):
    base = {
        "id": 1,
        "name": "Smart Contract Auditor",
        "slug": "smart-contract-auditor",
        "problem_statement": "Teams ship Solidity contracts with reentrancy bugs and no audit trail.",
        "proposed_capability": "Static-analyze uploaded contracts and grade them against a rule catalog.",
        "target_users": "Blockchain engineers and security reviewers.",
        "novelty_score": 0.72,
        "market_score": 0.61,
        "fit_score": 0.55,
        "effort_estimate": 0.30,
        "compliance_risk": 0.10,
        "composite_score": 0.58,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Acceptance criterion: spec_md + contract with >=1 table and >=1 route
# ---------------------------------------------------------------------------
def test_generate_spec_yields_spec_md_and_contract():
    result = sg.generate_spec(_concept(), persist=False)

    assert isinstance(result["spec_md"], str) and result["spec_md"].strip()
    contract = result["canvas_contract"]
    assert len(contract["tables"]) >= 1
    assert len(contract["routes"]) >= 1
    # spec_md actually reflects the concept + contract.
    assert "Smart Contract Auditor" in result["spec_md"]
    assert "## DB Tables" in result["spec_md"]
    assert "## Routes" in result["spec_md"]


# ---------------------------------------------------------------------------
# Deterministic contract derivation
# ---------------------------------------------------------------------------
def test_contract_fields_are_derived_deterministically():
    c = _concept()
    a = sg.build_canvas_contract(c)
    b = sg.build_canvas_contract(c)
    assert a == b  # pure / repeatable

    assert a["slug"] == "smart-contract-auditor"
    assert a["title"] == "Smart Contract Auditor"
    assert a["env_flag"] == "ICDEV_SMART_CONTRACT_AUDITOR_ENABLED"
    # contract shape the task-graph templater expects
    for key in ("slug", "title", "env_flag", "tables", "modules", "routes",
                "iqe_collections", "needs_mcp", "needs_reflex"):
        assert key in a


def test_table_prefix_is_acronym_and_tables_have_event_log():
    contract = sg.build_canvas_contract(_concept())
    names = [t["name"] for t in contract["tables"]]
    # 'smart-contract-auditor' -> 'sca' (stopwords dropped, initials taken)
    assert names[0] == "sca_items"
    assert any(t["append_only"] for t in contract["tables"])  # at least one append-only log
    assert "sca_events" in names


def test_routes_include_page_and_iqe():
    contract = sg.build_canvas_contract(_concept())
    kinds = {r["kind"] for r in contract["routes"]}
    assert "page" in kinds
    assert "iqe" in kinds
    page = next(r for r in contract["routes"] if r["kind"] == "page")
    assert page["path"] == "/smart-contract-auditor"


# ---------------------------------------------------------------------------
# Heuristic booleans
# ---------------------------------------------------------------------------
def test_needs_mcp_and_reflex_heuristics_fire():
    c = _concept(
        proposed_capability="An autonomous agent that continuously monitors an API pipeline.",
    )
    contract = sg.build_canvas_contract(c)
    assert contract["needs_mcp"] is True
    assert contract["needs_reflex"] is True
    # reflex capability adds a per-cycle runs table + reflex module
    assert any(t["name"].endswith("_runs") for t in contract["tables"])
    assert any("reflexes" in m["path"] for m in contract["modules"])


def test_plain_concept_has_no_mcp_or_reflex():
    c = _concept(
        proposed_capability="Render a static catalog of graded records for human review.",
        problem_statement="Reviewers lack a single place to see grades.",
        name="Grade Catalog",
        slug="grade-catalog",
    )
    contract = sg.build_canvas_contract(c)
    assert contract["needs_mcp"] is False
    assert contract["needs_reflex"] is False


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------
def test_missing_slug_falls_back_to_name():
    c = {"id": 5, "name": "Quantum Key Vault", "proposed_capability": "store keys"}
    contract = sg.build_canvas_contract(c)
    assert contract["slug"] == "quantum-key-vault"
    assert contract["env_flag"] == "ICDEV_QUANTUM_KEY_VAULT_ENABLED"
    assert len(contract["tables"]) >= 1 and len(contract["routes"]) >= 1


def test_empty_concept_still_buildable():
    result = sg.generate_spec({}, persist=False)
    contract = result["canvas_contract"]
    assert len(contract["tables"]) >= 1
    assert len(contract["routes"]) >= 1
    assert result["spec_md"].strip()


def test_task_count_is_positive():
    result = sg.generate_spec(_concept(), persist=False)
    assert result["task_count"] >= 8  # at least the 8-component baseline


# ---------------------------------------------------------------------------
# LLM-assist fallback (no provider -> deterministic template)
# ---------------------------------------------------------------------------
def test_llm_assist_disabled_uses_template(monkeypatch):
    monkeypatch.setenv("ICDEV_NO_LLM", "1")
    result = sg.generate_spec(_concept(), persist=False)
    assert "## Architecture Sketch" in result["spec_md"]
    # deterministic sketch references the slug route
    assert "/smart-contract-auditor" in result["spec_md"]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def test_generate_spec_persists_to_foundry_specs():
    from tools.db.storage import get_connection
    from tools.foundry.db.init_db import init_db

    init_db(force=True)
    conn = get_connection()
    try:
        # A concept must exist (FK). slug is UNIQUE and the test DB persists across
        # runs, so reuse an existing probe concept if one is already there.
        existing = conn.execute(
            "SELECT id FROM foundry_concepts WHERE slug = ?", ("persist-probe",)
        ).fetchone()
        if existing is not None:
            concept_id = existing[0]
        else:
            cur = conn.execute(
                "INSERT INTO foundry_concepts (run_id, name, slug, proposed_capability) "
                "VALUES (?, ?, ?, ?)",
                ("run-test", "Persist Probe", "persist-probe", "do a thing"),
            )
            conn.commit()
            concept_id = cur.lastrowid

        concept = _concept(id=concept_id, name="Persist Probe", slug="persist-probe")
        result = sg.generate_spec(concept, conn=conn)
        assert result["persisted"] is True

        row = conn.execute(
            "SELECT spec_md, canvas_contract, task_count FROM foundry_specs WHERE concept_id = ?",
            (concept_id,),
        ).fetchone()
        assert row is not None
        spec_md, contract_json, task_count = row[0], row[1], row[2]
        assert "Persist Probe" in spec_md
        contract = json.loads(contract_json)
        assert len(contract["tables"]) >= 1 and len(contract["routes"]) >= 1
        assert task_count >= 8
    finally:
        conn.close()


def test_no_id_concept_is_not_persisted():
    c = _concept()
    c.pop("id", None)
    result = sg.generate_spec(c, persist=True)
    assert result["persisted"] is False
    assert result["spec_id"] is None
