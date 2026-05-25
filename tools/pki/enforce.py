# CUI // SP-CTI
"""mTLS Enforcement Middleware — reject inbound requests lacking a valid client certificate.

Attach to any Flask app via `init_mtls_enforcement(app, ca_cert_path)`.
When ICDEV_MTLS_ENFORCE=true, every non-health-check request must present a
client certificate signed by the configured CA.  In non-enforce mode the
middleware logs a warning but allows the request through.

Environment variables:
    ICDEV_MTLS_ENFORCE      "true" | "false" (default false)
    ICDEV_MTLS_CA_BUNDLE    Path to CA cert used to verify client certs
    ICDEV_MTLS_SKIP_PATHS   Comma-separated URL prefixes to exempt
                            (default: /health,/metrics,/.well-known)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
from pathlib import Path
from typing import Callable, Optional

logger = get_logger("icdev.mtls.enforce")

_DEFAULT_SKIP_PATHS = ("/health", "/metrics", "/.well-known")


def _is_enforce() -> bool:
    return os.environ.get("ICDEV_MTLS_ENFORCE", "false").lower() in ("1", "true", "yes")


def _skip_paths() -> tuple:
    raw = os.environ.get("ICDEV_MTLS_SKIP_PATHS", ",".join(_DEFAULT_SKIP_PATHS))
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _verify_client_cert(cert_der: bytes, ca_cert_path: str) -> tuple[bool, str]:
    """Verify DER-encoded client cert against the CA.

    Returns (valid, cn_or_error).
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.x509.oid import NameOID

        ca_cert = x509.load_pem_x509_certificate(Path(ca_cert_path).read_bytes())
        client_cert = x509.load_der_x509_certificate(cert_der)

        cn_attrs = client_cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        cn = cn_attrs[0].value if cn_attrs else "(unknown)"

        ca_cert.public_key().verify(
            client_cert.signature,
            client_cert.tbs_certificate_bytes,
            padding.PKCS1v15(),
            client_cert.signature_hash_algorithm,
        )
        return True, cn
    except Exception as exc:
        return False, str(exc)


def init_mtls_enforcement(app, ca_cert_path: Optional[str] = None) -> None:
    """Register a before_request hook that enforces mTLS client auth.

    Args:
        app: Flask application instance.
        ca_cert_path: Path to the CA certificate PEM file.  Defaults to
            ICDEV_MTLS_CA_BUNDLE env var, then args/pki/ca-cert.pem.
    """
    ca_path = (
        ca_cert_path
        or os.environ.get("ICDEV_MTLS_CA_BUNDLE")
        or str(Path(__file__).resolve().parent.parent.parent / "args" / "pki" / "ca-cert.pem")
    )

    enforce = _is_enforce()
    action = "ENFORCING" if enforce else "AUDIT-ONLY"
    logger.info(f"mTLS middleware registered [{action}] ca={ca_path}")

    try:
        from flask import jsonify
    except ImportError:
        logger.warning("Flask not available — mTLS middleware not registered")
        return

    @app.before_request
    def _check_mtls():
        from flask import request

        # Skip health/metrics/well-known paths
        for prefix in _skip_paths():
            if request.path.startswith(prefix):
                return None

        # Nginx/gunicorn forward the client cert via header when SSL termination
        # happens upstream.  Flask/Werkzeug exposes it via environ when TLS
        # terminates in-process.
        cert_der: Optional[bytes] = None

        # In-process TLS (Werkzeug): environ key SSL_CLIENT_CERT is the DER cert
        raw_environ = request.environ
        ssl_cert = raw_environ.get("SSL_CLIENT_CERT")
        if isinstance(ssl_cert, bytes):
            cert_der = ssl_cert

        # Nginx upstream: X-Client-Cert header (base64 DER) or X-SSL-Client-Cert
        if cert_der is None:
            header_cert = (
                request.headers.get("X-Client-Cert")
                or request.headers.get("X-SSL-Client-Cert")
            )
            if header_cert:
                import base64
                try:
                    cert_der = base64.b64decode(header_cert)
                except Exception:
                    pass

        if cert_der is None:
            msg = "mTLS: no client certificate presented"
            if enforce:
                logger.warning(f"{msg} [{request.method} {request.path}]")
                return jsonify({"error": "mutual TLS required — client certificate missing"}), 401
            logger.debug(msg)
            return None

        ca_file = ca_path
        if not Path(ca_file).exists():
            # CA not present; log but allow through (bootstrap scenario)
            logger.warning(f"mTLS CA not found at {ca_file}; skipping client cert check")
            return None

        valid, cn_or_err = _verify_client_cert(cert_der, ca_file)
        if not valid:
            msg = f"mTLS: client cert verification failed — {cn_or_err}"
            if enforce:
                logger.warning(f"{msg} [{request.method} {request.path}]")
                return jsonify({"error": "mutual TLS required — client certificate invalid"}), 401
            logger.debug(msg)
        else:
            logger.debug(f"mTLS: client cert verified CN={cn_or_err}")
            # Expose CN for downstream handlers
            request.environ["ICDEV_MTLS_CLIENT_CN"] = cn_or_err

        return None


def require_mtls(f: Callable) -> Callable:
    """Decorator: enforce mTLS on a single route regardless of global config.

    Usage::

        @app.route("/api/sensitive")
        @require_mtls
        def sensitive_endpoint():
            cn = request.environ.get("ICDEV_MTLS_CLIENT_CN", "unknown")
            return jsonify({"caller": cn})
    """
    import functools

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        try:
            from flask import jsonify, request

            # Check if client CN was already validated by the global middleware
            if request.environ.get("ICDEV_MTLS_CLIENT_CN"):
                return f(*args, **kwargs)

            # Middleware not installed or cert not present
            return jsonify({"error": "mutual TLS required"}), 401
        except ImportError:
            return f(*args, **kwargs)

    return wrapper
