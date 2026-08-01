"""Tests for tools.security.canvas_access — explicit canvas access grants."""
import os
import sys
import sqlite3
from pathlib import Path

import pytest

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# ── fixtures ──────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_access_grants (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    principal_type TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    canvas_name TEXT NOT NULL,
    access_level TEXT NOT NULL DEFAULT 'read',
    granted_by TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    UNIQUE (tenant_id, principal_type, principal_id, canvas_name)
);
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    status TEXT DEFAULT 'active',
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    added_by TEXT,
    added_at TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE IF NOT EXISTS group_roles (
    group_id TEXT NOT NULL,
    role TEXT NOT NULL,
    canvas_scope TEXT,
    granted_by TEXT,
    granted_at TEXT,
    PRIMARY KEY (group_id, role, canvas_scope)
);
"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    yield db_path


@pytest.fixture(autouse=True)
def patch_deps(db, monkeypatch):
    import tools.security.canvas_access as _ca

    # Translating wrapper — canvas_access authors %s for PostgreSQL.
    from _sql_compat import connect as _tconnect

    def _test_conn():
        return _tconnect(db)

    monkeypatch.setattr(_ca, "_conn", _test_conn)


# ── tests ─────────────────────────────────────────────────────────────────────

class TestGrantRevoke:
    def test_grant_returns_id(self, db):
        from tools.security.canvas_access import grant_access
        gid = grant_access("t1", "user", "alice", "proposals", "read", granted_by="admin")
        assert gid

    def test_grant_persists(self, db):
        from tools.security.canvas_access import grant_access
        import sqlite3 as _sq
        grant_access("t1", "user", "bob", "cpmp", "write", granted_by="admin")
        conn = _sq.connect(str(db))
        row = conn.execute(
            "SELECT access_level FROM canvas_access_grants WHERE tenant_id='t1' AND principal_id='bob'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "write"

    def test_duplicate_grant_upserts(self, db):
        from tools.security.canvas_access import grant_access
        import sqlite3 as _sq
        grant_access("t1", "user", "carol", "proposals", "read", granted_by="admin")
        grant_access("t1", "user", "carol", "proposals", "write", granted_by="admin")
        conn = _sq.connect(str(db))
        rows = conn.execute(
            "SELECT access_level FROM canvas_access_grants WHERE principal_id='carol'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "write"

    def test_revoke_removes_grant(self, db):
        from tools.security.canvas_access import grant_access, revoke_access, check_access
        grant_access("t1", "user", "dave", "proposals", "read", granted_by="admin")
        revoke_access("t1", "user", "dave", "proposals", revoked_by="admin")
        assert not check_access("dave", "t1", "proposals", required_level="read")


class TestCheckAccess:
    def test_direct_user_grant(self, db):
        from tools.security.canvas_access import grant_access, check_access
        grant_access("t1", "user", "eve", "dsoc", "read", granted_by="admin")
        assert check_access("eve", "t1", "dsoc", required_level="read")

    def test_no_grant_denied(self, db):
        from tools.security.canvas_access import check_access
        assert not check_access("nobody", "t1", "proposals", required_level="read")

    def test_write_satisfies_read(self, db):
        from tools.security.canvas_access import grant_access, check_access
        grant_access("t1", "user", "frank", "proposals", "write", granted_by="admin")
        assert check_access("frank", "t1", "proposals", required_level="read")

    def test_read_does_not_satisfy_write(self, db):
        from tools.security.canvas_access import grant_access, check_access
        grant_access("t1", "user", "grace", "proposals", "read", granted_by="admin")
        assert not check_access("grace", "t1", "proposals", required_level="write")

    def test_admin_satisfies_write(self, db):
        from tools.security.canvas_access import grant_access, check_access
        grant_access("t1", "user", "henry", "admin", "admin", granted_by="admin")
        assert check_access("henry", "t1", "admin", required_level="write")

    def test_cross_tenant_denied(self, db):
        from tools.security.canvas_access import grant_access, check_access
        grant_access("t1", "user", "ivan", "proposals", "admin", granted_by="admin")
        assert not check_access("ivan", "t2", "proposals", required_level="read")

    def test_role_grant(self, db):
        from tools.security.canvas_access import grant_access
        grant_access("t1", "role", "tenant_admin", "admin", "admin", granted_by="system")
        # tenant_admin role should grant admin access — check_access uses stored g.user_role
        import sqlite3 as _sq
        conn = _sq.connect(str(db))
        row = conn.execute(
            "SELECT id FROM canvas_access_grants WHERE principal_type='role' AND principal_id='tenant_admin'"
        ).fetchone()
        conn.close()
        assert row is not None


class TestGroupAccessResolution:
    def _seed_group(self, db, group_id: str, user_id: str, tenant_id: str) -> None:
        """Insert group + membership directly into the test DB."""
        import sqlite3 as _sq
        conn = _sq.connect(str(db))
        conn.execute(
            "INSERT OR IGNORE INTO groups (id, tenant_id, name, created_at, status) VALUES (?, ?, ?, datetime('now'), 'active')",
            (group_id, tenant_id, f"Group {group_id}"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO group_members (group_id, user_id, added_at) VALUES (?, ?, datetime('now'))",
            (group_id, user_id),
        )
        conn.commit()
        conn.close()

    def test_group_grant_resolved(self, db):
        from tools.security.canvas_access import grant_access, check_access
        self._seed_group(db, "grp-1", "julia", "t1")
        grant_access("t1", "group", "grp-1", "govcon", "read", granted_by="admin")
        assert check_access("julia", "t1", "govcon", required_level="read")

    def test_group_grant_not_leaked_to_other_user(self, db):
        from tools.security.canvas_access import grant_access, check_access
        # Grant is to grp-2 but kim is not a member
        self._seed_group(db, "grp-2", "julia", "t1")  # julia is member, not kim
        grant_access("t1", "group", "grp-2", "govcon", "write", granted_by="admin")
        assert not check_access("kim", "t1", "govcon", required_level="read")
