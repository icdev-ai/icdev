# CUI // SP-CTI
"""PKI Certificate Management — generate CA, server, and client certificates.

Generates a self-signed root CA and issues TLS certificates for all ICDEV™
agent servers and API clients.  All output goes under args/pki/ by default;
override with --out-dir or PKI_OUT_DIR env var.

Usage:
    python tools/pki/generate.py --generate-ca
    python tools/pki/generate.py --generate-server --cn orchestrator --san localhost,127.0.0.1
    python tools/pki/generate.py --generate-client --cn agent-client
    python tools/pki/generate.py --generate-all --json
    python tools/pki/generate.py --list --json

Outputs per certificate:
    <name>-cert.pem   X.509 certificate (PEM)
    <name>-key.pem    Private key (PEM, 0600)
    ca-cert.pem       Root CA certificate (shared)
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
import json
import os
import stat
import sys
from pathlib import Path
from typing import List, Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT_DIR = BASE_DIR / "args" / "pki"

# All 15 A2A agents plus the dashboard and API gateway
AGENT_NAMES = [
    "orchestrator",
    "architect",
    "builder",
    "compliance",
    "security",
    "infrastructure",
    "knowledge",
    "monitor",
    "mbse",
    "modernization",
    "requirements-analyst",
    "supply-chain",
    "simulation",
    "integration",
    "devsecops-zta",
    "gateway",
    "dashboard",
    "api-gateway",
]


def _out_dir() -> Path:
    d = Path(os.environ.get("PKI_OUT_DIR", str(DEFAULT_OUT_DIR)))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _generate_key(key_size: int = 4096) -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=key_size)


def _save_key(key: rsa.RSAPrivateKey, path: Path) -> None:
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)
    # Restrict to owner read-only
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # Windows — best effort


def _save_cert(cert: x509.Certificate, path: Path) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _parse_san(san_str: str) -> List[x509.GeneralName]:
    """Convert comma-separated SANs to x509.GeneralName list."""
    names: List[x509.GeneralName] = []
    for token in san_str.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            addr = ipaddress.ip_address(token)
            names.append(x509.IPAddress(addr))
        except ValueError:
            names.append(x509.DNSName(token))
    return names


def generate_ca(
    org: str = "ICDEV PKI Authority",
    validity_days: int = 3650,
    key_size: int = 4096,
    out_dir: Optional[Path] = None,
) -> dict:
    """Generate a self-signed root CA certificate.

    Returns:
        {"cert": str(path), "key": str(path), "created": bool}
    """
    out = out_dir or _out_dir()
    cert_path = out / "ca-cert.pem"
    key_path = out / "ca-key.pem"

    key = _generate_key(key_size)
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
        x509.NameAttribute(NameOID.COMMON_NAME, f"{org} Root CA"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=validity_days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    _save_key(key, key_path)
    _save_cert(cert, cert_path)
    return {"cert": str(cert_path), "key": str(key_path), "created": True}


def generate_server_cert(
    cn: str,
    san: str = "localhost,127.0.0.1",
    org: str = "ICDEV PKI Authority",
    validity_days: int = 365,
    key_size: int = 2048,
    out_dir: Optional[Path] = None,
) -> dict:
    """Issue a server TLS certificate signed by the local CA.

    Returns:
        {"cert": str(path), "key": str(path), "ca": str(path)}
    """
    out = out_dir or _out_dir()
    ca_cert_path = out / "ca-cert.pem"
    ca_key_path = out / "ca-key.pem"

    if not ca_cert_path.exists() or not ca_key_path.exists():
        raise FileNotFoundError(
            f"CA not found in {out}. Run --generate-ca first."
        )

    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)

    key = _generate_key(key_size)
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
    ])
    san_names = _parse_san(san) or [x509.DNSName("localhost")]
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=validity_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(san_names),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    cert_path = out / f"{cn}-cert.pem"
    key_path = out / f"{cn}-key.pem"
    _save_key(key, key_path)
    _save_cert(cert, cert_path)
    return {"cert": str(cert_path), "key": str(key_path), "ca": str(ca_cert_path)}


def generate_client_cert(
    cn: str,
    org: str = "ICDEV PKI Authority",
    validity_days: int = 365,
    key_size: int = 2048,
    out_dir: Optional[Path] = None,
) -> dict:
    """Issue a client authentication certificate signed by the local CA.

    Returns:
        {"cert": str(path), "key": str(path), "ca": str(path)}
    """
    out = out_dir or _out_dir()
    ca_cert_path = out / "ca-cert.pem"
    ca_key_path = out / "ca-key.pem"

    if not ca_cert_path.exists() or not ca_key_path.exists():
        raise FileNotFoundError(
            f"CA not found in {out}. Run --generate-ca first."
        )

    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)

    key = _generate_key(key_size)
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=validity_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    cert_path = out / f"{cn}-cert.pem"
    key_path = out / f"{cn}-key.pem"
    _save_key(key, key_path)
    _save_cert(cert, cert_path)
    return {"cert": str(cert_path), "key": str(key_path), "ca": str(ca_cert_path)}


def generate_all(
    org: str = "ICDEV PKI Authority",
    out_dir: Optional[Path] = None,
) -> dict:
    """Generate CA + server + client certs for all ICDEV™ agents.

    Returns dict with results for every name generated.
    """
    out = out_dir or _out_dir()
    results: dict = {}

    results["ca"] = generate_ca(org=org, out_dir=out)

    for name in AGENT_NAMES:
        san = f"{name},localhost,127.0.0.1"
        results[f"server:{name}"] = generate_server_cert(
            cn=name, san=san, org=org, out_dir=out
        )
        results[f"client:{name}"] = generate_client_cert(
            cn=f"{name}-client", org=org, out_dir=out
        )

    # Generic API client cert
    results["client:api-client"] = generate_client_cert(
        cn="api-client", org=org, out_dir=out
    )

    results["out_dir"] = str(out)
    results["total"] = len(results) - 2  # exclude meta keys
    return results


def list_certs(out_dir: Optional[Path] = None) -> list:
    """List all PEM certificate files with their CN and expiry."""
    out = out_dir or _out_dir()
    results = []
    if not out.exists():
        return results

    for cert_file in sorted(out.glob("*-cert.pem")):
        try:
            cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
            cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            cn = cn_attrs[0].value if cn_attrs else cert_file.stem
            expiry = cert.not_valid_after_utc
            days_left = (expiry - datetime.datetime.now(datetime.timezone.utc)).days
            results.append({
                "file": cert_file.name,
                "cn": cn,
                "expires": expiry.strftime("%Y-%m-%d"),
                "days_left": days_left,
                "expired": days_left < 0,
                "warning": 0 <= days_left < 30,
            })
        except Exception as exc:
            results.append({"file": cert_file.name, "error": str(exc)})

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ PKI Certificate Manager")
    parser.add_argument("--generate-ca", action="store_true", help="Generate root CA")
    parser.add_argument("--generate-server", action="store_true", help="Generate server cert")
    parser.add_argument("--generate-client", action="store_true", help="Generate client cert")
    parser.add_argument("--generate-all", action="store_true", help="Generate CA + all agent certs")
    parser.add_argument("--list", action="store_true", help="List existing certificates")
    parser.add_argument("--cn", default="icdev-agent", help="Common name for cert")
    parser.add_argument(
        "--san",
        default="localhost,127.0.0.1",
        help="Comma-separated SANs (hostnames or IPs)",
    )
    parser.add_argument("--org", default="ICDEV PKI Authority", help="Organization name")
    parser.add_argument("--validity-days", type=int, default=365, help="Cert validity (days)")
    parser.add_argument("--key-size", type=int, default=2048, help="RSA key size (bits)")
    parser.add_argument("--out-dir", help="Output directory (default: args/pki/)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    out = Path(args.out_dir) if args.out_dir else None

    try:
        if args.generate_all:
            result = generate_all(org=args.org, out_dir=out)
        elif args.generate_ca:
            result = generate_ca(org=args.org, validity_days=args.validity_days, out_dir=out)
        elif args.generate_server:
            result = generate_server_cert(
                cn=args.cn,
                san=args.san,
                org=args.org,
                validity_days=args.validity_days,
                key_size=args.key_size,
                out_dir=out,
            )
        elif args.generate_client:
            result = generate_client_cert(
                cn=args.cn,
                org=args.org,
                validity_days=args.validity_days,
                key_size=args.key_size,
                out_dir=out,
            )
        elif args.list:
            result = list_certs(out_dir=out)
        else:
            parser.print_help()
            sys.exit(0)

        if args.json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, list):
                for item in result:
                    status = "EXPIRED" if item.get("expired") else ("WARN" if item.get("warning") else "OK")
                    print(f"  [{status}] {item.get('file')}  CN={item.get('cn')}  expires={item.get('expires')}  days_left={item.get('days_left')}")
            else:
                for k, v in result.items():
                    print(f"  {k}: {v}")

    except Exception as exc:
        if args.json_output:
            print(json.dumps({"status": "error", "error": str(exc)}))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
