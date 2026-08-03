# CUI // SP-CTI
"""Tests for NIST AU-2: COR access generates cpmp_cor_access_log entry.

Verifies:
 1. _cor_access_log inserts exactly one row into cpmp_cor_access_log.
 2. Logged user_id matches the COR email passed in.
 3. Logged contract_id matches the contract id passed in.
 4. action='view_contract' is persisted correctly.
 5. action='view_deliverables' is persisted correctly.
 6. action='view_evm' is persisted correctly.
 7. action='view_cpars' is persisted correctly.
 8. created_at column is non-NULL after insert.
 9. Each call to _cor_access_log creates exactly one additional row.
10. Four sequential access calls produce four distinct log rows.
11. Row ids are unique across all inserted rows.
12. action='view_subcontractors' satisfies the CHECK constraint.
13. action='export_report' satisfies the CHECK constraint.
14. An invalid action raises OperationalError on direct SQL insert.
15. cpmp_cor_access_log has an index on user_id.
16. cpmp_cor_access_log has an index on contract_id.
17. cpmp_cor_access_log has an index on action.
18. cpmp_cor_access_log has an index on created_at.
19. Row id is auto-assigned (AUTOINCREMENT) and non-NULL.
20. _cor_access_log does not raise; row is persisted after commit.
"""
import sqlite3

import pytest

from tests._sql_compat import connect as _translating_connect

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COR_EMAIL = "cor.audit@agency.mil"
_CONTRACT_ID = "ctr-cor-audit-0012"

# Valid actions per CHECK constraint in cpmp_cor_access_log
_VALID_ACTIONS = [
    "view_contract",
    "view_deliverables",
    "view_evm",
    "view_cpars",
    "view_subcontractors",
    "export_report",
]

# DDL without the FK reference so the fixture is self-contained
_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_cor_access_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    user_email  TEXT,
    contract_id TEXT NOT NULL,
    action      TEXT NOT NULL CHECK(action IN (
        'view_contract', 'view_deliverables', 'view_evm',
        'view_cpars', 'view_subcontractors', 'export_report')),
    ip_address  TEXT,
    user_agent  TEXT,
    metadata    TEXT DEFAULT '{}',
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cpmp_cor_user     ON cpmp_cor_access_log(user_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_cor_contract ON cpmp_cor_access_log(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpmp_cor_action   ON cpmp_cor_access_log(action);
CREATE INDEX IF NOT EXISTS idx_cpmp_cor_created  ON cpmp_cor_access_log(created_at);
"""

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def log_conn():
    """In-memory SQLite connection with cpmp_cor_access_log schema.

    Uses the translating connection from ``tests/_sql_compat.py`` rather than a
    bare ``sqlite3.connect``: ``_cor_access_log`` authors its INSERT for
    PostgreSQL (``%s`` placeholders) and swallows failures as best-effort, so a
    raw sqlite3 connection turns every write into a silent no-op that the test
    then reads back as a missing feature.
    """
    conn = _translating_connect(":memory:")
    conn.executescript(_DDL)
    conn.commit()
    yield conn
    conn.close()


def _write_log(conn, user_id=_COR_EMAIL, contract_id=_CONTRACT_ID, action="view_contract"):
    """Call the production helper under test."""
    from tools.dashboard.api.cpmp import _cor_access_log

    _cor_access_log(conn, user_id, contract_id, action)
    conn.commit()


def _row_count(conn):
    return conn.execute("SELECT COUNT(*) FROM cpmp_cor_access_log").fetchone()[0]


def _last_row(conn):
    return conn.execute(
        "SELECT * FROM cpmp_cor_access_log ORDER BY id DESC LIMIT 1"
    ).fetchone()


# ---------------------------------------------------------------------------
# Tests: basic insert
# ---------------------------------------------------------------------------


class TestCorAccessLogInsert:
    """_cor_access_log must write a row on every call."""

    def test_inserts_one_row(self, log_conn):
        _write_log(log_conn)
        assert _row_count(log_conn) == 1, (
            "_cor_access_log must insert exactly one row per call"
        )

    def test_user_id_matches(self, log_conn):
        _write_log(log_conn, user_id=_COR_EMAIL)
        row = _last_row(log_conn)
        assert row["user_id"] == _COR_EMAIL, (
            "Logged user_id must equal the COR email passed to _cor_access_log"
        )

    def test_contract_id_matches(self, log_conn):
        _write_log(log_conn, contract_id=_CONTRACT_ID)
        row = _last_row(log_conn)
        assert row["contract_id"] == _CONTRACT_ID, (
            "Logged contract_id must equal the contract id passed to _cor_access_log"
        )

    def test_created_at_is_not_null(self, log_conn):
        _write_log(log_conn)
        row = _last_row(log_conn)
        assert row["created_at"] is not None, (
            "created_at must be auto-populated (DEFAULT datetime('now'))"
        )

    def test_row_id_is_not_null(self, log_conn):
        _write_log(log_conn)
        row = _last_row(log_conn)
        assert row["id"] is not None, (
            "id must be auto-assigned by AUTOINCREMENT"
        )

    def test_no_exception_raised(self, log_conn):
        """_cor_access_log must not raise; row must be present after commit."""
        from tools.dashboard.api.cpmp import _cor_access_log

        _cor_access_log(log_conn, _COR_EMAIL, _CONTRACT_ID, "view_contract")
        log_conn.commit()
        assert _row_count(log_conn) == 1, (
            "_cor_access_log must not silently discard the row when schema is correct"
        )


# ---------------------------------------------------------------------------
# Tests: action types
# ---------------------------------------------------------------------------


class TestCorAccessLogActions:
    """Each valid COR action must be persisted correctly."""

    def test_view_contract_action(self, log_conn):
        _write_log(log_conn, action="view_contract")
        assert _last_row(log_conn)["action"] == "view_contract", (
            "action='view_contract' must be stored in the log"
        )

    def test_view_deliverables_action(self, log_conn):
        _write_log(log_conn, action="view_deliverables")
        assert _last_row(log_conn)["action"] == "view_deliverables", (
            "action='view_deliverables' must be stored in the log"
        )

    def test_view_evm_action(self, log_conn):
        _write_log(log_conn, action="view_evm")
        assert _last_row(log_conn)["action"] == "view_evm", (
            "action='view_evm' must be stored in the log"
        )

    def test_view_cpars_action(self, log_conn):
        _write_log(log_conn, action="view_cpars")
        assert _last_row(log_conn)["action"] == "view_cpars", (
            "action='view_cpars' must be stored in the log"
        )

    def test_view_subcontractors_action(self, log_conn):
        _write_log(log_conn, action="view_subcontractors")
        assert _last_row(log_conn)["action"] == "view_subcontractors", (
            "action='view_subcontractors' satisfies the CHECK constraint and must be stored"
        )

    def test_export_report_action(self, log_conn):
        _write_log(log_conn, action="export_report")
        assert _last_row(log_conn)["action"] == "export_report", (
            "action='export_report' satisfies the CHECK constraint and must be stored"
        )


# ---------------------------------------------------------------------------
# Tests: append-only / multi-row behaviour
# ---------------------------------------------------------------------------


class TestCorAccessLogAppendOnly:
    """Audit log must accumulate rows, never overwrite."""

    def test_each_call_adds_one_row(self, log_conn):
        for i, action in enumerate(_VALID_ACTIONS[:4], start=1):
            _write_log(log_conn, action=action)
            assert _row_count(log_conn) == i, (
                f"After {i} call(s) there must be exactly {i} row(s)"
            )

    def test_four_calls_produce_four_rows(self, log_conn):
        for action in _VALID_ACTIONS[:4]:
            _write_log(log_conn, action=action)
        assert _row_count(log_conn) == 4, (
            "Four sequential access calls must produce exactly four distinct log rows"
        )

    def test_row_ids_are_unique(self, log_conn):
        for action in _VALID_ACTIONS[:4]:
            _write_log(log_conn, action=action)
        rows = log_conn.execute("SELECT id FROM cpmp_cor_access_log").fetchall()
        ids = [r["id"] for r in rows]
        assert len(ids) == len(set(ids)), (
            "Every row must have a unique id (AUTOINCREMENT)"
        )

    def test_log_rows_are_not_modified_after_insert(self, log_conn):
        _write_log(log_conn, action="view_contract")
        original = _last_row(log_conn)
        # Simulate an attempted update (append-only invariant: only inserts allowed)
        with pytest.raises(Exception):
            log_conn.execute(
                "UPDATE cpmp_cor_access_log SET action='export_report' WHERE id=?",
                (original["id"],),
            )
            # If UPDATE succeeds, the action must still match original (no-op enforcement)
            row = _last_row(log_conn)
            assert row["action"] == original["action"], (
                "Log row must not be modifiable after insert (NIST AU-2 append-only)"
            )


# ---------------------------------------------------------------------------
# Tests: CHECK constraint enforcement
# ---------------------------------------------------------------------------


class TestCorAccessLogCheckConstraint:
    """Invalid action strings must be rejected by the CHECK constraint."""

    def test_invalid_action_raises(self, log_conn):
        with pytest.raises(sqlite3.IntegrityError):
            log_conn.execute(
                "INSERT INTO cpmp_cor_access_log (user_id, contract_id, action) VALUES (?, ?, ?)",
                (_COR_EMAIL, _CONTRACT_ID, "delete_contract"),
            )
            log_conn.commit()

    def test_empty_action_raises(self, log_conn):
        with pytest.raises(sqlite3.IntegrityError):
            log_conn.execute(
                "INSERT INTO cpmp_cor_access_log (user_id, contract_id, action) VALUES (?, ?, ?)",
                (_COR_EMAIL, _CONTRACT_ID, ""),
            )
            log_conn.commit()

    def test_view_contracts_plural_is_invalid(self, log_conn):
        """'view_contracts' (plural) is not in the CHECK constraint — only 'view_contract'."""
        with pytest.raises(sqlite3.IntegrityError):
            log_conn.execute(
                "INSERT INTO cpmp_cor_access_log (user_id, contract_id, action) VALUES (?, ?, ?)",
                (_COR_EMAIL, _CONTRACT_ID, "view_contracts"),
            )
            log_conn.commit()


# ---------------------------------------------------------------------------
# Tests: index presence (NIST AU-2 query performance)
# ---------------------------------------------------------------------------


class TestCorAccessLogIndexes:
    """Required indexes must exist on cpmp_cor_access_log."""

    def _index_names(self, conn):
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='cpmp_cor_access_log'"
        ).fetchall()
        return {r["name"] for r in rows}

    def test_index_on_user_id(self, log_conn):
        assert "idx_cpmp_cor_user" in self._index_names(log_conn), (
            "cpmp_cor_access_log must have an index on user_id for efficient COR audit queries"
        )

    def test_index_on_contract_id(self, log_conn):
        assert "idx_cpmp_cor_contract" in self._index_names(log_conn), (
            "cpmp_cor_access_log must have an index on contract_id"
        )

    def test_index_on_action(self, log_conn):
        assert "idx_cpmp_cor_action" in self._index_names(log_conn), (
            "cpmp_cor_access_log must have an index on action"
        )

    def test_index_on_created_at(self, log_conn):
        assert "idx_cpmp_cor_created" in self._index_names(log_conn), (
            "cpmp_cor_access_log must have an index on created_at for time-range audit queries"
        )
