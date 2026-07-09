# CUI // SP-CTI
"""prop-fix-12 — RLS-aware reads for govcon/proposals/cpmp surfaces.

Verifies:
1. tools/dashboard/api/cpmp.py::_get_db no longer clears the security context
   inside a Flask request (the stale ``set_security_context(None)`` bypass is
   gone), so ``_attach_flask_security_context`` wiring takes effect and
   StorageCursor._inject_rls applies tenant_id + classification predicates.
2. Outside a request context the CLI/background bypass still applies.
3. The exact raw-read shape used by the proposals dashboard page
   (``SELECT * FROM proposal_opportunities ORDER BY due_date ASC``) gets a
   WHERE predicate injected before ORDER BY by inject_row_predicate.
"""

import importlib

import pytest

flask = pytest.importorskip("flask")

cpmp_mod = importlib.import_module("tools.dashboard.api.cpmp")
from tools.security.row_security import inject_row_predicate  # noqa: E402
from tools.security.security_context import SecurityContext  # noqa: E402


@pytest.fixture
def _tmp_db(tmp_path, monkeypatch):
    """Point cpmp's module-level DB_PATH at a throwaway SQLite file.

    ICDEV_DB_PATH must point at the SAME file: get_connection() treats a
    db_path that differs from the main DB as a dedicated canvas/aux SQLite
    file and intentionally skips RLS attachment for those.
    """
    db_file = tmp_path / "prop_fix_12.db"
    monkeypatch.setattr(cpmp_mod, "DB_PATH", db_file)
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    return db_file


def test_cpmp_get_db_outside_request_context_bypasses_rls(_tmp_db):
    conn = cpmp_mod._get_db()
    try:
        assert getattr(conn, "_security_context", None) is None
    finally:
        conn.close()


def test_cpmp_get_db_attaches_request_security_context(_tmp_db):
    app = flask.Flask(__name__)
    ctx = SecurityContext(user_id="u1", role="pm", tenant_id="tenant-a", classification="CUI")
    with app.test_request_context("/api/cpmp/contracts"):
        flask.g.security_context = ctx
        conn = cpmp_mod._get_db()
        try:
            assert getattr(conn, "_security_context", None) is ctx, (
                "cpmp _get_db must keep the g.security_context wired by "
                "_attach_flask_security_context — no set_security_context(None) "
                "bypass inside a request context (prop-fix-12)"
            )
        finally:
            conn.close()


def test_cpmp_get_db_no_unconditional_rls_bypass_in_source():
    import inspect

    src = inspect.getsource(cpmp_mod._get_db)
    if "set_security_context(None)" in src:
        assert "has_request_context" in src, (
            "set_security_context(None) in cpmp _get_db must be gated on "
            "not has_request_context()"
        )


def test_proposals_raw_read_gets_predicate_injected_before_order_by():
    sql = "SELECT * FROM proposal_opportunities ORDER BY due_date ASC"
    new_sql, extra, n_before = inject_row_predicate(
        sql,
        tenant_id="tenant-a",
        classifications={"CUI", "UNCLASSIFIED", "PUBLIC"},
        placeholder="%s",
    )
    assert "WHERE" in new_sql
    assert new_sql.index("WHERE") < new_sql.index("ORDER BY")
    assert "tenant_id = %s" in new_sql
    assert "classification IN" in new_sql
    assert extra[0] == "tenant-a"
    assert n_before == 0


def test_cpmp_join_read_qualifies_primary_alias():
    # Shape from /api/cpmp deliverables listing: JOIN — predicate must be
    # qualified with the primary table alias so PG does not raise AmbiguousColumn.
    sql = (
        "SELECT d.*, c.contract_number FROM cpmp_deliverables d "
        "JOIN cpmp_contracts c ON d.contract_id = c.id ORDER BY d.due_date"
    )
    new_sql, extra, _ = inject_row_predicate(
        sql, tenant_id="tenant-a", classifications={"CUI"}, placeholder="%s"
    )
    assert "d.tenant_id = %s" in new_sql
    assert "d.classification" in new_sql
    assert extra[0] == "tenant-a"
