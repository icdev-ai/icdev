# CUI // SP-CTI
"""POST /cortex/api/iqe-query must scope explicitly and bound its result set.

The route called ``execute_query(ast, conn=None)``, which makes every IQE
adapter open its OWN connection. Tenant scope and Bell-LaPadula read-down then
held only as far as ``get_connection()`` happened to find a usable
``flask.g.security_context`` — and when it did not, the query ran UNSCOPED,
returned every tenant's rows, and answered 200. Cortex is the component that
enforces tenant isolation, so its own query surface is the last place that may
assume the ambient path worked.

The DENY case is the acceptance proof: ``test_cross_tenant_rows_are_not_returned``
seeds two tenants' rows in one table and asserts tenant-b's rows are ABSENT from
tenant-a's answer. It runs against a real ``StorageConnection`` over a temp
SQLite DB so the app-level RLS predicate injection is genuinely exercised, and
it fails on the unfixed route (which returns both tenants' rows).

``flask.g.security_context`` is asserted in BOTH shapes the dashboard actually
sets: a dict (Cortex service-key binding) and a ``SecurityContext`` dataclass
(session user). The dataclass shape used to fall through to the "default"
tenant, which now matters because the route filters on that value.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_AUDIT_DDL = """
CREATE TABLE cortex_audit (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    function TEXT,
    outcome TEXT,
    blocked INTEGER,
    provenance_id TEXT,
    classification TEXT,
    tenant_id TEXT,
    created_at TEXT
)
"""

_IQE = 'foreach a in cortex.audit select a.id, a.tenant_id'


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    """Temp SQLite DB holding cortex_audit rows for two tenants."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "cortex_iqe.db"
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(db_path))
    try:
        conn.execute(_AUDIT_DDL)
        for tenant, n in (("tenant-a", 2), ("tenant-b", 3)):
            for i in range(n):
                conn.execute(
                    "INSERT INTO cortex_audit (id, session_id, function, outcome, "
                    "blocked, provenance_id, classification, tenant_id, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"{tenant}-{i}", "s1", "ask", "pass", 0, None, "CUI", tenant,
                     f"2026-08-14T0{i}:00:00+00:00"),
                )
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def make_client(audit_db, monkeypatch):
    """Build a test client for a bare Flask app carrying the cortex blueprint.

    *security_context* is placed on ``flask.g`` exactly as the dashboard auth
    middleware would, so the route reads it the same way the real one does.
    """
    from flask import Flask, g

    import tools.iqe.nl_to_iqe as nl_module
    from tools.cortex import blueprint as bp

    # The route translates via nl_to_iqe (an LLM call that degrades to a
    # select-all fallback). Pin the translation so the test asserts scoping and
    # bounding, not translation quality.
    monkeypatch.setattr(nl_module, "nl_to_iqe",
                        lambda question, collections: {"iqe": _IQE, "explanation": "pinned"})
    # init_db() would rebuild the whole cortex schema over the fixture DB.
    monkeypatch.setattr(bp, "_INIT_DONE", True)

    def _build(security_context):
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(bp.cortex_bp)

        @app.before_request
        def _attach():
            if security_context is not None:
                g.security_context = security_context

        return app.test_client()

    return _build


def _sec_ctx_object(tenant_id, classification="CUI"):
    from tools.security.security_context import SecurityContext

    return SecurityContext(user_id="u1", tenant_id=tenant_id, classification=classification)


# ---------------------------------------------------------------------------
# DENY case — the acceptance proof
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ctx_shape", ["dict", "dataclass"])
def test_cross_tenant_rows_are_not_returned(make_client, ctx_shape):
    """Tenant A's question must not surface tenant B's rows."""
    ctx = (
        {"tenant_id": "tenant-a", "user_id": "u1", "classification": "CUI"}
        if ctx_shape == "dict" else _sec_ctx_object("tenant-a")
    )
    resp = make_client(ctx).post("/cortex/api/iqe-query", json={"question": "recent audit rows"})

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["ok"] is True
    tenants = {row.get("tenant_id") for row in body["results"]}
    assert tenants == {"tenant-a"}, f"cross-tenant leak: {body['results']}"
    assert body["row_count"] == 2


def test_unscoped_request_does_not_see_a_tenants_rows(make_client):
    """No g.security_context at all -> the 'default' tenant, not everything.

    The unfixed route relied on ``get_connection()`` finding a context; with
    none present it read every tenant's rows and still answered 200.
    """
    resp = make_client(None).post("/cortex/api/iqe-query", json={"question": "audit"})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["results"] == []


def test_a_tenant_sees_only_its_own_rows(make_client):
    """The ALLOW half — scoping filters foreign rows, it does not empty the answer."""
    resp = make_client(_sec_ctx_object("tenant-b")).post(
        "/cortex/api/iqe-query", json={"question": "audit"})

    body = resp.get_json()
    assert resp.status_code == 200, body
    assert {row["tenant_id"] for row in body["results"]} == {"tenant-b"}
    assert body["row_count"] == 3


# ---------------------------------------------------------------------------
# Explicit threading (not ambient g pickup)
# ---------------------------------------------------------------------------

class _RecordingConn:
    """A connection that records the security context applied to it."""

    _backend = "sqlite"

    def __init__(self):
        self.security_context = None
        self.closed = False

    def set_security_context(self, ctx):
        self.security_context = ctx

    def close(self):
        self.closed = True


def test_route_applies_an_explicit_security_context_to_its_connection(make_client, monkeypatch):
    from tools.cortex import blueprint as bp
    import tools.iqe.executor as executor

    conn = _RecordingConn()
    monkeypatch.setattr(bp, "_open_query_connection", lambda: conn)
    seen = {}

    def _fake_execute(ast, c):
        seen["conn"] = c
        return []

    monkeypatch.setattr(executor, "execute_query", _fake_execute)

    resp = make_client(_sec_ctx_object("tenant-a", "SECRET")).post(
        "/cortex/api/iqe-query", json={"question": "audit"})

    assert resp.status_code == 200, resp.get_json()
    # The context reached the connection the query ran on — explicitly, before
    # execution — and the query ran on THAT connection, not conn=None.
    assert conn.security_context is not None
    assert conn.security_context.tenant_id == "tenant-a"
    assert conn.security_context.classification == "SECRET"
    assert seen["conn"] is conn
    assert conn.closed is True


def test_route_does_not_execute_with_conn_none(make_client, monkeypatch):
    """conn=None is the defect: it makes each adapter open its own connection."""
    import tools.iqe.executor as executor

    calls = []
    monkeypatch.setattr(executor, "execute_query", lambda ast, c: calls.append(c) or [])

    make_client(_sec_ctx_object("tenant-a")).post(
        "/cortex/api/iqe-query", json={"question": "audit"})

    assert calls and calls[0] is not None


def test_connection_is_closed_when_the_context_cannot_be_applied(make_client, monkeypatch):
    """Fail-closed refusal must not leak the connection it refused to scope."""
    from tools.cortex import analyst
    from tools.cortex import blueprint as bp

    conn = _RecordingConn()
    monkeypatch.setattr(bp, "_open_query_connection", lambda: conn)
    monkeypatch.setattr(
        analyst, "_apply_security_context",
        lambda c, ctx: (_ for _ in ()).throw(analyst.CortexAnalystError("unscopable")),
    )

    resp = make_client(_sec_ctx_object("tenant-a")).post(
        "/cortex/api/iqe-query", json={"question": "audit"})

    assert resp.status_code == 500
    assert conn.closed is True


def test_connection_is_closed_even_when_execution_raises(make_client, monkeypatch):
    from tools.cortex import blueprint as bp
    import tools.iqe.executor as executor

    conn = _RecordingConn()
    monkeypatch.setattr(bp, "_open_query_connection", lambda: conn)

    def _boom(ast, c):
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(executor, "execute_query", _boom)

    resp = make_client(_sec_ctx_object("tenant-a")).post(
        "/cortex/api/iqe-query", json={"question": "audit"})

    assert resp.status_code == 500
    assert conn.closed is True


# ---------------------------------------------------------------------------
# Bounded result set
# ---------------------------------------------------------------------------

def test_results_are_bounded_and_say_so(make_client, monkeypatch):
    from tools.cortex.constants import IQE_MAX_ROWS
    import tools.iqe.executor as executor

    rows = [{"id": i} for i in range(IQE_MAX_ROWS + 25)]
    monkeypatch.setattr(executor, "execute_query", lambda ast, c: list(rows))

    body = make_client(_sec_ctx_object("tenant-a")).post(
        "/cortex/api/iqe-query", json={"question": "everything"}).get_json()

    assert len(body["results"]) == IQE_MAX_ROWS
    assert body["row_count"] == IQE_MAX_ROWS
    assert body["truncated"] is True
    assert body["max_rows"] == IQE_MAX_ROWS


def test_a_small_result_set_is_not_reported_truncated(make_client, monkeypatch):
    from tools.cortex.constants import IQE_MAX_ROWS
    import tools.iqe.executor as executor

    monkeypatch.setattr(executor, "execute_query", lambda ast, c: [{"id": 1}])

    body = make_client(_sec_ctx_object("tenant-a")).post(
        "/cortex/api/iqe-query", json={"question": "one"}).get_json()

    assert body["row_count"] == 1
    assert body["truncated"] is False
    assert body["max_rows"] == IQE_MAX_ROWS


def test_missing_question_still_400s(make_client):
    resp = make_client(_sec_ctx_object("tenant-a")).post("/cortex/api/iqe-query", json={})
    assert resp.status_code == 400


def test_unparseable_translation_returns_empty_not_500(make_client, monkeypatch):
    import tools.iqe.nl_to_iqe as nl_module

    monkeypatch.setattr(nl_module, "nl_to_iqe",
                        lambda q, c: {"iqe": "this is not IQE(((", "explanation": ""})

    resp = make_client(_sec_ctx_object("tenant-a")).post(
        "/cortex/api/iqe-query", json={"question": "?"})

    assert resp.status_code == 200
    assert resp.get_json()["results"] == []


# ---------------------------------------------------------------------------
# The context reader underneath (both shapes the dashboard sets)
# ---------------------------------------------------------------------------

def test_security_context_reader_handles_both_g_shapes():
    from flask import Flask, g

    from tools.cortex.blueprint import _security_context

    app = Flask(__name__)
    with app.test_request_context("/"):
        g.security_context = {"tenant_id": "t-dict", "classification": "SECRET"}
        assert _security_context() == ("t-dict", "SECRET")
    with app.test_request_context("/"):
        g.security_context = _sec_ctx_object("t-obj", "TOP SECRET")
        # The dataclass shape used to raise AttributeError on .get() and fall
        # through to ("default", "CUI").
        assert _security_context() == ("t-obj", "TOP SECRET")
    with app.test_request_context("/"):
        assert _security_context() == ("default", "CUI")
