# CUI // SP-CTI
"""PKI Certificate Validation — verify chain, expiry, and mTLS configuration.

Usage:
    python tools/pki/validate.py --check-chain --cert args/pki/orchestrator-cert.pem
    python tools/pki/validate.py --check-expiry --warn-days 30
    python tools/pki/validate.py --audit-env
    python tools/pki/validate.py --all --json
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Optional

try:
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.x509.oid import NameOID
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_PKI_DIR = BASE_DIR / "args" / "pki"

_REQUIRED_MTLS_ENV_VARS = [
    "ICDEV_MTLS_CLIENT_CERT",
    "ICDEV_MTLS_CLIENT_KEY",
    "ICDEV_MTLS_CA_BUNDLE",
]


def _load_cert(path: str | Path) -> "x509.Certificate":
    if not _CRYPTO_AVAILABLE:
        raise ImportError("cryptography package required")
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Certificate not found: {path}")
    return x509.load_pem_x509_certificate(p.read_bytes())


def check_chain(cert_path: str, ca_path: str) -> dict:
    """Verify cert_path is signed by the CA at ca_path.

    Returns:
        {"valid": bool, "cn": str, "issuer": str, "error": str|None}
    """
    try:
        cert = _load_cert(cert_path)
        ca_cert = _load_cert(ca_path)

        cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        cn = cn_attrs[0].value if cn_attrs else "(unknown)"
        issuer_attrs = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
        issuer = issuer_attrs[0].value if issuer_attrs else "(unknown)"

        # Verify signature using CA public key
        ca_pub = ca_cert.public_key()
        ca_pub.verify(
            cert.signature,
            cert.tbs_certificate_bytes,
            padding.PKCS1v15(),
            cert.signature_hash_algorithm,
        )

        return {"valid": True, "cn": cn, "issuer": issuer, "error": None}
    except Exception as exc:
        return {"valid": False, "cn": "", "issuer": "", "error": str(exc)}


def check_expiry(cert_path: str, warn_days: int = 30) -> dict:
    """Check certificate expiry.

    Returns:
        {"cn": str, "expires": str, "days_left": int, "expired": bool, "warning": bool}
    """
    try:
        cert = _load_cert(cert_path)
        cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        cn = cn_attrs[0].value if cn_attrs else Path(cert_path).stem

        expiry = cert.not_valid_after_utc
        now = datetime.datetime.now(datetime.timezone.utc)
        days_left = (expiry - now).days

        return {
            "cn": cn,
            "file": str(cert_path),
            "expires": expiry.strftime("%Y-%m-%d"),
            "days_left": days_left,
            "expired": days_left < 0,
            "warning": 0 <= days_left < warn_days,
            "ok": days_left >= warn_days,
        }
    except Exception as exc:
        return {"cn": "", "file": str(cert_path), "error": str(exc), "ok": False}


def check_expiry_all(pki_dir: Optional[Path] = None, warn_days: int = 30) -> list:
    """Check expiry for all *-cert.pem files in the PKI directory."""
    d = pki_dir or DEFAULT_PKI_DIR
    if not d.exists():
        return []
    results = []
    for f in sorted(d.glob("*-cert.pem")):
        results.append(check_expiry(str(f), warn_days=warn_days))
    return results


def audit_env() -> dict:
    """Check that all required mTLS env vars are set and the files exist.

    Returns:
        {"configured": bool, "vars": {name: {"set": bool, "file_exists": bool}}}
    """
    vars_status: dict = {}
    all_ok = True

    for var in _REQUIRED_MTLS_ENV_VARS:
        val = os.environ.get(var)
        if val:
            file_exists = Path(val).exists()
            vars_status[var] = {"set": True, "value": val, "file_exists": file_exists}
            if not file_exists:
                all_ok = False
        else:
            vars_status[var] = {"set": False, "file_exists": False}
            all_ok = False

    enforce = os.environ.get("ICDEV_MTLS_ENFORCE", "false").lower() in ("1", "true", "yes")
    verify = os.environ.get("ICDEV_MTLS_VERIFY", "true").lower() not in ("0", "false", "no")

    return {
        "configured": all_ok,
        "enforce_mode": enforce,
        "tls_verify": verify,
        "vars": vars_status,
    }


def audit_all(pki_dir: Optional[Path] = None, warn_days: int = 30) -> dict:
    """Full audit: env vars + all certificate expiry checks.

    Returns:
        {"ok": bool, "env": dict, "certs": list, "issues": list}
    """
    env = audit_env()
    certs = check_expiry_all(pki_dir=pki_dir, warn_days=warn_days)

    issues = []
    if not env["configured"]:
        missing = [v for v, s in env["vars"].items() if not s["set"]]
        issues.append(f"Missing env vars: {', '.join(missing)}")

    for c in certs:
        if c.get("expired"):
            issues.append(f"EXPIRED: {c.get('file')} (CN={c.get('cn')})")
        elif c.get("warning"):
            issues.append(f"EXPIRY WARNING: {c.get('file')} expires in {c.get('days_left')} days")

    ok = env["configured"] and not any(c.get("expired") for c in certs)
    return {"ok": ok, "env": env, "certs": certs, "issues": issues}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ICDEV™ PKI Certificate Validator")
    parser.add_argument("--check-chain", action="store_true", help="Verify cert is signed by CA")
    parser.add_argument("--cert", help="Certificate path to validate")
    parser.add_argument("--ca", help="CA certificate path")
    parser.add_argument("--check-expiry", action="store_true", help="Check certificate expiry")
    parser.add_argument("--warn-days", type=int, default=30, help="Days before expiry to warn")
    parser.add_argument("--audit-env", action="store_true", help="Audit mTLS env vars")
    parser.add_argument("--all", action="store_true", help="Full audit (env + all certs)")
    parser.add_argument("--pki-dir", help="PKI directory (default: args/pki/)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    pki_dir = Path(args.pki_dir) if args.pki_dir else None

    try:
        if args.all:
            result = audit_all(pki_dir=pki_dir, warn_days=args.warn_days)
        elif args.check_chain:
            if not args.cert:
                parser.error("--check-chain requires --cert")
            ca = args.ca or str((pki_dir or DEFAULT_PKI_DIR) / "ca-cert.pem")
            result = check_chain(args.cert, ca)
        elif args.check_expiry:
            if args.cert:
                result = check_expiry(args.cert, warn_days=args.warn_days)
            else:
                result = check_expiry_all(pki_dir=pki_dir, warn_days=args.warn_days)
        elif args.audit_env:
            result = audit_env()
        else:
            parser.print_help()
            sys.exit(0)

        if args.json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, list):
                for item in result:
                    if item.get("expired"):
                        print(f"  [EXPIRED] {item.get('file')}  expires={item.get('expires')}")
                    elif item.get("warning"):
                        print(f"  [WARN]    {item.get('file')}  days_left={item.get('days_left')}")
                    else:
                        print(f"  [OK]      {item.get('file')}  days_left={item.get('days_left')}")
            else:
                print(json.dumps(result, indent=2, default=str))

    except Exception as exc:
        if args.json_output:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
