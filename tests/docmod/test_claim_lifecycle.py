# CUI // SP-CTI
"""dmx-claims-02 (Phase C+D+E) — claim lifecycle: HITL promotion, deterministic
finding→claim linkage, anchor-drift auto-supersede, drift payload, and the
claims-panel UI surface.

Central proofs:
  * PRECISION — a deterministic ``TLS 1.2`` finding invalidates the EXACT anchored
    ``TLS 1.2`` claim and NOTHING else; a finding for an un-claimed subject flags
    nothing (no LLM anywhere on that edge — TRUST rule 1).
  * APPEND-ONLY — every HITL transition (promote/reject) and every invalidation is
    a NEW ``dic_claims`` row; prior rows are never mutated.
  * ANCHOR DRIFT — a claim whose chunk-local anchor no longer resolves verbatim is
    auto-``superseded``.
  * UI — the modernization (doc_detail) claims API returns 200 with seeded pending
    + invalidated claims, and the page renders the panel markup.

The LLM is never called here — linkage/verify/promotion are pure DB.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from tools.doc_modernization import claim_lifecycle as cl
from tools.doc_modernization.claim_extractor import persist_claims

REPO_ROOT = Path(__file__).resolve().parents[2]

_DDL_KEYS = ("dic_claims", "docmod_findings", "docmod_scan_runs")


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def conn():
    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection

    c = get_connection()
    for stmt in MINIMAL_ICDEV_SCHEMA.split(";"):
        if any(k in stmt for k in _DDL_KEYS) and "CREATE TABLE" in stmt:
            c.execute(stmt)
    c.execute(
        "CREATE TABLE IF NOT EXISTS dic_documents (doc_id TEXT PRIMARY KEY, "
        "collection_id TEXT, title TEXT, tenant_id TEXT, classification TEXT, created_at TEXT)"
    )
    c.execute("DELETE FROM dic_claims")
    c.execute("DELETE FROM docmod_findings")
    if not c.execute("SELECT run_id FROM docmod_scan_runs WHERE run_id='run-cl'").fetchone():
        c.execute("INSERT INTO docmod_scan_runs (run_id, scope_type, started_at) "
                  "VALUES ('run-cl','doc','2026-07-10T00:00:00')")
    c.commit()
    yield c
    try:
        c.execute("DELETE FROM dic_claims")
        c.execute("DELETE FROM docmod_findings")
        c.commit()
    finally:
        c.close()


def _rows(conn, doc_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM dic_claims WHERE doc_id = %s ORDER BY extracted_at, claim_id",
        (doc_id,),
    ).fetchall()]


def _seed_active_claim(conn, doc_id, *, subject="TLS 1.2", predicate="requires",
                       obj="all API endpoints", chunk_text=None, version_id="v1",
                       chunk_link_id="lnk-1"):
    """Persist a pending claim (verbatim-anchored) then HITL-promote it to active."""
    from tools.doc_modernization.claim_extractor import ClaimCandidate

    text = chunk_text or f"Section 3: {subject} is required for {obj} per policy."
    claim_text = f"{subject} is required" if subject in text else subject
    cand = ClaimCandidate(subject_label=subject, predicate=predicate,
                          claim_text=claim_text, subject_type="protocol",
                          object_label=obj, object_type="concept", confidence=0.9)
    ids = persist_claims(conn, doc_id, version_id, text, [cand],
                         chunk_link_id=chunk_link_id, section="Section 3", page=3)
    assert ids, f"seed claim not anchored in {text!r}"
    conn.commit()
    res = cl.promote_claim(conn, ids[0])
    conn.commit()
    assert res["status"] == "active"
    return res["claim_id"], text


def _seed_finding(conn, doc_id, entity_label="TLS 1.2", *, version_id="v1", state="open"):
    fid = f"fnd-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """INSERT INTO docmod_findings
           (finding_id, run_id, doc_id, version_id, pack_id, entity_label, entity_type,
            finding_type, currency_verdict, severity, rationale, confidence, state,
            dedupe_key, created_at)
           VALUES (%s,'run-cl',%s,%s,'crypto_protocols',%s,'protocol','deprecated_tech',
                   'deprecated','high','deprecated',1.0,%s,%s,'2026-07-10T00:00:00')""",
        (fid, doc_id, version_id, entity_label, state, f"dk-{uuid.uuid4().hex[:8]}"),
    )
    conn.commit()
    return fid


# ── Phase C: HITL promotion (append-only) ───────────────────────────────────────


def test_promote_is_append_only_transition(conn):
    doc = "doc-c1"
    from tools.doc_modernization.claim_extractor import ClaimCandidate
    text = "Policy: TLS 1.2 is required here."
    persist_claims(conn, doc, "v1", text,
                   [ClaimCandidate(subject_label="TLS 1.2", predicate="requires",
                                   claim_text="TLS 1.2 is required", confidence=0.9)],
                   chunk_link_id="lnk-1")
    conn.commit()
    pend = _rows(conn, doc)[0]
    assert pend["status"] == "pending_review"

    res = cl.promote_claim(conn, pend["claim_id"])
    conn.commit()
    assert res["status"] == "active"
    rows = _rows(conn, doc)
    assert len(rows) == 2  # append-only: original untouched + new active row
    active = cl._latest_for_key(conn, doc, pend["dedupe_key"])
    assert active["status"] == "active"
    assert active["supersedes_id"] == pend["claim_id"]
    # anchor/sentence identity preserved across the transition
    assert active["claim_text"] == pend["claim_text"]
    assert active["anchor_start"] == pend["anchor_start"]


def test_reject_moves_pending_to_superseded(conn):
    cid, _ = _seed_active_claim(conn, "doc-c2")  # active
    # A fresh pending claim to reject
    from tools.doc_modernization.claim_extractor import ClaimCandidate
    persist_claims(conn, "doc-c2b", "v1", "AES-128 is used here.",
                   [ClaimCandidate(subject_label="AES-128", predicate="uses",
                                   claim_text="AES-128 is used", confidence=0.9)])
    conn.commit()
    pend = _rows(conn, "doc-c2b")[0]
    res = cl.reject_claim(conn, pend["claim_id"])
    conn.commit()
    assert res["status"] == "superseded"
    latest = cl._latest_for_key(conn, "doc-c2b", pend["dedupe_key"])
    assert latest["status"] == "superseded"


def test_promote_guards_non_pending_and_non_head(conn):
    cid, _ = _seed_active_claim(conn, "doc-c3")  # already active
    # promoting an already-active claim's head → idempotent no-op, not an error
    res = cl.promote_claim(conn, cid)
    assert res.get("reason") == "already_in_state" and res.get("status") == "active"
    # promoting the superseded (non-head) pending row → refuses to fork history
    doc = "doc-c3"
    pending_row = [r for r in _rows(conn, doc) if r["status"] == "pending_review"][0]
    res2 = cl.promote_claim(conn, pending_row["claim_id"])
    assert res2.get("error") and "current state" in res2["error"]


def test_list_claims_returns_latest_state_only(conn):
    _seed_active_claim(conn, "doc-c4", subject="TLS 1.2")
    claims = cl.list_claims(conn, "doc-c4")
    assert len(claims) == 1  # supersede chain collapsed to head
    assert claims[0]["status"] == "active"
    assert claims[0]["linked_evidence_ids"] == []
    # status filter
    assert cl.list_claims(conn, "doc-c4", status="pending_review") == []


# ── Phase D: deterministic finding→claim linkage (PRECISION) ─────────────────────


def test_linkage_flags_exact_claim_and_nothing_else(conn):
    """A TLS 1.2 finding invalidates the TLS 1.2 claim ONLY — the AES-256 claim,
    with no matching finding, stays active. This is the sentence-level precision
    proof: the invalidated row carries the exact verbatim anchor span."""
    doc = "doc-d1"
    tls_id, tls_text = _seed_active_claim(conn, doc, subject="TLS 1.2",
                                          chunk_link_id="lnk-tls")
    aes_id, _ = _seed_active_claim(conn, doc, subject="AES-256", predicate="uses",
                                   obj="data at rest", chunk_link_id="lnk-aes")
    fid = _seed_finding(conn, doc, entity_label="TLS 1.2")

    out = cl.link_findings_to_claims(conn, doc, "v1")
    conn.commit()
    assert len(out["invalidated"]) == 1  # exactly one claim flagged

    claims = {c["subject_label"]: c for c in cl.list_claims(conn, doc)}
    assert claims["TLS 1.2"]["status"] == "invalidated"
    assert claims["AES-256"]["status"] == "active"  # precision: untouched
    inval = claims["TLS 1.2"]
    # the invalidated row carries the finding id + the verbatim anchored sentence
    assert fid in inval["linked_evidence_ids"]
    assert tls_text[inval["anchor_start"]:inval["anchor_end"]] == inval["claim_text"]
    assert inval["claim_text"] == "TLS 1.2 is required"


def test_linkage_matches_object_label(conn):
    doc = "doc-d2"
    _seed_active_claim(conn, doc, subject="Policy", predicate="mandates",
                       obj="TLS 1.2", chunk_link_id="lnk-1")
    _seed_finding(conn, doc, entity_label="TLS 1.2")
    out = cl.link_findings_to_claims(conn, doc, "v1")
    conn.commit()
    assert len(out["invalidated"]) == 1  # matched on object_label


def test_linkage_no_finding_flags_nothing(conn):
    doc = "doc-d3"
    _seed_active_claim(conn, doc, subject="TLS 1.2")
    # finding for a DIFFERENT, un-claimed subject
    _seed_finding(conn, doc, entity_label="SSLv3")
    out = cl.link_findings_to_claims(conn, doc, "v1")
    conn.commit()
    assert out["invalidated"] == []
    assert cl.list_claims(conn, doc)[0]["status"] == "active"


def test_linkage_ignores_pending_claims(conn):
    """Only ACTIVE (human-promoted) claims can be invalidated — the HITL gate."""
    doc = "doc-d4"
    from tools.doc_modernization.claim_extractor import ClaimCandidate
    persist_claims(conn, doc, "v1", "TLS 1.2 is required here.",
                   [ClaimCandidate(subject_label="TLS 1.2", predicate="requires",
                                   claim_text="TLS 1.2 is required", confidence=0.9)])
    conn.commit()  # left pending_review, NOT promoted
    _seed_finding(conn, doc, entity_label="TLS 1.2")
    out = cl.link_findings_to_claims(conn, doc, "v1")
    conn.commit()
    assert out["invalidated"] == []
    assert cl.list_claims(conn, doc)[0]["status"] == "pending_review"


def test_linkage_is_idempotent(conn):
    doc = "doc-d5"
    _seed_active_claim(conn, doc, subject="TLS 1.2")
    _seed_finding(conn, doc, entity_label="TLS 1.2")
    first = cl.link_findings_to_claims(conn, doc, "v1")
    conn.commit()
    assert len(first["invalidated"]) == 1
    second = cl.link_findings_to_claims(conn, doc, "v1")  # claim now invalidated
    conn.commit()
    assert second["invalidated"] == []  # not re-flagged (no longer active)


# ── Phase D: anchor-drift auto-supersede ────────────────────────────────────────


def test_verify_supersedes_on_anchor_drift(conn):
    doc = "doc-d6"
    cid, text = _seed_active_claim(conn, doc, subject="TLS 1.2", chunk_link_id="lnk-1")
    # chunk text edited so the anchored slice no longer equals claim_text
    edited = text.replace("TLS 1.2 is required", "modern transport security is used")
    out = cl.verify_claim_anchors(conn, doc, {"lnk-1": edited})
    conn.commit()
    assert len(out["superseded"]) == 1
    assert cl.list_claims(conn, doc)[0]["status"] == "superseded"


def test_verify_keeps_claim_when_anchor_holds(conn):
    doc = "doc-d7"
    cid, text = _seed_active_claim(conn, doc, subject="TLS 1.2", chunk_link_id="lnk-1")
    out = cl.verify_claim_anchors(conn, doc, {"lnk-1": text})  # unchanged
    conn.commit()
    assert out["superseded"] == []
    assert cl.list_claims(conn, doc)[0]["status"] == "active"


def test_verify_supersedes_when_chunk_missing(conn):
    doc = "doc-d8"
    _seed_active_claim(conn, doc, subject="TLS 1.2", chunk_link_id="lnk-1")
    out = cl.verify_claim_anchors(conn, doc, {})  # chunk gone entirely
    conn.commit()
    assert len(out["superseded"]) == 1


# ── drift payload helper ────────────────────────────────────────────────────────


def test_claim_for_finding_returns_invalidated_claim(conn):
    doc = "doc-d9"
    _seed_active_claim(conn, doc, subject="TLS 1.2", chunk_link_id="lnk-1")
    fid = _seed_finding(conn, doc, entity_label="TLS 1.2")
    cl.link_findings_to_claims(conn, doc, "v1")
    conn.commit()
    got = cl.claim_for_finding(conn, {"finding_id": fid, "doc_id": doc})
    assert got is not None
    assert got["status"] == "invalidated"
    assert got["claim_text"] == "TLS 1.2 is required"
    # a finding that touched no claim → None
    assert cl.claim_for_finding(conn, {"finding_id": "fnd-none", "doc_id": doc}) is None


# ── Phase E: claims-panel UI (test client) ──────────────────────────────────────


@pytest.fixture()
def client(conn):
    import flask

    import tools.document_intelligence.blueprint as bp_mod
    import tools.document_intelligence.modernization_routes  # noqa: F401

    app = flask.Flask(__name__)
    app.register_blueprint(bp_mod.dic_bp, url_prefix="/document-intelligence")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_claims_api_returns_pending_and_invalidated(client, conn):
    doc = "doc-ui1"
    # one active→invalidated claim + one still-pending claim
    _seed_active_claim(conn, doc, subject="TLS 1.2", chunk_link_id="lnk-1")
    fid = _seed_finding(conn, doc, entity_label="TLS 1.2")
    cl.link_findings_to_claims(conn, doc, "v1")
    from tools.doc_modernization.claim_extractor import ClaimCandidate
    persist_claims(conn, doc, "v1", "Also, SHA-256 is preferred here.",
                   [ClaimCandidate(subject_label="SHA-256", predicate="prefers",
                                   claim_text="SHA-256 is preferred", confidence=0.9)])
    conn.commit()

    resp = client.get(f"/document-intelligence/api/modernization/doc/{doc}/claims")
    assert resp.status_code == 200, resp.get_json()
    claims = {c["subject_label"]: c for c in resp.get_json()}
    assert claims["TLS 1.2"]["status"] == "invalidated"
    assert fid in claims["TLS 1.2"]["linked_evidence_ids"]
    assert claims["TLS 1.2"]["claim_text"] == "TLS 1.2 is required"
    assert claims["SHA-256"]["status"] == "pending_review"


def test_claims_promote_endpoint_append_only(client, conn):
    doc = "doc-ui2"
    from tools.doc_modernization.claim_extractor import ClaimCandidate
    persist_claims(conn, doc, "v1", "TLS 1.2 is required here.",
                   [ClaimCandidate(subject_label="TLS 1.2", predicate="requires",
                                   claim_text="TLS 1.2 is required", confidence=0.9)])
    conn.commit()
    pend = _rows(conn, doc)[0]
    resp = client.post(
        f"/document-intelligence/api/modernization/claims/{pend['claim_id']}/promote",
        json={})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["status"] == "active"
    assert len(_rows(conn, doc)) == 2  # append-only


def test_claims_reject_endpoint(client, conn):
    doc = "doc-ui3"
    from tools.doc_modernization.claim_extractor import ClaimCandidate
    persist_claims(conn, doc, "v1", "AES-128 is used here.",
                   [ClaimCandidate(subject_label="AES-128", predicate="uses",
                                   claim_text="AES-128 is used", confidence=0.9)])
    conn.commit()
    pend = _rows(conn, doc)[0]
    resp = client.post(
        f"/document-intelligence/api/modernization/claims/{pend['claim_id']}/reject",
        json={})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["status"] == "superseded"


def test_doc_detail_template_renders_claims_panel():
    detail = (REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"
              / "doc_detail.html").read_text(encoding="utf-8")
    assert "claims-panel" in detail and "claims-list" in detail
    assert "/api/modernization/doc/" in detail and "/claims" in detail
    assert "claimAction" in detail
    assert "'%.0f'|format" not in detail  # Jinja guardrail
    # icdev mirror stays in lockstep
    mirror = (REPO_ROOT / "icdev" / "tools" / "dashboard" / "templates"
              / "document_intelligence" / "doc_detail.html").read_text(encoding="utf-8")
    assert "claims-panel" in mirror


def test_icdev_mirror_module_present():
    assert (REPO_ROOT / "icdev" / "tools" / "doc_modernization"
            / "claim_lifecycle.py").exists()
