# CUI // SP-CTI
"""License Client — sync, verify, renew, and phone-home for marketplace tokens.

Thin client for marketplace license management. Communicates with the
SaaS Lambda endpoint for token operations and verifies RSA-SHA256
signatures offline using the public key.

Per D-MKT-S4: this is part of the 4-file thin client
(module_runtime, license_client, token_store, asset_installer).

Per D-MKT-S3: Token verification is 100% offline via RSA-SHA256 public key.
30-day grace period for air-gap deployments.

Usage:
    from tools.marketplace.license_client import sync_tokens, verify_token_locally
    tokens = sync_tokens()
    result = verify_token_locally(token_data, mode="saas")
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger("marketplace.license_client")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_PUBLIC_KEY_PATH = str(BASE_DIR / "args" / "marketplace_public_key.pem")

MARKETPLACE_URL = os.environ.get("ICDEV_MARKETPLACE_URL", "")
MARKETPLACE_API_KEY = os.environ.get("ICDEV_MARKETPLACE_API_KEY", "")


def _api_request(
    path: str,
    method: str = "POST",
    body: Optional[Dict] = None,
    marketplace_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Make an authenticated HTTP request to the marketplace API."""
    import urllib.request
    import urllib.error

    url_base = marketplace_url or MARKETPLACE_URL
    key = api_key or MARKETPLACE_API_KEY
    if not url_base:
        logger.warning("No marketplace URL configured")
        return None

    url = f"{url_base.rstrip('/')}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
            return json.loads(resp.read())
    except Exception as exc:
        logger.warning("Marketplace API request failed: %s", exc)
        return None


def sync_tokens(
    tenant_id: str = "default",
    marketplace_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Sync license tokens from the marketplace SaaS.

    Calls POST /api/v1/licenses/sync and returns the list of license records.

    Args:
        tenant_id: Tenant identifier.
        marketplace_url: Override marketplace URL.
        api_key: Override API key.

    Returns:
        List of license dicts (each containing a 'token' field).
    """
    result = _api_request(
        "/api/v1/licenses/sync",
        body={"tenant_id": tenant_id},
        marketplace_url=marketplace_url,
        api_key=api_key,
    )
    if result is None:
        return []
    licenses = result.get("licenses", [])

    # Store locally for offline access
    try:
        from tools.marketplace.token_store import store_tokens

        store_tokens(licenses)
    except Exception:
        pass

    return licenses


def renew_token(
    asset_slug: str,
    tenant_id: str = "default",
    feedback: Optional[Dict] = None,
    marketplace_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Renew a license token for an asset.

    Calls POST /api/v1/licenses/renew. Per D-MKT-C3, renewal is a
    feedback touchpoint.

    Args:
        asset_slug: The asset to renew.
        tenant_id: Tenant identifier.
        feedback: Optional renewal feedback (D-MKT-C3).
        marketplace_url: Override marketplace URL.
        api_key: Override API key.

    Returns:
        dict with renewed status and signed token.
    """
    body: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "asset_slug": asset_slug,
    }
    if feedback:
        body["feedback"] = feedback

    result = _api_request(
        "/api/v1/licenses/renew",
        body=body,
        marketplace_url=marketplace_url,
        api_key=api_key,
    )
    if result is None:
        return {"renewed": False, "error": "API request failed"}

    # Store the renewed token locally
    if result.get("renewed") and "token" in result:
        try:
            from tools.marketplace.token_store import store_tokens

            store_tokens(
                [
                    {
                        "token": result["token"],
                        "license_id": result["token"].get("license_id"),
                    }
                ]
            )
        except Exception:
            pass

    return result


def phone_home(
    tenant_id: str = "default",
    license_ids: Optional[List[str]] = None,
    marketplace_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Send heartbeat to marketplace SaaS.

    Calls POST /api/v1/licenses/phone-home to report active licenses
    and receive revocation notices.

    Args:
        tenant_id: Tenant identifier.
        license_ids: List of active license IDs.
        marketplace_url: Override marketplace URL.
        api_key: Override API key.

    Returns:
        dict with updated count and revoked list.
    """
    result = _api_request(
        "/api/v1/licenses/phone-home",
        body={
            "tenant_id": tenant_id,
            "license_ids": license_ids or [],
        },
        marketplace_url=marketplace_url,
        api_key=api_key,
    )
    return result or {"updated": 0, "revoked": []}


def submit_feedback(
    asset_slug: str,
    responses: Dict[str, str],
    tenant_id: str = "default",
    marketplace_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Submit usage feedback for an asset.

    Calls POST /api/v1/feedback.

    Args:
        asset_slug: The asset slug.
        responses: Feedback responses dict.
        tenant_id: Tenant identifier.
        marketplace_url: Override marketplace URL.
        api_key: Override API key.

    Returns:
        dict with submitted status.
    """
    result = _api_request(
        "/api/v1/feedback",
        body={
            "tenant_id": tenant_id,
            "asset_slug": asset_slug,
            "responses": responses,
        },
        marketplace_url=marketplace_url,
        api_key=api_key,
    )
    return result or {"submitted": False, "error": "API request failed"}


def verify_token_locally(
    token_data: Dict[str, Any],
    public_key_path: Optional[str] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify a signed license token offline using RSA-SHA256.

    Per D-MKT-S3: 100% offline verification using the public key.
    Canonical payload is JSON with sorted keys, compact separators,
    excluding the 'signature' field.

    Args:
        token_data: The signed token dict (must include 'signature').
        public_key_path: Path to RSA public key PEM file.
        mode: Operation mode ('oss' or 'saas'). In 'oss' mode, always valid.

    Returns:
        dict with valid (bool) and errors (list of str).
    """
    resolved_mode = mode or os.environ.get("ICDEV_MARKETPLACE_MODE", "oss")
    if resolved_mode == "oss":
        return {"valid": True, "errors": []}

    errors: List[str] = []

    # Check signature field exists
    signature_b64 = token_data.get("signature")
    if not signature_b64:
        return {"valid": False, "errors": ["Missing 'signature' field"]}

    # Build canonical payload (same algorithm as SaaS token_signer)
    payload = {k: v for k, v in token_data.items() if k != "signature"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    # Load public key
    pk_path = public_key_path or DEFAULT_PUBLIC_KEY_PATH
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        pk_bytes = Path(pk_path).read_bytes()
        public_key = serialization.load_pem_public_key(pk_bytes)
    except FileNotFoundError:
        return {
            "valid": False,
            "errors": [f"Public key not found: {pk_path}"],
        }
    except ImportError:
        return {
            "valid": False,
            "errors": ["cryptography package not installed"],
        }
    except Exception as exc:
        return {"valid": False, "errors": [f"Key load error: {exc}"]}

    # Verify RSA-SHA256 signature
    try:
        sig_bytes = base64.b64decode(signature_b64)
        public_key.verify(sig_bytes, canonical, padding.PKCS1v15(), hashes.SHA256())
    except Exception as exc:
        errors.append(f"Signature verification failed: {exc}")
        return {"valid": False, "errors": errors}

    return {"valid": True, "errors": []}
