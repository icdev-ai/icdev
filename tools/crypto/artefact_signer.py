# CUI // SP-CTI
# Authored in both tools/crypto/ and icdev/tools/crypto/ — keep the two in sync.
"""The factory's artefact key: ECDSA P-256 / SHA-256 from a KMS, the OS keyring or a keyless certificate (icx-ent-03).

``key_manager.py`` signs audit payloads and falls back to HMAC or to nothing when no key is set, because audit logging
must never break. An ARTEFACT signature may do neither: whoever verifies it holds a public key, so a MAC or a no-op is
refused here by construction. This module is ICDEV[IT]'s side of ICDEV[OS]'s one machine-key story
(``icdev_os/mesh/signers.py`` ``open_artefact_signer``, osx-ent-05): the same spec syntax, the same algorithm and the
same key id, so a key pinned in ICDEV[OS] is the key named here.

    open_signer("kms:alias/icdev-factory?region=us-gov-west-1")   ECC_NIST_P256 / ECDSA_SHA_256 through the FIPS endpoint
    open_signer("keyring:icdev-factory/signing")                  the OS keyring holds the unencrypted PKCS#8 PEM
    open_signer("file:/tmp/...pem")                               TESTS ONLY: a throwaway key in the temporary directory
    open_keyless(cfg)                                             an ephemeral key and a Fulcio certificate for it

Nothing here creates, stores or prints a long-lived key: setting the factory key is a site's or the owner's step, and
never the owner's operator or release key. Every signature is verified against the public key before it is returned.

**Keyless** (Sigstore Fulcio v2, ``POST /api/v2/signingCert``; fulcio.proto, read 2026-11-21) needs a CONFIGURED OIDC
issuer and Fulcio URL: there is no default, so an air-gapped site never reaches Sigstore's public-good instance by
accident. The proof of possession is a signature over the OIDC token's ``sub`` claim (fulcio.proto,
docs/how-certificate-issuing-works.md). The leaf certificate must carry the ephemeral key and name the configured
issuer (Fulcio's OIDC Issuer (V2) extension, OID 1.3.6.1.4.1.57264.1.8), or it is refused.

**The transparency log is OFF by default** (``transparency_log.mode: off``) and a build records that it is. With
``mode: rekor`` and a configured URL each bundle's DSSE envelope is submitted as a ``dsse`` 0.0.1 entry
(``POST /api/v1/log/entries``, Rekor openapi.yaml; HTTP 201) and what the log returned is recorded beside it. The
log's own signature (its signed entry timestamp) is NOT verified here: no log key is pinned, so a recorded entry is a
receipt, not a proof. Outbound HTTP is https (plain http only to a loopback literal, for tests), never follows a
redirect, caps the response, and holds one deadline over the whole exchange.
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Protocol

ALG = "ecdsa-p256-sha256"
KMS_MAX_RAW = 4096
KEYRING_DEFAULT = ("icdev-factory", "signing")
_P256_SPKI = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")
FULCIO_ISSUER_V2 = "1.3.6.1.4.1.57264.1.8"
MAX_RESPONSE = 1024 * 1024
LOOPBACK = ("127.0.0.1", "::1")


class SigningError(RuntimeError):
    """The key cannot sign: ``code`` says why (no_key, bad_key, kms, keyring, keyless, tlog, http)."""

    def __init__(self, message: str, code: str = "bad_key"):
        super().__init__(message)
        self.code = code


class ArtefactSigner(Protocol):
    mode: str

    def spki(self) -> bytes: ...

    def sign(self, message: bytes) -> bytes: ...


# ── the key's identity ──────────────────────────────────────────────────────


def key_id(spki_der: bytes) -> str:
    """SHA-256 of the SubjectPublicKeyInfo DER, hex (a Sigstore bundle's hint is the same digest in base64)."""
    return hashlib.sha256(spki_der).hexdigest()


def pem(spki_der: bytes) -> str:
    b = base64.b64encode(spki_der).decode()
    return "-----BEGIN PUBLIC KEY-----\n" + "\n".join(b[i:i + 64] for i in range(0, len(b), 64)) + \
        "\n-----END PUBLIC KEY-----\n"


def spki_from_pem(text: str | bytes) -> bytes:
    """A P-256 public key's SubjectPublicKeyInfo DER, from PEM; any other key is refused."""
    from cryptography.hazmat.primitives import serialization

    raw = text.encode() if isinstance(text, str) else text
    try:
        key = serialization.load_pem_public_key(raw.strip())
    except ValueError as exc:
        raise SigningError("not a PEM public key") from exc
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if not (len(der) == 91 and der.startswith(_P256_SPKI)):
        raise SigningError("an artefact public key must be ECDSA P-256")
    return der


def verify(spki_der: bytes, message: bytes, signature_der: bytes) -> bool:
    """ECDSA P-256 / SHA-256 over ``message``."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    if not (len(spki_der) == 91 and spki_der.startswith(_P256_SPKI)):
        return False
    try:
        serialization.load_der_public_key(spki_der).verify(signature_der, message, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def _checked(s, message: bytes, sig: bytes) -> bytes:
    if not verify(s.spki(), message, sig):
        raise SigningError("the key returned a signature that does not verify under its own public key")
    return sig


# ── a PEM key: the keyring's, or a throwaway test file ──────────────────────


class PemSigner:
    """A P-256 key held as PEM. RFC 6979 deterministic ECDSA, so the same key and statement give the same bytes."""

    mode = "key"

    def __init__(self, pem_bytes: bytes, *, source: str):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        try:
            key = serialization.load_pem_private_key(pem_bytes, password=None)
        except (ValueError, TypeError) as exc:
            raise SigningError("an artefact key is an unencrypted PKCS#8 PEM private key") from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
            raise SigningError("an artefact key must be ECDSA P-256")
        self._key, self.source = key, source

    def spki(self) -> bytes:
        from cryptography.hazmat.primitives import serialization

        return self._key.public_key().public_bytes(serialization.Encoding.DER,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo)

    def sign(self, message: bytes) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        return _checked(self, message, self._key.sign(message, ec.ECDSA(hashes.SHA256(), deterministic_signing=True)))


class KmsSigner:
    """An ECC_NIST_P256 key in AWS KMS, reached only through the FIPS endpoint. The private key never leaves KMS. A
    message up to KMS's RAW limit is sent whole, a longer one as its SHA-256 (the same ECDSA_SHA_256 signature)."""

    mode = "key"

    def __init__(self, key: str, region: str | None, client=None):
        if client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:
                raise SigningError("a kms: key needs boto3 (pip install boto3)", "kms") from exc
            client = boto3.client("kms", region_name=region, config=Config(use_fips_endpoint=True))
        self.key, self._client, self._spki = key, client, None
        self.source = f"kms:{key}"

    def spki(self) -> bytes:
        if self._spki is None:
            try:
                der = bytes(self._client.get_public_key(KeyId=self.key)["PublicKey"])
            except Exception as exc:
                raise SigningError(f"KMS did not return the key's public key ({type(exc).__name__})", "kms") from exc
            if not (len(der) == 91 and der.startswith(_P256_SPKI)):
                raise SigningError("the KMS key is not ECC_NIST_P256", "kms")
            self._spki = der
        return self._spki

    def sign(self, message: bytes) -> bytes:
        raw = len(message) <= KMS_MAX_RAW
        try:
            sig = self._client.sign(KeyId=self.key, Message=message if raw else hashlib.sha256(message).digest(),
                                    MessageType="RAW" if raw else "DIGEST",
                                    SigningAlgorithm="ECDSA_SHA_256")["Signature"]
        except Exception as exc:
            raise SigningError(f"KMS did not sign ({type(exc).__name__})", "kms") from exc
        return _checked(self, message, bytes(sig))


def _keyring_pem(service: str, name: str, backend=None) -> bytes:
    if backend is None:
        try:
            import keyring as backend
        except ImportError as exc:
            raise SigningError("a keyring: key needs the keyring package (pip install keyring)", "keyring") from exc
    try:
        value = backend.get_password(service, name)
    except Exception as exc:
        raise SigningError(f"the OS keyring could not be read ({type(exc).__name__})", "keyring") from exc
    if not value:
        raise SigningError(f"the OS keyring holds no key at {service}/{name}: setting it is the site's or the owner's",
                           "no_key")
    return value.encode() if isinstance(value, str) else value


def _test_key_root() -> Path:
    """Where a throwaway ``file:`` test key may live (review L1): the system's real temporary root, ``/tmp`` resolved,
    never ``tempfile.gettempdir()``, which honours TMPDIR and so would let an environment variable widen it. On
    Windows (no /tmp) it is the platform's temporary directory."""
    import os
    import tempfile

    return Path("/tmp").resolve() if os.name == "posix" else Path(tempfile.gettempdir()).resolve()  # nosec B108


def open_signer(spec: str | None, *, kms_client=None, keyring_backend=None) -> ArtefactSigner:
    """``kms:<key>?region=<region>``, ``keyring:<service>/<name>`` or ``file:<path>`` (tests) -- nothing else, and
    nothing is created. No key configured is refused (``no_key``), never a MAC or an unsigned build."""
    spec = str(spec or "").strip()
    if not spec:
        raise SigningError("no factory signing key is configured (args/factory_builds.yaml signing.key): setting it "
                           "is the site's or the owner's", "no_key")
    if spec.startswith("kms:"):
        key, _, query = spec[len("kms:"):].partition("?")
        region = (urllib.parse.parse_qs(query).get("region") or [None])[0]
        if not key or not region:
            raise SigningError("a kms: key is kms:<key id or alias>?region=<region>", "kms")
        return KmsSigner(key, region, client=kms_client)
    if spec.startswith("keyring:"):
        service, _, name = spec[len("keyring:"):].partition("/")
        return PemSigner(_keyring_pem(service or KEYRING_DEFAULT[0], name or KEYRING_DEFAULT[1], keyring_backend),
                         source=f"keyring:{service or KEYRING_DEFAULT[0]}/{name or KEYRING_DEFAULT[1]}")
    if spec.startswith("file:"):
        p = Path(spec[len("file:"):]).resolve()
        if not p.is_relative_to(_test_key_root()):
            raise SigningError("a file: key is a THROWAWAY test key in the temporary directory; a real key is kms: or "
                               "keyring:")
        if not p.is_file():
            raise SigningError(f"no key at {p}: a key is never created here", "no_key")
        return PemSigner(p.read_bytes(), source="file:(test key)")
    raise SigningError("an artefact key is kms:<key>?region=<region>, keyring:<service>/<name> or file:<path>")


# ── outbound HTTP: https only, no redirects, one deadline, a size cap ──────


def _allowed(url: str) -> bool:
    u = urllib.parse.urlsplit(url)
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in LOOPBACK)


def post_json(url: str, body: dict, *, deadline_s: float = 30.0, headers: dict | None = None,
              code: str = "http") -> tuple[int, Any]:
    """POST ``body`` as JSON; ``(status, parsed JSON)``. Refuses every 3xx, any scheme but https (http only to a
    loopback literal), a response over :data:`MAX_RESPONSE`; the deadline covers connect, TLS, headers and body."""
    import http.client
    import ssl

    if not _allowed(url):
        raise SigningError("an outbound endpoint is https (http only to a loopback literal, for a test)", code)
    u = urllib.parse.urlsplit(url)
    if u.scheme == "https":
        conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=deadline_s,
                                           context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=deadline_s)
    fired = threading.Event()

    def _cut():
        fired.set()
        sock = getattr(conn, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    timer = threading.Timer(deadline_s, _cut)
    timer.daemon = True
    timer.start()
    try:
        raw = json.dumps(body, sort_keys=True).encode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        conn.request("POST", path, body=raw, headers={"Content-Type": "application/json",
                                                       "Accept": "application/json", **(headers or {})})
        resp = conn.getresponse()
        if 300 <= resp.status < 400:
            raise SigningError(f"the endpoint answered a redirect ({resp.status}); redirects are never followed", code)
        data = resp.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            raise SigningError("the endpoint's answer is larger than allowed", code)
        status = resp.status
    except SigningError:
        raise
    except Exception as exc:
        why = "the deadline passed" if fired.is_set() else type(exc).__name__
        raise SigningError(f"the endpoint could not be reached ({why})", code) from exc
    finally:
        timer.cancel()
        conn.close()
    try:
        return status, json.loads(data or b"null")
    except (ValueError, RecursionError) as exc:
        raise SigningError("the endpoint's answer is not JSON", code) from exc


# ── keyless: an ephemeral key and a Fulcio certificate ─────────────────────


def _jwt_claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError) as exc:
        raise SigningError("the OIDC token is not a JWT", "keyless") from exc


class KeylessSigner:
    """An ephemeral P-256 key whose public key a CONFIGURED Fulcio certified for a CONFIGURED OIDC issuer. Its bundles
    carry the leaf certificate (Sigstore bundle v0.3 ``certificate``), not a public-key hint."""

    mode = "keyless"

    def __init__(self, *, issuer: str, fulcio_url: str, token: str, deadline_s: float = 30.0):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        claims = _jwt_claims(token)
        if str(claims.get("iss") or "") != issuer:
            raise SigningError("the OIDC token is not from the configured issuer", "keyless")
        sub = str(claims.get("sub") or "")
        if not sub:
            raise SigningError("the OIDC token names no subject", "keyless")
        self._key = ec.generate_private_key(ec.SECP256R1())
        spki_pem = self._key.public_key().public_bytes(serialization.Encoding.PEM,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        pop = self._key.sign(sub.encode(), ec.ECDSA(hashes.SHA256()))
        status, out = post_json(fulcio_url.rstrip("/") + "/api/v2/signingCert", {
            "credentials": {"oidcIdentityToken": token},
            "publicKeyRequest": {"publicKey": {"algorithm": "ECDSA", "content": spki_pem},
                                 "proofOfPossession": base64.b64encode(pop).decode()}},
            deadline_s=deadline_s, code="keyless")
        if status not in (200, 201) or not isinstance(out, dict):
            raise SigningError(f"Fulcio refused the certificate request (HTTP {status})", "keyless")
        sc = out.get("signedCertificateEmbeddedSct") or out.get("signedCertificateDetachedSct") or {}
        chain = ((sc.get("chain") or {}).get("certificates")) if isinstance(sc, dict) else None
        if not isinstance(chain, list) or not chain or not all(isinstance(c, str) for c in chain):
            raise SigningError("Fulcio returned no certificate chain", "keyless")
        try:
            leaf = x509.load_pem_x509_certificate(chain[0].encode())
        except ValueError as exc:
            raise SigningError("Fulcio's leaf certificate does not parse", "keyless") from exc
        if leaf.public_key().public_bytes(serialization.Encoding.DER,
                                          serialization.PublicFormat.SubjectPublicKeyInfo) != self.spki():
            raise SigningError("Fulcio's certificate is not for the key that asked", "keyless")
        try:
            ext = leaf.extensions.get_extension_for_oid(x509.ObjectIdentifier(FULCIO_ISSUER_V2)).value.value
            named = _der_utf8(ext)
        except Exception as exc:
            raise SigningError("Fulcio's certificate names no OIDC issuer", "keyless") from exc
        if named != issuer:
            raise SigningError("Fulcio's certificate names another OIDC issuer", "keyless")
        self.certificate_der = leaf.public_bytes(serialization.Encoding.DER)
        self.chain_pem = list(chain)
        self.identity = {"issuer": issuer, "subject": sub, "fulcio": fulcio_url}
        self.source = f"keyless:{issuer}"

    def spki(self) -> bytes:
        from cryptography.hazmat.primitives import serialization

        return self._key.public_key().public_bytes(serialization.Encoding.DER,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo)

    def sign(self, message: bytes) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        return _checked(self, message, self._key.sign(message, ec.ECDSA(hashes.SHA256())))


def _der_utf8(raw: bytes) -> str:
    """The value of a DER UTF8String (tag 0x0c) with a short or long length."""
    if not raw or raw[0] != 0x0C:
        raise ValueError("not a UTF8String")
    n, i = raw[1], 2
    if n & 0x80:
        k = n & 0x7F
        n, i = int.from_bytes(raw[2:2 + k], "big"), 2 + k
    return raw[i:i + n].decode("utf-8")


def open_keyless(keyless: dict, *, root: Path, deadline_s: float = 30.0) -> KeylessSigner:
    """``keyless``: ``{oidc_issuer, fulcio_url, token_file}`` from the reviewed config. All three are needed: there
    is no default issuer or Fulcio, so nothing reaches a public instance unless a site configured it."""
    issuer, fulcio, tf = (str((keyless or {}).get(k) or "").strip() for k in ("oidc_issuer", "fulcio_url", "token_file"))
    if not (issuer and fulcio and tf):
        raise SigningError("keyless signing needs a configured OIDC issuer, Fulcio URL and token file "
                           "(args/factory_builds.yaml keyless): none is set", "keyless")
    p = (root / tf).resolve() if not Path(tf).is_absolute() else Path(tf).resolve()
    if not p.is_file() or p.stat().st_size > 64 * 1024:
        raise SigningError("the configured OIDC token file is missing or too large", "keyless")
    return KeylessSigner(issuer=issuer, fulcio_url=fulcio, token=p.read_text(encoding="utf-8").strip(),
                         deadline_s=deadline_s)


# ── the transparency log ────────────────────────────────────────────────────


def submit_dsse(url: str, envelope: dict, spki_or_cert_pem: str, *, deadline_s: float = 30.0) -> dict:
    """Submit one DSSE envelope to a Rekor v1 log as a ``dsse`` 0.0.1 entry; what the log returned, as a Sigstore
    bundle TransparencyLogEntry plus ``uuid``. Raises :class:`SigningError` (``tlog``)."""
    entry = {"kind": "dsse", "apiVersion": "0.0.1",
             "spec": {"proposedContent": {"envelope": json.dumps(envelope, sort_keys=True),
                                          "verifiers": [base64.b64encode(spki_or_cert_pem.encode()).decode()]}}}
    status, out = post_json(url.rstrip("/") + "/api/v1/log/entries", entry, deadline_s=deadline_s, code="tlog")
    if status != 201 or not isinstance(out, dict) or len(out) != 1:
        raise SigningError(f"the transparency log refused the entry (HTTP {status})", "tlog")
    uuid, e = next(iter(out.items()))
    if not isinstance(e, dict) or not isinstance(e.get("logIndex"), int) or not isinstance(e.get("integratedTime"), int) \
            or not isinstance(e.get("logID"), str):
        raise SigningError("the transparency log's answer is not a log entry", "tlog")
    try:
        log_key = base64.b64encode(bytes.fromhex(e["logID"])).decode()
    except ValueError as exc:
        raise SigningError("the transparency log's id is not hex", "tlog") from exc
    tle = {"logIndex": str(e["logIndex"]), "logId": {"keyId": log_key},
           "kindVersion": {"kind": "dsse", "version": "0.0.1"}, "integratedTime": str(e["integratedTime"])}
    if isinstance(e.get("body"), str):
        tle["canonicalizedBody"] = e["body"]
    set_ = ((e.get("verification") or {}).get("signedEntryTimestamp"))
    if isinstance(set_, str):
        tle["inclusionPromise"] = {"signedEntryTimestamp": set_}
    return {"uuid": str(uuid)[:200], "entry": tle}


__all__ = ["ALG", "ArtefactSigner", "KeylessSigner", "KmsSigner", "PemSigner", "SigningError", "key_id", "open_keyless",
           "open_signer", "pem", "post_json", "spki_from_pem", "submit_dsse", "verify"]
