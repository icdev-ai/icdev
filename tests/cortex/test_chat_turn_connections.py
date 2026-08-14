# CUI // SP-CTI
"""One POST /cortex/api/chat opened FOUR separate DB connections (ctx-perf-03).

``chat_session.ensure_session``, ``chat_session.record_turn`` (twice) and
``blueprint._record_history`` each opened, committed and closed their own
connection for a single chat turn — on top of the governance audit's own, which
``db/init_db.record_governed_call`` had already collapsed for the audit pair
(cxo-perf-03). The chat store never got the same treatment.

These tests pin the fix at both altitudes:

  * **Cost** — counted with a CONNECTION COUNTER, never a timing assertion: the
    defect is "how many", and a timing assertion on a shared runner is a flake.
    The counter wraps ``get_connection`` on BOTH module aliases
    (``tools.db.storage`` and ``icdev.tools.db.storage``), which are distinct
    module objects — patching only one silently misses whichever copy the
    running blueprint imported.
  * **Correctness** — the writes are best-effort and swallowed at ``debug``, so
    a "collapsed to one connection" that also collapsed the rows would look
    identical from the outside. Every cost assertion is therefore paired with a
    read-back through the real ``GET /cortex/api/session/<id>`` route and a real
    ``cortex_search_history`` count.

Also pinned here: ``_record_history`` bound a Python ``bool`` to the INTEGER
``grounded`` column. psycopg2 adapts that to a PG ``boolean`` literal, which
PostgreSQL — the primary backend — refuses for an INTEGER column; the error was
then swallowed at debug, so the search-history row vanished on PG while SQLite
(which accepts a bool as 0/1) kept the suite green.

Connection ownership follows the same convention as ``record_governed_call``
and the IQE adapters: a caller-supplied connection is NEVER closed by the
helper that borrowed it, a self-opened one always is.

SQLite via the conftest ``icdev_db`` fixture; runtime is PostgreSQL. No DB
service, no LLM, no network.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The two aliases of every ICDEV module. `tools.x` and `icdev.tools.x` are
# DISTINCT module objects, so a fake installed on one is invisible to the other.
_STORAGE_ALIASES = ("tools.db.storage", "icdev.tools.db.storage")


class ConnectionCounter:
    """Wraps ``get_connection`` on every storage alias and counts the opens."""

    def __init__(self, monkeypatch):
        self._monkeypatch = monkeypatch
        self.opened = 0
        self.closed = 0
        self._mods = []
        for name in _STORAGE_ALIASES:
            try:
                self._mods.append(importlib.import_module(name))
            except ImportError:  # pragma: no cover — mirror may be absent
                continue

    def install(self):
        counter = self
        for mod in self._mods:
            real = mod.get_connection

            def counting(*a, _real=real, **k):
                conn = _real(*a, **k)
                counter.opened += 1
                real_close = conn.close

                def close_tracked(*ca, **ck):
                    counter.closed += 1
                    return real_close(*ca, **ck)

                conn.close = close_tracked
                return conn

            self._monkeypatch.setattr(mod, "get_connection", counting)
        return self


@pytest.fixture
def counter(monkeypatch):
    return ConnectionCounter(monkeypatch)


@pytest.fixture
def db(icdev_db, monkeypatch):
    """Point every connection at conftest's temp DB (carries the cortex_* tables)."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    import tools.dashboard.auth as _auth

    monkeypatch.setattr(_auth, "DB_PATH", str(icdev_db))
    return icdev_db


@pytest.fixture
def client(db):
    """A FRESH app per test with the cortex blueprint, logged in as test-admin.

    This used to register the blueprint onto the shared ``tools.dashboard.app``
    singleton behind an ``if "cortex" not in app.blueprints`` guard. That works
    when this file runs alone and fails in CI: any earlier module importing the
    same singleton and issuing one request locks Flask's setup phase, so the
    registration then raises

        AssertionError: The setup method 'register_blueprint' can no longer be
        called on the application. It has already handled its first request.

    The guard cannot help — it only skips registration when the blueprint is
    ALREADY there, and the failing case is precisely when it is not. Order
    decided whether the suite passed.

    cortex_bp carries its own ``before_request``, so it needs no dashboard
    middleware and a bare Flask app is enough. Building one per test also stops
    this file mutating global state other tests observe.
    """
    from flask import Flask

    from tools.cortex.blueprint import cortex_bp

    app = Flask(__name__)
    app.secret_key = "test-secret"          # session_transaction needs one
    app.config["TESTING"] = True
    app.register_blueprint(cortex_bp)

    with app.test_client() as test_client:
        with test_client.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        yield test_client


@pytest.fixture
def no_llm(monkeypatch):
    """Stub the facades so the route degrades without reaching an LLM.

    The governed facades own their OWN connection (record_governed_call), so
    degrading here isolates the count to the chat-persistence path under test.
    """
    import tools.cortex.api as cortex_api

    def _raise(*a, **k):
        raise RuntimeError("no LLM in test")

    for name in ("ask", "complete", "search"):
        monkeypatch.setattr(cortex_api, name, _raise)


OUTCOME = {
    "answer": "an answer",
    "grounded": True,
    "confidence": "include",
    "citations": [{"source": "doc-1"}],
    "governance": {"gates_run": ["retrieval"], "outcomes": {"retrieval": "pass"}, "blocked": False},
    "requires_confirm": False,
    "degraded": False,
}


def _count(db_path, table, session_id):
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE session_id = %s", (session_id,)
        ).fetchone()[0]
    finally:
        conn.close()


class TestConnectionCost:
    def test_persist_turn_opens_exactly_one_connection(self, db, counter):
        """Four writes (session + user turn + assistant turn + history) → ONE open.

        Pre-fix this was 4. The read-back below is not decoration: the writes are
        best-effort, so "1 connection" is only good news if the rows landed.
        """
        from tools.cortex import blueprint

        counter.install()
        blueprint._persist_turn(
            "cost-1", "ask", "compliance", "how many controls?", OUTCOME, "default", "CUI",
        )

        assert counter.opened == 1, (
            f"chat-turn persistence opened {counter.opened} connections, expected 1"
        )
        assert counter.closed == 1, "the connection _persist_turn opened must be closed"
        assert _count(db, "cortex_chat_sessions", "cost-1") == 1
        assert _count(db, "cortex_messages", "cost-1") == 2
        assert _count(db, "cortex_search_history", "cost-1") == 1

    def test_chat_request_opens_one_connection_for_persistence(
        self, db, client, no_llm, counter
    ):
        """End-to-end: the whole degraded chat route opens at most one connection.

        With the facades stubbed the governed path never runs, so every
        connection this request opens belongs to chat persistence. Pre-fix: 4.
        """
        client.post(  # warm _ensure_init / _SCHEMA_ENSURED out of the measurement
            "/cortex/api/chat",
            data=json.dumps({"question": "warm up", "mode": "ask"}),
            content_type="application/json",
        )

        counter.install()
        resp = client.post(
            "/cortex/api/chat",
            data=json.dumps({"question": "hello cortex", "mode": "ask", "session_id": "e2e-1"}),
            content_type="application/json",
        )

        assert resp.status_code == 200
        assert counter.opened <= 1, (
            f"POST /cortex/api/chat opened {counter.opened} connections, expected at most 1"
        )


class TestPersistenceStillWorks:
    def test_turn_is_readable_after_the_request(self, db, client, no_llm):
        """A turn survives the request and reloads through the real session route."""
        post = client.post(
            "/cortex/api/chat",
            data=json.dumps(
                {"question": "what is CMMC?", "mode": "ask", "domain": "compliance",
                 "session_id": "read-1"}
            ),
            content_type="application/json",
        )
        assert post.status_code == 200

        resp = client.get("/cortex/api/session/read-1")
        assert resp.status_code == 200
        body = resp.get_json()

        assert body["turn_count"] == 2
        user, assistant = body["turns"]
        assert user["role"] == "user"
        assert user["content"] == "what is CMMC?"
        assert user["turn_number"] == 1
        assert assistant["role"] == "assistant"
        assert assistant["turn_number"] == 2
        assert assistant["content"] == post.get_json()["answer"]

        assert body["session"] is not None
        assert body["session"]["domain"] == "compliance"
        assert _count(db, "cortex_search_history", "read-1") == 1

    def test_turn_numbers_increment_across_turns(self, db):
        """The MAX(turn_number)+1 read still sees the previous turn's commit."""
        from tools.cortex import blueprint

        for _ in range(3):
            blueprint._persist_turn(
                "multi-1", "ask", "general", "q", OUTCOME, "default", "CUI",
            )

        from tools.cortex import chat_session

        turns = chat_session.load_turns("multi-1")
        assert [t["turn_number"] for t in turns] == [1, 2, 3, 4, 5, 6]

    def test_trust_labels_survive_the_round_trip(self, db):
        """grounded/confidence/citations/governance rehydrate exactly as written."""
        from tools.cortex import blueprint, chat_session

        blueprint._persist_turn(
            "trust-1", "search", "compliance", "q", OUTCOME, "default", "CUI",
        )
        assistant = chat_session.load_turns("trust-1")[1]

        assert assistant["grounded"] is True
        assert assistant["confidence"] == "include"
        assert assistant["citations"] == [{"source": "doc-1"}]
        assert assistant["governance"]["gates_run"] == ["retrieval"]


class TestConnectionOwnership:
    def test_helpers_do_not_close_a_borrowed_connection(self, db):
        """A caller-supplied connection outlives the helpers that wrote through it."""
        from tools.cortex import blueprint, chat_session
        from tools.db.storage import get_connection

        conn = get_connection()
        closes = []
        real_close = conn.close
        conn.close = lambda *a, **k: closes.append(1)
        try:
            chat_session.ensure_session(
                "own-1", user_id="u", mode="ask", domain="general",
                tenant_id="default", classification="CUI", conn=conn,
            )
            chat_session.record_turn("own-1", "user", "q", conn=conn)
            blueprint._record_history("own-1", "ask", "general", "q", conn=conn)
            assert closes == [], "a borrowed connection must never be closed by the borrower"
            # ...and it is still usable afterwards.
            assert conn.execute(
                "SELECT COUNT(*) FROM cortex_messages WHERE session_id = %s", ("own-1",)
            ).fetchone()[0] == 1
        finally:
            conn.close = real_close
            conn.close()

    def test_helpers_close_a_self_opened_connection(self, db, counter):
        """Called standalone, each helper still owns and closes its connection."""
        from tools.cortex import chat_session

        counter.install()
        chat_session.ensure_session(
            "own-2", user_id="u", mode="ask", domain="general",
            tenant_id="default", classification="CUI",
        )
        assert counter.opened == 1
        assert counter.closed == 1


class TestFailureIsolation:
    def test_a_failed_session_write_does_not_lose_the_turns(self, db, monkeypatch):
        """Session bookkeeping is best-effort; the conversation rows still land.

        On PostgreSQL a failed statement poisons the whole transaction, so
        sharing one connection means a broken session write would take the turn
        rows down with it unless the failure is rolled back — the same hazard
        record_governed_call names for the audit row.
        """
        from tools.cortex import blueprint, chat_session

        def boom(*a, **k):
            raise RuntimeError("session table exploded")

        monkeypatch.setattr(chat_session, "ensure_session", boom)
        blueprint._persist_turn(
            "isolate-1", "ask", "general", "q", OUTCOME, "default", "CUI",
        )

        assert _count(db, "cortex_messages", "isolate-1") == 2
        assert _count(db, "cortex_search_history", "isolate-1") == 1

    def test_persist_turn_never_raises_without_a_usable_db(self, monkeypatch):
        """The answer is returned even when persistence is impossible."""
        from tools.cortex import blueprint

        for name in _STORAGE_ALIASES:
            try:
                mod = importlib.import_module(name)
            except ImportError:  # pragma: no cover
                continue
            monkeypatch.setattr(
                mod, "get_connection", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db"))
            )

        blueprint._persist_turn(
            "nodb-1", "ask", "general", "q", OUTCOME, "default", "CUI",
        )  # must not raise


class TestGroundedColumnType:
    def test_history_binds_grounded_as_int_not_bool(self, db):
        """``grounded`` is an INTEGER column — a bool binds to PG boolean and fails.

        psycopg2 adapts Python ``True`` to a PG ``boolean``, which PostgreSQL
        refuses for an INTEGER column; the error was swallowed at debug, so the
        row silently vanished on the primary backend while SQLite (bool == 0/1)
        kept the tests green. Asserts the BOUND PARAMETER, since only PG is
        strict enough to notice at runtime.
        """
        from tools.cortex import blueprint

        captured = []

        class RecordingConn:
            def execute(self, sql, params=None):
                captured.append((sql, params))
                return self

            def fetchone(self):
                return (0,)

            def commit(self):
                pass

        blueprint._record_history(
            "typed-1", "ask", "general", "q", grounded=True, conn=RecordingConn(),
        )

        assert captured, "_record_history wrote nothing"
        params = captured[0][1]
        grounded = params[8]
        assert grounded == 1
        assert not isinstance(grounded, bool), (
            "grounded must bind as int for the INTEGER column; a bool becomes a PG boolean"
        )

    def test_message_grounded_binds_as_int(self, db):
        from tools.cortex import chat_session

        captured = []

        class RecordingConn:
            def execute(self, sql, params=None):
                captured.append((sql, params))
                return self

            def fetchone(self):
                return (0,)

            def commit(self):
                pass

        chat_session.record_turn("typed-2", "assistant", "a", grounded=True, conn=RecordingConn())

        insert = [p for sql, p in captured if "INSERT" in sql][0]
        assert insert[6] == 1
        assert not isinstance(insert[6], bool)
