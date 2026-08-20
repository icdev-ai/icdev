# CUI // SP-CTI
"""cef-ui-03: HITL approve/reject for resolve-produced proposals, wired to the
EXISTING review routes.

A redline proposal has TWO identities and they were never connected:

  * a ``docmod_findings`` row  -> the DISPOSITION door,
    ``POST /api/modernization/findings/<id>/resolve``
  * a ``dic_suggestions`` row  -> the APPLY door,
    ``POST /api/suggestions/<id>/accept|reject`` (the only writer of
    ``dic_sections.content`` for a proposal)

Measured on the live PostgreSQL board 2026-08-18: 49 findings in state
``redline_drafted``, 49 ``dic_suggestions`` rows all ``pending``, and ZERO rows
in ``dic_suggestion_decisions``. Rejecting a finding left its proposal fully
applyable through the other door; accepting one did nothing to the proposal at
all.

These tests pin the four things the card asks for:
  1. the decision flows through the EXISTING routes -- the decision-route set is
     frozen, so a new approval path fails the suite;
  2. no proposal reaches the document without a recorded human decision;
  3. the redline citation hard-block still fires on a hallucinated citation;
  4. accept and reject are both audited, on both doors.
"""
from __future__ import annotations

import json
import uuid

import flask
import pytest

# -- fixtures ----------------------------------------------------------------

_EXTRA_DDL = [
    """CREATE TABLE IF NOT EXISTS dic_documents (
        doc_id TEXT PRIMARY KEY, collection_id TEXT, title TEXT,
        tenant_id TEXT, classification TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_versions (
        version_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL,
        version_no INTEGER NOT NULL DEFAULT 1, origin TEXT, status TEXT,
        assigned_to TEXT, review_notes TEXT, content_sha256 TEXT,
        created_at TEXT, created_by TEXT, tenant_id TEXT, classification TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_sections (
        section_id TEXT PRIMARY KEY, version_id TEXT, doc_id TEXT,
        heading TEXT, content TEXT, citations_json TEXT, status TEXT,
        origin TEXT, assigned_to TEXT, reviewed_by TEXT, reviewed_at TEXT,
        created_at TEXT, created_by TEXT, tenant_id TEXT, classification TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_team_access (
        collection_id TEXT, user_id TEXT, role TEXT)""",
    # Keep in step with tests/conftest.py's audit_trail (which mirrors the LIVE
    # PostgreSQL shape). Declared here rather than sliced out of
    # MINIMAL_ICDEV_SCHEMA because several other statements mention the table.
    """CREATE TABLE IF NOT EXISTS audit_trail (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,
        event_type TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
        details TEXT, affected_files TEXT, classification TEXT DEFAULT 'CUI',
        ip_address TEXT, session_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        hash TEXT, previous_hash TEXT, signature TEXT)""",
]

_SCHEMA_KEYS = ("docmod_findings", "docmod_scan_runs")


@pytest.fixture()
def db():
    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection

    conn = get_connection()
    for stmt in MINIMAL_ICDEV_SCHEMA.split(";"):
        if "CREATE TABLE" in stmt and any(k in stmt for k in _SCHEMA_KEYS):
            conn.execute(stmt)
    for ddl in _EXTRA_DDL:
        conn.execute(ddl)
    # suggestion_store lazily creates its own two tables
    from tools.document_intelligence.suggestion_store import _ensure_tables
    _ensure_tables(conn)
    for table in ("docmod_findings", "dic_suggestions", "dic_suggestion_decisions",
                  "audit_trail", "dic_sections", "dic_versions"):
        conn.execute(f"DELETE FROM {table}")  # nosec B608 - fixture reset, constant names
    if not conn.execute("SELECT run_id FROM docmod_scan_runs WHERE run_id='run-hitl'").fetchone():
        conn.execute("INSERT INTO docmod_scan_runs (run_id, scope_type, started_at) "
                     "VALUES ('run-hitl','doc','2026-08-18T00:00:00')")
    conn.commit()
    conn.close()
    yield


@pytest.fixture()
def client():
    import tools.document_intelligence.blueprint as bp_mod
    import tools.document_intelligence.modernization_routes  # noqa: F401

    app = flask.Flask(__name__)
    app.register_blueprint(bp_mod.dic_bp, url_prefix="/document-intelligence")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


P = "/document-intelligence"


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _seed_section(doc_id: str, content: str = "All services shall use TLS 1.1.") -> str:
    section_id = f"sec-{uuid.uuid4().hex[:10]}"
    conn = _conn()
    conn.execute(
        "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, "
        "status, origin, created_at) VALUES (%s,%s,%s,'Security',%s,'draft','human_authored',"
        "'2026-08-18T00:00:00')",
        (section_id, f"{doc_id}_v1", doc_id, content),
    )
    conn.commit()
    conn.close()
    return section_id


def _seed_proposal(state: str = "redline_drafted") -> tuple[str, str, str, str]:
    """Seed one resolve-produced proposal: finding + linked pending suggestion.

    Returns (finding_id, suggestion_id, doc_id, section_id).
    """
    from tools.document_intelligence.suggestion_store import create_suggestion

    doc_id = f"doc-{uuid.uuid4().hex[:8]}"
    section_id = _seed_section(doc_id)
    suggestion_id = create_suggestion(
        doc_id=doc_id, section_id=section_id, collection_id="col-hitl",
        canvas_source="doc_modernization",
        suggested_content="All services shall use TLS 1.2 or higher "
                          "[source: rule:crypto-tls-02].",
        current_content="All services shall use TLS 1.1.",
        rationale="[docmod] TLS 1.1 is deprecated (RFC 8996).",
    )
    finding_id = f"fnd-{uuid.uuid4().hex[:12]}"
    conn = _conn()
    conn.execute(
        """INSERT INTO docmod_findings
           (finding_id, run_id, doc_id, version_id, pack_id, entity_label, entity_type,
            finding_type, currency_verdict, severity, rationale, evidence_json,
            recommended_replacement, confidence, state, redline_suggestion_id,
            dedupe_key, section_heading, created_at)
           VALUES (%s,'run-hitl',%s,'v1','crypto_protocols','TLS 1.1','protocol',
                   'deprecated_tech','deprecated','high','TLS 1.1 is deprecated.',%s,
                   'TLS 1.2 or higher',1.0,%s,%s,%s,'Security','2026-08-18T00:00:00')""",
        (finding_id, doc_id,
         json.dumps([{"source": "rule:crypto-tls-02", "detail": "RFC 8996", "date": ""}]),
         state, suggestion_id, f"dk-{uuid.uuid4().hex[:8]}"),
    )
    conn.commit()
    conn.close()
    return finding_id, suggestion_id, doc_id, section_id


def _suggestion_status(suggestion_id: str) -> str:
    conn = _conn()
    row = conn.execute("SELECT status FROM dic_suggestions WHERE suggestion_id = %s",
                       (suggestion_id,)).fetchone()
    conn.close()
    return dict(row)["status"] if row else ""


def _section_content(section_id: str) -> str:
    conn = _conn()
    row = conn.execute("SELECT content FROM dic_sections WHERE section_id = %s",
                       (section_id,)).fetchone()
    conn.close()
    return (dict(row).get("content") or "") if row else ""


def _audit_rows(action_prefix: str = "") -> list[dict]:
    conn = _conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT event_type, actor, action, details FROM audit_trail ORDER BY id").fetchall()]
    conn.close()
    if action_prefix:
        rows = [r for r in rows if (r.get("action") or "").startswith(action_prefix)]
    return rows


# -- 1. the existing routes, and only the existing routes --------------------

# Frozen 2026-08-18. cef-ui-03 wires HITL approve/reject through these and adds
# NO route: a new approval path shows up here as a new entry.
_DECISION_ROUTES = {
    f"{P}/api/modernization/claims/<claim_id>/reject",
    f"{P}/api/modernization/findings/<finding_id>/resolve",
    f"{P}/api/review/<item_id>/approve",
    f"{P}/api/review/<item_id>/reject",
    f"{P}/api/sections/<section_id>/approve",
    f"{P}/api/sections/<section_id>/reject",
    f"{P}/api/suggestions/<suggestion_id>/accept",
    f"{P}/api/suggestions/<suggestion_id>/reject",
}


#: Routes that MATCH the keyword scan but DECIDE NOTHING, each with the reason
#: it is not a decision. Enumerated BY NAME rather than matched by pattern, for
#: the reason every census in this repo is enumerated: a pattern quietly widens,
#: and the next route that happens to contain the word would inherit the
#: exemption without anyone reading it.
#:
#: This does NOT weaken the guard. A new approve/reject/accept route still fails
#: unless someone adds it here, and adding a route that takes a disposition to a
#: list headed "decides nothing" is a claim a reviewer can check in one look.
_NON_DECISION_KEYWORD_ROUTES = {
    # cef-ui-01. Here the verb means `cortex.resolve` -- the governed EVIDENCE
    # seam -- not a HITL disposition. The handler runs a retrieval and renders
    # verdict, citations and SME advisory; it takes no disposition, writes no
    # dic_suggestion_decisions row and mutates no document. The collision is on
    # the WORD: cef-ui-03's own decision route ends in the same verb, where it
    # means "dispose of this finding".
    f"{P}/api/docdrift/resolve",
    f"{P}/api/docdrift/resolve-batch",
}


def test_no_new_approval_path_is_introduced(client):
    app = client.application
    found = {
        str(rule) for rule in app.url_map.iter_rules()
        if any(k in str(rule) for k in ("approve", "reject", "resolve", "accept", "decide"))
    } - _NON_DECISION_KEYWORD_ROUTES
    assert found == _DECISION_ROUTES, (
        "the decision-route surface changed - cef-ui-03 requires approve/reject to "
        f"flow through the EXISTING routes.\nadded: {sorted(found - _DECISION_ROUTES)}\n"
        f"removed: {sorted(_DECISION_ROUTES - found)}"
    )


# -- 2. no proposal reaches the document without a recorded human decision ---

def test_accepting_the_finding_does_not_apply_the_proposal(client, db):
    """Accepting a FINDING means 'yes, this document is stale'. It is NOT
    authorisation to write LLM prose into the document - that needs the second,
    explicit decision at the apply door."""
    finding_id, suggestion_id, _doc, section_id = _seed_proposal()
    before = _section_content(section_id)

    resp = client.post(f"{P}/api/modernization/findings/{finding_id}/resolve",
                       json={"disposition": "accepted", "reviewer": "alice"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()

    assert _section_content(section_id) == before, "accept auto-applied the proposal"
    assert _suggestion_status(suggestion_id) == "pending"
    # the route hands the reviewer the still-pending proposal + its apply door
    assert body["redline_suggestion_id"] == suggestion_id
    assert body["proposal"]["status"] == "pending"
    assert body["proposal"]["apply_url"].endswith(f"/api/suggestions/{suggestion_id}/accept")


def test_rejecting_the_finding_makes_the_proposal_unapplyable(client, db):
    """The cascade only ever REMOVES an apply capability. A proposal a human
    declined must not stay applyable through the other door."""
    finding_id, suggestion_id, _doc, section_id = _seed_proposal()
    before = _section_content(section_id)

    resp = client.post(f"{P}/api/modernization/findings/{finding_id}/resolve",
                       json={"disposition": "rejected", "reviewer": "alice"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["proposal"]["status"] == "rejected"
    assert _suggestion_status(suggestion_id) == "rejected"

    # the decision is on the append-only decision log too
    from tools.document_intelligence.suggestion_store import get_decisions_for_suggestion
    decisions = get_decisions_for_suggestion(suggestion_id)
    assert [d["decision"] for d in decisions] == ["rejected"]
    assert decisions[0]["decided_by"] == "alice"

    # and the apply door now refuses
    applied = client.post(f"{P}/api/suggestions/{suggestion_id}/accept", json={})
    assert applied.status_code == 409
    assert _section_content(section_id) == before


def test_apply_refuses_when_the_decision_cannot_be_recorded(client, db, monkeypatch):
    """Fail-closed: the decision is recorded BEFORE the section is mutated, and
    a decision that cannot be recorded means the proposal does not land."""
    import tools.document_intelligence.suggestion_store as store

    _finding, suggestion_id, _doc, section_id = _seed_proposal()
    before = _section_content(section_id)

    def _boom(*a, **k):
        raise RuntimeError("decision log unavailable")

    monkeypatch.setattr(store, "decide_suggestion", _boom)

    resp = client.post(f"{P}/api/suggestions/{suggestion_id}/accept", json={})
    assert resp.status_code == 500
    body = resp.get_json()
    assert body.get("applied") is False
    assert body.get("decision_recorded") is False
    assert _section_content(section_id) == before, (
        "the proposal reached the document with no recorded human decision"
    )


def test_apply_records_the_decision_then_lands_the_content(client, db):
    _finding, suggestion_id, _doc, section_id = _seed_proposal()

    resp = client.post(f"{P}/api/suggestions/{suggestion_id}/accept",
                       json={"note": "verified against RFC 8996"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert "TLS 1.2 or higher" in _section_content(section_id)
    assert _suggestion_status(suggestion_id) == "accepted"

    from tools.document_intelligence.suggestion_store import get_decisions_for_suggestion
    assert [d["decision"] for d in get_decisions_for_suggestion(suggestion_id)] == ["accepted"]


# -- 3. the TRUST citation hard-block is preserved ---------------------------

def test_hallucinated_citation_still_hard_blocks_before_any_proposal_exists(db, monkeypatch):
    """The block is upstream of every door here: a hallucinated citation means
    no dic_suggestions row is ever created, so there is nothing to approve."""
    from tools.doc_modernization import redline_drafter as rd

    doc_id = f"doc-{uuid.uuid4().hex[:8]}"
    finding_id = f"fnd-{uuid.uuid4().hex[:12]}"
    conn = _conn()
    conn.execute(
        """INSERT INTO docmod_findings
           (finding_id, run_id, doc_id, version_id, pack_id, entity_label, entity_type,
            finding_type, currency_verdict, severity, rationale, evidence_json,
            recommended_replacement, confidence, state, dedupe_key, section_heading, created_at)
           VALUES (%s,'run-hitl',%s,'v1','crypto_protocols','TLS 1.1','protocol',
                   'deprecated_tech','deprecated','high','TLS 1.1 is deprecated.',%s,
                   'TLS 1.2 or higher',1.0,'open',%s,'Security','2026-08-18T00:00:00')""",
        (finding_id, doc_id,
         json.dumps([{"source": "rule:crypto-tls-02", "detail": "RFC 8996", "date": ""}]),
         f"dk-{uuid.uuid4().hex[:8]}"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(rd, "_invoke_llm",
                        lambda s, u: "Use TLS 1.2 or higher [source: rule:made-up-99].")
    result = rd.draft_redline(finding_id)

    assert result.status == "blocked"
    assert "hallucinated" in result.reason
    assert result.suggestion_id is None
    conn = _conn()
    n_sug = dict(conn.execute(
        "SELECT COUNT(*) AS n FROM dic_suggestions WHERE doc_id = %s", (doc_id,)
    ).fetchone())["n"]
    states = [dict(r)["state"] for r in conn.execute(
        "SELECT state FROM docmod_findings WHERE doc_id = %s", (doc_id,)).fetchall()]
    conn.close()
    assert n_sug == 0
    assert states == ["open"]


# -- 4. accept and reject are both audited -----------------------------------

@pytest.mark.parametrize("disposition", ["accepted", "rejected"])
def test_finding_disposition_is_audited(client, db, disposition):
    finding_id, suggestion_id, _doc, _sec = _seed_proposal()
    resp = client.post(f"{P}/api/modernization/findings/{finding_id}/resolve",
                       json={"disposition": disposition, "reviewer": "alice"})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    rows = _audit_rows("docmod_finding.")
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["event_type"] == "dic.hitl_decision"
    assert row["action"] == f"docmod_finding.{disposition}"
    assert row["actor"] == "alice"
    details = json.loads(row["details"])
    assert details["finding_id"] == finding_id
    assert details["redline_suggestion_id"] == suggestion_id


@pytest.mark.parametrize("verb,expected", [("accept", "accepted"), ("reject", "rejected")])
def test_proposal_apply_decision_is_audited(client, db, verb, expected):
    _finding, suggestion_id, _doc, section_id = _seed_proposal()
    resp = client.post(f"{P}/api/suggestions/{suggestion_id}/{verb}", json={})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    rows = _audit_rows("dic_suggestion.")
    assert len(rows) == 1, rows
    assert rows[0]["event_type"] == "dic.hitl_decision"
    assert rows[0]["action"] == f"dic_suggestion.{expected}"
    details = json.loads(rows[0]["details"])
    assert details["suggestion_id"] == suggestion_id
    assert details["section_id"] == section_id
    assert details["applied"] is (verb == "accept")


@pytest.mark.parametrize("verb", ["approve", "reject"])
def test_review_queue_version_decision_is_audited(client, db, verb):
    doc_id = f"doc-{uuid.uuid4().hex[:8]}"
    version_id = f"{doc_id}_v1"
    conn = _conn()
    conn.execute("INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, "
                 "created_at) VALUES (%s,%s,1,'ai_generated','pending_review',"
                 "'2026-08-18T00:00:00')", (version_id, doc_id))
    conn.commit()
    conn.close()

    resp = client.post(f"{P}/api/review/{version_id}/{verb}",
                       json={"type": "version", "reviewer": "alice"})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    rows = _audit_rows("dic_version.")
    assert len(rows) == 1, rows
    assert rows[0]["event_type"] == "dic.hitl_decision"
    assert rows[0]["action"] == f"dic_version.{'approved' if verb == 'approve' else 'rejected'}"
    assert rows[0]["actor"] == "alice"


def test_ssp_fragment_review_event_type_is_in_the_audit_vocabulary():
    """acoic._review_fragment has logged every human SSP-fragment decision under
    'dic.ssp_fragment.review' since it was written - an event type that is not
    in VALID_EVENT_TYPES, so log_event raised ValueError before touching the DB.
    The route's `except Exception` fallback then did an UNAUDITED UPDATE, which
    is exactly what the declared fail-closed audit was there to prevent."""
    from tools.audit.audit_logger import VALID_EVENT_TYPES

    assert "dic.ssp_fragment.review" in VALID_EVENT_TYPES
    assert "dic.hitl_decision" in VALID_EVENT_TYPES


def test_ssp_fragment_review_audit_actually_writes_a_row(db):
    from tools.audit.audit_logger import log_event

    entry_id = log_event(
        event_type="dic.ssp_fragment.review", actor="alice",
        action="ssp_fragment.approved", details={"fragment_id": "frag-1"},
        raise_on_error=True,
    )
    assert entry_id != -1
    assert _audit_rows("ssp_fragment.")


def test_every_non_decision_exemption_still_exists(client):
    """An exemption for a route that no longer exists is stale, and a stale
    exemption is how a list like this rots into a blanket."""
    live = {str(rule) for rule in client.application.url_map.iter_rules()}
    missing = sorted(_NON_DECISION_KEYWORD_ROUTES - live)
    assert not missing, f"exempted route(s) no longer exist: {missing}"


def test_no_exemption_overlaps_the_decision_surface():
    """The two sets must stay disjoint. A route in both would be exempted from
    the guard that is supposed to freeze it."""
    assert not (_NON_DECISION_KEYWORD_ROUTES & _DECISION_ROUTES)
