# CUI // SP-CTI
"""IQE adapter + seed-query tests for the Cortex canvas (ctx-canvas-03).

Exercises the real end-to-end IQE path against seeded fixture data:

  seed rows -> get_connection() -> tools/iqe/adapters/cortex.py adapters
            -> executor.execute_query() -> projected rows

Every one of the three shipped seed queries in
``context/iqe/queries/cortex/*.iqe`` is parsed and executed through the module
executor, plus the collections are exercised directly and through the blueprint
``POST /cortex/api/iqe-query`` route. Nothing here calls an LLM — the seed
``.iqe`` files are raw IQE, parsed directly; the blueprint route uses the
deterministic ``nl_to_iqe`` fallback.

The adapters read through ``get_connection()`` (global RLS predicate applies).
No SecurityContext is set outside a Flask request, so seeded rows are visible;
this mirrors how ``iqe_dispatch`` runs in a request that has already
authenticated + read-down scoped the caller.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEED_DIR = ROOT / "context" / "iqe" / "queries" / "cortex"


# ── fixture data ────────────────────────────────────────────────────────────────
_SESSIONS = [
    # session_id, user_id, mode, domain, title, status
    ("sess-1", "alice", "ask", "compliance", "NIST lookup", "active"),
    ("sess-2", "bob", "search", "network", "Topology sweep", "active"),
    ("sess-3", "carol", "complete", "general", "Draft memo", "closed"),
]

_SEARCH_HISTORY = [
    # query_id, session_id, user_id, mode, domain, query_text, strategy, result_count, grounded
    ("q-1", "sess-1", "alice", "search", "compliance", "AC-2 controls", "rag", 5, 1),
    ("q-2", "sess-2", "bob", "search", "network", "core routers", "kg", 3, 1),
    ("q-3", "sess-1", "alice", "ask", "compliance", "who owns AC-2", "hybrid", 0, 0),
]

_AUDIT = [
    # id, session_id, function, outcome, blocked
    ("a-1", "sess-1", "search", "pass", 0),
    ("a-2", "sess-2", "ask", "pass", 0),
    ("a-3", "sess-3", "complete", "blocked", 1),
]


@pytest.fixture
def seeded_cortex_db(tmp_path, monkeypatch):
    """Point storage at a fresh SQLite DB, create the Cortex tables, seed rows.

    Yields nothing — collections are read through ``get_connection()`` (conn=None
    path) which resolves ``ICDEV_DB_PATH``, so the executor and adapters pick up
    the same file automatically.
    """
    db_path = tmp_path / "cortex_iqe.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    # Rebuild the canvas schema in this fresh DB (reset the once-only guard).
    import importlib
    init_mod = importlib.import_module("tools.cortex.db.init_db")
    monkeypatch.setattr(init_mod, "_INIT_DONE", False, raising=False)
    init_mod.init_db()

    # Make sure the cortex collections are registered on the shared executor even
    # if a prior test cleared the registry.
    from tools.iqe import executor as _executor
    from tools.iqe.adapters import cortex as cortex_adapter  # noqa: F401
    _executor.register_collection("cortex.chat_sessions", cortex_adapter.sessions_adapter)
    _executor.register_collection("cortex.audit", cortex_adapter.audit_adapter)
    _executor.register_collection(
        "cortex.search_history", cortex_adapter.search_history_adapter
    )

    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.executemany(
            "INSERT INTO cortex_chat_sessions "
            "(session_id, user_id, mode, domain, title, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            _SESSIONS,
        )
        conn.executemany(
            "INSERT INTO cortex_search_history "
            "(query_id, session_id, user_id, mode, domain, query_text, "
            "strategy, result_count, grounded) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _SEARCH_HISTORY,
        )
        conn.executemany(
            "INSERT INTO cortex_audit "
            "(id, session_id, function, outcome, blocked) "
            "VALUES (?, ?, ?, ?, ?)",
            _AUDIT,
        )
        conn.commit()
    finally:
        conn.close()

    yield db_path


def _run_iqe(iqe_text: str):
    """Parse raw IQE text and execute it through the module executor."""
    from tools.iqe.executor import execute_query
    from tools.iqe.parser import parse as iqe_parse

    ast = iqe_parse(iqe_text)
    return execute_query(ast, conn=None)


# ── adapters return seeded rows ────────────────────────────────────────────────
class TestAdaptersReadSeededData:
    def test_sessions_collection_returns_all_rows(self, seeded_cortex_db):
        rows = _run_iqe("foreach s in cortex.chat_sessions select s.session_id, s.status")
        ids = sorted(r["session_id"] for r in rows)
        assert ids == ["sess-1", "sess-2", "sess-3"]

    def test_audit_collection_returns_all_rows(self, seeded_cortex_db):
        rows = _run_iqe("foreach a in cortex.audit select a.id")
        assert len(rows) == 3

    def test_search_history_collection_returns_all_rows(self, seeded_cortex_db):
        rows = _run_iqe(
            "foreach h in cortex.search_history select h.query_id, h.result_count"
        )
        assert len(rows) == 3


# ── every shipped seed .iqe file executes and returns the expected shape ───────
class TestShippedSeedQueries:
    def test_seed_dir_has_at_least_three_queries(self):
        files = sorted(SEED_DIR.glob("*.iqe"))
        assert len(files) >= 3, f"expected >=3 seed queries, found {len(files)}"

    def test_01_recent_sessions(self, seeded_cortex_db):
        text = (SEED_DIR / "01_recent_sessions.iqe").read_text(encoding="utf-8")
        rows = _run_iqe(text)
        # All three sessions, projected to the selected fields.
        assert len(rows) == 3
        assert set(rows[0]) == {
            "session_id", "mode", "domain", "status", "user_id", "created_at"
        }

    def test_02_ungrounded_answers(self, seeded_cortex_db):
        text = (SEED_DIR / "02_ungrounded_answers.iqe").read_text(encoding="utf-8")
        rows = _run_iqe(text)
        # Only the single grounded == false row survives the where clause.
        assert len(rows) == 1
        assert rows[0]["query_text"] == "who owns AC-2"
        assert set(rows[0]) == {
            "query_text", "mode", "domain", "strategy", "result_count"
        }

    def test_03_blocked_invocations(self, seeded_cortex_db):
        text = (SEED_DIR / "03_blocked_invocations.iqe").read_text(encoding="utf-8")
        rows = _run_iqe(text)
        # Only the single blocked == true audit row survives.
        assert len(rows) == 1
        assert rows[0]["function"] == "complete"
        assert rows[0]["outcome"] == "blocked"

    def test_all_seed_files_execute_without_error(self, seeded_cortex_db):
        """Smoke every shipped seed query — none may raise on real data."""
        for path in sorted(SEED_DIR.glob("*.iqe")):
            rows = _run_iqe(path.read_text(encoding="utf-8"))
            assert isinstance(rows, list)


# ── blueprint POST /cortex/api/iqe-query (canonical dispatch path) ─────────────
class TestBlueprintIqeRoute:
    @pytest.fixture
    def client(self, seeded_cortex_db):
        """Mount the cortex blueprint on a bare Flask app.

        The full dashboard app attaches an auth ``before_request`` that queries
        ``dashboard_api_keys`` — tables the isolated fixture DB does not carry.
        A bare app exercises the exact ``/cortex/api/iqe-query`` handler + IQE
        dispatch + adapters against the seeded DB with no auth/RLS coupling
        (auth is covered by test_blueprint_routes.py / test_chat_routing.py).
        """
        from flask import Flask

        from tools.cortex.blueprint import cortex_bp

        app = Flask(__name__)
        app.register_blueprint(cortex_bp)
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_iqe_query_route_executes_against_seeded_data(self, client):
        resp = client.post(
            "/cortex/api/iqe-query", json={"question": "show all cortex sessions"}
        )
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["ok"] is True
        assert payload["iqe"].startswith("foreach")
        # nl_to_iqe routes an unfiltered "sessions" question to cortex.chat_sessions;
        # the three seeded rows come back.
        assert payload["row_count"] == len(_SESSIONS)

    def test_iqe_query_route_requires_question(self, client):
        resp = client.post("/cortex/api/iqe-query", json={})
        assert resp.status_code == 400
