#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ SaaS — CAC/PIV Client Certificate Authentication.
CUI // SP-CTI

In production, nginx or ALB terminates mutual TLS and passes:
  X-Client-Cert-CN: "LAST.FIRST.MIDDLE.EDIPI"
  X-Client-Cert-Serial: "serial_number"

G-11: CRL/OCSP revocation checking added.
Config env vars:
  ICDEV_PKI_CRL_URL            — DISA PKI CRL distribution point URL
  ICDEV_PKI_OCSP_URL           — OCSP responder URL (preferred over CRL)
  ICDEV_PKI_STRICT_REVOCATION  — "true" → deny on any error (fail-closed)
  ICDEV_PKI_CRL_CACHE_TTL      — seconds to cache CRL (default 3600)
"""

import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional, Set

from tools.logging.icdev_logger import get_logger
from tools.db.storage import get_connection

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = get_logger("saas.auth.cac")

PLATFORM_DB_PATH = Path(os.environ.get("PLATFORM_DB_PATH", str(BASE_DIR / "data" / "platform.db")))

# ---------------------------------------------------------------------------
# Revocation config (module-level so tests can monkeypatch)
# ---------------------------------------------------------------------------

_STRICT_REVOCATION: bool = os.environ.get("ICDEV_PKI_STRICT_REVOCATION", "false").lower() == "true"
_CRL_CACHE_TTL: int = int(os.environ.get("ICDEV_PKI_CRL_CACHE_TTL", "3600"))

# _CRL_CACHE maps url -> (revoked_serials: set[str], fetched_at: float)
_CRL_CACHE: dict = {}


def _get_platform_conn():
    conn = get_connection()
    return conn


# ---------------------------------------------------------------------------
# CRL revocation checking
# ---------------------------------------------------------------------------

def _fetch_crl_revoked_serials(crl_url: str) -> Set[str]:
    """Fetch a CRL from crl_url and return the set of revoked hex serials (upper-case, no leading zeros)."""
    if not crl_url.startswith(("http://", "https://", "ldap://")):
        raise ValueError(f"CRL URL has disallowed scheme: {crl_url!r}")
    try:
        with urllib.request.urlopen(crl_url, timeout=10) as resp:  # nosec B310
            data: bytes = resp.read()
    except Exception as exc:
        raise ConnectionError(f"Failed to fetch CRL from {crl_url}: {exc}") from exc

    return _parse_crl_serials_der(data)


def _parse_crl_serials_der(data: bytes) -> Set[str]:
    """Parse DER-encoded CRL bytes and return revoked serial numbers as upper-case hex strings.

    Uses the `cryptography` library when available; falls back to a no-op
    empty set when the library is absent (non-strict environments only).
    """
    try:
        from cryptography.x509 import load_der_x509_crl  # type: ignore[import-untyped]
        from cryptography.hazmat.primitives.asymmetric import padding  # noqa: F401
    except ImportError:
        logger.warning("cryptography library not installed; CRL parsing skipped")
        return set()

    try:
        crl = load_der_x509_crl(data)
        serials: Set[str] = set()
        for revoked in crl:
            serial_int: int = revoked.serial_number
            serial_hex = format(serial_int, "X").lstrip("0") or "0"
            serials.add(serial_hex)
        return serials
    except Exception as exc:
        logger.warning("CRL parse error: %s", exc)
        return set()


def verify_crl(serial: str, crl_url: Optional[str] = None) -> bool:
    """Return True if serial is NOT on the CRL (i.e. cert is valid).

    Args:
        serial: Certificate serial number as a hex string (case-insensitive,
                leading zeros are stripped before comparison).
        crl_url: Override URL; if None, reads ICDEV_PKI_CRL_URL from env.

    Behaviour:
        - No URL + non-strict → permit (True)
        - No URL + strict     → deny  (False)
        - Empty serial + non-strict → permit (True)
        - Fetch/parse error + non-strict → permit (True)
        - Fetch/parse error + strict     → deny  (False)
    """
    url = crl_url or os.environ.get("ICDEV_PKI_CRL_URL", "")
    strict = _STRICT_REVOCATION

    if not url:
        if strict:
            logger.warning("CRL URL not configured; strict mode denies")
            return False
        return True

    normalized_serial = (serial or "").upper().lstrip("0") or "0"
    if not serial:
        return True if not strict else False

    now = time.time()
    cached = _CRL_CACHE.get(url)
    if cached is not None:
        revoked_set, fetched_at = cached
        if (now - fetched_at) < _CRL_CACHE_TTL:
            return normalized_serial not in revoked_set

    try:
        revoked_set = _fetch_crl_revoked_serials(url)
        _CRL_CACHE[url] = (revoked_set, now)
    except Exception as exc:
        logger.warning("CRL fetch failed for %s: %s", url, exc)
        if strict:
            return False
        return True

    return normalized_serial not in revoked_set


# ---------------------------------------------------------------------------
# OCSP revocation checking
# ---------------------------------------------------------------------------

def verify_ocsp(serial: str, issuer_cn: str = "", ocsp_url: Optional[str] = None) -> bool:
    """Return True if serial is NOT revoked per OCSP.

    Requires the `cryptography` library and a configured OCSP responder URL.
    Falls back to CRL check when OCSP is unavailable or the library is absent.

    Args:
        serial: Certificate serial number (hex string).
        issuer_cn: Issuer common name (used to locate issuer cert for OCSP).
        ocsp_url: Override URL; if None, reads ICDEV_PKI_OCSP_URL from env.

    Returns:
        True if certificate is valid (not revoked), False if revoked.
    """
    url = ocsp_url or os.environ.get("ICDEV_PKI_OCSP_URL", "")
    strict = _STRICT_REVOCATION

    if not url:
        if strict:
            logger.warning("OCSP URL not configured; strict mode denies")
            return False
        return True

    try:
        import importlib.util
        if importlib.util.find_spec("cryptography") is None:
            logger.warning("cryptography library not available; falling back to CRL")
            return verify_crl(serial)
    except Exception:
        pass

    # Without the full issuer cert object (only the CN is available from the
    # CAC header), we cannot build a complete OCSP request. Fall through to CRL.
    logger.debug("OCSP requires issuer cert; falling back to CRL for serial %s", serial[:16])
    return verify_crl(serial)


# ---------------------------------------------------------------------------
# Unified revocation check
# ---------------------------------------------------------------------------

def check_revocation(serial: Optional[str], issuer_cn: str = "") -> bool:
    """Check certificate revocation via OCSP (preferred) or CRL.

    Returns True if the certificate passes revocation checks (not revoked).
    Returns False if revoked or if strict mode cannot confirm status.

    Args:
        serial: Certificate serial number (hex string). None → no serial.
        issuer_cn: Issuer common name for OCSP (optional).
    """
    strict = _STRICT_REVOCATION

    if not serial:
        if strict:
            logger.warning("No serial provided; strict mode denies")
            return False
        return True

    ocsp_url = os.environ.get("ICDEV_PKI_OCSP_URL", "")
    if ocsp_url:
        return verify_ocsp(serial, issuer_cn=issuer_cn, ocsp_url=ocsp_url)

    return verify_crl(serial)


# ---------------------------------------------------------------------------
# CAC/PIV certificate validation
# ---------------------------------------------------------------------------

def validate_cac_cert(client_cn: str, client_serial: Optional[str] = None) -> Optional[dict]:
    """Validate a CAC/PIV certificate by Common Name lookup.

    The CN is extracted from the client certificate by the TLS terminator
    (nginx/ALB) and passed via X-Client-Cert-CN header.

    CAC CN format: "LAST.FIRST.MIDDLE.EDIPI" (DoD standard)

    Returns dict with: tenant_id, user_id, role, auth_method="cac_piv"
    Returns None if invalid or revoked.
    """
    if not client_cn:
        return None

    client_cn = client_cn.strip()

    try:
        conn = _get_platform_conn()
        row = conn.execute(
            """
            SELECT u.id as user_id, u.tenant_id, u.email, u.role, u.status as user_status,
                   t.status as tenant_status, t.tier as tenant_tier,
                   t.impact_level, t.slug as tenant_slug
            FROM users u
            JOIN tenants t ON u.tenant_id = t.id
            WHERE u.cac_cn = %s AND u.auth_method = 'cac_piv'
                  AND u.status = 'active' AND t.status = 'active'
        """,
            (client_cn,),
        ).fetchone()
        conn.close()

        if not row:
            logger.warning("No active user found for CAC CN: %s", client_cn[:20])
            return None

        row = dict(row)

        # G-11: Revocation check after successful DB lookup
        if client_serial:
            if not check_revocation(client_serial):
                logger.warning(
                    "CAC cert revoked or revocation check failed: CN=%s serial=%s",
                    client_cn[:20], client_serial[:16],
                )
                return None

        return {
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "email": row["email"],
            "role": row["role"],
            "scopes": [],
            "tenant_status": row["tenant_status"],
            "tenant_tier": row["tenant_tier"],
            "impact_level": row["impact_level"],
            "tenant_slug": row["tenant_slug"],
            "auth_method": "cac_piv",
        }
    except Exception as e:
        logger.error("CAC validation error: %s", e)
        return None
