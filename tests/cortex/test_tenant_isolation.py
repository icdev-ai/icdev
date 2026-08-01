# CUI // SP-CTI
"""Cross-tenant isolation tests for the Cortex analyst data path (ctx-expose-03).

Two layers:

1. ``test_cortex_tenant_isolation_on_postgresql`` — the acceptance proof. RLS
   predicates + Bell-LaPadula read-down only exist on PostgreSQL, so under the
   SQLite-forced repo conftest this SKIPS cleanly (it never passes vacuously).
   Exercise the live PG proof one of two ways:

     * standalone:  ICDEV_STORAGE_BACKEND=postgresql \
                        python tools/cortex/db/verify_tenant_isolation.py --json
     * this test:   CORTEX_ISOLATION_PG=1 pytest \
                        tests/cortex/test_tenant_isolation.py -q

   ``pytest tests/cortex/test_tenant_isolation.py -q`` ALONE (SQLite conftest)
   SKIPS the PG test and is NOT sufficient acceptance.

2. The ``_execute_nlq_readonly`` unit tests run under SQLite and give CI
   regression coverage of the ctx-expose-03 fix itself: the NLQ fallback now
   threads the caller's tenant_id + classification into its execution
   connection instead of the dashboard's context-free ``execute_safely``.
"""
from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# 1. Live PostgreSQL cross-tenant leakage proof (skips under SQLite conftest)
# ---------------------------------------------------------------------------
@pytest.fixture
def _pg_backend():
    """Opt the process into the postgresql backend when CORTEX_ISOLATION_PG=1.

    The repo conftest force-sets ICDEV_STORAGE_BACKEND=sqlite; get_connection
    reads that env var live, so flipping it here (and restoring after) routes
    the verifier at the live PG instance without touching conftest.
    """
    if os.environ.get("CORTEX_ISOLATION_PG") != "1":
        yield
        return
    saved = os.environ.get("ICDEV_STORAGE_BACKEND")
    os.environ["ICDEV_STORAGE_BACKEND"] = "postgresql"
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("ICDEV_STORAGE_BACKEND", None)
        else:
            os.environ["ICDEV_STORAGE_BACKEND"] = saved


def test_cortex_tenant_isolation_on_postgresql(_pg_backend):
    """Tenant A cannot retrieve tenant B's rows through the analyst entry points."""
    from tools.cortex.db.verify_tenant_isolation import run_verification

    report = run_verification()
    if report["status"] == "skipped":
        pytest.skip(report["reason"])
    assert report["status"] == "passed", report

    names = {c["name"] for c in report["checks"]}
    # connection primitive + both live analyst entry-point paths (IQE + NLQ).
    assert {"connection", "analyst_iqe", "analyst_nlq"} <= names, names
    for check in report["checks"]:
        assert check["passed"], f"{check['name']} leaked: {check['failures']}"


# ---------------------------------------------------------------------------
# 2. SQLite-runnable regression coverage of the ctx-expose-03 fix
# ---------------------------------------------------------------------------
class _StubCursor:
    def __init__(self, rows):
        self._rows = rows
        self.description = [("tenant_id",), ("content",)]

    def fetchmany(self, n):
        return list(self._rows)

    def fetchone(self):
        return None  # no overflow row -> truncated=False


class _StubConn:
    """Records the applied security context; serves canned rows."""

    _backend = "sqlite"

    def __init__(self, rows):
        self._rows = rows
        self.security_context = None
        self.closed = False
        self.executed = []

    def set_security_context(self, ctx):
        self.security_context = ctx

    def execute(self, sql, params=None):
        self.executed.append(sql)
        if sql.startswith("PRAGMA"):
            return _StubCursor([])
        return _StubCursor(self._rows)

    def close(self):
        self.closed = True


def test_execute_nlq_readonly_threads_security_context(monkeypatch):
    from tools.cortex import analyst
    from tools.cortex.schemas import CortexContext

    stub = _StubConn([{"tenant_id": "t-a", "content": "row"}])
    monkeypatch.setattr(analyst, "_open_connection", lambda: stub)

    ctx = CortexContext(tenant_id="t-a", classification="SECRET", user_id="u1")
    out = analyst._execute_nlq_readonly("SELECT tenant_id, content FROM x", ctx)

    # The caller's tenant/classification are threaded into the connection so
    # RLS predicate injection filters the read (the isolation control).
    assert stub.security_context is not None
    assert stub.security_context.tenant_id == "t-a"
    assert stub.security_context.classification == "SECRET"
    assert stub.security_context.user_id == "u1"
    # Connection is always closed, and rows are returned in the execute_safely shape.
    assert stub.closed is True
    assert out["rows"] == [{"tenant_id": "t-a", "content": "row"}]
    assert out["row_count"] == 1
    assert out["truncated"] is False


def test_execute_nlq_readonly_skips_pragma_on_postgres(monkeypatch):
    from tools.cortex import analyst
    from tools.cortex.schemas import CortexContext

    stub = _StubConn([])
    stub._backend = "postgresql"  # PRAGMA would abort a PG transaction
    monkeypatch.setattr(analyst, "_open_connection", lambda: stub)

    analyst._execute_nlq_readonly("SELECT 1", CortexContext(tenant_id="t"))

    assert not any(s.startswith("PRAGMA") for s in stub.executed)


class _NoSecCtxConn:
    """A connection that CANNOT carry a security context (no setter)."""

    _backend = "sqlite"

    def __init__(self, rows=None):
        self._rows = rows or []
        self.closed = False

    def execute(self, sql, params=None):
        return _StubCursor(self._rows)

    def close(self):
        self.closed = True


def test_apply_security_context_raises_when_unscopable_and_fail_closed():
    from tools.cortex.analyst import CortexAnalystError, _apply_security_context
    from tools.cortex.schemas import CortexContext

    # A connection that can't carry the RLS context must NOT silently run
    # unscoped when the caller demanded fail-closed.
    with pytest.raises(CortexAnalystError, match="security context"):
        _apply_security_context(_NoSecCtxConn(), CortexContext(tenant_id="t-a", fail_closed=True))


def test_apply_security_context_warns_but_continues_when_unscopable_default(monkeypatch):
    from tools.cortex import analyst
    from tools.cortex.schemas import CortexContext

    # Default (fail-open) posture: warn, don't crash — but the warning makes the
    # unscoped query observable instead of silently swallowed. Capture on the
    # module logger directly (icdev_logger does not propagate to caplog's root).
    warnings = []
    monkeypatch.setattr(
        analyst.logger, "warning",
        lambda msg, *args, **kw: warnings.append(msg % args if args else msg),
    )
    analyst._apply_security_context(_NoSecCtxConn(), CortexContext(tenant_id="t-a"))
    assert any("security context not applied" in w for w in warnings)


def test_apply_security_context_applies_when_supported():
    from tools.cortex.analyst import _apply_security_context
    from tools.cortex.schemas import CortexContext

    stub = _StubConn([])
    _apply_security_context(stub, CortexContext(tenant_id="t-b", classification="SECRET"))
    assert stub.security_context is not None
    assert stub.security_context.tenant_id == "t-b"
    assert stub.security_context.classification == "SECRET"


def test_build_security_context_maps_fields_and_blanks_tenant():
    from tools.cortex.analyst import _build_security_context
    from tools.cortex.schemas import CortexContext

    sec = _build_security_context(
        CortexContext(tenant_id="t-9", user_id="u", classification="SECRET")
    )
    assert sec.tenant_id == "t-9"
    assert sec.user_id == "u"
    assert sec.classification == "SECRET"

    # Empty tenant_id maps to None (unscoped), not the literal "".
    blank = _build_security_context(CortexContext())
    assert blank.tenant_id is None
    assert blank.classification == "CUI"
