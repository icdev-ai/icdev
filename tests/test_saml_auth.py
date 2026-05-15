#!/usr/bin/env python3
"""Tests for tools/saas/auth/saml_auth.py — SAML 2.0 CAC/PIV authentication.

CUI // SP-CTI
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

# Ensure project root on path
BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def saml_config():
    return {
        "strict": True,
        "sp": {
            "entity_id": "https://icdev.local/sp",
            "assertion_consumer_service": {
                "url": "https://icdev.local/saml/acs",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "single_logout_service": {
                "url": "https://icdev.local/saml/slo",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "name_id_format": "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
        },
        "idp": {
            "entity_id": "https://eis.disa.mil/idp",
            "single_sign_on_service": {
                "url": "https://eis.disa.mil/idp/profile/SAML2/Redirect/SSO",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "single_logout_service": {
                "url": "https://eis.disa.mil/idp/profile/SAML2/Redirect/SLO",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": "",
        },
        "security": {
            "authn_requests_signed": False,
            "want_assertions_signed": False,
            "want_name_id": True,
            "requested_authn_context": True,
            "requested_authn_context_comparison": "exact",
            "requested_authn_contexts": [
                "urn:oasis:names:tc:SAML:2.0:ac:classes:X509",
            ],
        },
    }


@pytest.fixture
def sample_saml_response_xml():
    """Minimal valid SAML Response (unsigned, for structure testing)."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
                ID="_resp1" Version="2.0"
                IssueInstant="2024-01-01T00:00:00Z"
                Destination="https://icdev.local/saml/acs"
                InResponseTo="_req1">
    <samlp:Status>
        <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
    </samlp:Status>
    <saml:Assertion ID="_assert1" Version="2.0" IssueInstant="2024-01-01T00:00:00Z">
        <saml:Issuer>https://eis.disa.mil/idp</saml:Issuer>
        <saml:Subject>
            <saml:NameID Format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent">1234567890</saml:NameID>
            <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
                <saml:SubjectConfirmationData NotOnOrAfter="2099-01-01T00:00:00Z"
                                               Recipient="https://icdev.local/saml/acs"
                                               InResponseTo="_req1"/>
            </saml:SubjectConfirmation>
        </saml:Subject>
        <saml:Conditions NotBefore="2020-01-01T00:00:00Z" NotOnOrAfter="2099-01-01T00:00:00Z">
            <saml:AudienceRestriction>
                <saml:Audience>https://icdev.local/sp</saml:Audience>
            </saml:AudienceRestriction>
        </saml:Conditions>
        <saml:AuthnStatement AuthnInstant="2024-01-01T00:00:00Z" SessionIndex="sess1">
            <saml:AuthnContext>
                <saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:X509</saml:AuthnContextClassRef>
            </saml:AuthnContext>
        </saml:AuthnStatement>
        <saml:AttributeStatement>
            <saml:Attribute Name="uid">
                <saml:AttributeValue>1234567890</saml:AttributeValue>
            </saml:Attribute>
            <saml:Attribute Name="cn">
                <saml:AttributeValue>SMITH.JOHN.A.1234567890</saml:AttributeValue>
            </saml:Attribute>
            <saml:Attribute Name="mail">
                <saml:AttributeValue>john.smith@mail.mil</saml:AttributeValue>
            </saml:Attribute>
            <saml:Attribute Name="eduPersonPrincipalName">
                <saml:AttributeValue>john.smith@mail.mil</saml:AttributeValue>
            </saml:Attribute>
        </saml:AttributeStatement>
    </saml:Assertion>
</samlp:Response>
""".encode("utf-8")


# ---------------------------------------------------------------------------
# AuthNRequest
# ---------------------------------------------------------------------------

def test_generate_authn_request(saml_config):
    from tools.saas.auth.saml_auth import generate_authn_request

    result = generate_authn_request(saml_config, relay_state="abc123")

    assert result["id"].startswith("_")
    assert result["xml"].startswith(b"<")
    assert b"AuthnRequest" in result["xml"]
    assert b"https://icdev.local/sp" in result["xml"]
    assert b"https://icdev.local/saml/acs" in result["xml"]
    assert "SAMLRequest=" in result["url"]
    assert "RelayState=abc123" in result["url"]
    assert result["saml_request_b64"]
    assert result["relay_state"] == "abc123"


def test_generate_authn_request_without_relay_state(saml_config):
    from tools.saas.auth.saml_auth import generate_authn_request

    result = generate_authn_request(saml_config)
    assert "RelayState" not in result["url"]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def test_parse_saml_response_success(saml_config, sample_saml_response_xml):
    from tools.saas.auth.saml_auth import parse_saml_response

    b64_resp = base64.b64encode(sample_saml_response_xml).decode("ascii")
    result = parse_saml_response(b64_resp, saml_config)

    assert result["valid"] is True
    assert result["name_id"] == "1234567890"
    assert result["session_index"] == "sess1"
    assert result["authn_context_class_ref"] == "urn:oasis:names:tc:SAML:2.0:ac:classes:X509"
    assert result["in_response_to"] == "_req1"
    assert "attributes" in result


def test_parse_saml_response_missing_status(saml_config):
    from tools.saas.auth.saml_auth import SAMLValidationError, parse_saml_response

    xml = b'<?xml version="1.0"?><samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" ID="_r1" Version="2.0" IssueInstant="2024-01-01T00:00:00Z"><saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_a1" Version="2.0" IssueInstant="2024-01-01T00:00:00Z"><saml:Issuer>x</saml:Issuer></saml:Assertion></samlp:Response>'
    b64_resp = base64.b64encode(xml).decode("ascii")

    with pytest.raises(SAMLValidationError, match="Missing Status"):
        parse_saml_response(b64_resp, saml_config)


def test_parse_saml_response_status_failure(saml_config):
    from tools.saas.auth.saml_auth import SAMLValidationError, parse_saml_response

    xml = b'<?xml version="1.0"?><samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" ID="_r1" Version="2.0" IssueInstant="2024-01-01T00:00:00Z"><samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:AuthnFailed"/></samlp:Status></samlp:Response>'
    b64_resp = base64.b64encode(xml).decode("ascii")

    with pytest.raises(SAMLValidationError, match="status failure"):
        parse_saml_response(b64_resp, saml_config)


# ---------------------------------------------------------------------------
# Attribute mapping
# ---------------------------------------------------------------------------

def test_map_dod_attributes():
    from tools.saas.auth.saml_auth import map_dod_attributes

    attrs = {
        "uid": ["1234567890"],
        "cn": ["SMITH.JOHN.A.1234567890"],
        "mail": ["john.smith@mail.mil"],
        "eduPersonPrincipalName": ["john.smith@mail.mil"],
    }
    result = map_dod_attributes(attrs)

    assert result["edipi"] == "1234567890"
    assert result["cac_cn"] == "SMITH.JOHN.A.1234567890"
    assert result["email"] == "john.smith@mail.mil"
    assert result["principal_name"] == "john.smith@mail.mil"
    assert result["display_name"] == "JOHN SMITH"


def test_map_dod_attributes_fallback_nameid():
    from tools.saas.auth.saml_auth import map_dod_attributes

    attrs = {"NameID": ["1234567890"]}
    result = map_dod_attributes(attrs)
    assert result["edipi"] == "1234567890"


def test_map_dod_attributes_display_name_from_email():
    from tools.saas.auth.saml_auth import map_dod_attributes

    attrs = {"mail": ["jane.doe@mail.mil"]}
    result = map_dod_attributes(attrs)
    assert result["display_name"] == "jane.doe"


# ---------------------------------------------------------------------------
# SP Metadata
# ---------------------------------------------------------------------------

def test_generate_sp_metadata(saml_config):
    from tools.saas.auth.saml_auth import generate_sp_metadata

    metadata = generate_sp_metadata(saml_config)
    assert metadata.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert 'entityID="https://icdev.local/sp"' in metadata
    assert "AssertionConsumerService" in metadata
    assert "https://icdev.local/saml/acs" in metadata


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------

def test_resolve_saml_config_defaults(monkeypatch):
    from tools.saas.auth.saml_auth import DEFAULT_SAML_CONFIG, resolve_saml_config

    monkeypatch.setenv("ICDEV_SAML_SP_ENTITY_ID", "https://test.local/sp")
    monkeypatch.setenv("ICDEV_SAML_ACS_URL", "https://test.local/saml/acs")

    config = resolve_saml_config("")
    assert config["sp"]["entity_id"] == "https://test.local/sp"
    assert config["sp"]["assertion_consumer_service"]["url"] == "https://test.local/saml/acs"
    assert config["idp"]["entity_id"] == DEFAULT_SAML_CONFIG["idp"]["entity_id"]


# ---------------------------------------------------------------------------
# CAC/PIV certificate extraction
# ---------------------------------------------------------------------------

def test_extract_cac_cert_from_assertion(sample_saml_response_xml):
    from tools.saas.auth.saml_auth import extract_cac_cert_from_assertion

    result = extract_cac_cert_from_assertion(sample_saml_response_xml)
    assert result is None  # sample has no certificate attribute


# ---------------------------------------------------------------------------
# DoD IdP presets
# ---------------------------------------------------------------------------

def test_dod_idp_presets():
    from tools.saas.auth.saml_auth import DOD_IDP_PRESETS

    assert "disa_eis" in DOD_IDP_PRESETS
    preset = DOD_IDP_PRESETS["disa_eis"]
    assert preset["entity_id"] == "https://eis.disa.mil/idp"
    assert "attribute_mapping" in preset


# ---------------------------------------------------------------------------
# Flask routes (integration)
# ---------------------------------------------------------------------------

def test_saml_metadata_route():
    from tools.saas.auth.saml_routes import saml_bp
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(saml_bp)

    with app.test_client() as client:
        resp = client.get("/saml/metadata")
        assert resp.status_code == 200
        assert resp.content_type.startswith("application/samlmetadata")
        data = resp.data.decode("utf-8")
        assert "EntityDescriptor" in data
        assert "entityID=" in data


def test_saml_status_route_no_tenant():
    from tools.saas.auth.saml_routes import saml_bp
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(saml_bp)

    with app.test_client() as client:
        resp = client.get("/saml/status")
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["code"] == "MISSING_TENANT"


def test_saml_login_route_no_tenant():
    from tools.saas.auth.saml_routes import saml_bp
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(saml_bp)

    with app.test_client() as client:
        resp = client.get("/saml/login")
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["code"] == "MISSING_TENANT"


def test_saml_acs_missing_response():
    from tools.saas.auth.saml_routes import saml_bp
    from flask import Flask

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(saml_bp)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["saml_tenant_id"] = "test-tenant"
        resp = client.post("/saml/acs", data={})
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["code"] == "MISSING_SAML_RESPONSE"


# ---------------------------------------------------------------------------
# Middleware integration
# ---------------------------------------------------------------------------

def test_middleware_extract_credentials_saml_session():
    from tools.saas.auth.middleware import _extract_credentials
    from flask import Flask, session, request

    app = Flask(__name__)
    app.secret_key = "test-secret"

    with app.test_request_context("/"):
        session["auth_method"] = "saml"
        session["user_id"] = "user-123"
        session["tenant_id"] = "tenant-456"
        session["saml_name_id"] = "1234567890"

        creds = _extract_credentials(request)
        assert creds is not None
        assert creds["method"] == "saml_session"
        assert creds["user_id"] == "user-123"
        assert creds["tenant_id"] == "tenant-456"
