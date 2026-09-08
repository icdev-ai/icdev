# CUI // SP-CTI
"""dwr-anchor-05 — accepting a suggestion stops reporting success over zero rows.

THE DEFECT. Measured on the live PG board 2026-09-07: 58 of 58 dic_suggestions
carried an empty section_id and ``api_suggestion_accept`` ran
``UPDATE dic_sections ... WHERE section_id = ''`` -- ZERO rows -- committed,
wrote the decision row AND the HITL audit row, and returned
``{"status": "accepted"}``. The RED test below is exactly that shape: a
suggestion whose section matches no row. On the pre-change tree it returns 200.

What this file proves, through the real route on the per-module SQLite the
docmod conftest points ``tools.db.storage`` at:

  1. a suggestion whose section does not exist is REFUSED (409), and NOTHING is
     written -- no decision row, no audit row, status still pending;
  2. an ``unanchored`` suggestion over a real section is refused the same way;
  3. a verified anchor is SPLICED -- the text around the span survives -- and
     the AI draft that shipped is recorded as ``applied_text``;
  4. an anchor that no longer resolves against the live section is refused, the
     suggestion is SUPERSEDED on the append-only decision chain, the document is
     untouched, and no HUMAN decision is recorded;
  5. edit-then-accept writes the human's text and records it BESIDE the draft;
  6. the cef-ui-03 ordering (decision -> audit -> apply) and both fail-closed
     legs are preserved exactly.
"""
from __future__ import annotations

import json
import uuid

import flask
import pytest

from tools.document_intelligence import suggestion_store as store

P = "/document-intelligence"
SECTION = "Hosts on the enclave shall use TLS 1.1 for transport. Rotate keys yearly."
OLD = "TLS 1.1"
NEW = "TLS 1.2 or higher [source: rule:crypto-tls-02]"

_DDL = [
    """CREATE TABLE IF NOT EXISTS dic_documents (
        doc_id TEXT PRIMARY KEY, collection_id TEXT, title TEXT,
        tenant_id TEXT, classification TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_sections (
        section_id TEXT PRIMARY KEY, version_id TEXT, doc_id TEXT,
        heading TEXT, content TEXT, citations_json TEXT, status TEXT,
        origin TEXT, assigned_to TEXT, reviewed_by TEXT, reviewed_at TEXT,
        created_at TEXT, created_by TEXT, tenant_id TEXT, classification TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_team_access (
        collection_id TEXT, user_id TEXT, role TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_edit_history (
        edit_id TEXT PRIMARY KEY, section_id TEXT NOT NULL, doc_id TEXT,
        version_id TEXT, editor TEXT NOT NULL, content_before TEXT,
        content_after TEXT, char_delta INTEGER, diff_summary TEXT,
        edited_at TEXT NOT NULL, tenant_id TEXT, classification TEXT DEFAULT 'CUI')""",
    # Kept in step with tests/conftest.py's audit_trail (the LIVE PG shape).
    """CREATE TABLE IF NOT EXISTS audit_trail (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,
        event_type TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
        details TEXT, affected_files TEXT, classification TEXT DEFAULT 'CUI',
        ip_address TEXT, session_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        hash TEXT, previous_hash TEXT, signature TEXT)""",
]


# ── fixtures ──────────────────────────────────────────────────────────────────

def _conn():
    from tools.db.storage import get_connection
    return get_connection()


@pytest.fixture()
def db():
    conn = _conn()
    for ddl in _DDL:
        conn.execute(ddl)
    store._ensure_tables(conn)
    for table in ("dic_suggestions", "dic_suggestion_decisions", "audit_trail",
                  "dic_sections", "dic_edit_history"):
        conn.execute(f"DELETE FROM {table}")  # nosec B608 - fixture reset, constant names
    conn.commit()
    conn.close()
    yield


@pytest.fixture()
def client():
    import tools.document_intelligence.blueprint as bp_mod

    app = flask.Flask(__name__)
    app.register_blueprint(bp_mod.dic_bp, url_prefix=P)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _seed_section(content: str = SECTION) -> str:
    section_id = f"sec-{uuid.uuid4().hex[:10]}"
    conn = _conn()
    conn.execute(
        "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, "
        "status, origin, created_at) VALUES (%s,'v1','doc-1','Security',%s,'draft',"
        "'human_authored','2026-09-07T00:00:00')",
        (section_id, content),
    )
    conn.commit()
    conn.close()
    return section_id


def _set_section(section_id: str, content: str) -> None:
    conn = _conn()
    conn.execute("UPDATE dic_sections SET content = %s WHERE section_id = %s",
                 (content, section_id))
    conn.commit()
    conn.close()


def _section_content(section_id: str):
    conn = _conn()
    row = conn.execute("SELECT content, origin FROM dic_sections WHERE section_id = %s",
                       (section_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _anchored(section_id: str, content: str = SECTION, old: str = OLD, new: str = NEW,
              basis: str = "exact", **extra) -> str:
    start = content.index(old)
    return store.create_suggestion(
        section_id=section_id, doc_id="doc-1", collection_id="col-1",
        canvas_source="doc_modernization", suggested_content=new,
        current_content=content, rationale="[docmod] TLS 1.1 is deprecated.",
        origin_kind="docmod_redline", anchor_section_id=section_id,
        anchor_start=start, anchor_end=start + len(old), anchor_text=old,
        anchor_basis=basis, **extra,
    )


def _audit_rows() -> list[dict]:
    conn = _conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT event_type, actor, action, details FROM audit_trail ORDER BY id").fetchall()]
    conn.close()
    return [r for r in rows if (r.get("action") or "").startswith("dic_suggestion.")]


def _decisions(sid: str) -> list[dict]:
    return store.get_decisions_for_suggestion(sid)


def _nothing_was_written(sid: str, section_id: str | None, before) -> None:
    assert store.get_suggestion(sid)["status"] == "pending"
    assert _decisions(sid) == []
    assert _audit_rows() == []
    if section_id is not None:
        assert _section_content(section_id)["content"] == before


# ── 1. THE RED TEST: no section, no success ───────────────────────────────────

def test_accepting_a_suggestion_whose_section_matches_no_row_is_not_a_success(client, db):
    """The live board's shape: 58 of 58 rows with an EMPTY section_id. On the
    pre-change tree this returned 200 {"status": "accepted"} over zero rows."""
    sid = store.create_suggestion(
        section_id="", doc_id="doc-1", collection_id="col-1",
        canvas_source="doc_modernization", suggested_content=NEW,
        current_content="", origin_kind="docmod_redline", anchor_basis="unanchored",
        anchor_text=OLD,
    )
    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={})
    body = resp.get_json()
    assert resp.status_code != 200, body
    assert body.get("status") != "accepted"
    assert resp.status_code == 409
    assert body["error"] == "section_not_found"
    assert body["applied"] is False
    assert body["decision_recorded"] is False
    _nothing_was_written(sid, None, None)


def test_a_named_but_missing_section_is_refused_too(client, db):
    section_id = _seed_section()
    sid = _anchored(section_id)
    conn = _conn()
    conn.execute("DELETE FROM dic_sections WHERE section_id = %s", (section_id,))
    conn.commit()
    conn.close()
    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "section_not_found"
    _nothing_was_written(sid, None, None)


# ── 2. unanchored is refused, never guessed at ────────────────────────────────

def test_an_unanchored_suggestion_over_a_real_section_is_refused(client, db):
    """The redline_drafter shape today (dwr-anchor-03): origin recorded, basis
    honestly unanchored, the sought text known. A real section exists and holds
    that text ONCE — the route still refuses rather than relocating it itself."""
    section_id = _seed_section()
    sid = store.create_suggestion(
        section_id=section_id, doc_id="doc-1", collection_id="col-1",
        canvas_source="doc_modernization", suggested_content=NEW,
        current_content=SECTION, origin_kind="docmod_redline",
        anchor_basis="unanchored", anchor_text=OLD,
    )
    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={})
    body = resp.get_json()
    assert resp.status_code == 409
    assert body["error"] == "unanchored"
    assert body["anchor"]["basis"] == "unanchored"
    assert body["applied"] is False
    _nothing_was_written(sid, section_id, SECTION)


# ── 3. a verified anchor is SPLICED ───────────────────────────────────────────

def test_accept_splices_the_span_and_keeps_the_rest_of_the_section(client, db):
    section_id = _seed_section()
    sid = _anchored(section_id)

    resp = client.post(f"{P}/api/suggestions/{sid}/accept",
                       json={"note": "verified against RFC 8996", "reviewer": "alice"})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["status"] == "accepted" and body["applied"] is True
    assert body["section_id"] == section_id
    assert body["applied_text_source"] == "ai_draft"
    assert body["anchor"] == {"basis": "exact", "start": SECTION.index(OLD),
                              "end": SECTION.index(OLD) + len(OLD)}

    expected = SECTION.replace(OLD, NEW)
    after = _section_content(section_id)
    assert after["content"] == expected, "the span was not spliced in place"
    assert after["content"] != NEW, "the whole section was overwritten with the passage"
    assert after["origin"] == "ai_generated"

    row = store.get_suggestion(sid)
    assert row["status"] == "accepted"
    assert row["applied_text"] == NEW and row["applied_by"] == "alice"
    assert row["suggested_content"] == NEW
    assert [d["decision"] for d in _decisions(sid)] == ["accepted"]

    rows = _audit_rows()
    assert len(rows) == 1 and rows[0]["action"] == "dic_suggestion.accepted"
    details = json.loads(rows[0]["details"])
    assert details["applied"] is True
    assert details["anchor"] == body["anchor"]
    assert details["applied_text_source"] == "ai_draft"
    assert body["application_recorded"] is True

    conn = _conn()
    hist = [dict(r) for r in conn.execute(
        "SELECT content_before, content_after FROM dic_edit_history WHERE section_id = %s",
        (section_id,)).fetchall()]
    conn.close()
    assert hist == [{"content_before": SECTION, "content_after": expected}]


def test_a_whole_section_anchor_replaces_the_whole_section(client, db):
    """The crowdsource / section_draft shape (dwr-anchor-03): exact over 0:len."""
    section_id = _seed_section()
    sid = store.create_suggestion(
        section_id=section_id, doc_id="doc-1", collection_id="col-1",
        canvas_source="crowdsource", suggested_content="Rewritten in full.",
        current_content=SECTION, origin_kind="crowdsource",
        **store.whole_section_anchor(section_id, SECTION),
    )
    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={})
    assert resp.status_code == 200, resp.get_json()
    assert _section_content(section_id)["content"] == "Rewritten in full."


def test_a_relocated_anchor_is_verified_by_its_offsets(client, db):
    section_id = _seed_section()
    sid = _anchored(section_id, basis="relocated")
    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["anchor"]["basis"] == "relocated"
    assert _section_content(section_id)["content"] == SECTION.replace(OLD, NEW)


# ── 4. drift: superseded, document untouched, no human decision ───────────────

def test_a_stale_anchor_is_refused_and_the_suggestion_is_superseded(client, db):
    section_id = _seed_section()
    sid = _anchored(section_id)
    # The document moved on under the proposal: the span now holds other text.
    drifted = SECTION.replace(OLD, "TLS 1.0")
    _set_section(section_id, drifted)

    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={})
    body = resp.get_json()
    assert resp.status_code == 409
    assert body["error"] == "anchor_stale"
    assert body["applied"] is False and body["decision_recorded"] is False
    assert body["suggestion_status"] == "superseded"
    assert body["anchor"]["expected_text"] == OLD
    assert body["anchor"]["found_text"] == "TLS 1.0"

    assert _section_content(section_id)["content"] == drifted
    assert store.get_suggestion(sid)["status"] == "superseded"
    decisions = _decisions(sid)
    assert [d["decision"] for d in decisions] == ["superseded"]
    assert decisions[0]["decided_by"].startswith("system:")
    assert "anchor_stale" in decisions[0]["note"]
    assert _audit_rows() == [], "a drift refusal is not a human decision"


def test_a_superseded_suggestion_cannot_be_accepted_afterwards(client, db):
    section_id = _seed_section()
    sid = _anchored(section_id)
    _set_section(section_id, SECTION.replace(OLD, "TLS 1.0"))
    assert client.post(f"{P}/api/suggestions/{sid}/accept", json={}).status_code == 409
    # Even after the section is restored, the row is decided: no second chance
    # without a fresh proposal over the current text.
    _set_section(section_id, SECTION)
    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={})
    assert resp.status_code == 409
    body = resp.get_json()
    # dwr-collab-01 renamed this refusal to the machine-readable
    # ``already_decided`` -- ONE key for both doors and for both ways of
    # reaching it -- and made it carry the state that STANDS. The behaviour
    # pinned here is unchanged (409, nothing written); the payload is stricter.
    assert body["error"] == "already_decided"
    assert body["decision_recorded"] is False and body["applied"] is False
    assert body["attempted"] == "accept"
    assert body["current"]["status"] == "superseded"
    # No human decided a supersede, and none is invented for it.
    assert body["current"]["decided_by"] in (None, "system:anchor_verify")
    assert _section_content(section_id)["content"] == SECTION


def test_a_shortened_section_puts_the_offsets_out_of_range_and_refuses(client, db):
    section_id = _seed_section()
    sid = _anchored(section_id)
    _set_section(section_id, "short")
    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "anchor_stale"
    assert _section_content(section_id)["content"] == "short"


# ── 5. edit-then-accept ───────────────────────────────────────────────────────

def test_edit_then_accept_writes_the_human_text_and_records_it_beside_the_draft(client, db):
    section_id = _seed_section()
    sid = _anchored(section_id)
    human = "TLS 1.3"
    resp = client.post(f"{P}/api/suggestions/{sid}/accept",
                       json={"applied_text": human, "applied_by": "bob", "reviewer": "alice"})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["applied_text_source"] == "human_edit" and body["applied_by"] == "bob"

    after = _section_content(section_id)
    assert after["content"] == SECTION.replace(OLD, human)
    assert after["origin"] == "ai_assisted"

    row = store.get_suggestion(sid)
    assert row["applied_text"] == human and row["applied_by"] == "bob"
    assert row["suggested_content"] == NEW, "the AI draft was overwritten by the human text"
    details = json.loads(_audit_rows()[0]["details"])
    assert details["applied_text_source"] == "human_edit"
    assert details["applied_by"] == "bob"
    assert [d["decided_by"] for d in _decisions(sid)] == ["alice"]


def test_edit_then_accept_without_applied_by_names_the_reviewer(client, db):
    section_id = _seed_section()
    sid = _anchored(section_id)
    resp = client.post(f"{P}/api/suggestions/{sid}/accept",
                       json={"applied_text": "TLS 1.3", "reviewer": "alice"})
    assert resp.status_code == 200, resp.get_json()
    assert store.get_suggestion(sid)["applied_by"] == "alice"


def test_a_non_string_applied_text_is_a_usage_error_and_writes_nothing(client, db):
    section_id = _seed_section()
    sid = _anchored(section_id)
    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={"applied_text": 7})
    assert resp.status_code == 400
    _nothing_was_written(sid, section_id, SECTION)


# ── 6. the cef-ui-03 ordering is PRESERVED EXACTLY ────────────────────────────

def test_decision_failure_still_lands_nothing(client, db, monkeypatch):
    section_id = _seed_section()
    sid = _anchored(section_id)

    def _boom(*a, **k):
        raise RuntimeError("decision log unavailable")
    # dwr-collab-01: the door calls ``decide_outcome``. Patching the old name
    # intercepted nothing and this guard passed without ever firing.
    monkeypatch.setattr(store, "decide_outcome", _boom)

    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={})
    body = resp.get_json()
    assert resp.status_code == 500
    assert body["error"] == "decision_not_recorded"
    assert body["decision_recorded"] is False and body["applied"] is False
    assert _section_content(section_id)["content"] == SECTION
    assert _audit_rows() == []


def test_audit_failure_still_lands_nothing(client, db, monkeypatch):
    import tools.document_intelligence.blueprint as bp_mod
    section_id = _seed_section()
    sid = _anchored(section_id)

    def _boom(*a, **k):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(bp_mod, "_record_hitl_decision", _boom)

    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={})
    body = resp.get_json()
    assert resp.status_code == 500
    assert body["error"] == "decision_not_audited"
    assert body["decision_recorded"] is True and body["applied"] is False
    assert _section_content(section_id)["content"] == SECTION
    # the decision DID stand — recorded, visible, and honestly not applied
    assert store.get_suggestion(sid)["status"] == "accepted"
    assert store.get_suggestion(sid)["applied_text"] is None


def test_the_anchor_is_checked_before_the_decision_is_written(client, db, monkeypatch):
    """A refusal must never cost a decision row: with the decision writer
    broken, a stale suggestion is still refused as stale, not as
    decision_not_recorded, because the anchor check comes FIRST."""
    section_id = _seed_section()
    sid = _anchored(section_id)
    _set_section(section_id, SECTION.replace(OLD, "TLS 1.0"))

    def _boom(*a, **k):
        raise AssertionError("decide_suggestion was reached before the anchor check")
    monkeypatch.setattr(store, "decide_suggestion", _boom)

    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "anchor_stale"


# ── the store's own pieces ────────────────────────────────────────────────────

class TestVerifyAnchor:
    def _row(self, **kw):
        base = {"anchor_basis": "exact", "anchor_start": SECTION.index(OLD),
                "anchor_end": SECTION.index(OLD) + len(OLD), "anchor_text": OLD}
        base.update(kw)
        return base

    def test_holds(self):
        v = store.verify_anchor(self._row(), SECTION)
        assert v["ok"] is True and v["found_text"] == OLD

    def test_missing_section_is_section_missing_not_stale(self):
        assert store.verify_anchor(self._row(), None)["reason"] == "section_missing"

    def test_empty_section_is_not_missing(self):
        v = store.verify_anchor(self._row(anchor_start=0, anchor_end=0, anchor_text=""), "")
        assert v["ok"] is True

    def test_unanchored_never_verifies_even_when_the_text_is_present_once(self):
        v = store.verify_anchor(self._row(anchor_basis="unanchored", anchor_start=None,
                                          anchor_end=None), SECTION)
        assert v["ok"] is False and v["reason"] == "unanchored"

    def test_drift_reports_what_the_slice_holds_now(self):
        v = store.verify_anchor(self._row(), SECTION.replace(OLD, "TLS 1.0"))
        assert v["ok"] is False and v["reason"] == "anchor_stale"
        assert v["found_text"] == "TLS 1.0"

    def test_bool_offsets_are_not_offsets(self):
        v = store.verify_anchor(self._row(anchor_start=True, anchor_end=8), SECTION)
        assert v["ok"] is False and v["reason"] == "no_offsets"


class TestSupersede:
    def test_needs_a_reason(self, db):
        with pytest.raises(ValueError):
            store.supersede_suggestion("sug_x", "")

    def test_only_a_pending_row_is_superseded(self, db):
        section_id = _seed_section()
        sid = _anchored(section_id)
        assert store.decide_suggestion(sid, "rejected", "alice") is True
        assert store.supersede_suggestion(sid, "anchor_stale") is False
        assert store.get_suggestion(sid)["status"] == "rejected"
        assert [d["decision"] for d in _decisions(sid)] == ["rejected"]

    def test_decide_suggestion_refuses_the_superseded_verdict(self, db):
        """Superseding is a mechanism's verdict, never a human's: the human
        decision writer does not spell it."""
        with pytest.raises(ValueError):
            store.decide_suggestion("sug_x", store.SUPERSEDED_DECISION, "alice")
