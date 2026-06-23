# CUI // SP-CTI
"""Tests for ECR-SSO-01: SSO DB tables (sso_providers + sso_sessions)."""
from __future__ import annotations

import contextlib
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Helper — mirrors test_component_registry.py's _sqlite_conn_factory
# ---------------------------------------------------------------------------

def _sqlite_conn_factory(db_path):
    """Return a get_connection-style factory bound to a SQLite file."""
    def _factory():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return contextlib.closing(conn)
    return _factory


@pytest.fixture
def _sso_db(icdev_db, monkeypatch):
    """Fixture: patch get_connection to use the temp icdev_db with SSO schema."""
    from tools.db import storage
    monkeypatch.setattr(storage, "get_connection", _sqlite_conn_factory(icdev_db))
    return icdev_db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_sso_tables_exist(_sso_db):
    """sso_providers and sso_sessions tables are created by conftest schema."""
    from tools.db import storage

    with storage.get_connection() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sso_providers'"
        ).fetchone()
        assert row is not None, "sso_providers table not found"

        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sso_sessions'"
        ).fetchone()
        assert row is not None, "sso_sessions table not found"


def test_sso_provider_insert_and_query(_sso_db):
    """Can insert a SAML provider and query it back."""
    from tools.db import storage

    pid = f"test-provider-{uuid.uuid4().hex[:8]}"
    with storage.get_connection() as conn:
        conn.execute(
            "INSERT INTO sso_providers (id, tenant_id, name, protocol) VALUES (?, ?, ?, ?)",
            (pid, "test-tenant", "Test IdP", "saml"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, protocol FROM sso_providers WHERE id = ?", (pid,)
        ).fetchone()
    assert row is not None
    assert row[1] == "saml"


def test_sso_oidc_provider_insert(_sso_db):
    """Can insert an OIDC provider."""
    from tools.db import storage

    pid = f"oidc-provider-{uuid.uuid4().hex[:8]}"
    with storage.get_connection() as conn:
        conn.execute(
            "INSERT INTO sso_providers (id, tenant_id, name, protocol, client_id, metadata_url) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pid, "test-tenant", "Okta OIDC", "oidc", "client-abc",
             "https://example.okta.com/.well-known/openid-configuration"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT protocol, client_id FROM sso_providers WHERE id = ?", (pid,)
        ).fetchone()
    assert row is not None
    assert row[0] == "oidc"
    assert row[1] == "client-abc"


def test_sso_session_insert_and_query(_sso_db):
    """Can insert and query sso_sessions."""
    from tools.db import storage

    sid = f"test-session-{uuid.uuid4().hex[:8]}"
    with storage.get_connection() as conn:
        conn.execute(
            "INSERT INTO sso_sessions (id, tenant_id, provider_id, name_id) VALUES (?, ?, ?, ?)",
            (sid, "test-tenant", "test-provider", "user@example.com"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, name_id FROM sso_sessions WHERE id = ?", (sid,)
        ).fetchone()
    assert row is not None
    assert row[1] == "user@example.com"


def test_sso_protocol_constraint(_sso_db):
    """sso_providers rejects protocol values outside ('saml','oidc')."""
    from tools.db import storage

    pid = f"bad-protocol-{uuid.uuid4().hex[:8]}"
    with storage.get_connection() as conn:
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO sso_providers (id, tenant_id, name, protocol) VALUES (?, ?, ?, ?)",
                (pid, "test-tenant", "Bad IdP", "kerberos"),
            )
            conn.commit()


def test_sso_auth_package_importable():
    """tools.auth package is importable."""
    import tools.auth  # noqa: F401
