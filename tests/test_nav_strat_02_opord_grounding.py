#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for OPORD paragraph grounding (nav-strat-02).

Covers the TRUST invariant applied to tools/strategos/opord.py:
  * synthesized paragraphs carry inline [source: <id>] citations that resolve
    to injected evidence -> grounded verdict
  * a fabricated (hallucinated) citation -> flagged ungrounded, and approval is
    blocked unless an explicit, audited force override is supplied
  * the no-LLM fallback is labeled and claims NO citations
  * the OPORD detail / approval payloads surface the grounding verdict
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")

# Shim-aware imports: resolve the concrete module objects so monkeypatch.setattr
# targets the same module the code-under-test imports at call time.
storage = importlib.import_module("tools.db.storage")
opord = importlib.import_module("tools.strategos.opord")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sg_opords (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    theater         TEXT DEFAULT 'unspecified',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    task_org        TEXT,
    situation       TEXT,
    mission         TEXT,
    execution       TEXT,
    sustainment     TEXT,
    command_signal  TEXT,
    grounding       TEXT,
    grounding_status TEXT DEFAULT 'pending',
    status          TEXT DEFAULT 'draft',
    created_by      TEXT DEFAULT 'analyst',
    approved_by     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sg_opord_grounding_audit (
    id                TEXT PRIMARY KEY,
    opord_id          TEXT NOT NULL,
    action            TEXT NOT NULL,
    grounding_status  TEXT,
    actor             TEXT,
    reason            TEXT,
    created_at        TEXT NOT NULL
);
"""


@pytest.fixture(autouse=True)
def _iso_db(monkeypatch, tmp_path):
    """Route opord storage to a dedicated SQLite file (RLS-free, %s->? translated)."""
    db_path = str(tmp_path / "test_opord_grounding.db")
    real_get = storage.get_connection

    def _fake_get(db_path_arg=None):
        return real_get(db_path=db_path)

    monkeypatch.setattr(storage, "get_connection", _fake_get)
    monkeypatch.setattr(storage, "is_pg", lambda: False)

    conn = real_get(db_path=db_path)
    try:
        for stmt in _SCHEMA.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()
    yield db_path


def _patch_sources(monkeypatch, allowed_ids):
    sources = [{"id": i, "title": f"Doctrine {i}", "excerpt": "excerpt"} for i in allowed_ids]

    def _fake_gather(scenario, theater):
        return sources, list(allowed_ids), "\n".join(f"[source: {i}]" for i in allowed_ids)

    monkeypatch.setattr(opord, "_gather_sources", _fake_gather)


def _patch_llm(monkeypatch, content, used_llm=True):
    monkeypatch.setattr(opord, "_llm_call", lambda prompt, has_sources=False: (content, used_llm))


def _new_opord():
    r = opord.create_opord(title="Op Test", theater="taiwan", scenario="test")
    return r["opord_id"]


# ── 1. Grounded paragraph passes ────────────────────────────────────────────

def test_grounded_paragraph_carries_resolving_citation(monkeypatch):
    _patch_sources(monkeypatch, ["clausewitz-cog-001"])
    _patch_llm(monkeypatch, "Enemy massed at the strait. [source: clausewitz-cog-001]")

    oid = _new_opord()
    res = opord.synthesize_paragraph(oid, 1, scenario="PLA incursion")

    assert res["grounding"]["status"] == opord.GROUNDING_GROUNDED
    assert res["grounding"]["cited_count"] == 1
    assert res["grounding"]["hallucinated"] == []
    assert res["grounding_status"] == opord.GROUNDING_GROUNDED


def test_missing_citation_is_ungrounded(monkeypatch):
    _patch_sources(monkeypatch, ["clausewitz-cog-001"])
    _patch_llm(monkeypatch, "Enemy massed at the strait with no attribution whatsoever.")

    oid = _new_opord()
    res = opord.synthesize_paragraph(oid, 1, scenario="PLA incursion")

    assert res["grounding"]["status"] == opord.GROUNDING_UNGROUNDED
    assert res["grounding"]["has_citations"] is False


# ── 2. Fabricated citation -> flagged; approval blocked without force ────────

def test_fabricated_citation_flagged_and_blocks_approval(monkeypatch):
    _patch_sources(monkeypatch, ["clausewitz-cog-001"])
    _patch_llm(monkeypatch, "Enemy CoG identified. [source: totally-made-up-999]")

    oid = _new_opord()
    res = opord.synthesize_paragraph(oid, 1, scenario="PLA incursion")

    assert res["grounding"]["status"] == opord.GROUNDING_UNGROUNDED
    assert "totally-made-up-999" in res["grounding"]["hallucinated"]

    # Approval must be blocked without an override.
    blocked = opord.approve_opord(oid, approved_by="cmdr")
    assert blocked["status"] == "blocked"
    assert blocked["forced"] is False
    assert blocked["grounding_status"] == opord.GROUNDING_UNGROUNDED

    # Force without a reason is still blocked.
    no_reason = opord.approve_opord(oid, approved_by="cmdr", force=True, force_reason="  ")
    assert no_reason["status"] == "blocked"

    # Force WITH a reason approves and records an audit row.
    forced = opord.approve_opord(
        oid, approved_by="cmdr", force=True, force_reason="Time-critical; cited offline."
    )
    assert forced["status"] == "approved (forced)"
    assert forced["forced"] is True

    o = opord.get_opord(oid)
    assert o["status"] == "approved"
    assert o["approved_by"] == "cmdr"

    # Audit trail recorded the override.
    conn = storage.get_connection()
    try:
        rows = conn.execute(
            "SELECT action, actor, reason FROM sg_opord_grounding_audit WHERE opord_id = ?",
            (oid,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "force_approve"
    assert "Time-critical" in rows[0][2]


def test_grounded_opord_approves_without_force(monkeypatch):
    _patch_sources(monkeypatch, ["clausewitz-cog-001"])
    _patch_llm(monkeypatch, "Grounded prose. [source: clausewitz-cog-001]")

    oid = _new_opord()
    for para in range(1, 6):
        opord.synthesize_paragraph(oid, para, scenario="PLA incursion")

    result = opord.approve_opord(oid, approved_by="cmdr")
    assert result["status"] == "approved"
    assert result["forced"] is False
    assert result["grounding_status"] == opord.GROUNDING_GROUNDED

    # No audit override row for a clean approval.
    conn = storage.get_connection()
    try:
        rows = conn.execute(
            "SELECT 1 FROM sg_opord_grounding_audit WHERE opord_id = ?", (oid,)
        ).fetchall()
    finally:
        conn.close()
    assert rows == []


# ── 3. Fallback path labeled, no fabricated citations ───────────────────────

def test_fallback_is_labeled_and_claims_no_citations(monkeypatch):
    _patch_sources(monkeypatch, ["clausewitz-cog-001"])
    _patch_llm(monkeypatch, "", used_llm=False)  # LLM unavailable

    oid = _new_opord()
    res = opord.synthesize_paragraph(oid, 2, scenario="PLA incursion")

    assert res["grounding"]["status"] == opord.GROUNDING_FALLBACK
    assert res["grounding"]["has_citations"] is False
    assert "[source:" not in res["content"]
    assert "TEMPLATE" in res["content"]
    # A fallback drags the whole OPORD to a non-approvable state.
    assert res["grounding_status"] == opord.GROUNDING_FALLBACK

    blocked = opord.approve_opord(oid, approved_by="cmdr")
    assert blocked["status"] == "blocked"


# ── 4. Approval / detail payload surfaces the grounding verdict ──────────────

def test_detail_payload_includes_grounding_verdict(monkeypatch):
    _patch_sources(monkeypatch, ["clausewitz-cog-001"])
    _patch_llm(monkeypatch, "Grounded. [source: clausewitz-cog-001]")

    oid = _new_opord()
    opord.synthesize_paragraph(oid, 1, scenario="PLA incursion")

    o = opord.get_opord(oid)
    assert "grounding" in o
    assert "grounding_status" in o
    assert o["grounding"]["situation"]["status"] == opord.GROUNDING_GROUNDED
    assert o["grounding_status"] == opord.GROUNDING_GROUNDED
    assert o["grounded"] is True


def test_approve_return_payload_carries_grounding_status(monkeypatch):
    _patch_sources(monkeypatch, ["clausewitz-cog-001"])
    _patch_llm(monkeypatch, "Ungrounded prose, no cite.")

    oid = _new_opord()
    opord.synthesize_paragraph(oid, 1, scenario="PLA incursion")

    result = opord.approve_opord(oid, approved_by="cmdr")
    assert "grounding_status" in result
    assert result["grounding_status"] == opord.GROUNDING_UNGROUNDED


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
