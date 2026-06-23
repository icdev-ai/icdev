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


# ---------------------------------------------------------------------------
# ecr-sso-02: SAML SP integration tests
# ---------------------------------------------------------------------------

def _make_saml_provider(conn, provider_id: str, idp_url: str) -> None:
    """Insert a test SAML provider row."""
    conn.execute(
        "INSERT INTO sso_providers (id, tenant_id, name, protocol, metadata_url) "
        "VALUES (?, ?, ?, 'saml', ?)",
        (provider_id, "test-tenant", "Test IdP", idp_url),
    )
    conn.commit()


def test_saml_metadata_valid(_sso_db):
    """generate_sp_metadata returns valid XML with SPSSODescriptor."""
    from xml.etree import ElementTree as ET
    from tools.db import storage
    from tools.auth.saml import generate_sp_metadata

    pid = f"saml-{uuid.uuid4().hex[:8]}"
    with storage.get_connection() as conn:
        _make_saml_provider(conn, pid, "https://idp.example.com/sso")

    xml_str = generate_sp_metadata(pid)
    root = ET.fromstring(xml_str)
    _META = "urn:oasis:names:tc:SAML:2.0:metadata"
    assert root.tag == f"{{{_META}}}EntityDescriptor"
    assert root.get("entityID", "").endswith(f"/auth/saml/{pid}/metadata")
    sp = root.find(f"{{{_META}}}SPSSODescriptor")
    assert sp is not None
    acs = sp.find(f"{{{_META}}}AssertionConsumerService")
    assert acs is not None
    assert "acs" in acs.get("Location", "")


def test_saml_login_redirect(_sso_db):
    """initiate_saml_login returns a URL containing SAMLRequest param."""
    from tools.db import storage
    from tools.auth.saml import initiate_saml_login

    pid = f"saml-{uuid.uuid4().hex[:8]}"
    with storage.get_connection() as conn:
        _make_saml_provider(conn, pid, "https://idp.example.com/sso")

    url = initiate_saml_login(pid, relay_state="/dashboard")
    assert url.startswith("https://idp.example.com/sso")
    assert "SAMLRequest=" in url
    assert "RelayState=" in url


def test_saml_acs_parses_response():
    """process_acs_response correctly parses a minimal SAML Response."""
    import base64
    from xml.etree import ElementTree as ET
    from tools.auth.saml import process_acs_response

    _SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
    _SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"

    # Build a minimal SAML Response XML
    root = ET.Element(f"{{{_SAMLP}}}Response")
    assertion = ET.SubElement(root, f"{{{_SAML}}}Assertion")
    subject = ET.SubElement(assertion, f"{{{_SAML}}}Subject")
    ET.SubElement(subject, f"{{{_SAML}}}NameID").text = "user@example.com"
    attr_stmt = ET.SubElement(assertion, f"{{{_SAML}}}AttributeStatement")
    attr = ET.SubElement(
        attr_stmt, f"{{{_SAML}}}Attribute",
        attrib={"Name": "email"}
    )
    ET.SubElement(attr, f"{{{_SAML}}}AttributeValue").text = "user@example.com"

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    b64 = base64.b64encode(xml_bytes).decode("ascii")

    result = process_acs_response(b64, relay_state="/dashboard")
    assert result["name_id"] == "user@example.com"
    assert result["attributes"].get("email") == ["user@example.com"]
    assert result["relay_state"] == "/dashboard"


def test_saml_acs_rejects_invalid_b64():
    """process_acs_response raises ValueError for non-base64 input."""
    from tools.auth.saml import process_acs_response

    with pytest.raises(ValueError, match="base64"):
        process_acs_response("NOT_BASE64!!!", relay_state="/")
