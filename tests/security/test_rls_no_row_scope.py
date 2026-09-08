# CUI // SP-CTI
"""A statement that addresses NO ROWS must never have a WHERE bolted onto it.

THE DEFECT, MEASURED on the live PostgreSQL board 2026-09-08 (dwr-ws-03).
------------------------------------------------------------------------
``tools/audit/chain.py`` opens its column probe with a SAVEPOINT so that a probe
failure cannot poison the caller's transaction. Inside a Flask request a
security context is attached to the connection, and ``inject_row_predicate`` --
whose final branch reads "SELECT (and anything else)" -- produced::

    SAVEPOINT icdev_audit_chain_probe WHERE (classification IS NULL OR ...)

which is a syntax error. On PostgreSQL a failed statement ABORTS the whole
transaction, so every statement after it raised ``InFailedSqlTransaction``: the
chain probe reported "no chain columns" on a database that has them, and the
``INSERT INTO audit_trail`` that followed could not run either. **The
containment written to make a probe failure harmless was itself the statement
that got corrupted.**

The consequence is the one this class of defect always has -- a swallowed
failure reading as a clean answer. ``log_event``'s default path is best-effort,
so an audit row written from inside an authenticated request was DROPPED with
no error anywhere. It surfaced only on the fail-closed path: cef-ui-03's
``_record_hitl_decision``, where the DIC accept door answered
``500 decision_not_audited``, which is what this card hit. CORROBORATION rather
than inference: ``audit_trail`` held ZERO ``dic.hitl_decision`` rows lifetime on
2026-09-08, although that audit door shipped in August and
``dic_suggestion_decisions`` held rows -- decisions were being recorded and
never once audited.

THIS IS THE THIRD INSTANCE OF ONE DEFECT, and the first two each got a predicate:
  * ``SELECT 1`` on /api/health          -> ``_is_tableless_select``
  * ``SELECT 1 FROM pg_extension``       -> ``_is_system_table``
Neither can see this shape, because the statement names no relation for a
reason neither anticipated: it operates on the TRANSACTION, not on data.

WHAT THIS TEST REFUSES, IN BOTH DIRECTIONS. A guard that skips injection is a
privilege escalation if it is too wide, so the negative half -- that SELECT,
UPDATE, DELETE and INSERT are still filtered -- is not decoration. A test that
only proved the SAVEPOINT is left alone would also pass for a guard that had
stopped filtering everything.
"""
from __future__ import annotations

import pytest

from tools.security.row_security import inject_row_predicate

#: What a real request supplies. `classifications` is the Bell-LaPadula
#: read-down set the injector builds from the caller's clearance.
CTX = {
    "tenant_id": "default",
    "classifications": {"CUI", "UNCLASSIFIED"},
}


def _inject(sql: str):
    return inject_row_predicate(sql, placeholder="%s", **CTX)


# ── The measured statement ────────────────────────────────────────────────────

def test_the_audit_chain_savepoint_is_left_alone():
    """The exact statement that aborted the transaction on the live board."""
    sql = "SAVEPOINT icdev_audit_chain_probe"
    new_sql, extra, _ = _inject(sql)
    assert new_sql == sql, (
        "the audit chain's SAVEPOINT was rewritten -- this is the defect: a "
        "WHERE clause on a SAVEPOINT is a syntax error, and on PostgreSQL that "
        "aborts the transaction and drops the audit row that follows"
    )
    assert extra == ()


@pytest.mark.parametrize("sql", [
    "SAVEPOINT icdev_audit_chain_probe",
    "RELEASE SAVEPOINT icdev_audit_chain_probe",
    "ROLLBACK TO SAVEPOINT icdev_audit_chain_probe",
])
def test_every_statement_the_containment_issues_is_left_alone(sql):
    """All three, because containment is only sound if the whole trio survives.

    Corrupting the RELEASE leaves a savepoint standing; corrupting the ROLLBACK
    TO leaves the caller's transaction aborted after a failure that was supposed
    to be contained. Testing only the one that was observed would leave the
    other two one edit away.
    """
    new_sql, extra, _ = _inject(sql)
    assert new_sql == sql
    assert extra == ()


# ── The class the same rule closes ────────────────────────────────────────────

@pytest.mark.parametrize("sql", [
    # Transaction control
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "START TRANSACTION",
    "END",
    # Session control -- `set_pg_session_vars` runs one of these on every
    # connection that carries a context.
    "SET search_path TO public",
    "RESET ALL",
    "DISCARD ALL",
    # Locking / maintenance
    "LOCK TABLE audit_trail IN SHARE MODE",
    "VACUUM ANALYZE audit_trail",
    # DDL. The pre-existing guard's comment already says "INSERT, DDL, PRAGMA:
    # never modify -- these have no WHERE clause and injecting anything would
    # corrupt the statement structure", and then checked only CREATE.
    "ALTER TABLE dic_sections ADD COLUMN probe TEXT",
    "DROP TABLE IF EXISTS scratch",
    "TRUNCATE TABLE scratch",
    "CREATE TABLE IF NOT EXISTS scratch (id INTEGER)",
    "PRAGMA table_info(audit_trail)",
])
def test_a_statement_with_no_row_scope_is_never_rewritten(sql):
    new_sql, extra, _ = _inject(sql)
    assert new_sql == sql, f"{sql!r} was rewritten; it addresses no rows"
    assert extra == ()


# ── The half that keeps the guard from becoming a hole ────────────────────────

@pytest.mark.parametrize("sql", [
    "SELECT * FROM dic_sections",
    "SELECT content FROM dic_sections WHERE section_id = %s",
    "UPDATE dic_sections SET content = %s WHERE section_id = %s",
    "DELETE FROM dic_suggestions WHERE suggestion_id = %s",
])
def test_a_statement_that_reads_or_writes_rows_is_still_filtered(sql):
    """Skipping a statement that CAN reach rows is a privilege escalation.

    This is why the guard is an explicit list of leading keywords and not
    anything inferred from the statement's shape.
    """
    new_sql, extra, _ = _inject(sql)
    assert new_sql != sql, f"{sql!r} lost its row predicate"
    assert "classification" in new_sql
    assert "default" in extra


def test_select_one_is_still_handled_by_its_own_predicate():
    """`SELECT 1` is NOT in the no-row-scope set, and must not need to be.

    It is caught earlier by `_is_tableless_select` (qa-fail-6a87916931be3793).
    Asserting the OUTCOME here rather than which predicate produced it keeps the
    two guards independent: adding SELECT to the keyword set would exempt every
    read in the platform.
    """
    from tools.security.row_security import _has_no_row_scope

    assert not _has_no_row_scope("SELECT 1"), (
        "SELECT must never be in the no-row-scope set -- it would exempt every "
        "read in the platform from row security"
    )
    new_sql, extra, _ = _inject("SELECT 1")
    assert new_sql == "SELECT 1"
    assert extra == ()


def test_explain_is_not_exempt():
    """EXPLAIN wraps a real query, and that query must still be filtered."""
    from tools.security.row_security import _has_no_row_scope

    assert not _has_no_row_scope("EXPLAIN SELECT * FROM dic_sections")


def test_the_guard_reads_the_leading_keyword_case_insensitively():
    from tools.security.row_security import _has_no_row_scope

    assert _has_no_row_scope("savepoint x")
    assert _has_no_row_scope("  \n  SavePoint x  ")
    assert _has_no_row_scope("COMMIT;")


def test_the_two_copies_of_the_guard_agree():
    """`tools/` and the `icdev/` mirror must carry the same rule.

    They are ONE module object in a source checkout (xit-decl-02), but the
    packaged copy is what a wheel installs, and a row-security rule that differs
    between them is a security difference nobody would look for.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    a = root / "tools" / "security" / "row_security.py"
    b = root / "icdev" / "tools" / "security" / "row_security.py"
    # ASSERTED, never skipped. A `pytest.skip` on a missing mirror would make
    # this test pass on exactly the tree where the mirror had been deleted --
    # a skipped test satisfies the coverage claim while asserting nothing.
    assert b.exists(), f"the icdev/ mirror is missing: {b}"
    assert a.read_bytes() == b.read_bytes(), (
        "tools/security/row_security.py and its icdev/ mirror have drifted"
    )
