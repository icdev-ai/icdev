"""Tests for tools.security.group_manager — user groups, membership, role resolution."""
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

@pytest.fixture
def db(tmp_path, monkeypatch):
    """Temporary SQLite DB with groups schema pre-seeded."""
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
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
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            classification TEXT DEFAULT 'CUI',
            status TEXT DEFAULT 'active',
            created_at TEXT
        );
    """)
    conn.commit()
    conn.close()
    yield db_path


@pytest.fixture(autouse=True)
def patch_get_connection(db, monkeypatch):
    """Patch _conn() in group_manager to use the test DB."""
    import tools.security.group_manager as _gm

    # Translating wrapper — group_manager authors %s for PostgreSQL.
    from _sql_compat import connect as _tconnect

    def _test_conn():
        return _tconnect(db)

    monkeypatch.setattr(_gm, "_conn", _test_conn)


# ── tests ─────────────────────────────────────────────────────────────────────

class TestCreateGroup:
    def test_create_returns_uuid(self, db):
        from tools.security.group_manager import create_group
        gid = create_group("t1", "Alpha Team", "Test group", created_by="admin")
        assert gid and len(gid) > 8

    def test_create_persists(self, db):
        from tools.security.group_manager import create_group
        import sqlite3 as _sq
        gid = create_group("t1", "Beta Team", created_by="admin")
        conn = _sq.connect(str(db))
        row = conn.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()
        conn.close()
        assert row is not None
        assert row[2] == "Beta Team"

    def test_create_default_classification(self, db):
        from tools.security.group_manager import create_group
        import sqlite3 as _sq
        gid = create_group("t1", "Gamma", created_by="admin")
        conn = _sq.connect(str(db))
        row = conn.execute("SELECT classification FROM groups WHERE id=?", (gid,)).fetchone()
        conn.close()
        assert row[0] == "CUI"


class TestMembership:
    def test_add_member(self, db):
        from tools.security.group_manager import create_group, add_member, get_user_groups
        gid = create_group("t1", "Ops", created_by="admin")
        add_member(gid, "user-1", added_by="admin")
        groups = get_user_groups("user-1", "t1")
        assert any(g["id"] == gid for g in groups)

    def test_add_duplicate_member_is_noop(self, db):
        from tools.security.group_manager import create_group, add_member, get_user_groups
        gid = create_group("t1", "DevOps", created_by="admin")
        add_member(gid, "user-2", added_by="admin")
        add_member(gid, "user-2", added_by="admin")  # second add must not raise
        groups = get_user_groups("user-2", "t1")
        assert len([g for g in groups if g["id"] == gid]) == 1

    def test_remove_member(self, db):
        from tools.security.group_manager import create_group, add_member, remove_member, get_user_groups
        gid = create_group("t1", "Temp", created_by="admin")
        add_member(gid, "user-3", added_by="admin")
        remove_member(gid, "user-3")
        groups = get_user_groups("user-3", "t1")
        assert all(g["id"] != gid for g in groups)

    def test_get_user_groups_scoped_to_tenant(self, db):
        from tools.security.group_manager import create_group, add_member, get_user_groups
        g1 = create_group("t1", "T1 Group", created_by="admin")
        g2 = create_group("t2", "T2 Group", created_by="admin")
        add_member(g1, "user-x", added_by="admin")
        add_member(g2, "user-x", added_by="admin")
        t1_groups = get_user_groups("user-x", "t1")
        assert all(g["tenant_id"] == "t1" for g in t1_groups)


class TestRoleAssignment:
    def test_assign_global_role(self, db):
        from tools.security.group_manager import create_group, assign_role, resolve_effective_roles
        gid = create_group("t1", "Developers", created_by="admin")
        assign_role(gid, "developer", canvas_scope=None, granted_by="admin")
        # user has no direct role; resolution should be empty without membership
        roles = resolve_effective_roles("no-such-user", "t1")
        assert "developer" not in roles

    def test_resolve_roles_via_membership(self, db):
        from tools.security.group_manager import create_group, add_member, assign_role, resolve_effective_roles
        gid = create_group("t1", "Compliance", created_by="admin")
        add_member(gid, "user-c", added_by="admin")
        assign_role(gid, "compliance_officer", canvas_scope=None, granted_by="admin")
        roles = resolve_effective_roles("user-c", "t1")
        assert "compliance_officer" in roles

    def test_resolve_roles_canvas_scoped(self, db):
        from tools.security.group_manager import create_group, add_member, assign_role, resolve_effective_roles
        gid = create_group("t1", "Proposal Writers", created_by="admin")
        add_member(gid, "user-p", added_by="admin")
        assign_role(gid, "developer", canvas_scope="proposals", granted_by="admin")
        roles = resolve_effective_roles("user-p", "t1", canvas_scope="proposals")
        assert "developer" in roles

    def test_resolve_roles_canvas_scope_not_leaked(self, db):
        from tools.security.group_manager import create_group, add_member, assign_role, resolve_effective_roles
        gid = create_group("t1", "Proposal Writers2", created_by="admin")
        add_member(gid, "user-q", added_by="admin")
        assign_role(gid, "developer", canvas_scope="proposals", granted_by="admin")
        # Querying without matching canvas_scope — canvas-scoped roles should not appear
        roles = resolve_effective_roles("user-q", "t1", canvas_scope="cpmp")
        assert "developer" not in roles


class TestGroupPermissionCheck:
    def test_check_permission_via_group(self, db):
        from tools.security.group_manager import create_group, add_member, assign_role, check_group_permission
        gid = create_group("t1", "Auditors", created_by="admin")
        add_member(gid, "user-a", added_by="admin")
        assign_role(gid, "auditor", canvas_scope=None, granted_by="admin")
        # auditor role should have read access to compliance category
        result = check_group_permission("user-a", "t1", "compliance", "GET")
        assert result is True

    def test_check_permission_denied_no_group(self, db):
        from tools.security.group_manager import check_group_permission
        result = check_group_permission("user-nobody", "t1", "admin", "DELETE")
        assert result is False
