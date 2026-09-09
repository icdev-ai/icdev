# CUI // SP-CTI
"""Two reviewers in one document, without a lost write (dwr-collab-01).

THE DEFECT, in two halves, and only the first is a UI problem.

1. NOBODY'S PAGE HEARD ANYTHING. dwr-ws-03 applies a decision in place for the
   reviewer who clicked and for nobody else. A second reviewer's rail keeps
   offering Accept on a change somebody accepted ten minutes ago, beside a
   document showing the text that change replaced. There was no document-scoped
   feed at all: ``/api/events/poll`` is bound to ``hook_events``, a
   platform-wide tool-call feed with no notion of a document, and the presence
   substrate this canvas already carries was never on the workspace page.

2. AND THE SECOND DECISION WAS NOT REFUSED, IT WAS WRITTEN.
   ``decide_suggestion`` SELECTed the status, compared it to ``pending`` in
   Python, and then ran an UNCONDITIONAL ``UPDATE ... WHERE suggestion_id=%s``.
   The row is not held between those statements, so two reviewers can both read
   ``pending``, both pass the guard and both write: the second UPDATE overwrites
   the first, TWO rows land on the append-only decision chain, and both
   reviewers are told 200. That is a lost human decision on the only writer of
   ``dic_sections.content`` for a proposal.

   A POLL CANNOT FIX THAT. Two reviewers can always click inside one poll
   interval, however short. The poll shortens the window; the CONDITIONAL UPDATE
   closes it, and the tests below drive the two statements in the interleaving
   that used to lose the write rather than asserting the SQL string.

WHAT ELSE IS PINNED HERE, each one a thing that would otherwise fail GREEN:

  the cursor never advances on an unmeasured read -- the failure mode where the
  next poll reports "no changes" for a window nobody ever looked at;
  ``baseline`` is not ``no_changes``, and ``no_changes`` is not ``unmeasured``;
  counts are ``None``, never ``0``, when nothing was measured;
  a truncated page leaves the cursor on the LAST DELIVERED ROW, never on now;
  ``alone`` is a measured presence verdict and an unreadable registry is not it;
  the poll route is a GET with no POST sibling, and the module writes nothing.
"""
from __future__ import annotations

import ast
import re
import threading
import uuid
from pathlib import Path

import flask
import pytest

from tools.document_intelligence import collab
from tools.document_intelligence import suggestion_store as store

P = "/document-intelligence"
REPO = Path(__file__).resolve().parents[2]
COLLAB = REPO / "tools/document_intelligence/collab.py"
TEMPLATE = REPO / "tools/dashboard/templates/document_intelligence/workspace.html"
BLUEPRINT = REPO / "tools/document_intelligence/blueprint.py"

_DDL = [
    """CREATE TABLE IF NOT EXISTS dic_sections (
        section_id TEXT PRIMARY KEY, version_id TEXT, doc_id TEXT,
        heading TEXT, content TEXT, status TEXT, origin TEXT,
        created_at TEXT, tenant_id TEXT, classification TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_edit_history (
        edit_id TEXT PRIMARY KEY, section_id TEXT NOT NULL, doc_id TEXT,
        version_id TEXT, editor TEXT NOT NULL, content_before TEXT,
        content_after TEXT, char_delta INTEGER, diff_summary TEXT,
        edited_at TEXT NOT NULL, tenant_id TEXT, classification TEXT DEFAULT 'CUI')""",
    """CREATE TABLE IF NOT EXISTS dic_presence_sessions (
        session_key TEXT PRIMARY KEY, doc_id TEXT NOT NULL, user_id TEXT NOT NULL,
        joined_at TEXT NOT NULL, last_seen TEXT NOT NULL, expires_at TEXT NOT NULL,
        active_section_id TEXT, tenant_id TEXT, classification TEXT DEFAULT 'CUI')""",
    """CREATE TABLE IF NOT EXISTS dic_team_access (
        collection_id TEXT, user_id TEXT, role TEXT)""",
    """CREATE TABLE IF NOT EXISTS audit_trail (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,
        event_type TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
        details TEXT, affected_files TEXT, classification TEXT DEFAULT 'CUI',
        ip_address TEXT, session_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        hash TEXT, previous_hash TEXT, signature TEXT)""",
]

DOC = "doc-collab"


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


@pytest.fixture()
def db():
    conn = _conn()
    for ddl in _DDL:
        conn.execute(ddl)
    store._ensure_tables(conn)
    for table in ("dic_suggestions", "dic_suggestion_decisions", "dic_sections",
                  "dic_edit_history", "dic_presence_sessions", "audit_trail"):
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


def _proposal(doc_id: str = DOC, section_id: str = "sec-1") -> str:
    return store.create_suggestion(
        section_id=section_id, doc_id=doc_id, collection_id="col-1",
        canvas_source="doc_modernization", suggested_content="TLS 1.2",
        current_content="TLS 1.1", rationale="deprecated",
    )


def _statuses(sid: str) -> str:
    conn = _conn()
    row = conn.execute("SELECT status FROM dic_suggestions WHERE suggestion_id = %s",
                       (sid,)).fetchone()
    conn.close()
    return dict(row)["status"] if row else None


# ══ 1. THE LOST WRITE ════════════════════════════════════════════════════════

def test_the_second_write_is_refused_when_the_read_was_already_stale(db, monkeypatch):
    """THE CARD'S CENTRAL CLAIM, at the ONE interleaving that lost the write.

    A competing decision commits BETWEEN this call's ``SELECT status`` and its
    ``UPDATE``. That is precisely what PostgreSQL's default READ COMMITTED
    permits -- the SELECT sees the state as of its own start and the row is not
    held afterwards -- so the Python-side ``!= "pending"`` guard passes over a
    read that is already false, and the old unconditional
    ``UPDATE ... WHERE suggestion_id=%s`` then overwrote a decision a human had
    already made, wrote a SECOND row onto the append-only chain, and returned
    success to both reviewers.

    THE INTERLEAVING IS INJECTED, NOT RACED FOR, and that is deliberate. The
    threading version of this test (below) passes on the pre-fix tree as
    readily as on the fixed one: SQLite serialises writers with a
    database-level lock, so the two calls never overlap in the window the
    defect lives in, and a test that passes both ways proves only that the
    function is deterministic. Verified by reverting the conditional UPDATE and
    running it five times -- 5 green. This one goes red on that same tree.
    """
    sid = _proposal()
    # PATCHED ON ``store``, NOT ON ``tools.db.storage``: suggestion_store binds
    # ``get_connection`` at import, so a patch on the storage module lands on an
    # object the code under test never looks at -- the interleaving then never
    # happens and this test passes having proved nothing. The ``fired`` assert
    # below is what makes that failure loud instead of silent.
    real_get_connection = store.get_connection
    fired = {"n": 0}

    class _Interleaved:
        """A connection that lets a competing reviewer commit after our SELECT."""

        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, params=()):
            result = self._inner.execute(sql, params)
            if "SELECT status FROM dic_suggestions" in sql and not fired["n"]:
                fired["n"] = 1
                # Bob commits, on his own connection, while our transaction sits
                # between its read and its write.
                store.decide_outcome(sid, "rejected", "bob")
            return result

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

    def _wrapped(*a, **k):
        conn = real_get_connection(*a, **k)
        return _Interleaved(conn) if not fired["n"] else conn

    monkeypatch.setattr(store, "get_connection", _wrapped)
    out = store.decide_outcome(sid, "accepted", "alice")
    monkeypatch.undo()

    assert fired["n"] == 1, "the interleaving never happened -- this test proved nothing"
    assert out["recorded"] is False, (
        "alice's accept overwrote bob's rejection -- a human decision was lost")
    assert out["outcome"] == store.DECIDE_ALREADY
    assert _statuses(sid) == "rejected", "the loser's status overwrote the winner's"
    rows = store.get_decisions_for_suggestion(sid)
    assert [r["decision"] for r in rows] == ["rejected"], (
        "the append-only chain carries a decision that did not stand")


def test_two_reviewers_deciding_at_once_produce_one_decision(db):
    """The same invariant under REAL concurrency, whatever the engine does.

    Honest about its own reach: on SQLite the engine's write lock already
    serialises these two calls, so this passes on the pre-fix tree too and
    cannot be cited as evidence the race is closed --
    ``test_the_second_write_is_refused_when_the_read_was_already_stale`` is what
    proves that. It earns its place by pinning the OUTCOME (one decision, one
    row, agreeing status) on whatever backend it is run against, including the
    PostgreSQL board where the interleaving is reachable for real.
    """
    sid = _proposal()
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def race(decision, who):
        barrier.wait(timeout=10)   # both threads inside the window, together
        results.append(store.decide_outcome(sid, decision, who))

    threads = [threading.Thread(target=race, args=("accepted", "alice")),
               threading.Thread(target=race, args=("rejected", "bob"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    recorded = [r for r in results if r["recorded"]]
    refused = [r for r in results if not r["recorded"]]
    assert len(recorded) == 1, "both decisions were written -- one human's was lost"
    assert len(refused) == 1
    assert refused[0]["outcome"] == store.DECIDE_ALREADY

    rows = store.get_decisions_for_suggestion(sid)
    assert len(rows) == 1, (
        "the append-only decision chain carries a decision that did not stand")
    assert _statuses(sid) == rows[0]["decision"], (
        "the row's status and its decision row disagree about what was decided")


def test_the_loser_is_told_who_holds_the_decision(db):
    """"Already decided" names nobody. A refused reviewer needs a person."""
    sid = _proposal()
    assert store.decide_outcome(sid, "accepted", "alice", note="ships it")["recorded"]

    out = store.decide_outcome(sid, "rejected", "bob")
    assert out["recorded"] is False
    assert out["outcome"] == store.DECIDE_ALREADY
    current = out["current"]
    assert current["status"] == "accepted"
    assert current["decision"]["decided_by"] == "alice"
    assert current["decision"]["decided_at"]
    assert current["decision"]["note"] == "ships it"


def test_a_refused_decision_writes_absolutely_nothing(db):
    """Not a status, not a decision row. The refusal is not a partial write."""
    sid = _proposal()
    store.decide_outcome(sid, "accepted", "alice")
    before = store.get_decisions_for_suggestion(sid)

    store.decide_outcome(sid, "rejected", "bob")

    assert store.get_decisions_for_suggestion(sid) == before
    assert _statuses(sid) == "accepted"


def test_a_missing_row_is_not_found_and_not_already_decided(db):
    """Three outcomes, three callers' responses: 404, 409, 500. Never one."""
    out = store.decide_outcome("sug-does-not-exist", "accepted", "alice")
    assert out["outcome"] == store.DECIDE_NOT_FOUND
    assert out["current"] is None


def test_decide_suggestion_keeps_its_bool_contract(db):
    """Every existing caller reads a bool. The refactor must not move that."""
    sid = _proposal()
    assert store.decide_suggestion(sid, "accepted", "alice") is True
    assert store.decide_suggestion(sid, "rejected", "bob") is False
    assert store.decide_suggestion("nope", "accepted", "alice") is False


def test_supersede_is_conditional_too(db):
    """Two retirements racing are NOT two identical writes: an anchor-stale
    supersede carries no successor and a redraft's carries one, so a lost write
    here silently dead-ends the chain a reader follows from a retired change."""
    sid = _proposal()
    assert store.supersede_suggestion(sid, "anchor_stale") is True
    assert store.supersede_suggestion(
        sid, "redrafted", successor_suggestion_id="sug-new") is False
    conn = _conn()
    row = conn.execute("SELECT successor_suggestion_id FROM dic_suggestions "
                       "WHERE suggestion_id = %s", (sid,)).fetchone()
    conn.close()
    assert dict(row)["successor_suggestion_id"] in (None, ""), (
        "the loser's successor id overwrote the winner's retirement")
    assert len(store.get_decisions_for_suggestion(sid)) == 1


# ══ 2. THE DOOR'S 409 CARRIES THE STATE THAT STANDS ══════════════════════════

def test_the_second_reviewers_reject_is_refused_with_the_current_state(client, db):
    sid = _proposal()
    assert store.decide_outcome(sid, "accepted", "alice")["recorded"]

    resp = client.post(f"{P}/api/suggestions/{sid}/reject", json={"reviewer": "bob"})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["error"] == "already_decided"
    assert body["attempted"] == "reject"
    assert body["decision_recorded"] is False and body["applied"] is False
    assert body["current"]["status"] == "accepted"
    assert body["current"]["decided_by"] == "alice"
    assert "alice" in body["message"]


def test_the_second_reviewers_accept_is_refused_with_the_current_state(client, db):
    sid = _proposal()
    assert store.decide_outcome(sid, "rejected", "bob")["recorded"]

    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={"reviewer": "alice"})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["error"] == "already_decided"
    assert body["attempted"] == "accept"
    assert body["current"]["decided_by"] == "bob"


def test_no_decider_is_invented_for_a_mechanism(client, db):
    """A ``superseded`` status is written by a mechanism. The refusal says the
    change is superseded and names NOBODY -- filling the decider in from the
    status would attribute a machine's retirement to a human."""
    sid = _proposal()
    assert store.supersede_suggestion(sid, "anchor_stale", superseded_by="") is True

    resp = client.post(f"{P}/api/suggestions/{sid}/reject", json={"reviewer": "bob"})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["current"]["status"] == "superseded"
    assert not body["current"]["decided_by"]
    assert "superseded" in body["message"]


def test_a_mechanism_is_named_as_a_mechanism_and_not_as_a_decider(client, db):
    """``supersede_suggestion`` writes the MECHANISM into ``decided_by``
    (``system:anchor_verify``) exactly so a retirement can never read as
    somebody's accept-or-reject (dwr-ev-03). Rendering it through the
    "<who> already <status> this change" template would undo that one sentence
    later -- the reader is handed a name in the grammatical position a decider
    occupies."""
    sid = _proposal()
    assert store.supersede_suggestion(sid, "anchor_stale") is True   # default actor

    resp = client.post(f"{P}/api/suggestions/{sid}/accept", json={"reviewer": "bob"})
    assert resp.status_code == 409
    msg = resp.get_json()["message"]
    assert "superseded" in msg and "the document moved under it" in msg
    assert "system:anchor_verify already" not in msg
    # The mechanism is still DISCLOSED -- withholding it would be the opposite
    # error, hiding what retired the change.
    assert "system:anchor_verify" in msg


# ══ 3. THE CURSOR ════════════════════════════════════════════════════════════

def test_baseline_is_not_no_changes(db):
    """A first poll defines no window, so it can assert nothing about one."""
    out = collab.poll_document(DOC, "")
    assert out["state"] == collab.STATE_BASELINE
    assert out["changes"] == []
    assert collab.is_valid_cursor(out["cursor"])


def test_no_changes_is_a_measurement(db):
    out = collab.poll_document(DOC, collab.now_cursor())
    assert out["state"] == collab.STATE_NO_CHANGES
    assert out["change_count"] == 0 and out["section_count"] == 0
    assert out["changes_state"] == collab.FEED_OK


def test_a_decision_after_the_cursor_is_delivered(db):
    sid = _proposal()
    cursor = collab.now_cursor()
    store.decide_outcome(sid, "accepted", "alice")

    out = collab.poll_document(DOC, cursor)
    assert out["state"] == collab.STATE_CHANGES
    got = {c["suggestion_id"]: c for c in out["changes"]}
    assert sid in got
    assert got[sid]["status"] == "accepted"
    assert got[sid]["settled"] is True
    assert got[sid]["decision"]["decided_by"] == "alice"


def test_the_cursor_is_inclusive_so_a_same_instant_row_is_not_lost(db):
    """``>``, not ``>=``, drops any row stamped in the cursor's own microsecond.
    Re-delivering costs a suppressed redraw; missing costs a lost decision."""
    sid = _proposal()
    store.decide_outcome(sid, "accepted", "alice")
    conn = _conn()
    stamp = dict(conn.execute(
        "SELECT updated_at FROM dic_suggestions WHERE suggestion_id = %s",
        (sid,)).fetchone())["updated_at"]
    conn.close()

    out = collab.poll_document(DOC, stamp)      # cursor EQUALS the row's stamp
    assert [c["suggestion_id"] for c in out["changes"]] == [sid]


def test_an_unmeasured_poll_echoes_the_cursor_unchanged(db, monkeypatch):
    """THE TRAP. Advancing past a window the read failed on skips it for good,
    and the next poll reports a quiet document for a window nobody looked at."""
    cursor = collab.now_cursor()

    def _boom(*a, **k):
        raise RuntimeError("database unreachable")
    monkeypatch.setattr("tools.db.storage.get_connection", _boom)

    out = collab.poll_document(DOC, cursor)
    assert out["state"] == collab.STATE_UNMEASURED
    assert out["cursor"] == cursor
    assert out["change_count"] is None and out["section_count"] is None


def test_a_bad_cursor_is_refused_rather_than_compared(db):
    """The SQL comparison is lexicographic and is only sound for a fixed UTC
    offset: ``09:00+05:00`` sorts AFTER ``09:00+00:00`` while being earlier."""
    for bad in ("yesterday", "2026-09-08T21:00:00+05:00", "2026-09-08", ""):
        if bad == "":
            continue   # empty means baseline, a different answer
        out = collab.poll_document(DOC, bad)
        assert out["state"] == collab.STATE_UNMEASURED
        assert out["reason"] == "cursor_not_utc_iso8601"
        assert out["cursor"] == bad


def test_every_writer_stamps_utc_isoformat(db):
    """What makes the text comparison sound. Asserted of the STAMPS the writers
    actually produce, not of their source."""
    sid = _proposal()
    store.decide_outcome(sid, "accepted", "alice")
    conn = _conn()
    row = dict(conn.execute(
        "SELECT created_at, updated_at FROM dic_suggestions WHERE suggestion_id = %s",
        (sid,)).fetchone())
    drow = dict(conn.execute(
        "SELECT decided_at FROM dic_suggestion_decisions WHERE suggestion_id = %s",
        (sid,)).fetchone())
    conn.close()
    for stamp in (row["created_at"], row["updated_at"], drow["decided_at"]):
        assert collab.is_valid_cursor(stamp), stamp


def test_a_truncated_page_leaves_the_cursor_on_the_last_delivered_row(db, monkeypatch):
    """Advancing to ``now`` past rows this call did not return drops them."""
    monkeypatch.setattr(collab, "MAX_DELTAS", 2)
    cursor = collab.now_cursor()
    sids = [_proposal() for _ in range(4)]
    for sid in sids:
        store.decide_outcome(sid, "accepted", "alice")

    out = collab.poll_document(DOC, cursor)
    assert out["truncated"] is True
    assert out["deferred"] is None          # "more, count unknown" -- never 0
    assert len(out["changes"]) == 2
    assert out["cursor"] == out["changes"][-1]["updated_at"]

    # And the backlog DRAINS. Each page re-delivers its own last row (the
    # inclusive cursor) and adds one, so a bound of 2 takes three polls to clear
    # four rows -- the property that matters is that it terminates and drops
    # nothing, not how many polls it takes.
    delivered = {c["suggestion_id"] for c in out["changes"]}
    polls, cur = 1, out["cursor"]
    while out["truncated"] and polls < 10:
        out = collab.poll_document(DOC, cur)
        delivered |= {c["suggestion_id"] for c in out["changes"]}
        assert out["cursor"] >= cur, "the cursor went backwards and the poll cannot drain"
        cur = out["cursor"]
        polls += 1
    assert out["truncated"] is False, "the poll never drained its backlog"
    assert delivered == set(sids), "a truncated page dropped rows for good"


def test_a_partial_read_is_unmeasured_not_quiet(db, monkeypatch):
    """One feed unreadable is not a window in which nothing happened."""
    def _boom(*a, **k):
        raise RuntimeError("dic_edit_history is gone")
    monkeypatch.setattr(collab, "_section_deltas", _boom)

    out = collab.poll_document(DOC, collab.now_cursor())
    assert out["state"] == collab.STATE_UNMEASURED
    assert out["changes_state"] == collab.FEED_OK
    assert out["sections_state"] == collab.FEED_UNMEASURED
    assert out["section_count"] is None


def test_another_documents_change_is_not_delivered(db):
    cursor = collab.now_cursor()
    other = _proposal(doc_id="doc-other")
    store.decide_outcome(other, "accepted", "alice")
    out = collab.poll_document(DOC, cursor)
    assert out["changes"] == []


# ══ 4. THE SECTION FEED ══════════════════════════════════════════════════════

def _edit(section_id: str, doc_id: str = DOC):
    conn = _conn()
    conn.execute(
        "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, "
        "status, origin, created_at) VALUES (%s,'v1',%s,'H','body','draft','human','x')"
        if not _section_exists(section_id) else
        "UPDATE dic_sections SET content='body' WHERE section_id=%s",
        (section_id, doc_id) if not _section_exists(section_id) else (section_id,),
    )
    conn.commit()
    conn.close()
    from tools.document_intelligence.history_recorder import record_edit
    record_edit(section_id=section_id, editor="alice",
                content_before="before", content_after="after " + uuid.uuid4().hex[:6])


def _section_exists(section_id: str) -> bool:
    conn = _conn()
    row = conn.execute("SELECT 1 FROM dic_sections WHERE section_id = %s",
                       (section_id,)).fetchone()
    conn.close()
    return row is not None


def test_a_section_edit_is_delivered_once_per_section(db):
    """The client re-reads a section whole, so three edits are one delta."""
    cursor = collab.now_cursor()
    _edit("sec-body")
    _edit("sec-body")

    out = collab.poll_document(DOC, cursor)
    assert out["state"] == collab.STATE_CHANGES
    assert [s["section_id"] for s in out["sections"]] == ["sec-body"]
    assert out["sections"][0]["edits"] == 2


def test_another_documents_section_edit_is_not_delivered(db):
    """The scope comes from ``dic_sections.doc_id``, not from the history row's
    own ``doc_id``, which the caller supplies and does not always write."""
    cursor = collab.now_cursor()
    _edit("sec-elsewhere", doc_id="doc-other")
    out = collab.poll_document(DOC, cursor)
    assert out["sections"] == []


# ══ 5. PRESENCE ══════════════════════════════════════════════════════════════

def test_alone_is_measured_and_excludes_me(db):
    from tools.document_intelligence.presence_registry import join_document
    join_document(DOC, "alice")
    p = collab.others_present(DOC, me="alice")
    assert p["state"] == collab.PRESENCE_ALONE
    assert p["count"] == 0 and p["others"] == []


def test_another_reviewer_is_reported_by_name(db):
    from tools.document_intelligence.presence_registry import join_document
    join_document(DOC, "alice")
    join_document(DOC, "bob")
    p = collab.others_present(DOC, me="alice")
    assert p["state"] == collab.PRESENCE_OTHERS
    assert [o["user_id"] for o in p["others"]] == ["bob"]


def test_an_unreadable_registry_is_never_alone(db, monkeypatch):
    """"Only you are here" over an unreadable registry tells a reviewer nobody
    else can be deciding the change in front of them."""
    def _boom(*a, **k):
        raise RuntimeError("presence table is gone")
    monkeypatch.setattr(
        "tools.document_intelligence.presence_registry.get_present_users", _boom)
    p = collab.others_present(DOC, me="alice")
    assert p["state"] == collab.PRESENCE_UNMEASURED
    assert p["count"] is None


def test_presence_is_read_through_the_registry_not_a_private_select():
    """``presence_registry`` owns the TTL, the expiry purge and the shape of a
    session row. A second SELECT here is a second opinion about who is present.
    Structural: a behavioural test passes for a copy that happens to agree."""
    tree = ast.parse(COLLAB.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "dic_presence_sessions" not in node.value, (
                "collab.py names the presence table in SQL of its own")


# ══ 6. STRUCTURAL ════════════════════════════════════════════════════════════

def test_the_poll_route_is_a_get_with_no_post_sibling(client):
    rules = [r for r in client.application.url_map.iter_rules()
             if str(r.rule).endswith("/api/workspace/poll")]
    assert rules, "the poll route is not registered"
    for r in rules:
        assert "POST" not in r.methods, "a read-only feed grew a write door"
        assert "GET" in r.methods


def test_the_poll_writes_nothing(db):
    """Reads, decides nothing, applies nothing -- and does NOT heartbeat, so a
    reviewer's visibility to others never depends on a feed whose failure mode
    is an exponential backoff."""
    tree = ast.parse(COLLAB.read_text(encoding="utf-8"))
    # DOCSTRINGS ARE EXCLUDED, and that is not a loophole: this module's
    # docstring EXPLAINS which writers it does not perform, so a naive scan
    # flags the file's own account of itself -- the trap
    # ``perfect_score_census`` records, where its first census entry would have
    # been the previous fix's comment explaining the defect.
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in docstrings:
                continue
            sql = node.value.upper()
            for verb in ("INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE", "ALTER TABLE"):
                assert verb not in sql, f"collab.py issues {verb.strip()}"
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            assert name not in ("commit", "heartbeat", "ping", "join_document"), (
                f"collab.py calls {name}() -- the poll must stay a read")


def test_the_page_polls_its_own_endpoint_and_not_the_hook_feed():
    """``/api/events/poll`` is bound to ``hook_events`` -- a platform-wide
    tool-call feed with no notion of a document. Reuse the CLIENT pattern, not
    that endpoint."""
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "/api/workspace/poll" in html
    # Asked of what the page FETCHES, not of what it mentions: the comment
    # above the poll loop names ``/api/events/poll`` in order to say it is the
    # wrong feed, and a substring scan would flag the page's own reasoning.
    fetched = re.findall(r"fetch\(\s*'([^']+)'", html)
    fetched += re.findall(r"var url = '([^']+)'", html)
    assert not [u for u in fetched if "/api/events/poll" in u], fetched


def test_the_page_does_not_open_an_sse_stream():
    """D103: polling replaces SSE as the primary transport, and an SSE stream
    holds a synchronous WSGI worker open per reader."""
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "EventSource" not in html
    assert "presence/stream" not in html


def test_the_page_seeds_its_cursor_from_the_render(client, db):
    """A cursor taken when the FIRST POLL arrives leaves a window -- render,
    ship, parse, fetch -- in which another reviewer's decision is never
    delivered. Stamped at render, that window is closed."""
    from tools.document_intelligence.workspace import workspace_context
    ctx = workspace_context(DOC)
    assert collab.is_valid_cursor(ctx["poll_cursor"])
    assert "poll_cursor" in TEMPLATE.read_text(encoding="utf-8")


def test_the_refusal_shape_is_one_shape_for_both_doors():
    """A client reads ``error: already_decided`` without knowing which rung
    refused, and both doors route through the one helper."""
    src = BLUEPRINT.read_text(encoding="utf-8")
    assert src.count("def _decided_refusal(") == 1
    # accept: the pre-read guard and the conditional-UPDATE loser; reject: both.
    assert src.count("_decided_refusal(") >= 5
    assert "suggestion already decided" not in src, (
        "the old prose refusal survives somewhere and carries no state")


def test_the_reject_door_no_longer_calls_the_bool_only_writer():
    """``decide_suggestion`` cannot tell 'somebody else decided' (409) from 'the
    write failed' (500), and a reviewer told 500 reloads and tries again."""
    src = BLUEPRINT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef)
                and node.name in ("api_suggestion_accept", "api_suggestion_reject")):
            continue
        called = {getattr(c.func, "attr", None) or getattr(c.func, "id", None)
                  for c in ast.walk(node) if isinstance(c, ast.Call)}
        assert "decide_outcome" in called, f"{node.name} does not use decide_outcome"
        assert "decide_suggestion" not in called, (
            f"{node.name} still writes through the bool-only seam")


def test_no_perfect_score_or_zero_fallback_over_an_empty_denominator():
    """args/perfect_score_gate.yaml is ratcheted to 0. A poll that reported a
    rate would be one rounding away from the same defect."""
    src = COLLAB.read_text(encoding="utf-8")
    assert not re.search(r"else\s+100\.0", src)
    assert not re.search(r"_pct", src), (
        "collab reports counts and states, never a rate -- add one and it needs "
        "an empty-denominator rail of its own")
