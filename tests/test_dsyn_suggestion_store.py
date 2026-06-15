# CUI // SP-CTI
"""Tests for tools/document_intelligence/suggestion_store.py (dsyn-adapt-03)."""
from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── SQLite shim ───────────────────────────────────────────────────────────────

class _FakeConn:
    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._db.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self._db.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._db.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.commit()


def _make_db(tmp_path: Path) -> str:
    path = str(tmp_path / "sug_test.db")
    # Let suggestion_store create the tables via _ensure_tables
    db = sqlite3.connect(path)
    db.commit()
    db.close()
    return path


@pytest.fixture()
def db(tmp_path):
    return _make_db(tmp_path)


@pytest.fixture(autouse=True)
def patch_conn(db):
    import tools.document_intelligence.suggestion_store as mod

    @contextmanager
    def _patched():
        raw = sqlite3.connect(db)
        raw.row_factory = sqlite3.Row
        c = _FakeConn(raw)
        try:
            yield c
        finally:
            c.commit()
            raw.close()

    with patch.object(mod, "get_connection", _patched):
        yield


# ── create_suggestion ─────────────────────────────────────────────────────────

def test_create_returns_suggestion_id():
    from tools.document_intelligence.suggestion_store import create_suggestion
    sid = create_suggestion(suggested_content="Update section 3 with new policy text.")
    assert sid.startswith("sug_")


def test_created_suggestion_appears_in_pending():
    from tools.document_intelligence.suggestion_store import create_suggestion, get_pending_suggestions
    sid = create_suggestion(suggested_content="Revise the runbook introduction.")
    pending = get_pending_suggestions()
    ids = [s["suggestion_id"] for s in pending]
    assert sid in ids


def test_create_sets_status_pending():
    from tools.document_intelligence.suggestion_store import create_suggestion, get_suggestion
    sid = create_suggestion(suggested_content="Add a new compliance note.")
    s = get_suggestion(sid)
    assert s["status"] == "pending"


def test_create_stores_canvas_source():
    from tools.document_intelligence.suggestion_store import create_suggestion, get_suggestion
    sid = create_suggestion(suggested_content="NDC topology changed.", canvas_source="ndc")
    s = get_suggestion(sid)
    assert s["canvas_source"] == "ndc"


def test_create_stores_collection_id():
    from tools.document_intelligence.suggestion_store import create_suggestion, get_suggestion
    sid = create_suggestion(
        suggested_content="Policy update.",
        collection_id="col-policy-01",
    )
    s = get_suggestion(sid)
    assert s["collection_id"] == "col-policy-01"


def test_create_stores_trigger_event_id():
    from tools.document_intelligence.suggestion_store import create_suggestion, get_suggestion
    sid = create_suggestion(
        suggested_content="Runbook revision.",
        trigger_event_id="ev-abc123",
    )
    s = get_suggestion(sid)
    assert s["trigger_event_id"] == "ev-abc123"


# ── get_pending_suggestions filters ───────────────────────────────────────────

def test_filter_by_collection_id():
    from tools.document_intelligence.suggestion_store import create_suggestion, get_pending_suggestions
    sid_a = create_suggestion(suggested_content="A", collection_id="col-a")
    sid_b = create_suggestion(suggested_content="B", collection_id="col-b")
    results = get_pending_suggestions(collection_id="col-a")
    ids = [s["suggestion_id"] for s in results]
    assert sid_a in ids
    assert sid_b not in ids


def test_filter_by_canvas_source():
    from tools.document_intelligence.suggestion_store import create_suggestion, get_pending_suggestions
    sid_ndc = create_suggestion(suggested_content="NDC", canvas_source="ndc")
    sid_zig = create_suggestion(suggested_content="ZIG", canvas_source="zig")
    results = get_pending_suggestions(canvas_source="ndc")
    ids = [s["suggestion_id"] for s in results]
    assert sid_ndc in ids
    assert sid_zig not in ids


def test_get_pending_excludes_accepted():
    from tools.document_intelligence.suggestion_store import (
        create_suggestion, decide_suggestion, get_pending_suggestions
    )
    sid = create_suggestion(suggested_content="Accept me.")
    decide_suggestion(sid, "accepted", "reviewer_alice")
    pending = get_pending_suggestions()
    ids = [s["suggestion_id"] for s in pending]
    assert sid not in ids


def test_filter_by_status_accepted():
    from tools.document_intelligence.suggestion_store import (
        create_suggestion, decide_suggestion, get_pending_suggestions
    )
    sid = create_suggestion(suggested_content="To accept.")
    decide_suggestion(sid, "accepted", "reviewer_bob")
    accepted = get_pending_suggestions(status="accepted")
    ids = [s["suggestion_id"] for s in accepted]
    assert sid in ids


# ── get_suggestion ────────────────────────────────────────────────────────────

def test_get_suggestion_returns_none_for_missing():
    from tools.document_intelligence.suggestion_store import get_suggestion
    assert get_suggestion("sug_doesnotexist") is None


def test_get_suggestion_returns_full_row():
    from tools.document_intelligence.suggestion_store import create_suggestion, get_suggestion
    sid = create_suggestion(
        suggested_content="Full row test.",
        section_id="sec-01",
        doc_id="doc-01",
        rationale="Test rationale.",
    )
    s = get_suggestion(sid)
    assert s["section_id"] == "sec-01"
    assert s["doc_id"] == "doc-01"
    assert s["rationale"] == "Test rationale."
    assert s["suggested_content"] == "Full row test."


# ── decide_suggestion ─────────────────────────────────────────────────────────

def test_accept_changes_status():
    from tools.document_intelligence.suggestion_store import (
        create_suggestion, decide_suggestion, get_suggestion
    )
    sid = create_suggestion(suggested_content="Accept test.")
    result = decide_suggestion(sid, "accepted", "alice")
    assert result is True
    assert get_suggestion(sid)["status"] == "accepted"


def test_reject_changes_status():
    from tools.document_intelligence.suggestion_store import (
        create_suggestion, decide_suggestion, get_suggestion
    )
    sid = create_suggestion(suggested_content="Reject test.")
    decide_suggestion(sid, "rejected", "bob")
    assert get_suggestion(sid)["status"] == "rejected"


def test_decide_inserts_decision_row():
    from tools.document_intelligence.suggestion_store import (
        create_suggestion, decide_suggestion, get_decisions_for_suggestion
    )
    sid = create_suggestion(suggested_content="Decision row test.")
    decide_suggestion(sid, "accepted", "carol", note="Looks good.")
    decisions = get_decisions_for_suggestion(sid)
    assert len(decisions) == 1
    d = decisions[0]
    assert d["decision"] == "accepted"
    assert d["decided_by"] == "carol"
    assert d["note"] == "Looks good."


def test_decide_already_accepted_returns_false():
    from tools.document_intelligence.suggestion_store import create_suggestion, decide_suggestion
    sid = create_suggestion(suggested_content="Already decided.")
    decide_suggestion(sid, "accepted", "dave")
    result = decide_suggestion(sid, "rejected", "eve")
    assert result is False


def test_decide_missing_suggestion_returns_false():
    from tools.document_intelligence.suggestion_store import decide_suggestion
    result = decide_suggestion("sug_ghost", "accepted", "nobody")
    assert result is False


def test_decide_invalid_decision_raises():
    from tools.document_intelligence.suggestion_store import create_suggestion, decide_suggestion
    sid = create_suggestion(suggested_content="Invalid decision.")
    with pytest.raises(ValueError):
        decide_suggestion(sid, "maybe", "frank")


# ── Append-only guarantee (decisions cannot be updated/deleted via decide) ────

def test_decision_row_is_immutable_on_second_decide():
    """Second decide on a decided suggestion returns False — no new decision row."""
    from tools.document_intelligence.suggestion_store import (
        create_suggestion, decide_suggestion, get_decisions_for_suggestion
    )
    sid = create_suggestion(suggested_content="Immutable test.")
    decide_suggestion(sid, "accepted", "grace")
    decide_suggestion(sid, "rejected", "heidi")  # blocked — already decided
    decisions = get_decisions_for_suggestion(sid)
    # Only one decision row should exist
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "accepted"
