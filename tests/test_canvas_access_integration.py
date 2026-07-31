#!/usr/bin/env python3
# CUI // SP-CTI
"""Integration tests for tools.security.canvas_access — grant/revoke/check end-to-end.

These tests exercise the full grant lifecycle through the DB layer: creating grants,
checking access, revoking, expiry, and group resolution.

Run: pytest tests/test_canvas_access_integration.py -v --tb=short
"""
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")

import tools.security.canvas_access as _ca
import tools.security.group_manager as _gm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


@pytest.fixture()
def ca_db(tmp_path, monkeypatch):
    """Isolated SQLite DB wired into canvas_access and group_manager modules."""
    db_path = tmp_path / "ca_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()

    # Translating wrapper — canvas_access and group_manager author %s for
    # PostgreSQL. unclosable= keeps the shared connection alive across the
    # close() both modules call in their finally blocks.
    from _sql_compat import translating as _translating

    shared = _translating(conn, unclosable=True)
    monkeypatch.setattr(_ca, "_conn", lambda: shared)
    monkeypatch.setattr(_gm, "_conn", lambda: shared)
    return conn


# ---------------------------------------------------------------------------
# Basic grant / check / revoke lifecycle
# ---------------------------------------------------------------------------

class TestGrantLifecycle:
    def test_grant_and_check(self, ca_db):
        _ca.grant_access("t1", "user", "alice", "proposals", "read", "admin")
        assert _ca.check_access("alice", "t1", "proposals", "read") is True

    def test_no_grant_denied(self, ca_db):
        assert _ca.check_access("bob", "t1", "proposals", "read") is False

    def test_write_implies_read(self, ca_db):
        _ca.grant_access("t1", "user", "alice", "proposals", "write", "admin")
        assert _ca.check_access("alice", "t1", "proposals", "read") is True
        assert _ca.check_access("alice", "t1", "proposals", "write") is True

    def test_read_does_not_satisfy_write(self, ca_db):
        _ca.grant_access("t1", "user", "alice", "proposals", "read", "admin")
        assert _ca.check_access("alice", "t1", "proposals", "write") is False

    def test_admin_satisfies_all(self, ca_db):
        _ca.grant_access("t1", "user", "alice", "proposals", "admin", "admin")
        assert _ca.check_access("alice", "t1", "proposals", "read") is True
        assert _ca.check_access("alice", "t1", "proposals", "write") is True
        assert _ca.check_access("alice", "t1", "proposals", "admin") is True

    def test_revoke_blocks_access(self, ca_db):
        _ca.grant_access("t1", "user", "alice", "proposals", "read", "admin")
        _ca.revoke_access("t1", "user", "alice", "proposals")
        assert _ca.check_access("alice", "t1", "proposals", "read") is False

    def test_cross_tenant_isolation(self, ca_db):
        _ca.grant_access("t1", "user", "alice", "proposals", "read", "admin")
        # alice has read on proposals in t1 — must NOT have access in t2
        assert _ca.check_access("alice", "t2", "proposals", "read") is False

    def test_canvas_isolation(self, ca_db):
        _ca.grant_access("t1", "user", "alice", "proposals", "read", "admin")
        # proposals grant must NOT bleed to cpmp
        assert _ca.check_access("alice", "t1", "cpmp", "read") is False


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------

class TestExpiry:
    def test_active_not_expired(self, ca_db):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _ca.grant_access("t1", "user", "alice", "proposals", "read", "admin", expires_at=future)
        assert _ca.check_access("alice", "t1", "proposals", "read") is True

    def test_expired_grant_denied(self, ca_db):
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        _ca.grant_access("t1", "user", "alice", "proposals", "read", "admin", expires_at=past)
        assert _ca.check_access("alice", "t1", "proposals", "read") is False


# ---------------------------------------------------------------------------
# Role grants
# ---------------------------------------------------------------------------

class TestRoleGrants:
    def test_role_grant_permits_user_with_role(self, ca_db):
        _ca.grant_access("t1", "role", "developer", "proposals", "read", "admin")
        # check_access with a role grant — the user must have that role
        # check_access by itself doesn't know roles; it checks direct + group + role grants
        assert _ca.check_access("any_dev_user", "t1", "proposals", "read",
                                 user_role="developer") is True

    def test_role_grant_denied_for_wrong_role(self, ca_db):
        _ca.grant_access("t1", "role", "developer", "proposals", "read", "admin")
        assert _ca.check_access("viewer_user", "t1", "proposals", "read",
                                 user_role="viewer") is False


# ---------------------------------------------------------------------------
# Group grants
# ---------------------------------------------------------------------------

class TestGroupGrants:
    def test_group_member_gets_access(self, ca_db):
        now = datetime.now(timezone.utc).isoformat()
        gid = str(uuid.uuid4())
        ca_db.execute(
            "INSERT INTO groups VALUES (?, 't1', 'govcon-team', '', 'CUI', 'active', 'admin', ?, ?)",
            (gid, now, now),
        )
        ca_db.execute(
            "INSERT INTO group_members VALUES (?, 'alice', 'admin', ?)",
            (gid, now),
        )
        ca_db.commit()

        _ca.grant_access("t1", "group", gid, "proposals", "write", "admin")
        assert _ca.check_access("alice", "t1", "proposals", "write") is True

    def test_non_group_member_denied(self, ca_db):
        now = datetime.now(timezone.utc).isoformat()
        gid = str(uuid.uuid4())
        ca_db.execute(
            "INSERT INTO groups VALUES (?, 't1', 'govcon-team', '', 'CUI', 'active', 'admin', ?, ?)",
            (gid, now, now),
        )
        ca_db.execute(
            "INSERT INTO group_members VALUES (?, 'alice', 'admin', ?)",
            (gid, now),
        )
        ca_db.commit()

        _ca.grant_access("t1", "group", gid, "proposals", "write", "admin")
        assert _ca.check_access("bob", "t1", "proposals", "write") is False


# ---------------------------------------------------------------------------
# list_grants
# ---------------------------------------------------------------------------

class TestListGrants:
    def test_list_shows_active_grants(self, ca_db):
        _ca.grant_access("t1", "user", "alice", "proposals", "read", "admin")
        _ca.grant_access("t1", "user", "bob", "proposals", "write", "admin")
        grants = _ca.list_grants("t1", "proposals")
        assert len(grants) == 2

    def test_list_excludes_revoked(self, ca_db):
        _ca.grant_access("t1", "user", "alice", "proposals", "read", "admin")
        _ca.revoke_access("t1", "user", "alice", "proposals")
        grants = _ca.list_grants("t1", "proposals")
        assert len(grants) == 0

    def test_list_scoped_to_canvas(self, ca_db):
        _ca.grant_access("t1", "user", "alice", "proposals", "read", "admin")
        _ca.grant_access("t1", "user", "alice", "cpmp", "read", "admin")
        proposals_grants = _ca.list_grants("t1", "proposals")
        assert len(proposals_grants) == 1
        assert proposals_grants[0]["canvas_name"] == "proposals"
