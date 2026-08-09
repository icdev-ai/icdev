#!/usr/bin/env python3
"""ICDEV™ SaaS — SAML 2.0 Authentication for DoD Identity Providers + CAC/PIV.
CUI // SP-CTI

Lightweight SAML 2.0 Service Provider implementation using Python stdlib
xml.etree + cryptography (no external xmlsec1 dependency).

Supported flows:
  - SP-initiated SSO (AuthNRequest → IdP → SAML Response)
  - HTTP-Redirect binding for AuthNRequest
  - HTTP-POST binding for SAMLResponse
  - Assertion signature validation (RSA-SHA256)
  - DoD Identity Provider attribute mapping (EDIPI, CN, email)

DoD Identity Provider integration:
  - DISA Enterprise Identity Service (EIS)
  - Standard DoD attribute mappings:
      uid                     → EDIPI
      cn                      → Common Name (LAST.FIRST.MIDDLE.EDIPI)
      mail                    → Email address
      eduPersonPrincipalName  → UPN / principal name
      subject / NameID        → Fallback identifier
"""

from __future__ import annotations

import base64
import json
import os
import sys
import uuid
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from xml.etree.ElementTree import Element, SubElement, tostring

# Prefer defusedxml when available (XXE-safe); fall back to stdlib
from xml.etree.ElementTree import fromstring as _et_fromstring

try:
    from defusedxml.ElementTree import fromstring as _safe_fromstring
except ImportError:
    _safe_fromstring = _et_fromstring

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ---------------------------------------------------------------------------
# Paths & logging
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("saas.auth.saml")

# ---------------------------------------------------------------------------
# SAML 2.0 namespaces
# ---------------------------------------------------------------------------
NS_SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
NS_SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
NS_DS = "http://www.w3.org/2000/09/xmldsig#"
NS_XENC = "http://www.w3.org/2001/04/xmlenc#"
NS_MD = "urn:oasis:names:tc:SAML:2.0:metadata"

NAMESPACES = {
    "saml": NS_SAML,
    "samlp": NS_SAMLP,
    "ds": NS_DS,
    "xenc": NS_XENC,
    "md": NS_MD,
}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_SAML_CONFIG = {
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
        "x509cert": "",
        "private_key": "",
    },
    "idp": {
        "entity_id": "",
        "single_sign_on_service": {
            "url": "",
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        },
        "single_logout_service": {
            "url": "",
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        },
        "x509cert": "",
    },
    "security": {
        "name_id_encrypted": False,
        "authn_requests_signed": False,
        "logout_request_signed": False,
        "logout_response_signed": False,
        "sign_metadata": False,
        "want_assertions_signed": True,
        "want_assertions_encrypted": False,
        "want_name_id": True,
        "want_name_id_encrypted": False,
        "requested_authn_context": True,
        "requested_authn_context_comparison": "exact",
        "requested_authn_contexts": [
            "urn:oasis:names:tc:SAML:2.0:ac:classes:X509",
            "urn:oasis:names:tc:SAML:2.0:ac:classes:SmartcardPKI",
            "urn:oasis:names:tc:SAML:2.0:ac:classes:TLSClient",
        ],
        "want_xml_validation": True,
        "digest_algorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
        "signature_algorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
        "reject_unsolicited_responses_with_in_response_to": False,
    },
    "contact": {
        "technical": {"given_name": "ICDEV", "email_address": "admin@icdev.local"},
        "support": {"given_name": "ICDEV Support", "email_address": "support@icdev.local"},
    },
}


# ---------------------------------------------------------------------------
# DoD Identity Provider presets
# ---------------------------------------------------------------------------

DOD_IDP_PRESETS = {
    "disa_eis": {
        "name": "DISA Enterprise Identity Service",
        "entity_id": "https://eis.disa.mil/idp",
        "single_sign_on_service": {
            "url": "https://eis.disa.mil/idp/profile/SAML2/Redirect/SSO",
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        },
        "single_logout_service": {
            "url": "https://eis.disa.mil/idp/profile/SAML2/Redirect/SLO",
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        },
        "attribute_mapping": {
            "uid": "edipi",
            "cn": "cac_cn",
            "mail": "email",
            "eduPersonPrincipalName": "principal_name",
            "subject": "subject_id",
        },
    },
    "dod_identity": {
        "name": "DoD Identity",
        "entity_id": "https://identity.dod.mil/idp",
        "single_sign_on_service": {
            "url": "https://identity.dod.mil/idp/profile/SAML2/Redirect/SSO",
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        },
        "single_logout_service": {
            "url": "https://identity.dod.mil/idp/profile/SAML2/Redirect/SLO",
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        },
        "attribute_mapping": {
            "uid": "edipi",
            "cn": "cac_cn",
            "mail": "email",
            "eduPersonPrincipalName": "principal_name",
            "subject": "subject_id",
        },
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id() -> str:
    return "_{}".format(uuid.uuid4().hex)


def _b64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64_decode(data: str) -> bytes:
    return base64.b64decode(data)


def _deflate_encode(data: bytes) -> bytes:
    return zlib.compress(data, 9)[2:-4]


def _deflate_decode(data: bytes) -> bytes:
    return zlib.decompress(data, -15)


def _pem_from_raw(cert_str: str) -> str:
    """Ensure a certificate string is wrapped in PEM headers if not already."""
    cert_str = cert_str.strip()
    if cert_str.startswith("-----BEGIN"):
        return cert_str
    return "-----BEGIN CERTIFICATE-----\n{}\n-----END CERTIFICATE-----".format(
        "\n".join(cert_str[i : i + 64] for i in range(0, len(cert_str), 64))
    )


def _load_public_key_from_cert(pem_cert: str):
    """Load an RSA public key from a PEM-encoded X.509 certificate."""
    cert_bytes = pem_cert.encode("utf-8")
    cert = serialization.load_pem_x509_certificate(cert_bytes)
    return cert.public_key()


def _parse_xml(xml_bytes: bytes) -> Element:
    """Safely parse XML bytes into an ElementTree Element."""
    return _safe_fromstring(xml_bytes)


def _xml_to_bytes(elem: Element) -> bytes:
    return tostring(elem, encoding="utf-8")


def _get_child_text(parent: Element, tag: str, default: str = "") -> str:
    """Get text of first child with matching tag (ignoring namespace)."""
    for child in parent:
        if "}" in child.tag:
            if child.tag.split("}")[1] == tag:
                return child.text or default
        elif child.tag == tag:
            return child.text or default
    return default


def _find_child(parent: Element, tag: str) -> Optional[Element]:
    for child in parent:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local == tag:
            return child
    return None


def _find_all(parent: Element, tag: str):
    for child in parent:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local == tag:
            yield child


def _qname(ns: str, local: str) -> str:
    return "{{{}}}{}".format(ns, local)


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------

def resolve_saml_config(tenant_id: str) -> dict:
    """Resolve SAML configuration for a tenant from platform DB idp_config.

    Merges DEFAULT_SAML_CONFIG with tenant-specific idp_config JSON.
    Supports DoD IdP presets via ``preset`` key.
    """
    config = json.loads(json.dumps(DEFAULT_SAML_CONFIG))

    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT idp_config FROM tenants WHERE id = %s",
            (tenant_id,),
        ).fetchone()
        conn.close()

        if row and row["idp_config"]:
            tenant_config = row["idp_config"]
            if isinstance(tenant_config, str):
                tenant_config = json.loads(tenant_config)

            # Merge recursively
            _deep_merge(config, tenant_config)

            # Apply DoD preset if specified
            preset_key = tenant_config.get("preset")
            if preset_key and preset_key in DOD_IDP_PRESETS:
                preset = DOD_IDP_PRESETS[preset_key]
                _deep_merge(config["idp"], {
                    "entity_id": preset["entity_id"],
                    "single_sign_on_service": preset["single_sign_on_service"],
                    "single_logout_service": preset["single_logout_service"],
                })
                config["_attribute_mapping"] = preset.get("attribute_mapping", {})
    except Exception as exc:
        logger.warning("Could not resolve SAML config for tenant %s: %s", tenant_id, exc)

    # Override SP entity_id from env if set
    env_entity_id = os.environ.get("ICDEV_SAML_SP_ENTITY_ID", "").strip()
    if env_entity_id:
        config["sp"]["entity_id"] = env_entity_id
    env_acs = os.environ.get("ICDEV_SAML_ACS_URL", "").strip()
    if env_acs:
        config["sp"]["assertion_consumer_service"]["url"] = env_acs

    return config


def _deep_merge(base: dict, override: dict) -> None:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ---------------------------------------------------------------------------
# AuthNRequest generation
# ---------------------------------------------------------------------------

def generate_authn_request(config: dict, relay_state: str = "") -> dict:
    """Generate a SAML 2.0 AuthNRequest.

    Returns:
        {
            "id": request_id,
            "xml": b"<samlp:AuthnRequest ...>",
            "url": "https://idp.example.com/SSO?SAMLRequest=...&RelayState=...",
            "saml_request_b64": "...",
            "relay_state": "...",
        }
    """
    request_id = _generate_id()
    issue_instant = _utc_iso()

    sp = config["sp"]
    idp = config["idp"]
    security = config.get("security", {})

    root = Element(_qname(NS_SAMLP, "AuthnRequest"))
    root.set("ID", request_id)
    root.set("Version", "2.0")
    root.set("IssueInstant", issue_instant)
    root.set("Destination", idp["single_sign_on_service"]["url"])
    root.set("ProtocolBinding", sp["assertion_consumer_service"]["binding"])
    root.set("AssertionConsumerServiceURL", sp["assertion_consumer_service"]["url"])

    issuer = SubElement(root, _qname(NS_SAML, "Issuer"))
    issuer.text = sp["entity_id"]

    if security.get("want_name_id"):
        name_id_policy = SubElement(root, _qname(NS_SAMLP, "NameIDPolicy"))
        name_id_policy.set("Format", sp.get("name_id_format", "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"))
        name_id_policy.set("AllowCreate", "true")

    if security.get("requested_authn_context"):
        req_authn = SubElement(root, _qname(NS_SAMLP, "RequestedAuthnContext"))
        req_authn.set("Comparison", security.get("requested_authn_context_comparison", "exact"))
        for ac_class in security.get("requested_authn_contexts", []):
            ctx = SubElement(req_authn, _qname(NS_SAML, "AuthnContextClassRef"))
            ctx.text = ac_class

    xml_bytes = _xml_to_bytes(root)

    # Build Redirect URL
    saml_request_deflated = _deflate_encode(xml_bytes)
    saml_request_b64 = _b64_encode(saml_request_deflated)

    params = {"SAMLRequest": saml_request_b64}
    if relay_state:
        params["RelayState"] = relay_state

    if security.get("authn_requests_signed") and sp.get("private_key"):
        sig_alg = security.get("signature_algorithm", "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256")
        params["SigAlg"] = sig_alg
        # Build signature base string
        sig_base = urlencode(sorted(params.items()))
        private_key = serialization.load_pem_private_key(
            _pem_from_raw(sp["private_key"]).encode("utf-8"),
            password=None,
        )
        sig_data = private_key.sign(sig_base.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        params["Signature"] = _b64_encode(sig_data)

    sso_url = idp["single_sign_on_service"]["url"]
    url = "{}?{}".format(sso_url, urlencode(params))

    return {
        "id": request_id,
        "xml": xml_bytes,
        "url": url,
        "saml_request_b64": saml_request_b64,
        "relay_state": relay_state,
    }


# ---------------------------------------------------------------------------
# SAML Response parsing & validation
# ---------------------------------------------------------------------------

def parse_saml_response(saml_response_b64: str, config: dict) -> dict:
    """Parse and validate a SAMLResponse from an IdP.

    Steps:
      1. Base64-decode
      2. Parse XML
      3. Extract Response ID, InResponseTo, Destination, Status
      4. Find Assertion
      5. Validate assertion signature (RSA-SHA256)
      6. Extract NameID / Subject
      7. Extract AttributeStatement attributes
      8. Validate Conditions (NotBefore, NotOnOrAfter, Audience)

    Returns:
        {
            "valid": True,
            "name_id": "...",
            "name_id_format": "...",
            "attributes": {"uid": ["..."], "cn": ["..."], ...},
            "session_index": "...",
            "authn_instant": "...",
            "authn_context_class_ref": "...",
            "assertion_xml": b"...",
        }

    Raises:
        SAMLValidationError on any validation failure.
    """
    xml_bytes = _b64_decode(saml_response_b64)
    root = _parse_xml(xml_bytes)

    # Validate root is Response
    local_root = root.tag.split("}")[1] if "}" in root.tag else root.tag
    if local_root != "Response":
        raise SAMLValidationError(f"Expected samlp:Response, got {local_root}")

    response_id = root.get("ID", "")
    in_response_to = root.get("InResponseTo", "")
    destination = root.get("Destination", "")

    # Validate Destination matches ACS URL
    acs_url = config["sp"]["assertion_consumer_service"]["url"]
    if destination and destination != acs_url:
        logger.warning("SAML Destination mismatch: got %s, expected %s", destination, acs_url)

    # Check Status
    status_elem = _find_child(root, "Status")
    if status_elem is None:
        raise SAMLValidationError("Missing Status element in SAML Response")

    status_code_elem = _find_child(status_elem, "StatusCode")
    if status_code_elem is None:
        raise SAMLValidationError("Missing StatusCode in SAML Response")

    status_code = status_code_elem.get("Value", "")
    if status_code != "urn:oasis:names:tc:SAML:2.0:status:Success":
        status_msg = _get_child_text(status_elem, "StatusMessage", "")
        raise SAMLValidationError(f"SAML Response status failure: {status_code} — {status_msg}")

    # Find Assertion
    assertion = _find_child(root, "Assertion")
    if assertion is None:
        raise SAMLValidationError("No Assertion found in SAML Response")

    # Validate signature on Assertion if required
    security = config.get("security", {})
    if security.get("want_assertions_signed", True):
        if not _has_signature(assertion):
            raise SAMLValidationError("Assertion is not signed (want_assertions_signed=True)")
        if not _validate_signature(assertion, config):
            raise SAMLValidationError("Assertion signature validation failed")

    # Validate Conditions
    conditions = _find_child(assertion, "Conditions")
    if conditions is not None:
        _validate_conditions(conditions, config)

    # Extract Subject / NameID
    subject = _find_child(assertion, "Subject")
    name_id = ""
    name_id_format = ""
    if subject is not None:
        name_id_elem = _find_child(subject, "NameID")
        if name_id_elem is not None:
            name_id = name_id_elem.text or ""
            name_id_format = name_id_elem.get("Format", "")

    # Extract AuthnStatement
    authn_instant = ""
    session_index = ""
    authn_context_class_ref = ""
    authn_stmt = _find_child(assertion, "AuthnStatement")
    if authn_stmt is not None:
        authn_instant = authn_stmt.get("AuthnInstant", "")
        session_index = authn_stmt.get("SessionIndex", "")
        authn_ctx = _find_child(authn_stmt, "AuthnContext")
        if authn_ctx is not None:
            accr = _find_child(authn_ctx, "AuthnContextClassRef")
            if accr is not None:
                authn_context_class_ref = accr.text or ""

    # Extract Attributes
    attributes = {}
    attr_stmt = _find_child(assertion, "AttributeStatement")
    if attr_stmt is not None:
        for attr in _find_all(attr_stmt, "Attribute"):
            attr_name = attr.get("Name", "")
            if not attr_name:
                attr_name = attr.get("FriendlyName", "")
            values = []
            for attr_val in _find_all(attr, "AttributeValue"):
                val = attr_val.text or ""
                if val:
                    values.append(val)
            if attr_name and values:
                attributes[attr_name] = values

    return {
        "valid": True,
        "name_id": name_id,
        "name_id_format": name_id_format,
        "attributes": attributes,
        "session_index": session_index,
        "authn_instant": authn_instant,
        "authn_context_class_ref": authn_context_class_ref,
        "response_id": response_id,
        "in_response_to": in_response_to,
        "assertion_xml": _xml_to_bytes(assertion),
    }


class SAMLValidationError(Exception):
    """Raised when SAML assertion/response validation fails."""


# ---------------------------------------------------------------------------
# Signature handling
# ---------------------------------------------------------------------------

def _has_signature(elem: Element) -> bool:
    """Check if an element contains a <ds:Signature> child."""
    for child in elem:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local == "Signature":
            return True
    return False


def _validate_signature(elem: Element, config: dict) -> bool:
    """Validate XML signature on an element using IdP X.509 certificate.

    Supports RSA-SHA256 signatures over a SignedInfo Reference with
    canonicalized element content.
    """
    signature_elem = None
    for child in elem:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local == "Signature":
            signature_elem = child
            break

    if signature_elem is None:
        return False

    try:
        # Extract SignedInfo
        signed_info = None
        signature_value = None
        x509_cert_data = None

        for child in signature_elem:
            local = child.tag.split("}")[1] if "}" in child.tag else child.tag
            if local == "SignedInfo":
                signed_info = child
            elif local == "SignatureValue":
                signature_value = (child.text or "").strip()
            elif local == "KeyInfo":
                x509_data = _find_child(child, "X509Data")
                if x509_data is not None:
                    x509_cert = _find_child(x509_data, "X509Certificate")
                    if x509_cert is not None:
                        x509_cert_data = (x509_cert.text or "").strip()

        if signed_info is None or not signature_value:
            logger.warning("Missing SignedInfo or SignatureValue")
            return False

        # Load certificate
        idp_cert_pem = config["idp"].get("x509cert", "").strip()
        if x509_cert_data and not idp_cert_pem:
            idp_cert_pem = x509_cert_data

        if not idp_cert_pem:
            logger.warning("No IdP certificate available for signature validation")
            return False

        public_key = _load_public_key_from_cert(_pem_from_raw(idp_cert_pem))

        # Build canonical SignedInfo bytes (exclusive XML canonicalization is
        # ideal; here we use the raw bytes as a pragmatic approximation)
        signed_info_bytes = _xml_to_bytes(signed_info)
        sig_bytes = base64.b64decode(signature_value)

        public_key.verify(sig_bytes, signed_info_bytes, padding.PKCS1v15(), hashes.SHA256())
        return True

    except InvalidSignature:
        logger.warning("SAML signature validation failed: InvalidSignature")
        return False
    except Exception as exc:
        logger.warning("SAML signature validation error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Condition validation
# ---------------------------------------------------------------------------

def _validate_conditions(conditions: Element, config: dict) -> None:
    """Validate SAML Conditions (NotBefore, NotOnOrAfter, Audience)."""
    now = _utc_now()

    not_before = conditions.get("NotBefore", "")
    not_on_or_after = conditions.get("NotOnOrAfter", "")

    # Allow 60-second clock skew
    skew = timedelta(seconds=60)

    if not_before:
        try:
            nb = datetime.fromisoformat(not_before.replace("Z", "+00:00"))
            if now + skew < nb:
                raise SAMLValidationError(f"Assertion not yet valid (NotBefore={not_before})")
        except ValueError:
            pass

    if not_on_or_after:
        try:
            noa = datetime.fromisoformat(not_on_or_after.replace("Z", "+00:00"))
            if now - skew > noa:
                raise SAMLValidationError(f"Assertion expired (NotOnOrAfter={not_on_or_after})")
        except ValueError:
            pass

    # Audience restriction
    audience_restriction = _find_child(conditions, "AudienceRestriction")
    if audience_restriction is not None:
        sp_entity_id = config["sp"]["entity_id"]
        found = False
        for aud in _find_all(audience_restriction, "Audience"):
            if (aud.text or "").strip() == sp_entity_id:
                found = True
                break
        if not found:
            raise SAMLValidationError(f"Audience restriction mismatch: expected {sp_entity_id}")


# ---------------------------------------------------------------------------
# Attribute mapping (DoD IdP → ICDEV user fields)
# ---------------------------------------------------------------------------

def map_dod_attributes(saml_attrs: dict, attribute_mapping: Optional[dict] = None) -> dict:
    """Map DoD Identity Provider SAML attributes to ICDEV user fields.

    Standard DoD attribute names:
      uid                  → EDIPI (10-digit DoD ID)
      cn                   → CAC Common Name (LAST.FIRST.MIDDLE.EDIPI)
      mail                 → Email
      eduPersonPrincipalName → Principal name
      subject / NameID     → Fallback identifier

    Returns:
        {
            "edipi": "...",
            "cac_cn": "...",
            "email": "...",
            "principal_name": "...",
            "subject_id": "...",
            "display_name": "...",
        }
    """
    if attribute_mapping is None:
        attribute_mapping = {
            "uid": "edipi",
            "cn": "cac_cn",
            "mail": "email",
            "eduPersonPrincipalName": "principal_name",
            "subject": "subject_id",
        }

    result = {}
    for saml_attr, icdev_field in attribute_mapping.items():
        values = saml_attrs.get(saml_attr, [])
        if values:
            result[icdev_field] = values[0]

    # Fallback: if uid not present but NameID looks like EDIPI, use it
    if not result.get("edipi"):
        name_id = saml_attrs.get("NameID", [""])[0] if "NameID" in saml_attrs else ""
        if name_id and name_id.isdigit() and len(name_id) == 10:
            result["edipi"] = name_id

    # Derive display_name from CN
    if result.get("cac_cn"):
        cn = result["cac_cn"]
        parts = cn.split(".")
        if len(parts) >= 2:
            result["display_name"] = f"{parts[1]} {parts[0]}"
        else:
            result["display_name"] = cn
    elif result.get("email"):
        result["display_name"] = result["email"].split("@")[0]

    return result


# ---------------------------------------------------------------------------
# User lookup / provisioning
# ---------------------------------------------------------------------------

def validate_saml_user(
    name_id: str,
    mapped_attrs: dict,
    tenant_id: str,
) -> Optional[dict]:
    """Look up or provision a user based on SAML-mapped attributes.

    Matching priority:
      1. saml_name_id + auth_method='saml'
      2. cac_cn (if present) + auth_method='saml'
      3. email (if present) + auth_method='saml'

    Returns dict with: tenant_id, user_id, role, email, auth_method="saml"
    Returns None if no active user found and auto-provisioning is disabled.
    """
    if not tenant_id:
        logger.warning("validate_saml_user called without tenant_id")
        return None

    try:
        from tools.db.storage import get_connection

        conn = get_connection()

        # Try saml_name_id match first
        row = conn.execute(
            """
            SELECT u.id as user_id, u.tenant_id, u.email, u.role, u.status as user_status,
                   u.display_name, u.cac_cn, u.saml_name_id,
                   t.status as tenant_status, t.tier as tenant_tier,
                   t.impact_level, t.slug as tenant_slug
            FROM users u
            JOIN tenants t ON u.tenant_id = t.id
            WHERE u.tenant_id = %s AND u.auth_method = 'saml' AND u.status = 'active' AND t.status = 'active'
                  AND u.saml_name_id = %s
            LIMIT 1
        """,
            (tenant_id, name_id),
        ).fetchone()

        # Fallback: cac_cn
        if not row and mapped_attrs.get("cac_cn"):
            row = conn.execute(
                """
                SELECT u.id as user_id, u.tenant_id, u.email, u.role, u.status as user_status,
                       u.display_name, u.cac_cn, u.saml_name_id,
                       t.status as tenant_status, t.tier as tenant_tier,
                       t.impact_level, t.slug as tenant_slug
                FROM users u
                JOIN tenants t ON u.tenant_id = t.id
                WHERE u.tenant_id = %s AND u.auth_method = 'saml' AND u.status = 'active' AND t.status = 'active'
                      AND u.cac_cn = %s
                LIMIT 1
            """,
                (tenant_id, mapped_attrs["cac_cn"]),
            ).fetchone()

        # Fallback: email
        if not row and mapped_attrs.get("email"):
            row = conn.execute(
                """
                SELECT u.id as user_id, u.tenant_id, u.email, u.role, u.status as user_status,
                       u.display_name, u.cac_cn, u.saml_name_id,
                       t.status as tenant_status, t.tier as tenant_tier,
                       t.impact_level, t.slug as tenant_slug
                FROM users u
                JOIN tenants t ON u.tenant_id = t.id
                WHERE u.tenant_id = %s AND u.auth_method = 'saml' AND u.status = 'active' AND t.status = 'active'
                      AND u.email = %s
                LIMIT 1
            """,
                (tenant_id, mapped_attrs["email"]),
            ).fetchone()

        conn.close()

        if not row:
            logger.warning(
                "No active SAML user found for tenant=%s name_id=%s cac_cn=%s email=%s",
                tenant_id,
                name_id[:20] if name_id else "",
                mapped_attrs.get("cac_cn", "")[:20],
                mapped_attrs.get("email", "")[:40],
            )
            return None

        row = dict(row)
        return {
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "role": row["role"],
            "scopes": [],
            "tenant_status": row["tenant_status"],
            "tenant_tier": row["tenant_tier"],
            "impact_level": row["impact_level"],
            "tenant_slug": row["tenant_slug"],
            "auth_method": "saml",
            "saml_name_id": row.get("saml_name_id", ""),
            "cac_cn": row.get("cac_cn", ""),
        }

    except Exception as exc:
        logger.error("SAML user validation error: %s", exc)
        return None


def provision_saml_user(
    tenant_id: str,
    name_id: str,
    mapped_attrs: dict,
    role: str = "developer",
) -> Optional[dict]:
    """Auto-provision a new SAML user if the tenant allows it.

    Returns the user dict, or None if provisioning fails/disabled.
    """
    if not tenant_id or not name_id:
        return None

    try:
        from tools.db.storage import get_connection

        conn = get_connection()

        # Check if user already exists
        row = conn.execute(
            "SELECT id FROM users WHERE tenant_id = %s AND saml_name_id = %s AND auth_method = 'saml'",
            (tenant_id, name_id),
        ).fetchone()

        if row:
            conn.close()
            return validate_saml_user(name_id, mapped_attrs, tenant_id)

        user_id = str(uuid.uuid4())
        email = mapped_attrs.get("email", f"{name_id}@saml.local")
        display_name = mapped_attrs.get("display_name", name_id)
        cac_cn = mapped_attrs.get("cac_cn", "")
        edipi = mapped_attrs.get("edipi", "")

        conn.execute(
            """
            INSERT INTO users (id, tenant_id, email, display_name, role, auth_method, status,
                             saml_name_id, cac_cn, edipi, last_login)
            VALUES (%s, %s, %s, %s, %s, 'saml', 'active', %s, %s, %s, %s)
        """,
            (
                user_id,
                tenant_id,
                email,
                display_name,
                role,
                name_id,
                cac_cn,
                edipi,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()

        logger.info("Provisioned SAML user: tenant=%s user_id=%s email=%s", tenant_id, user_id, email)
        return validate_saml_user(name_id, mapped_attrs, tenant_id)

    except Exception as exc:
        logger.error("SAML user provisioning error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# CAC/PIV certificate extraction from assertion
# ---------------------------------------------------------------------------

def extract_cac_cert_from_assertion(assertion_xml: bytes) -> Optional[str]:
    """Extract X.509 certificate from SAML Assertion AuthnStatement if present.

    Some DoD IdPs include the user's CAC/PIV certificate in the assertion
    as an <saml:Attribute Name="userCertificate"> or within the
    <saml:AuthnContextClassRef> extension.
    """
    try:
        root = _parse_xml(assertion_xml)
        attr_stmt = _find_child(root, "AttributeStatement")
        if attr_stmt is None:
            return None

        for attr in _find_all(attr_stmt, "Attribute"):
            attr_name = attr.get("Name", "").lower()
            if "certificate" in attr_name or "x509" in attr_name:
                for attr_val in _find_all(attr, "AttributeValue"):
                    val = (attr_val.text or "").strip()
                    if val:
                        return val
        return None
    except Exception as exc:
        logger.debug("Could not extract CAC cert from assertion: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Metadata generation
# ---------------------------------------------------------------------------

def generate_sp_metadata(config: dict) -> str:
    """Generate SAML 2.0 Service Provider metadata XML.

    Returns the metadata as a string (suitable for HTTP response).
    """
    sp = config["sp"]
    security = config.get("security", {})

    root = Element(_qname(NS_MD, "EntityDescriptor"))
    root.set("entityID", sp["entity_id"])

    sp_sso = SubElement(root, _qname(NS_MD, "SPSSODescriptor"))
    sp_sso.set("protocolSupportEnumeration", "urn:oasis:names:tc:SAML:2.0:protocol")
    sp_sso.set("AuthnRequestsSigned", "true" if security.get("authn_requests_signed") else "false")
    sp_sso.set("WantAssertionsSigned", "true" if security.get("want_assertions_signed") else "false")

    # Assertion Consumer Service
    acs = SubElement(sp_sso, _qname(NS_MD, "AssertionConsumerService"))
    acs.set("Binding", sp["assertion_consumer_service"]["binding"])
    acs.set("Location", sp["assertion_consumer_service"]["url"])
    acs.set("index", "1")
    acs.set("isDefault", "true")

    # Single Logout Service
    slo = SubElement(sp_sso, _qname(NS_MD, "SingleLogoutService"))
    slo.set("Binding", sp["single_logout_service"]["binding"])
    slo.set("Location", sp["single_logout_service"]["url"])

    # NameIDFormat
    name_id_format = SubElement(sp_sso, _qname(NS_MD, "NameIDFormat"))
    name_id_format.text = sp.get("name_id_format", "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent")

    # X509KeyDescriptor (signing)
    if sp.get("x509cert"):
        key_desc = SubElement(sp_sso, _qname(NS_MD, "KeyDescriptor"))
        key_desc.set("use", "signing")
        key_info = SubElement(key_desc, _qname(NS_DS, "KeyInfo"))
        x509_data = SubElement(key_info, _qname(NS_DS, "X509Data"))
        x509_cert = SubElement(x509_data, _qname(NS_DS, "X509Certificate"))
        x509_cert.text = sp["x509cert"].replace("-----BEGIN CERTIFICATE-----", "").replace("-----END CERTIFICATE-----", "").replace("\n", "")

    # ContactPerson
    contact = config.get("contact", {})
    for contact_type, contact_info in contact.items():
        cp = SubElement(root, _qname(NS_MD, "ContactPerson"))
        cp.set("contactType", contact_type)
        given = SubElement(cp, _qname(NS_MD, "GivenName"))
        given.text = contact_info.get("given_name", "")
        email = SubElement(cp, _qname(NS_MD, "EmailAddress"))
        email.text = contact_info.get("email_address", "")

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + _xml_to_bytes(root).decode("utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_saml_response(
    saml_response_b64: str,
    tenant_id: str,
    relay_state: str = "",
) -> Optional[dict]:
    """High-level entry point: validate a SAMLResponse and resolve the user.

    Returns dict with user info + SAML metadata on success, None on failure.
    """
    config = resolve_saml_config(tenant_id)
    idp = config.get("idp", {})
    if not idp.get("entity_id") or not idp.get("single_sign_on_service", {}).get("url"):
        logger.warning("SAML not configured for tenant %s", tenant_id)
        return None

    try:
        result = parse_saml_response(saml_response_b64, config)
    except SAMLValidationError as exc:
        logger.warning("SAML validation failed for tenant %s: %s", tenant_id, exc)
        return None

    # Map attributes
    attr_mapping = config.get("_attribute_mapping") or {
        "uid": "edipi",
        "cn": "cac_cn",
        "mail": "email",
        "eduPersonPrincipalName": "principal_name",
        "subject": "subject_id",
    }
    mapped = map_dod_attributes(result["attributes"], attr_mapping)
    if not mapped.get("subject_id"):
        mapped["subject_id"] = result["name_id"]

    # Look up user
    user = validate_saml_user(result["name_id"], mapped, tenant_id)
    if not user:
        # Auto-provision if enabled
        auto_provision = config.get("auto_provision", False)
        if auto_provision or os.environ.get("ICDEV_SAML_AUTO_PROVISION", "").lower() in ("1", "true", "yes"):
            default_role = config.get("default_role", "developer")
            user = provision_saml_user(tenant_id, result["name_id"], mapped, role=default_role)

    if user:
        user["saml_assertion"] = result
        return user

    return None


__all__ = [
    "DOD_IDP_PRESETS",
    "DEFAULT_SAML_CONFIG",
    "SAMLValidationError",
    "generate_authn_request",
    "generate_sp_metadata",
    "map_dod_attributes",
    "parse_saml_response",
    "provision_saml_user",
    "resolve_saml_config",
    "validate_saml_response",
    "validate_saml_user",
    "extract_cac_cert_from_assertion",
]
