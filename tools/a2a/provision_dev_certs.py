#!/usr/bin/env python3
# CUI // SP-CTI
"""Cross-platform development TLS certificate provisioning for ICDEV™ A2A agents.

Generates a self-signed CA and per-agent certificates/keys stored in
`data/certs/`. Works on Windows, macOS, and Linux without external tools.

Usage:
    python tools/a2a/provision_dev_certs.py              # generate all certs
    python tools/a2a/provision_dev_certs.py --check    # verify certs exist
    python tools/a2a/provision_dev_certs.py --clean      # remove and regenerate

Requires: cryptography (pip install cryptography)
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
import sys
from pathlib import Path
from typing import List

# Ensure repo root is on PYTHONPATH
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger("a2a.certs")

CERTS_DIR = ROOT / "data" / "certs"

AGENT_NAMES: List[str] = [
    "orchestrator",
    "architect",
    "builder",
    "compliance",
    "security",
    "infra",
    "knowledge",
    "monitor",
    "mbse",
    "modernization",
    "requirements-analyst",
    "supply-chain",
    "simulation",
    "gateway",
    "devsecops",
    "integration",
]

DAYS_VALID = 365
KEY_SIZE = 2048


try:
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False


def _generate_key():
    """Generate an RSA private key."""
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=KEY_SIZE,
        backend=default_backend(),
    )


def _build_name(common_name: str) -> x509.Name:
    """Build an X509 subject/Issuer name."""
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "DC"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Washington"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ICDEV"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


def _make_ca() -> tuple:
    """Generate CA key and self-signed certificate. Returns (key, cert)."""
    key = _generate_key()
    subject = _build_name("ICDEV Dev CA")
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=DAYS_VALID))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256(), default_backend())
    )
    return key, cert


def _make_agent_cert(agent_name: str, ca_key, ca_cert) -> tuple:
    """Generate an agent key and cert signed by the CA. Returns (key, cert)."""
    key = _generate_key()
    subject = _build_name(f"{agent_name}.icdev.local")

    # Subject Alternative Name for localhost / 127.0.0.1
    san = x509.SubjectAlternativeName(
        [
            x509.DNSName("localhost"),
            x509.DNSName(f"{agent_name}.icdev.local"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=DAYS_VALID))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(san, critical=False)
        .sign(ca_key, hashes.SHA256(), default_backend())
    )
    return key, cert


def _write_pem(path: Path, data: bytes) -> None:
    """Write PEM data to path, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    logger.info("Wrote %s", path)


def _key_to_pem(key) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _cert_to_pem(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def provision(check: bool = False, clean: bool = False) -> dict:
    """Generate or verify development certificates.

    Args:
        check: If True, only verify existing certs and return status.
        clean: If True, remove existing certs before generating.

    Returns:
        Dict with paths and validation results.
    """
    if not _CRYPTO_AVAILABLE:
        raise ImportError(
            "cryptography library is required. Install: pip install cryptography"
        )

    result = {
        "certs_dir": str(CERTS_DIR),
        "ca_cert": str(CERTS_DIR / "ca.crt"),
        "ca_key": str(CERTS_DIR / "ca.key"),
        "agents": {},
        "all_exist": True,
    }

    if clean and CERTS_DIR.exists():
        for f in CERTS_DIR.iterdir():
            f.unlink()
        logger.info("Cleaned existing certs in %s", CERTS_DIR)

    # Check mode
    if check:
        ca_exists = (CERTS_DIR / "ca.crt").exists() and (CERTS_DIR / "ca.key").exists()
        result["ca_exists"] = ca_exists
        if not ca_exists:
            result["all_exist"] = False
        for name in AGENT_NAMES:
            cert_path = CERTS_DIR / f"{name}.crt"
            key_path = CERTS_DIR / f"{name}.key"
            exists = cert_path.exists() and key_path.exists()
            result["agents"][name] = {"exists": exists, "cert": str(cert_path), "key": str(key_path)}
            if not exists:
                result["all_exist"] = False
        return result

    # Generate CA
    logger.info("Generating CA certificate...")
    ca_key, ca_cert = _make_ca()
    _write_pem(CERTS_DIR / "ca.key", _key_to_pem(ca_key))
    _write_pem(CERTS_DIR / "ca.crt", _cert_to_pem(ca_cert))

    # Generate per-agent certs
    for name in AGENT_NAMES:
        logger.info("Generating certificate for %s...", name)
        key, cert = _make_agent_cert(name, ca_key, ca_cert)
        _write_pem(CERTS_DIR / f"{name}.key", _key_to_pem(key))
        _write_pem(CERTS_DIR / f"{name}.crt", _cert_to_pem(cert))
        result["agents"][name] = {
            "cert": str(CERTS_DIR / f"{name}.crt"),
            "key": str(CERTS_DIR / f"{name}.key"),
            "exists": True,
        }

    result["ca_exists"] = True
    logger.info("Development certificates provisioned in %s", CERTS_DIR)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision development TLS certificates for ICDEV™ A2A agents"
    )
    parser.add_argument("--check", action="store_true", help="Verify existing certs")
    parser.add_argument("--clean", action="store_true", help="Remove and regenerate")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    try:
        result = provision(check=args.check, clean=args.clean)
    except Exception as exc:
        logger.error("Certificate provisioning failed: %s", exc)
        sys.exit(1)

    if args.json:
        import json

        print(json.dumps(result, indent=2))
    else:
        print(f"CA cert:  {result['ca_cert']}")
        print(f"CA key:   {result['ca_key']}")
        for name, info in result["agents"].items():
            status = "OK" if info.get("exists") else "MISSING"
            print(f"  [{status}] {name}: {info['cert']}")
        if not result.get("all_exist", True):
            print("\nSome certificates are missing. Re-run without --check to generate.")
            sys.exit(1)
        print("\nAll certificates present.")


if __name__ == "__main__":
    main()
