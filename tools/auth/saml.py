# CUI // SP-CTI
"""SAML 2.0 Service Provider integration for ICDEV enterprise SSO.

Implements SP metadata, AuthnRequest generation, and ACS response parsing
using Python stdlib only — no external SAML library required.
"""
from __future__ import annotations

import base64
import os
import uuid
import zlib
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree as ET  # nosec B405 # construction only; parse via defusedxml

# The IdP-supplied SAMLResponse is untrusted input. Parse it with defusedxml,
# which forbids entity expansion (billion-laughs) and external DTD/entity
# references (XXE). Stdlib ET above is used only to BUILD XML we emit.
from defusedxml.ElementTree import fromstring as _safe_fromstring

_SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
_SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
_META = "urn:oasis:names:tc:SAML:2.0:metadata"

ET.register_namespace("saml", _SAML)
ET.register_namespace("samlp", _SAMLP)
ET.register_namespace("md", _META)

_NAMEID_EMAIL = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
_BINDING_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"


def _base_url() -> str:
    return os.environ.get("ICDEV_BASE_URL", "http://localhost:5050").rstrip("/")


def _get_provider(provider_id: str) -> dict:
    from tools.db import storage  # late import so monkeypatch works in tests

    with storage.get_connection() as conn:
        row = conn.execute(
            "SELECT id, tenant_id, name, protocol, entity_id, metadata_url, "
            "client_id, attr_mapping, claims_mapping, enabled "
            "FROM sso_providers WHERE id = %s AND protocol = 'saml'",
            (provider_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"SAML provider not found: {provider_id!r}")
    keys = [
        "id", "tenant_id", "name", "protocol", "entity_id",
        "metadata_url", "client_id", "attr_mapping", "claims_mapping", "enabled",
    ]
    return dict(zip(keys, tuple(row)))


def _sp_entity_id(provider_id: str) -> str:
    return f"{_base_url()}/auth/saml/{provider_id}/metadata"


def _acs_url(provider_id: str) -> str:
    return f"{_base_url()}/auth/saml/{provider_id}/acs"


def generate_sp_metadata(provider_id: str) -> str:
    """Return SP EntityDescriptor XML string for the given provider."""
    _get_provider(provider_id)  # validate provider exists
    root = ET.Element(
        f"{{{_META}}}EntityDescriptor",
        attrib={"entityID": _sp_entity_id(provider_id)},
    )
    sp_desc = ET.SubElement(
        root,
        f"{{{_META}}}SPSSODescriptor",
        attrib={
            "AuthnRequestsSigned": "false",
            "WantAssertionsSigned": "true",
            "protocolSupportEnumeration": _SAMLP,
        },
    )
    ET.SubElement(sp_desc, f"{{{_META}}}NameIDFormat").text = _NAMEID_EMAIL
    ET.SubElement(
        sp_desc,
        f"{{{_META}}}AssertionConsumerService",
        attrib={
            "Binding": _BINDING_POST,
            "Location": _acs_url(provider_id),
            "index": "1",
            "isDefault": "true",
        },
    )
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def initiate_saml_login(provider_id: str, relay_state: str = "/") -> str:
    """Build a SAML AuthnRequest and return the IdP redirect URL."""
    provider = _get_provider(provider_id)
    idp_sso_url = (provider.get("metadata_url") or provider.get("entity_id") or "").strip()
    if not idp_sso_url:
        raise ValueError(f"No IdP SSO URL configured for provider {provider_id!r}")

    request_id = f"_{uuid.uuid4().hex}"
    issue_instant = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    root = ET.Element(
        f"{{{_SAMLP}}}AuthnRequest",
        attrib={
            "ID": request_id,
            "Version": "2.0",
            "IssueInstant": issue_instant,
            "Destination": idp_sso_url,
            "AssertionConsumerServiceURL": _acs_url(provider_id),
            "ProtocolBinding": _BINDING_POST,
        },
    )
    ET.SubElement(root, f"{{{_SAML}}}Issuer").text = _sp_entity_id(provider_id)
    ET.SubElement(
        root,
        f"{{{_SAMLP}}}NameIDPolicy",
        attrib={"Format": _NAMEID_EMAIL, "AllowCreate": "true"},
    )

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    # HTTP-Redirect binding: raw DEFLATE (strip 2-byte zlib header + 4-byte checksum)
    deflated = zlib.compress(xml_bytes)[2:-4]
    encoded = base64.b64encode(deflated).decode("ascii")
    params = urlencode({"SAMLRequest": encoded, "RelayState": relay_state})
    return f"{idp_sso_url}?{params}"


def process_acs_response(
    saml_response_b64: str,
    relay_state: str = "/",
) -> dict[str, Any]:
    """Decode and parse a SAML Response from the IdP ACS POST.

    Returns dict with: name_id (str|None), attributes (dict), relay_state (str).
    Raises ValueError on parse failure.
    """
    try:
        xml_bytes = base64.b64decode(saml_response_b64)
    except Exception as exc:
        raise ValueError("Invalid base64 SAMLResponse") from exc
    try:
        root = _safe_fromstring(xml_bytes)
    except (ET.ParseError, ValueError) as exc:
        # ValueError covers defusedxml's DefusedXmlException subclasses
        # (EntitiesForbidden / DTDForbidden / ExternalReferenceForbidden).
        raise ValueError("Invalid SAMLResponse XML") from exc

    name_id_el = root.find(f".//{{{_SAML}}}NameID")
    name_id = name_id_el.text if name_id_el is not None else None

    attributes: dict[str, list[str]] = {}
    for attr_el in root.findall(f".//{{{_SAML}}}Attribute"):
        attr_name = attr_el.get("Name", "")
        values = [
            v.text or ""
            for v in attr_el.findall(f"{{{_SAML}}}AttributeValue")
        ]
        attributes[attr_name] = values

    return {"name_id": name_id, "attributes": attributes, "relay_state": relay_state}


def apply_attr_mapping(raw_attributes: dict, attr_mapping_json: str | None) -> dict:
    """Map SAML attribute names to ICDEV field names via provider attr_mapping JSON.

    attr_mapping_json: e.g. '{"icdev_roles": "Role"}' — SAML attr "Role" → "icdev_roles".
    Returns raw_attributes unchanged if mapping is absent or unparseable.
    """
    import json as _json

    if not attr_mapping_json:
        return raw_attributes
    try:
        mapping: dict = _json.loads(attr_mapping_json)
    except Exception:
        return raw_attributes
    return {icdev_field: raw_attributes[saml_attr]
            for icdev_field, saml_attr in mapping.items()
            if saml_attr in raw_attributes}
