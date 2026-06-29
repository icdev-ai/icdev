"""Tests for DIC conversational, KG-grounded session memory (dic-adapt-08).

DIC chat is stateless per query, so a follow-up like "and its retention
period?" loses the document subject established by the previous turn.
``tools.document_intelligence.chat_memory`` adds grounded, citable session
memory that reconstructs such follow-ups *without* re-stating context.

These tests pin the load-bearing guarantees:

* a two-turn conversation — a follow-up resolves the prior turn's grounded
  document subject and the answer stays cited (the acceptance scenario);
* the remembered subject is grounded (from citations) and never fabricated —
  a turn with no grounded evidence records nothing;
* memory is RLS-scoped: one tenant never recalls another tenant's turns;
* turning memory off restores fully stateless behaviour.
"""
from __future__ import annotations

import importlib

import pytest

cm = importlib.import_module("tools.document_intelligence.chat_memory")
se = importlib.import_module("tools.document_intelligence.search_engine")
bp = importlib.import_module("tools.document_intelligence.blueprint")

_SUBJECT = "Acme Master Services Agreement"


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

def _result(i, title=_SUBJECT, content="Records are retained for 7 years."):
    return se.DICSearchResult(
        chunk_id=f"chunk-{i}",
        doc_id=f"doc-{i}",
        doc_title=title,
        content=content,
        score=0.9 - i * 0.1,
        citation=se.Citation(
            doc_id=f"doc-{i}", doc_title=title, chunk_id=f"chunk-{i}", page=i,
        ),
    )


_TEST_SESSION_PREFIXES = ("sess-", "conv-", "shared-", "never-seen")


@pytest.fixture(autouse=True)
def _memory_on(monkeypatch):
    # Default-on for module tests regardless of ambient env.
    monkeypatch.setenv("ICDEV_DIC_CHAT_MEMORY", "1")
    # Ensure the (brand-new) table exists in the test DB and start each test
    # from a clean slate so prior runs don't leak turns across sessions.
    from tools.document_intelligence.db import init_db as dic_init

    dic_init._INIT_DONE = False
    dic_init.init_db()
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        clauses = " OR ".join(["session_id LIKE ?"] * len(_TEST_SESSION_PREFIXES))
        conn.execute(
            f"DELETE FROM dic_chat_memory WHERE {clauses}",
            tuple(f"{p}%" for p in _TEST_SESSION_PREFIXES),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
    yield


# --------------------------------------------------------------------------- #
# Follow-up detection
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("q", [
    "and its retention period?",
    "what about its owner?",
    "How about that document?",
    "Does it expire?",
    "their classification levels",
])
def test_is_followup_true(q):
    assert cm.is_followup(q) is True


@pytest.mark.parametrize("q", [
    "What is the data retention policy in the Acme agreement?",
    "list all contracts signed in 2024",
    "Who owns the budget document?",
])
def test_is_followup_false(q):
    assert cm.is_followup(q) is False


# --------------------------------------------------------------------------- #
# Grounded subject extraction
# --------------------------------------------------------------------------- #

def test_extract_subject_is_grounded():
    subject, doc_id, doc_ids, entities = cm.extract_subject([_result(1), _result(2)])
    assert subject == _SUBJECT
    assert doc_id == "doc-1"
    assert "doc-1" in doc_ids and "doc-2" in doc_ids
    assert _SUBJECT in entities


def test_extract_subject_empty_when_no_evidence():
    assert cm.extract_subject([]) == ("", "", [], [])


# --------------------------------------------------------------------------- #
# Persistence + RLS scoping
# --------------------------------------------------------------------------- #

def test_record_and_recall_roundtrip():
    tid = cm.record_turn(
        "sess-1", "retention policy?", "7 years [doc]", [_result(1)],
        tenant_id="default", classification="CUI",
    )
    assert tid
    turn = cm.recall_last_turn("sess-1", tenant_id="default")
    assert turn is not None
    assert turn.subject == _SUBJECT
    assert turn.citations and turn.citations[0]["doc_id"] == "doc-1"


def test_record_no_subject_is_refused():
    # No grounded evidence → nothing to anchor → must NOT fabricate a turn.
    assert cm.record_turn("sess-x", "q", "a", []) is None
    assert cm.recall_last_turn("sess-x", tenant_id="default") is None


def test_memory_is_tenant_scoped():
    cm.record_turn("shared-sess", "q", "a", [_result(1)], tenant_id="tenant-a")
    # Same session id, different tenant must not see tenant-a's turn.
    assert cm.recall_last_turn("shared-sess", tenant_id="tenant-b") is None
    assert cm.recall_last_turn("shared-sess", tenant_id="tenant-a") is not None


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #

def test_resolve_followup_prepends_grounded_subject():
    cm.record_turn("sess-2", "retention policy?", "ans", [_result(1)], tenant_id="default")
    res = cm.resolve_followup("sess-2", "and its retention period?", tenant_id="default")
    assert res.is_followup is True
    assert res.subject == _SUBJECT
    assert _SUBJECT in res.resolved_query
    assert res.resolved_query != res.original_query


def test_resolve_non_followup_is_noop():
    cm.record_turn("sess-3", "retention policy?", "ans", [_result(1)], tenant_id="default")
    res = cm.resolve_followup("sess-3", "what is the budget for 2025?", tenant_id="default")
    assert res.is_followup is False
    assert res.resolved_query == res.original_query


def test_resolve_without_prior_turn_is_noop():
    res = cm.resolve_followup("never-seen", "and its retention period?", tenant_id="default")
    assert res.is_followup is False
    assert res.resolved_query == "and its retention period?"


# --------------------------------------------------------------------------- #
# Toggle
# --------------------------------------------------------------------------- #

def test_memory_enabled_env_killswitch(monkeypatch):
    monkeypatch.setenv("ICDEV_DIC_CHAT_MEMORY", "0")
    assert cm.memory_enabled({}) is False
    assert cm.memory_enabled({"memory": True}) is False  # env wins


def test_memory_enabled_per_request_flag(monkeypatch):
    monkeypatch.setenv("ICDEV_DIC_CHAT_MEMORY", "1")
    assert cm.memory_enabled({"memory": False}) is False
    assert cm.memory_enabled({"memory": True}) is True
    assert cm.memory_enabled({}) is True


# --------------------------------------------------------------------------- #
# End-to-end via the chat API: the acceptance scenario
# --------------------------------------------------------------------------- #

class _FakeEngine:
    """Stand-in DICSearchEngine that records the query it was asked to search."""

    calls: list[str] = []

    def __init__(self, *a, **k):
        pass

    def search(self, query, collection_id=None, top_k=10, **k):
        _FakeEngine.calls.append(query)
        return [_result(1), _result(2)]


@pytest.fixture
def client(monkeypatch):
    _FakeEngine.calls = []
    monkeypatch.setattr(se, "DICSearchEngine", _FakeEngine)
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(bp.dic_bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_two_turn_followup_resolves_and_stays_cited(client):
    sid = "conv-acceptance"

    # Turn 1: establish the document subject.
    r1 = client.post("/document-intelligence/api/chat", json={
        "message": "What is the data retention policy in the Acme Master Services Agreement?",
        "session_id": sid,
    })
    assert r1.status_code == 200
    d1 = r1.get_json()
    assert d1["citations"], "first turn must be grounded with citations"
    assert d1["memory"] is True

    # Turn 2: a bare follow-up — no restated context.
    r2 = client.post("/document-intelligence/api/chat", json={
        "message": "and its retention period?",
        "session_id": sid,
    })
    assert r2.status_code == 200
    d2 = r2.get_json()

    # The follow-up resolved the prior turn's grounded subject...
    assert d2["resolved_subject"] == _SUBJECT
    # ...the actual retrieval query was reconstructed with that subject...
    assert _SUBJECT in _FakeEngine.calls[-1]
    assert _FakeEngine.calls[-1] != "and its retention period?"
    # ...and the answer remained grounded with citations.
    assert d2["citations"]


def test_memory_off_restores_stateless(client):
    sid = "conv-stateless"
    client.post("/document-intelligence/api/chat", json={
        "message": "What is the data retention policy in the Acme Master Services Agreement?",
        "session_id": sid,
        "memory": False,
    })
    r2 = client.post("/document-intelligence/api/chat", json={
        "message": "and its retention period?",
        "session_id": sid,
        "memory": False,
    })
    d2 = r2.get_json()
    # No reconstruction: the raw follow-up was searched verbatim.
    assert d2["resolved_subject"] == ""
    assert d2["memory"] is False
    assert _FakeEngine.calls[-1] == "and its retention period?"
