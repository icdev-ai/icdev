"""dcpr-fix-05 connection sweep.

Canvas tables (dd_*, data_designs) have no classification/tenant_id columns, so
they must be reached through get_canvas_connection() (which skips the global RLS
predicate) rather than get_connection() (which raises UndefinedColumn).

These tests assert that:
  * anomaly_detector._get_conn() calls get_canvas_connection()
  * mcp_scanner.scan_design_id() calls get_canvas_connection()
  * neither module still imports get_connection for canvas access.

Patching is shim-aware (importlib + setattr on tools.db.storage).
"""

import importlib
import inspect


class _FakeCursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """Minimal StorageConnection stand-in that records executed SQL."""

    def __init__(self, row=None):
        self.row = row
        self.executed = []
        self.committed = False
        self.closed = False

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return _FakeCursor(self.row)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def _patch_canvas_conn(monkeypatch, fake_conn):
    """Patch get_canvas_connection on the storage module the callees import from."""
    storage = importlib.import_module("tools.db.storage")
    calls = {"n": 0}

    def _factory(*_args, **_kwargs):
        calls["n"] += 1
        return fake_conn

    monkeypatch.setattr(storage, "get_canvas_connection", _factory, raising=True)
    return calls


def test_anomaly_detector_uses_canvas_connection(monkeypatch):
    mod = importlib.import_module("tools.data_canvas.anomaly_detector")
    fake = _FakeConn(row=None)
    calls = _patch_canvas_conn(monkeypatch, fake)

    # save_anomaly_run exercises _get_conn() -> get_canvas_connection()
    run_id = mod.save_anomaly_run({"overall_risk": "low"}, profile_id="p1")

    assert calls["n"] >= 1, "get_canvas_connection was not called"
    assert run_id  # returns the inserted run_id
    assert fake.committed is True
    assert fake.closed is True
    # Wrote to the canvas dd_ table
    assert any("dd_anomaly_runs" in sql for sql, _ in fake.executed)


def test_anomaly_get_conn_returns_canvas_connection(monkeypatch):
    mod = importlib.import_module("tools.data_canvas.anomaly_detector")
    fake = _FakeConn()
    calls = _patch_canvas_conn(monkeypatch, fake)

    conn = mod._get_conn()
    assert conn is fake
    assert calls["n"] == 1


def test_mcp_scanner_uses_canvas_connection(monkeypatch):
    mod = importlib.import_module("tools.data_canvas.mcp_scanner")
    # Row is None -> scan_design_id returns "not found" but still opened a conn.
    fake = _FakeConn(row=None)
    calls = _patch_canvas_conn(monkeypatch, fake)

    result = mod.scan_design_id("some-design-id")

    assert calls["n"] >= 1, "get_canvas_connection was not called"
    assert fake.closed is True
    assert "error" in result  # design not found, but path exercised the canvas conn
    # design_id is parameterized (not string-formatted into the SQL)
    assert any(
        "data_designs" in sql and params == ("some-design-id",)
        for sql, params in fake.executed
    )


def test_no_get_connection_import_for_canvas_access():
    """Neither module should import the RLS-attaching get_connection anymore."""
    for name in (
        "tools.data_canvas.anomaly_detector",
        "tools.data_canvas.mcp_scanner",
    ):
        mod = importlib.import_module(name)
        src = inspect.getsource(mod)
        assert "get_canvas_connection" in src, f"{name} should use get_canvas_connection"
        assert "import get_connection" not in src, (
            f"{name} still imports get_connection (RLS predicate breaks canvas tables)"
        )
