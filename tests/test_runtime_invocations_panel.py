# CUI // SP-CTI
"""Tests for the Runtime Performance panel — /api/runtime-invocations/summary.

Uses a real SQLite database rather than a mocked connection. The value of this
endpoint is that it aggregates a live table correctly and reports honestly when
it cannot; a mock would assert the shape of the call instead of the truth of the
answer, and the two failures this guards against (an injected RLS predicate, an
absent table) are both invisible to a mock.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest
from flask import Flask

from tools.dashboard.api import runtime_invocations as api
from tools.observability import invocation_recorder

#: `import tools.db.storage as storage` resolves to the *icdev* module object via
#: the backward-compat shim, while `from tools.db.storage import get_connection`
#: — what the code under test writes — reads sys.modules['tools.db.storage'].
#: They are two different objects, so patching the first would silently not take.
_storage = importlib.import_module("tools.db.storage")


def _connect(path) -> "object":
    """A REAL StorageConnection over a throwaway SQLite file.

    Not a hand-rolled double: the rollup emits PostgreSQL-style ``%s``, and it
    is ``StorageConnection`` that translates it. A raw sqlite3 connection would
    raise ``near "%": syntax error`` — which ``summary()`` swallows — so a test
    built on one would assert its own no-op. Using the real wrapper also means
    ``set_security_context`` and ``close`` are the real implementations the
    endpoint depends on.
    """
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    return _storage.StorageConnection(raw, "sqlite")


_DDL = (
    "CREATE TABLE runtime_invocations ("
    " id TEXT PRIMARY KEY, surface TEXT NOT NULL, name TEXT NOT NULL,"
    " session_id TEXT, project_id TEXT, parent_id TEXT, started_at TEXT NOT NULL,"
    " completed_at TEXT, duration_ms INTEGER, status TEXT NOT NULL DEFAULT 'running',"
    " error_class TEXT, error_message TEXT, arg_keys TEXT,"
    " classification TEXT DEFAULT 'CUI', created_at TEXT)"
)

#: (id, surface, name, duration_ms, status)
_ROWS = [
    ("i1", "mcp", "rag_search", 120, "ok"),
    ("i2", "mcp", "rag_search", 80, "error"),
    ("i3", "mcp", "kanban_list_tasks", 10, "ok"),
    ("i4", "agent", "builder", 5000, "ok"),
    # No duration: still `running`, so avg/max come back NULL for this group.
    ("i5", "persona", "analyst", None, "running"),
]


def _seed(path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(_DDL)
    for inv_id, surface, name, duration, status in _ROWS:
        conn.execute(
            "INSERT INTO runtime_invocations (id, surface, name, started_at,"
            " duration_ms, status) VALUES (?, ?, ?, ?, ?, ?)",
            (inv_id, surface, name, "2026-08-07T12:00:00+00:00", duration, status),
        )
    conn.commit()
    conn.close()


@pytest.fixture()
def seeded(tmp_path):
    path = tmp_path / "runtime.db"
    _seed(path)
    return path


def _mount(path, monkeypatch):
    """Test client whose real ``_get_db`` opens *path*.

    ``get_connection`` is patched rather than ``_get_db`` so the production
    code path — including the security-context detach and the close — runs for
    real. Patching ``_get_db`` would skip the very line this endpoint depends on.
    Returns (client, opened) where *opened* collects every connection handed out.
    """
    opened = []

    def _fake_get_connection(*_a, **_kw):
        conn = _connect(path)
        opened.append(conn)
        return conn

    monkeypatch.setattr(_storage, "get_connection", _fake_get_connection)
    app = Flask(__name__)
    app.register_blueprint(api.runtime_invocations_api,
                           url_prefix="/api/runtime-invocations")
    return app.test_client(), opened


@pytest.fixture()
def client(seeded, monkeypatch):
    """Flask test client with the blueprint mounted at its legacy prefix."""
    return _mount(seeded, monkeypatch)[0]


@pytest.fixture()
def empty_client(tmp_path, monkeypatch):
    """Same, but pointed at a database where the migration never ran."""
    path = tmp_path / "unmigrated.db"
    sqlite3.connect(str(path)).close()
    return _mount(path, monkeypatch)[0]


def _rows_by_name(payload):
    return {r["name"]: r for r in payload["rows"]}


# ------------------------------------------------------- summary(conn=...)

def test_summary_reads_through_a_passed_connection(seeded):
    """The conn parameter is what lets the panel escape RLS injection."""
    conn = _connect(seeded)
    rows = invocation_recorder.summary(conn=conn)
    assert {r["name"] for r in rows} == {
        "rag_search", "kanban_list_tasks", "builder", "analyst"}


def test_summary_does_not_close_a_connection_it_did_not_open(seeded):
    """The caller owns what it passes — closing it would break the next read."""
    conn = _connect(seeded)
    invocation_recorder.summary(conn=conn)
    assert conn._closed is False
    # Still usable, which is the property that actually matters.
    assert invocation_recorder.summary(conn=conn, surface="mcp")


def test_summary_surface_filter_applies_to_a_passed_connection(seeded):
    conn = _connect(seeded)
    rows = invocation_recorder.summary(conn=conn, surface="agent")
    assert [r["name"] for r in rows] == ["builder"]


def test_summary_limit_bounds_names_not_invocations(seeded):
    conn = _connect(seeded)
    rows = invocation_recorder.summary(conn=conn, limit=1)
    # rag_search is the busiest name (2 calls), so it is the one that survives.
    assert [r["name"] for r in rows] == ["rag_search"]
    assert rows[0]["calls"] == 2


# ------------------------------------------------------------ the endpoint

def test_summary_endpoint_aggregates_calls_and_errors(client):
    payload = client.get("/api/runtime-invocations/summary").get_json()
    assert payload["ok"] is True
    assert payload["available"] is True
    rows = _rows_by_name(payload)
    assert rows["rag_search"]["calls"] == 2
    assert rows["rag_search"]["errors"] == 1
    assert rows["rag_search"]["avg_ms"] == 100.0
    assert rows["rag_search"]["max_ms"] == 120
    assert rows["kanban_list_tasks"]["errors"] == 0


def test_summary_endpoint_reports_every_surface(client):
    """All four surfaces breakdown — the acceptance criterion for the panel."""
    payload = client.get("/api/runtime-invocations/summary").get_json()
    assert {r["surface"] for r in payload["rows"]} == {"mcp", "agent", "persona"}
    # `role` recorded nothing and must still appear: a silent surface is the
    # finding, not an absence.
    assert set(payload["by_surface"]) == set(invocation_recorder.SURFACES)
    assert payload["by_surface"]["role"] == {"names": 0, "calls": 0, "errors": 0}
    assert payload["by_surface"]["mcp"]["calls"] == 3
    assert payload["by_surface"]["mcp"]["errors"] == 1
    assert payload["by_surface"]["agent"]["names"] == 1


def test_summary_endpoint_totals_match_the_returned_rows(client):
    payload = client.get("/api/runtime-invocations/summary").get_json()
    assert payload["totals"] == {"names": 4, "calls": 5, "errors": 1}
    assert payload["truncated"] is False


def test_summary_endpoint_computes_error_rate(client):
    rows = _rows_by_name(client.get("/api/runtime-invocations/summary").get_json())
    assert rows["rag_search"]["error_rate"] == 0.5
    assert rows["builder"]["error_rate"] == 0.0


def test_summary_endpoint_keeps_null_durations_null(client):
    """A group with no completed call has no average — not an average of zero."""
    rows = _rows_by_name(client.get("/api/runtime-invocations/summary").get_json())
    assert rows["analyst"]["avg_ms"] is None
    assert rows["analyst"]["max_ms"] is None
    assert rows["analyst"]["calls"] == 1


def test_summary_endpoint_filters_by_surface(client):
    payload = client.get("/api/runtime-invocations/summary?surface=mcp").get_json()
    assert payload["surface"] == "mcp"
    assert {r["surface"] for r in payload["rows"]} == {"mcp"}


def test_summary_endpoint_rejects_an_unknown_surface(client):
    """A typo must not silently answer the all-surfaces question instead."""
    response = client.get("/api/runtime-invocations/summary?surface=tools")
    assert response.status_code == 400
    assert "unknown surface" in response.get_json()["error"]


def test_summary_endpoint_flags_truncation(client):
    payload = client.get("/api/runtime-invocations/summary?limit=2").get_json()
    assert len(payload["rows"]) == 2
    assert payload["truncated"] is True


@pytest.mark.parametrize("raw,expected", [
    ("0", 1), ("-5", 1), ("99999", api._MAX_LIMIT),
    ("", api._DEFAULT_LIMIT), ("abc", api._DEFAULT_LIMIT), (None, api._DEFAULT_LIMIT),
])
def test_limit_is_clamped(raw, expected):
    assert api._parse_limit(raw) == expected


def test_summary_endpoint_distinguishes_absent_table_from_idle_runtime(empty_client):
    """The failure this panel exists to avoid: an empty table that is a lie."""
    payload = empty_client.get("/api/runtime-invocations/summary").get_json()
    assert payload["ok"] is True
    assert payload["available"] is False
    assert payload["rows"] == []
    assert payload["backend"]


def test_summary_endpoint_detaches_the_row_security_context(seeded, monkeypatch):
    """runtime_invocations has no tenant_id; an injected predicate makes the
    rollup fail, and summary() renders that failure as an empty table."""
    client, opened = _mount(seeded, monkeypatch)
    client.get("/api/runtime-invocations/summary")

    assert opened, "the endpoint never opened a connection"
    assert opened[0]._security_context is None
    assert opened[0]._closed is True, "the endpoint leaked its connection"


# `_get_db`'s `except AttributeError` — for a connection with no
# set_security_context — is deliberately NOT covered here. Exercising it means
# patching the connection factory to an object that is not a translating
# connection, which is the exact shape `check_test_db_isolation` exists to
# reject, and earning a green test by teaching a gate to ignore its own smell is
# a worse trade than leaving two defensive lines uncovered.


# -------------------------------------------------------- registration

def test_blueprint_is_declared_in_the_api_registry():
    """ALL_BLUEPRINTS is what tooling enumerates; an unlisted mount is invisible."""
    from tools.dashboard.api import ALL_BLUEPRINTS

    assert ("runtime_invocations_api", "/api/v1/runtime-invocations", False) \
        in ALL_BLUEPRINTS


def test_register_api_blueprints_mounts_both_prefixes():
    """The panel fetches /api/runtime-invocations/summary by hardcoded path.

    Registration is therefore load-bearing for the UI, not just for the API —
    if the mount silently moves, the panel renders "could not load" and nothing
    else fails. Asserts the legacy alias as well as the /api/v1 path.
    """
    from tools.dashboard.api import register_api_blueprints

    app = Flask(__name__)
    register_api_blueprints(app)
    rules = {str(r) for r in app.url_map.iter_rules()}
    assert "/api/runtime-invocations/summary" in rules
    assert "/api/v1/runtime-invocations/summary" in rules


# --------------------------------------------------------------- helpers

def test_by_surface_counts_only_the_returned_rows():
    rows = [
        {"surface": "mcp", "name": "a", "calls": 3, "errors": 1},
        {"surface": "mcp", "name": "b", "calls": 1, "errors": 0},
    ]
    out = api._by_surface(rows)
    assert out["mcp"] == {"names": 2, "calls": 4, "errors": 1}
    assert out["agent"]["calls"] == 0


def test_by_surface_keeps_an_unrecognised_surface_rather_than_dropping_it():
    """A surface added to the table before SURFACES catches up is still data."""
    out = api._by_surface([{"surface": "future", "name": "x", "calls": 2, "errors": 0}])
    assert out["future"]["calls"] == 2


def test_decorate_error_rate_is_safe_at_zero_calls():
    assert api._decorate([{"calls": 0, "errors": 0}])[0]["error_rate"] == 0.0
