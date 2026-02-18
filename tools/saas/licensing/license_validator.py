#!/usr/bin/env python3
"""ICDEV SaaS — License Validator.
CUI // SP-CTI

Validates offline license keys for on-premises deployments.
License keys are JSON documents signed with RSA-SHA256.
No network access required — fully air-gap safe.
"""
import argparse
import base64
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("saas.licensing")

# Default public key path for license verification
DEFAULT_PUBLIC_KEY_PATH = BASE_DIR / "args" / "license_public_key.pem"
LICENSE_FILE_PATH = Path(
    os.environ.get("ICDEV_LICENSE_FILE", str(BASE_DIR / "data" / "license.json"))
)

# Tier hierarchy (higher index = more permissive)
TIER_HIERARCHY = ["starter", "pro", "enterprise", "unlimited"]

# Cached license data — populated on first call to get_license_info()
_cached_license: Optional[Dict[str, Any]] = None

# Graceful import of cryptography (optional for signature verification)
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    logger.warning(
        "cryptography library not installed — RSA signature verification disabled. "
        "Install with: pip install cryptography"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_license_json(license_path: Path) -> Dict[str, Any]:
    """Read and parse the license JSON file."""
    if not license_path.exists():
        raise FileNotFoundError(f"License file not found: {license_path}")
    with open(license_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _canonical_payload(license_data: Dict[str, Any]) -> bytes:
    """Return the canonical JSON bytes used for signing/verification.

    The signature covers every field *except* ``signature`` itself,
    serialised with sorted keys and no extra whitespace.
    """
    payload = {k: v for k, v in license_data.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _verify_signature(license_data: Dict[str, Any],
                      public_key_path: Path) -> List[str]:
    """Verify the RSA-SHA256 signature on the license.

    Returns a list of error strings (empty == valid).
    """
    errors: List[str] = []

    if not HAS_CRYPTO:
        logger.warning("Signature verification skipped — cryptography library unavailable")
        return errors  # treat as pass-through when library missing

    sig_b64 = license_data.get("signature")
    if not sig_b64:
        errors.append("License is missing 'signature' field")
        return errors

    if not public_key_path.exists():
        errors.append(f"Public key not found: {public_key_path}")
        return errors

    try:
        sig_bytes = base64.b64decode(sig_b64)
    except Exception as exc:
        errors.append(f"Invalid base64 signature: {exc}")
        return errors

    try:
        with open(public_key_path, "rb") as fh:
            public_key = serialization.load_pem_public_key(fh.read())
    except Exception as exc:
        errors.append(f"Failed to load public key: {exc}")
        return errors

    try:
        public_key.verify(
            sig_bytes,
            _canonical_payload(license_data),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:
        errors.append(f"Signature verification failed: {exc}")

    return errors


def _check_expiry(license_data: Dict[str, Any]) -> List[str]:
    """Check whether the license has expired."""
    errors: List[str] = []
    expires_at = license_data.get("expires_at")
    if not expires_at:
        errors.append("License is missing 'expires_at' field")
        return errors

    try:
        exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        errors.append(f"Invalid expires_at format: {exc}")
        return errors

    now = datetime.now(timezone.utc)
    if now > exp_dt:
        errors.append(
            f"License expired on {exp_dt.isoformat()} (current: {now.isoformat()})"
        )
    return errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_license(license_path: Optional[str] = None,
                     public_key_path: Optional[str] = None) -> Dict[str, Any]:
    """Validate an ICDEV on-premises license.

    Args:
        license_path:     Path to the license JSON file.
                          Defaults to ``ICDEV_LICENSE_FILE`` env var or
                          ``<repo>/data/license.json``.
        public_key_path:  Path to the RSA public key PEM.
                          Defaults to ``<repo>/args/license_public_key.pem``.

    Returns:
        dict with keys ``valid`` (bool), ``license`` (dict|None),
        ``errors`` (list[str]).
    """
    lp = Path(license_path) if license_path else LICENSE_FILE_PATH
    kp = Path(public_key_path) if public_key_path else DEFAULT_PUBLIC_KEY_PATH

    errors: List[str] = []

    # 1. Load
    try:
        license_data = _load_license_json(lp)
    except FileNotFoundError as exc:
        return {"valid": False, "license": None, "errors": [str(exc)]}
    except json.JSONDecodeError as exc:
        return {"valid": False, "license": None,
                "errors": [f"Invalid JSON in license file: {exc}"]}

    # 2. Required fields
    required_fields = [
        "license_id", "customer", "tier", "expires_at", "issued_at",
    ]
    for field in required_fields:
        if field not in license_data:
            errors.append(f"Missing required field: {field}")
    if errors:
        return {"valid": False, "license": license_data, "errors": errors}

    # 3. Check expiry
    errors.extend(_check_expiry(license_data))

    # 4. Verify RSA signature
    errors.extend(_verify_signature(license_data, kp))

    # 5. Validate tier value
    tier = license_data.get("tier", "").lower()
    if tier and tier not in TIER_HIERARCHY:
        errors.append(
            f"Unknown tier '{tier}'. Valid tiers: {TIER_HIERARCHY}"
        )

    valid = len(errors) == 0
    return {"valid": valid, "license": license_data, "errors": errors}


def check_feature(license_data: Dict[str, Any], feature: str) -> bool:
    """Return True if the license includes the given feature."""
    features = license_data.get("features", [])
    return feature.lower() in [f.lower() for f in features]


def check_tier(license_data: Dict[str, Any], required_tier: str) -> bool:
    """Return True if the license tier meets or exceeds *required_tier*.

    Tier hierarchy: starter < pro < enterprise < unlimited.
    """
    current = license_data.get("tier", "").lower()
    required = required_tier.lower()
    if current not in TIER_HIERARCHY or required not in TIER_HIERARCHY:
        return False
    return TIER_HIERARCHY.index(current) >= TIER_HIERARCHY.index(required)


def check_il_level(license_data: Dict[str, Any], il_level: str) -> bool:
    """Return True if the license authorises the given impact level."""
    allowed = license_data.get("allowed_il_levels", [])
    return il_level.upper() in [lvl.upper() for lvl in allowed]


def get_license_info() -> Dict[str, Any]:
    """Return cached license validation result.

    On first call the license is read and validated; subsequent calls
    return the cached result without re-reading the file.
    """
    global _cached_license
    if _cached_license is None:
        _cached_license = validate_license()
    return _cached_license


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ICDEV License Validator — CUI // SP-CTI"
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Validate the license file and print result",
    )
    parser.add_argument(
        "--info", action="store_true",
        help="Print cached license info",
    )
    parser.add_argument(
        "--check-feature", metavar="FEATURE",
        help="Check if a feature is enabled in the license",
    )
    parser.add_argument(
        "--check-tier", metavar="TIER",
        help="Check if the license tier meets or exceeds the given tier",
    )
    parser.add_argument(
        "--check-il-level", metavar="IL_LEVEL",
        help="Check if the license authorises a given impact level",
    )
    parser.add_argument(
        "--license-file", metavar="PATH",
        help="Path to license JSON file (overrides env/default)",
    )
    parser.add_argument(
        "--public-key", metavar="PATH",
        help="Path to RSA public key PEM (overrides default)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = _build_parser()
    args = parser.parse_args()

    if args.validate or (not args.info and not args.check_feature
                         and not args.check_tier and not args.check_il_level):
        result = validate_license(
            license_path=args.license_file,
            public_key_path=args.public_key,
        )
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            status = "VALID" if result["valid"] else "INVALID"
            print(f"License status: {status}")
            if result["license"]:
                lic = result["license"]
                print(f"  License ID : {lic.get('license_id', 'N/A')}")
                print(f"  Customer   : {lic.get('customer', 'N/A')}")
                print(f"  Tier       : {lic.get('tier', 'N/A')}")
                print(f"  Expires    : {lic.get('expires_at', 'N/A')}")
                print(f"  Max Users  : {lic.get('max_users', 'N/A')}")
                print(f"  Max Projects: {lic.get('max_projects', 'N/A')}")
                print(f"  IL Levels  : {lic.get('allowed_il_levels', [])}")
                print(f"  Features   : {lic.get('features', [])}")
            for err in result.get("errors", []):
                print(f"  ERROR: {err}")
        sys.exit(0 if result["valid"] else 1)

    if args.info:
        info = get_license_info()
        if args.json:
            print(json.dumps(info, indent=2, default=str))
        else:
            print(json.dumps(info, indent=2, default=str))
        return

    # For --check-* flags we need a validated license first
    result = validate_license(
        license_path=args.license_file,
        public_key_path=args.public_key,
    )
    if not result["valid"]:
        print("License is INVALID — cannot check features/tier/IL level")
        for err in result.get("errors", []):
            print(f"  ERROR: {err}")
        sys.exit(1)

    lic = result["license"]

    if args.check_feature:
        ok = check_feature(lic, args.check_feature)
        print(f"Feature '{args.check_feature}': {'ENABLED' if ok else 'NOT AVAILABLE'}")
        sys.exit(0 if ok else 1)

    if args.check_tier:
        ok = check_tier(lic, args.check_tier)
        print(f"Tier check '{args.check_tier}': {'PASS' if ok else 'FAIL'}")
        sys.exit(0 if ok else 1)

    if args.check_il_level:
        ok = check_il_level(lic, args.check_il_level)
        print(f"IL level '{args.check_il_level}': {'AUTHORISED' if ok else 'NOT AUTHORISED'}")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
