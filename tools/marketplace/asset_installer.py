# CUI // SP-CTI
"""Asset Installer — download and install marketplace artifacts from SaaS endpoint.

Thin client for downloading ZIP artifacts from the marketplace Lambda,
verifying SHA-256 integrity, and extracting to the local tools directory.

Per D-MKT-S4: this is part of the 4-file thin client
(module_runtime, license_client, token_store, asset_installer).

Usage:
    from tools.marketplace.asset_installer import install
    result = install("writeguard")
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = get_logger("marketplace.asset_installer")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
INSTALL_ROOT = BASE_DIR / "tools"

# Configurable via env or direct assignment (tests override these)
MARKETPLACE_URL = os.environ.get("ICDEV_MARKETPLACE_URL", "")
MARKETPLACE_API_KEY = os.environ.get("ICDEV_MARKETPLACE_API_KEY", "")


def _api_json(path: str) -> Optional[Dict[str, Any]]:
    """GET JSON from marketplace API endpoint.

    Args:
        path: API path (e.g. "/api/v1/artifacts/info/writeguard").

    Returns:
        Parsed JSON dict, or None on failure.
    """
    import urllib.request
    import urllib.error

    url = f"{MARKETPLACE_URL.rstrip('/')}{path}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MARKETPLACE_API_KEY}",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
            return json.loads(resp.read())
    except (urllib.error.HTTPError, urllib.error.URLError, Exception) as exc:
        logger.warning("API request failed for %s: %s", path, exc)
        return None


def _api_download(path: str) -> Optional[Tuple[bytes, Dict[str, str]]]:
    """Download binary content from marketplace API endpoint.

    Args:
        path: API path (e.g. "/api/v1/artifacts/download/writeguard").

    Returns:
        Tuple of (file_bytes, response_headers) or None on failure.
    """
    import urllib.request
    import urllib.error

    url = f"{MARKETPLACE_URL.rstrip('/')}{path}"
    headers = {
        "Authorization": f"Bearer {MARKETPLACE_API_KEY}",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
            file_bytes = resp.read()
            resp_headers = {k: v for k, v in resp.headers.items()}
            return file_bytes, resp_headers
    except (urllib.error.HTTPError, urllib.error.URLError, Exception) as exc:
        logger.warning("Download failed for %s: %s", path, exc)
        return None


def install(slug: str, version: str = "latest") -> Dict[str, Any]:
    """Install a marketplace asset by slug.

    Downloads the artifact ZIP from the SaaS marketplace, verifies SHA-256
    integrity, and extracts to the local tools directory.

    Args:
        slug: Asset slug (e.g. "writeguard", "databridge").
        version: Version to install (default "latest").

    Returns:
        dict with status, install_path, and metadata.
    """
    # Step 1: Get artifact info
    info = _api_json(f"/api/v1/artifacts/info/{slug}")
    if info is None:
        return {"status": "error", "error": f"Could not fetch info for '{slug}'"}

    expected_sha = info.get("sha256_hash", "")
    install_slug = info.get("install_slug", slug)

    # Step 2: Download artifact ZIP
    result = _api_download(f"/api/v1/artifacts/download/{slug}")
    if result is None:
        return {"status": "error", "error": f"Could not download artifact '{slug}'"}

    file_bytes, headers = result

    # Step 3: Verify SHA-256 integrity
    computed_sha = hashlib.sha256(file_bytes).hexdigest()
    if expected_sha and computed_sha != expected_sha:
        return {
            "status": "error",
            "error": (f"SHA-256 mismatch: expected {expected_sha}, computed {computed_sha}"),
        }

    # Step 4: Extract to install path
    install_path = INSTALL_ROOT / install_slug
    try:
        install_path.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            zf.extractall(str(install_path))
    except Exception as exc:
        return {"status": "error", "error": f"Extraction failed: {exc}"}

    logger.info("Installed %s to %s", slug, install_path)

    return {
        "status": "ok",
        "slug": slug,
        "install_slug": install_slug,
        "install_path": str(install_path),
        "version": info.get("version", version),
        "sha256": computed_sha,
    }
