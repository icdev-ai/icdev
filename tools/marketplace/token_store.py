# CUI // SP-CTI
"""Token Store — local JSON cache for marketplace license tokens.

Persists license tokens to disk so that module gating works offline
(air-gapped environments). Implements a 30-day grace period for
expired tokens per D-MKT-S3.

Per D-MKT-S4: this is part of the 4-file thin client
(module_runtime, license_client, token_store, asset_installer).

Storage: data/license_tokens.json (append/overwrite per slug).

Usage:
    from tools.marketplace.token_store import store_tokens, get_active_slugs
    store_tokens([{"token": {...}, "license_id": "lic-xxx"}])
    active = get_active_slugs()
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger("marketplace.token_store")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TOKEN_FILE = Path(
    os.environ.get(
        "ICDEV_LICENSE_TOKEN_FILE",
        str(BASE_DIR / "data" / "license_tokens.json"),
    )
)

# Grace period in days (D-MKT-S3: 30-day grace for air-gap)
GRACE_PERIOD_DAYS = 30


def _load_tokens() -> List[Dict[str, Any]]:
    """Load tokens from the JSON file."""
    if not TOKEN_FILE.exists():
        return []
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Failed to load token file %s: %s", TOKEN_FILE, exc)
        return []


def _save_tokens(tokens: List[Dict[str, Any]]) -> None:
    """Save tokens to the JSON file."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(
        json.dumps(tokens, indent=2, default=str),
        encoding="utf-8", newline="",
    )


def store_tokens(license_records: List[Dict[str, Any]]) -> int:
    """Store or update license tokens in the local cache.

    Each record should have a 'token' dict and optionally a 'license_id'.
    Tokens are upserted by license_id (or asset_slug as fallback).

    Args:
        license_records: List of dicts with 'token' and optional 'license_id'.

    Returns:
        Number of tokens stored/updated.
    """
    existing = _load_tokens()
    existing_by_id: Dict[str, int] = {}
    for idx, rec in enumerate(existing):
        token = rec.get("token", rec)
        lid = rec.get("license_id") or token.get("license_id", "")
        slug = token.get("asset_slug", "")
        key = lid or slug
        if key:
            existing_by_id[key] = idx

    stored = 0
    for record in license_records:
        token = record.get("token", record)
        lid = record.get("license_id") or token.get("license_id", "")
        slug = token.get("asset_slug", "")
        key = lid or slug
        if not key:
            continue

        entry = {"license_id": lid, "token": token}
        if key in existing_by_id:
            existing[existing_by_id[key]] = entry
        else:
            existing.append(entry)
            existing_by_id[key] = len(existing) - 1
        stored += 1

    _save_tokens(existing)

    # Invalidate module_runtime cache
    try:
        from tools.marketplace.module_runtime import invalidate_cache

        invalidate_cache()
    except Exception:
        pass

    return stored


def get_active_slugs() -> List[str]:
    """Return list of asset slugs with active (non-expired + grace) tokens.

    A token is considered active if:
    - expires_at is in the future, OR
    - expires_at is within the grace period (D-MKT-S3: 30 days)

    Returns:
        List of active asset slug strings.
    """
    tokens = _load_tokens()
    now = datetime.now(timezone.utc)
    grace_cutoff = now - timedelta(days=GRACE_PERIOD_DAYS)
    active = []

    for rec in tokens:
        token = rec.get("token", rec)
        slug = token.get("asset_slug")
        if not slug:
            continue

        expires_str = token.get("expires_at")
        if not expires_str:
            # No expiry = always active
            active.append(slug)
            continue

        try:
            expires_at = datetime.fromisoformat(expires_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            # Unparseable expiry: treat as expired
            continue

        if expires_at >= grace_cutoff:
            active.append(slug)

    return active


def get_token_ids() -> List[str]:
    """Return list of all stored license IDs.

    Returns:
        List of license_id strings.
    """
    tokens = _load_tokens()
    ids = []
    for rec in tokens:
        token = rec.get("token", rec)
        lid = rec.get("license_id") or token.get("license_id")
        if lid:
            ids.append(lid)
    return ids


def get_token_for_slug(slug: str) -> Optional[Dict[str, Any]]:
    """Get the stored token for a given asset slug.

    Args:
        slug: The asset slug.

    Returns:
        Token dict or None if not found.
    """
    tokens = _load_tokens()
    for rec in tokens:
        token = rec.get("token", rec)
        if token.get("asset_slug") == slug:
            return token
    return None


def get_days_until_expiry(slug: str) -> Optional[int]:
    """Get the number of days until a token expires.

    Args:
        slug: The asset slug.

    Returns:
        Number of days (negative if expired), or None if no token found.
    """
    token = get_token_for_slug(slug)
    if not token:
        return None

    expires_str = token.get("expires_at")
    if not expires_str:
        return None

    try:
        expires_at = datetime.fromisoformat(expires_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = expires_at - now
        return delta.days
    except (ValueError, TypeError):
        return None
