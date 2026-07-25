# CUI // SP-CTI
"""dmx-claims-02 (Phase A+B) — semantic claim extraction tests.

Covers: verbatim-anchor accept (span offsets), non-verbatim reject, sub-0.7
confidence drop, provenance persistence, dedupe_key stability across versions,
append-only supersede chain (latest-wins), CHECK constraint on status, RLS
columns populated, no-LLM rulebook fallback, and toggle-off = zero rows / zero
extractor calls. The LLM is always mocked — no real LLM calls.
"""
from __future__ import annotations

import uuid

import pytest

from tools.doc_modernization import claim_extractor as ce
from tools.doc_modernization.claim_extractor import (
    ClaimCandidate,
    anchor,
    dedupe_key,
    extract_and_persist_claims,
    extract_claim_candidates,
    extract_claim_candidates_no_llm,
    persist_claims,
    supersede_claim,
)


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def conn():
    """A sqlite connection (conftest forces sqlite) with an empty dic_claims."""
    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection

    c = get_connection()
    for stmt in MINIMAL_ICDEV_SCHEMA.split(";"):
        if "dic_claims" in stmt and "CREATE TABLE" in stmt:
            c.execute(stmt)
    c.execute("DELETE FROM dic_claims")
    c.commit()
    yield c
    try:
        c.execute("DELETE FROM dic_claims")
        c.commit()
    finally:
        c.close()


def _rows(conn, doc_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM dic_claims WHERE doc_id = %s ORDER BY extracted_at, claim_id",
        (doc_id,),
    ).fetchall()]


def _cand(claim_text="TLS 1.2 is required", subject="TLS 1.2", predicate="requires",
          obj="API endpoints", confidence=0.9, prov_model="llm"):
    return ClaimCandidate(
        subject_label=subject, predicate=predicate, claim_text=claim_text,
        subject_type="protocol", object_label=obj, object_type="concept",
        confidence=confidence, prov_model=prov_model,
    )


# ── anchor gate (pure) ─────────────────────────────────────────────────────────


def test_anchor_verbatim_returns_absolute_span():
    text = "The policy states TLS 1.2 is required for all endpoints."
    span = anchor("TLS 1.2 is required", text, base_offset=1000)
    local = text.find("TLS 1.2 is required")
    assert span == (1000 + local, 1000 + local + len("TLS 1.2 is required"))
    assert text[local:local + len("TLS 1.2 is required")] == "TLS 1.2 is required"


def test_anchor_non_verbatim_returns_none():
    text = "The policy mandates transport security."
    assert anchor("TLS 1.2 is required", text) is None


def test_anchor_empty_returns_none():
    assert anchor("", "some text") is None
    assert anchor("x", "") is None


# ── LLM candidate mapping (mocked extractor) ───────────────────────────────────


def test_extract_candidates_maps_triples(monkeypatch):
    def fake_graph(text, *, router=None):
        entities = [("TLS 1.2", "protocol"), ("API endpoints", "concept")]
        rels = [("TLS 1.2", "API endpoints", "requires", "TLS 1.2 is required")]
        return entities, rels

    monkeypatch.setattr(ce, "extract_graph_llm", fake_graph)
    cands = extract_claim_candidates("TLS 1.2 is required", router=object())
    assert len(cands) == 1
    c = cands[0]
    assert c.subject_label == "TLS 1.2"
    assert c.predicate == "requires"
    assert c.object_label == "API endpoints"
    assert c.subject_type == "protocol"
    assert c.object_type == "concept"
    assert c.claim_text == "TLS 1.2 is required"
    assert c.confidence == ce.LLM_CANDIDATE_CONFIDENCE
    assert c.prov_prompt_version == ce.CLAIM_PROMPT_VERSION


def test_extract_candidates_extractor_failure_is_graceful(monkeypatch):
    def boom(text, *, router=None):
        raise RuntimeError("no llm")

    monkeypatch.setattr(ce, "extract_graph_llm", boom)
    assert extract_claim_candidates("anything", router=object()) == []


# ── persistence gates ──────────────────────────────────────────────────────────


def test_persist_verbatim_accept_span_and_rls(conn):
    text = "Section 3: TLS 1.2 is required for all endpoints."
    ids = persist_claims(conn, "doc-1", "ver-1", text, [_cand()],
                         section="Section 3", chunk_link_id="lnk-1", page=3,
                         tenant_id="acme", classification="CUI")
    assert len(ids) == 1
    row = _rows(conn, "doc-1")[0]
    local = text.find("TLS 1.2 is required")
    assert row["anchor_start"] == local
    assert row["anchor_end"] == local + len("TLS 1.2 is required")
    assert text[row["anchor_start"]:row["anchor_end"]] == row["claim_text"]
    assert row["status"] == "pending_review"
    # RLS columns are first-class and populated.
    assert row["tenant_id"] == "acme"
    assert row["classification"] == "CUI"
    assert row["section"] == "Section 3"
    assert row["chunk_link_id"] == "lnk-1"
    assert row["page"] == 3


def test_persist_non_verbatim_rejected(conn):
    # claim_text paraphrased — cannot be located verbatim → rejected.
    text = "The policy mandates modern transport encryption."
    ids = persist_claims(conn, "doc-2", "ver-1", text, [_cand()])
    assert ids == []
    assert _rows(conn, "doc-2") == []


def test_persist_sub_confidence_dropped(conn):
    text = "TLS 1.2 is required here."
    ids = persist_claims(conn, "doc-3", "ver-1", text,
                         [_cand(claim_text="TLS 1.2 is required", confidence=0.5)])
    assert ids == []
    assert _rows(conn, "doc-3") == []


def test_provenance_persisted(conn):
    text = "TLS 1.2 is required here."
    persist_claims(conn, "doc-4", "ver-1", text,
                   [_cand(claim_text="TLS 1.2 is required", prov_model="kimi-k2")])
    row = _rows(conn, "doc-4")[0]
    assert row["prov_model"] == "kimi-k2"
    assert row["prov_prompt_version"] == ce.CLAIM_PROMPT_VERSION
    assert row["extracted_at"]  # ISO-8601 UTC, non-empty
    assert abs(float(row["confidence"]) - 0.9) < 1e-6


# ── dedupe + append-only chain ─────────────────────────────────────────────────


def test_dedupe_key_stable_across_versions():
    k1 = dedupe_key("doc-x", "TLS 1.2", "requires", "API endpoints")
    k2 = dedupe_key("doc-x", "tls 1.2", "REQUIRES", "api endpoints")
    assert k1 == k2  # case/whitespace-insensitive, version-independent


def test_dedupe_skips_second_persist(conn):
    text = "TLS 1.2 is required here."
    a = persist_claims(conn, "doc-5", "ver-1", text, [_cand(claim_text="TLS 1.2 is required")])
    assert len(a) == 1
    # Re-extraction (same proposition) does not create a duplicate.
    b = persist_claims(conn, "doc-5", "ver-2", text, [_cand(claim_text="TLS 1.2 is required")])
    assert b == []
    assert len(_rows(conn, "doc-5")) == 1


def test_append_only_supersede_chain_latest_wins(conn):
    text = "TLS 1.2 is required here."
    persist_claims(conn, "doc-6", "ver-1", text, [_cand(claim_text="TLS 1.2 is required")])
    latest = ce._latest_claims(conn, "doc-6")
    key = next(iter(latest))
    pending = latest[key]
    assert pending["status"] == "pending_review"

    active_id = supersede_claim(conn, pending, "active")
    conn.commit()
    latest = ce._latest_claims(conn, "doc-6")
    active = latest[key]
    assert active["status"] == "active"
    assert active["claim_id"] == active_id
    assert active["supersedes_id"] == pending["claim_id"]

    inval_id = supersede_claim(conn, active, "invalidated",
                               linked_evidence_ids=["fnd-abc"])
    conn.commit()
    rows = _rows(conn, "doc-6")
    assert len(rows) == 3  # append-only: 3 rows, never mutated
    latest = ce._latest_claims(conn, "doc-6")
    assert latest[key]["status"] == "invalidated"
    assert latest[key]["claim_id"] == inval_id
    assert latest[key]["supersedes_id"] == active_id
    # Anchor span preserved down the chain (sentence identity).
    assert latest[key]["claim_text"] == pending["claim_text"]
    assert latest[key]["anchor_start"] == pending["anchor_start"]


def test_supersede_rejects_invalid_status(conn):
    text = "TLS 1.2 is required here."
    persist_claims(conn, "doc-7", "ver-1", text, [_cand(claim_text="TLS 1.2 is required")])
    prior = next(iter(ce._latest_claims(conn, "doc-7").values()))
    with pytest.raises(ValueError):
        supersede_claim(conn, prior, "bogus_status")


def test_status_check_constraint_enforced(conn):
    # DB-level CHECK constraint independent of the Python guard.
    import sqlite3
    with pytest.raises((sqlite3.IntegrityError, Exception)):
        conn.execute(
            """INSERT INTO dic_claims
               (claim_id, doc_id, version_id, claim_text, anchor_start, anchor_end,
                subject_label, predicate, status, extracted_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (f"clm-{uuid.uuid4().hex[:12]}", "doc-8", "ver-1", "x", 0, 1,
             "TLS 1.2", "requires", "not_a_valid_status", ce._now()),
        )
        conn.commit()


# ── no-LLM rulebook fallback ───────────────────────────────────────────────────


def test_no_llm_fallback_rulebook_anchored(conn):
    text = "All legacy services still negotiate TLS 1.1 during the handshake."
    cands = extract_claim_candidates_no_llm(text)
    assert cands, "expected a crypto rulebook hit"
    tls = [c for c in cands if "TLS" in c.subject_label.upper()]
    assert tls, f"no TLS candidate in {[c.subject_label for c in cands]}"
    c = tls[0]
    assert c.prov_model == ce.NO_LLM_MODEL
    assert c.claim_text in text  # verbatim by construction
    ids = persist_claims(conn, "doc-9", "ver-1", text, cands)
    assert ids
    row = _rows(conn, "doc-9")[0]
    assert text[row["anchor_start"]:row["anchor_end"]] == row["claim_text"]
    assert row["prov_model"] == ce.NO_LLM_MODEL


# ── toggle gating (the load-bearing production invariant) ───────────────────────


def test_toggle_off_is_total_noop(conn, monkeypatch):
    calls = {"n": 0}

    def spy(text, *, router=None):
        calls["n"] += 1
        raise AssertionError("extractor must NOT be called when toggle off")

    monkeypatch.setattr(ce, "extract_graph_llm", spy)
    chunks = [("TLS 1.2 is required here.", ce.ChunkRef(doc_id="doc-10", version_id="ver-1"))]
    result = extract_and_persist_claims(
        conn, "doc-10", "ver-1", chunks,
        config={"claims": {"enabled": False, "pack_domains": ["crypto_protocols"]}},
    )
    assert result["enabled"] is False
    assert result["claims_new"] == 0
    assert calls["n"] == 0
    assert _rows(conn, "doc-10") == []


def test_toggle_on_end_to_end_persists_pending(conn, monkeypatch):
    def fake_graph(text, *, router=None):
        return ([("TLS 1.2", "protocol"), ("API endpoints", "concept")],
                [("TLS 1.2", "API endpoints", "requires", "TLS 1.2 is required")])

    monkeypatch.setattr(ce, "extract_graph_llm", fake_graph)
    chunks = [("Policy: TLS 1.2 is required for all API endpoints.",
               ce.ChunkRef(doc_id="doc-11", version_id="ver-1",
                           chunk_link_id="lnk-9", section="Policy", page=1))]
    result = extract_and_persist_claims(
        conn, "doc-11", "ver-1", chunks,
        config={"claims": {"enabled": True, "pack_domains": ["crypto_protocols"]}},
        router=object(),
    )
    assert result["enabled"] is True
    assert result["claims_new"] == 1
    row = _rows(conn, "doc-11")[0]
    assert row["status"] == "pending_review"
    assert row["subject_label"] == "TLS 1.2"
    assert row["predicate"] == "requires"
    assert row["object_label"] == "API endpoints"
    assert row["chunk_link_id"] == "lnk-9"
    src = "Policy: TLS 1.2 is required for all API endpoints."
    assert src[row["anchor_start"]:row["anchor_end"]] == "TLS 1.2 is required"


def test_toggle_on_idempotent_second_run(conn, monkeypatch):
    def fake_graph(text, *, router=None):
        return ([("TLS 1.2", "protocol"), ("API endpoints", "concept")],
                [("TLS 1.2", "API endpoints", "requires", "TLS 1.2 is required")])

    monkeypatch.setattr(ce, "extract_graph_llm", fake_graph)
    cfg = {"claims": {"enabled": True, "pack_domains": ["crypto_protocols"]}}
    chunks = [("TLS 1.2 is required.", ce.ChunkRef(doc_id="doc-12", version_id="ver-1"))]
    r1 = extract_and_persist_claims(conn, "doc-12", "ver-1", chunks, config=cfg, router=object())
    assert r1["claims_new"] == 1
    r2 = extract_and_persist_claims(conn, "doc-12", "ver-1", chunks, config=cfg, router=object())
    assert r2["claims_new"] == 0  # version_unchanged idempotency
    assert len(_rows(conn, "doc-12")) == 1
