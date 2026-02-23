#!/usr/bin/env python3
"""ICDEV SaaS — License Generator.
CUI // SP-CTI

Admin tool for generating RSA-SHA256 signed license keys.
Generates offline-verifiable license JSON for on-premises deployments.
"""
import argparse
import base64
import json
import logging
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("saas.licensing.generator")

# Valid tiers and features
VALID_TIERS = ["starter", "pro", "enterprise", "unlimited"]
VALID_FEATURES = [
    "cnssi_1253", "cato", "fedramp", "cmmc", "oscal", "emass",
    "mbse", "modernization", "self_healing", "sbd", "ivv",
]
VALID_IL_LEVELS = ["IL2", "IL4", "IL5", "IL6"]

# Graceful import of cryptography
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    logger.warning(
        "cryptography library not installed — license signing unavailable. "
        "Install with: pip install cryptography"
    )


# ---------------------------------------------------------------------------
# Key pair generation
# ---------------------------------------------------------------------------

def generate_keypair(output_dir: str) -> Dict[str, str]:
    """Generate an RSA 4096-bit key pair for license signing.

    Args:
        output_dir: Directory to write ``license_private_key.pem``
                    and ``license_public_key.pem``.

    Returns:
        dict with ``private_key_path`` and ``public_key_path``.
    """
    if not HAS_CRYPTO:
        raise RuntimeError(
            "cryptography library required for key generation. "
            "Install with: pip install cryptography"
        )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096,
    )

    priv_path = out / "license_private_key.pem"
    with open(priv_path, "wb") as fh:
        fh.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    pub_path = out / "license_public_key.pem"
    with open(pub_path, "wb") as fh:
        fh.write(private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ))

    logger.info("Key pair generated: %s, %s", priv_path, pub_path)
    return {
        "private_key_path": str(priv_path),
        "public_key_path": str(pub_path),
    }


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

def _sign_license(license_data: Dict[str, Any],
                  private_key_path: str) -> str:
    """Sign the canonical license payload with RSA-SHA256.

    Args:
        license_data:     License dict (without ``signature`` field).
        private_key_path: Path to the RSA private key PEM file.

    Returns:
        Base64-encoded RSA-SHA256 signature string.
    """
    if not HAS_CRYPTO:
        raise RuntimeError(
            "cryptography library required for signing. "
            "Install with: pip install cryptography"
        )

    pk_path = Path(private_key_path)
    if not pk_path.exists():
        raise FileNotFoundError(f"Private key not found: {pk_path}")

    with open(pk_path, "rb") as fh:
        private_key = serialization.load_pem_private_key(fh.read(), password=None)

    payload = {k: v for k, v in license_data.items() if k != "signature"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    signature = private_key.sign(
        canonical,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )

    return base64.b64encode(signature).decode("ascii")


# ---------------------------------------------------------------------------
# License generation
# ---------------------------------------------------------------------------

def generate_license(
    customer: str,
    tier: str,
    max_projects: int = -1,
    max_users: int = -1,
    il_levels: Optional[List[str]] = None,
    features: Optional[List[str]] = None,
    expires_days: int = 365,
    private_key_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a signed ICDEV license.

    Args:
        customer:         Customer / organisation name.
        tier:             License tier (starter, pro, enterprise, unlimited).
        max_projects:     Maximum projects (-1 = unlimited).
        max_users:        Maximum users (-1 = unlimited).
        il_levels:        Authorised impact levels (e.g. ["IL4", "IL5"]).
        features:         Enabled feature flags.
        expires_days:     Days until expiry from now.
        private_key_path: Path to RSA private key PEM.  If None,
                          the license is generated *unsigned*.

    Returns:
        Complete license dict including ``signature`` (or empty string
        if unsigned).
    """
    tier = tier.lower()
    if tier not in VALID_TIERS:
        raise ValueError(f"Invalid tier '{tier}'. Valid: {VALID_TIERS}")

    if il_levels is None:
        il_levels = ["IL2", "IL4"]
    if features is None:
        features = []

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=expires_days)

    license_data: Dict[str, Any] = {
        "license_id": f"lic-{uuid.uuid4().hex[:12]}",
        "customer": customer,
        "tier": tier,
        "max_projects": max_projects,
        "max_users": max_users,
        "allowed_il_levels": [lvl.upper() for lvl in il_levels],
        "features": [f.lower() for f in features],
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Sign if private key provided
    if private_key_path:
        license_data["signature"] = _sign_license(license_data, private_key_path)
        logger.info("License signed with %s", private_key_path)
    else:
        license_data["signature"] = ""
        logger.warning("License generated WITHOUT signature (no private key provided)")

    return license_data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ICDEV License Generator — CUI // SP-CTI"
    )
    sub = parser.add_subparsers(dest="command")

    # --- generate ---
    gen = sub.add_parser("generate", help="Generate a signed license key")
    gen.add_argument("--customer", required=True, help="Customer name")
    gen.add_argument("--tier", required=True, choices=VALID_TIERS, help="License tier")
    gen.add_argument("--max-projects", type=int, default=-1, help="Max projects (-1=unlimited)")
    gen.add_argument("--max-users", type=int, default=-1, help="Max users (-1=unlimited)")
    gen.add_argument("--il-levels", nargs="+", default=["IL2", "IL4"],
                     help="Allowed IL levels (e.g. IL4 IL5)")
    gen.add_argument("--features", nargs="+", default=[],
                     help=f"Enabled features: {VALID_FEATURES}")
    gen.add_argument("--expires-days", type=int, default=365, help="Days until expiry")
    gen.add_argument("--private-key", metavar="PATH", help="RSA private key PEM")
    gen.add_argument("--output", metavar="PATH", help="Write license JSON to file")

    # --- generate-keys ---
    keys = sub.add_parser("generate-keys", help="Generate RSA 4096-bit key pair")
    keys.add_argument("--output-dir", required=True, help="Output directory for PEM files")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")

    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "generate-keys":
        result = generate_keypair(args.output_dir)
        print(json.dumps(result, indent=2))
        return

    if args.command == "generate":
        license_data = generate_license(
            customer=args.customer,
            tier=args.tier,
            max_projects=args.max_projects,
            max_users=args.max_users,
            il_levels=args.il_levels,
            features=args.features,
            expires_days=args.expires_days,
            private_key_path=args.private_key,
        )

        output = json.dumps(license_data, indent=2)
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(output + "\n")
            print(f"License written to {out_path}")
        else:
            print(output)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
