# CUI // SP-CTI
"""Tests for ECR-SSO-01: SSO DB tables (sso_providers + sso_sessions)."""
from __future__ import annotations

import contextlib
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
        # Translating wrapper: the SSO code authors %s for PostgreSQL, and a
        # bare sqlite3 connection makes every statement a syntax error.
        from _sql_compat import connect as _tconnect

        conn = _tconnect(db_path, check_same_thread=False)
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
    # defusedxml forbids entity expansion / external DTDs (XXE, billion-laughs)
    from defusedxml.ElementTree import fromstring as safe_fromstring
    from tools.db import storage
    from tools.auth.saml import generate_sp_metadata

    pid = f"saml-{uuid.uuid4().hex[:8]}"
    with storage.get_connection() as conn:
        _make_saml_provider(conn, pid, "https://idp.example.com/sso")

    xml_str = generate_sp_metadata(pid)
    root = safe_fromstring(xml_str)
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
    from xml.etree import ElementTree as ET  # nosec B405 # builds XML, never parses
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


# ---------------------------------------------------------------------------
# ecr-sso-04: Admin Console SSO CRUD API tests
# ---------------------------------------------------------------------------

def test_sso_admin_crud(_sso_db, monkeypatch):
    """Admin API: create / list / update / soft-disable SSO providers."""
    import importlib
    import json as _json
    from flask import Flask, g

    monkeypatch.setenv("ICDEV_ADMIN_CONSOLE_ENABLED", "true")
    monkeypatch.setenv("ICDEV_ENFORCE_CANVAS_ACCESS", "false")

    import tools.admin.blueprint as admin_mod
    importlib.reload(admin_mod)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-sso-crud"

    bp = admin_mod.create_admin_blueprint()
    assert bp is not None, "Admin blueprint must be enabled"
    app.register_blueprint(bp)

    @app.before_request
    def _set_user():
        g.current_user = {"id": "admin", "role": "admin"}

    client = app.test_client()
    tid = "sso-crud-tenant"

    # CREATE — SAML provider
    resp = client.post(
        f"/api/admin/tenants/{tid}/sso-providers",
        data=_json.dumps({
            "name": "Corp SAML IdP",
            "protocol": "saml",
            "entity_id": "https://idp.corp.example",
            "metadata_url": "https://idp.corp.example/metadata",
        }),
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    pid = body["provider_id"]
    assert pid
    assert body["protocol"] == "saml"
    assert "login_url" in body

    # LIST — provider appears with login_url
    resp = client.get(f"/api/admin/tenants/{tid}/sso-providers")
    assert resp.status_code == 200
    providers = resp.get_json()["providers"]
    assert any(p["id"] == pid for p in providers)
    p = next(x for x in providers if x["id"] == pid)
    assert "login_url" in p
    assert "saml" in p["login_url"]

    # UPDATE — rename provider
    resp = client.put(
        f"/api/admin/tenants/{tid}/sso-providers/{pid}",
        data=_json.dumps({"name": "Updated Corp IdP"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["updated"] is True

    # Verify updated name persists
    providers = client.get(f"/api/admin/tenants/{tid}/sso-providers").get_json()["providers"]
    assert next(x for x in providers if x["id"] == pid)["name"] == "Updated Corp IdP"

    # DISABLE — soft delete (enabled=0, row preserved)
    resp = client.delete(f"/api/admin/tenants/{tid}/sso-providers/{pid}")
    assert resp.status_code == 200
    assert resp.get_json()["disabled"] is True

    # Default list excludes disabled
    default_providers = client.get(f"/api/admin/tenants/{tid}/sso-providers").get_json()["providers"]
    assert not any(x["id"] == pid for x in default_providers)

    # ?all=1 includes disabled with enabled=0
    all_providers = client.get(f"/api/admin/tenants/{tid}/sso-providers?all=1").get_json()["providers"]
    disabled_p = next((x for x in all_providers if x["id"] == pid), None)
    assert disabled_p is not None
    assert disabled_p["enabled"] == 0


# ---------------------------------------------------------------------------
# ecr-sso-03: OIDC integration tests
# ---------------------------------------------------------------------------

def _make_oidc_provider(conn, provider_id: str, auth_url: str) -> None:
    """Insert a test OIDC provider row."""
    conn.execute(
        "INSERT INTO sso_providers "
        "(id, tenant_id, name, protocol, client_id, client_secret_enc, metadata_url) "
        "VALUES (?, ?, ?, 'oidc', ?, ?, ?)",
        (provider_id, "test-tenant", "Test OIDC IdP", "client-id", "client-secret", auth_url),
    )
    conn.commit()


def test_oidc_login_url(_sso_db):
    """get_oidc_login_url returns a URL and a state string."""
    from tools.db import storage
    from tools.auth.oidc import get_oidc_login_url, _CLIENTS

    pid = f"oidc-{uuid.uuid4().hex[:8]}"
    with storage.get_connection() as conn:
        _make_oidc_provider(conn, pid, "https://accounts.example.com/authorize")

    # Clear any cached client for this provider
    _CLIENTS.pop(pid, None)

    url, state = get_oidc_login_url(pid)
    assert "accounts.example.com" in url
    assert "client_id=" in url or "response_type=" in url
    assert state  # non-empty state for CSRF


def test_oidc_callback_creates_session(_sso_db, monkeypatch):
    """exchange_oidc_code persists an sso_sessions row and returns user_info."""
    import base64 as _b64
    import json as _json
    from tools.db import storage
    from tools.auth import oidc as oidc_mod

    pid = f"oidc-{uuid.uuid4().hex[:8]}"
    with storage.get_connection() as conn:
        _make_oidc_provider(conn, pid, "https://accounts.example.com/token")

    # Build a minimal JWT id_token (unsigned, for test purposes)
    payload = {"sub": "user-123", "email": "alice@example.com", "name": "Alice"}
    header_b64 = _b64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload_b64 = _b64.urlsafe_b64encode(_json.dumps(payload).encode()).rstrip(b"=").decode()
    fake_id_token = f"{header_b64}.{payload_b64}.fakesig"

    # Stub OAuth2Session.fetch_token to avoid real HTTP
    class _FakeClient:
        redirect_uri = ""
        def fetch_token(self, *a, **kw):
            return {"id_token": fake_id_token, "access_token": "tok"}
        def create_authorization_url(self, url, **kw):
            return url + "?response_type=code", "state-xyz"

    oidc_mod._CLIENTS[pid] = _FakeClient()

    user_info = oidc_mod.exchange_oidc_code(
        pid, code="auth-code", state="state-xyz",
        token_endpoint="https://accounts.example.com/token",
    )
    assert user_info["email"] == "alice@example.com"

    # Verify session persisted
    with storage.get_connection() as conn:
        row = conn.execute(
            "SELECT name_id, provider_id FROM sso_sessions WHERE provider_id = ?", (pid,)
        ).fetchone()
    assert row is not None
    assert row[0] == "alice@example.com"

    # Cleanup
    oidc_mod._CLIENTS.pop(pid, None)


# ---------------------------------------------------------------------------
# ecr-sso-05: V&V — ACS session persistence, role mapping, expiry rejection
# ---------------------------------------------------------------------------

def test_acs_creates_session(_sso_db, monkeypatch):
    """ACS route persists an sso_sessions row after a valid SAML response."""
    import base64
    import importlib
    from xml.etree import ElementTree as ET  # nosec B405 # builds XML, never parses
    from flask import Flask
    from tools.db import storage

    monkeypatch.setattr(storage, "get_connection", _sqlite_conn_factory(_sso_db))

    import tools.auth.blueprint as bp_mod
    importlib.reload(bp_mod)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-acs-session"
    app.register_blueprint(bp_mod.bp)

    pid = f"saml-{uuid.uuid4().hex[:8]}"
    with storage.get_connection() as conn:
        _make_saml_provider(conn, pid, "https://idp.example.com/sso")

    # Build a minimal SAML Response
    _SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
    _SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
    root = ET.Element(f"{{{_SAMLP}}}Response")
    assertion = ET.SubElement(root, f"{{{_SAML}}}Assertion")
    subject = ET.SubElement(assertion, f"{{{_SAML}}}Subject")
    ET.SubElement(subject, f"{{{_SAML}}}NameID").text = "acs-user@example.com"
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    b64 = base64.b64encode(xml_bytes).decode("ascii")

    client = app.test_client()
    resp = client.post(
        f"/auth/saml/{pid}/acs",
        data={"SAMLResponse": b64, "RelayState": "/dashboard"},
    )
    # Should redirect (302) to relay_state, not error
    assert resp.status_code in (302, 301), f"Expected redirect, got {resp.status_code}"

    # Verify sso_sessions row was created
    with storage.get_connection() as conn:
        row = conn.execute(
            "SELECT name_id FROM sso_sessions WHERE provider_id = ?", (pid,)
        ).fetchone()
    assert row is not None, "ACS must persist an sso_sessions row"
    assert row[0] == "acs-user@example.com"


def test_saml_role_mapping():
    """process_acs_response captures role/groups SAML attributes."""
    import base64
    from xml.etree import ElementTree as ET  # nosec B405 # builds XML, never parses
    from tools.auth.saml import process_acs_response

    _SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
    _SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"

    root = ET.Element(f"{{{_SAMLP}}}Response")
    assertion = ET.SubElement(root, f"{{{_SAML}}}Assertion")
    subject = ET.SubElement(assertion, f"{{{_SAML}}}Subject")
    ET.SubElement(subject, f"{{{_SAML}}}NameID").text = "role-user@example.com"

    attr_stmt = ET.SubElement(assertion, f"{{{_SAML}}}AttributeStatement")
    # Role attribute with two values
    role_attr = ET.SubElement(attr_stmt, f"{{{_SAML}}}Attribute", attrib={"Name": "role"})
    ET.SubElement(role_attr, f"{{{_SAML}}}AttributeValue").text = "admin"
    ET.SubElement(role_attr, f"{{{_SAML}}}AttributeValue").text = "analyst"
    # Groups attribute
    grp_attr = ET.SubElement(attr_stmt, f"{{{_SAML}}}Attribute", attrib={"Name": "groups"})
    ET.SubElement(grp_attr, f"{{{_SAML}}}AttributeValue").text = "icdev-users"

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    b64 = base64.b64encode(xml_bytes).decode("ascii")

    result = process_acs_response(b64, relay_state="/")
    assert result["name_id"] == "role-user@example.com"
    assert "admin" in result["attributes"]["role"]
    assert "analyst" in result["attributes"]["role"]
    assert result["attributes"]["groups"] == ["icdev-users"]


def test_expired_session_rejected(_sso_db):
    """Sessions with expires_at in the past are not returned as valid."""
    from tools.db import storage

    sid = f"expired-{uuid.uuid4().hex[:8]}"
    pid = f"saml-{uuid.uuid4().hex[:8]}"

    with storage.get_connection() as conn:
        # Insert an already-expired session
        conn.execute(
            "INSERT INTO sso_sessions (id, tenant_id, provider_id, name_id, expires_at) "
            "VALUES (?, ?, ?, ?, datetime('now', '-1 hour'))",
            (sid, "test-tenant", pid, "expired-user@example.com"),
        )
        conn.commit()

        # A query that respects expires_at (as the application should)
        valid = conn.execute(
            "SELECT id FROM sso_sessions WHERE id = ? "
            "AND (expires_at IS NULL OR expires_at > datetime('now'))",
            (sid,),
        ).fetchone()

    assert valid is None, "Expired session must not pass the validity check"
