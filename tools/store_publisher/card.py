# CUI // SP-CTI
"""The publisher's Agent Card key and the card's JWS (icx-ent-04).

ICDEV[OS] verifies a store package's A2A Agent Card (A2A v1.0 section 8.4: JWS, RFC 7515, over the RFC 8785 JCS
canonical card without ``signatures`` and without default-valued fields) against the publisher's key IT pins
(``icdev_os/a2a/card_sign.py``; ES384 / P-384, ``kid`` = the first 16 hex of the SHA-256 of the key's SPKI DER). This
module produces exactly that, and verifies every card it signs before it is returned.

    open_signer("keyring:<service>/<name>")   the OS keyring holds the publisher's unencrypted PKCS#8 P-384 PEM
    PemSigner(pem, source=...)                a key held in memory -- the tests' THROWAWAY keys only
    sign_card(card, signer)                   the card with ``signatures: [{protected, signature}]``
    verify_card(card, {kid: spki})            the kid that verified, or CardError

Nothing here creates, stores or prints a key: setting the publisher's key in the keyring is the site's (or the
owner's) step, and it is never an operator or release key. There is no MAC and no unsigned fallback: no key is
``no_key``, refused.
"""
from __future__ import annotations

import hashlib
import json
import re

from tools.common import jcs as _jcs

ALG = "ES384"
STRUCT_FIELDS = frozenset({"params", "metadata"})
_KEYRING = re.compile(r"keyring:([A-Za-z0-9._-]{1,100})/([A-Za-z0-9._-]{1,100})", re.ASCII)


class CardError(ValueError):
    """The card cannot be signed or does not verify: ``code`` says why (no_key, bad_key, keyring, signature)."""

    def __init__(self, message: str, code: str = "signature"):
        super().__init__(message)
        self.code = code


# ── base64url (tools/common/jcs.py), the key's identity ─────────────────────


b64u = _jcs.b64u
unb64u = _jcs.unb64u


def kid(spki_der: bytes) -> str:
    return hashlib.sha256(spki_der).hexdigest()[:16]


def _p384_public(spki_der: bytes):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    try:
        key = serialization.load_der_public_key(spki_der)
    except (ValueError, TypeError) as exc:
        raise CardError("not a public key", "bad_key") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp384r1":
        raise CardError("a card key is ECDSA P-384", "bad_key")
    return key


def jwk(spki_der: bytes) -> dict:
    """The public JWK ICDEV[OS] pins for the publisher (``args/store.yaml`` publishers[].keys)."""
    nums = _p384_public(spki_der).public_numbers()
    return {"kty": "EC", "crv": "P-384", "x": b64u(nums.x.to_bytes(48, "big")), "y": b64u(nums.y.to_bytes(48, "big")),
            "kid": kid(spki_der), "use": "sig", "alg": ALG}


def spki_from_jwk(k: dict) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    if not isinstance(k, dict) or k.get("kty") != "EC" or k.get("crv") != "P-384":
        raise CardError("only EC P-384 keys", "bad_key")
    try:
        pub = ec.EllipticCurvePublicNumbers(int.from_bytes(unb64u(str(k["x"])), "big"),
                                            int.from_bytes(unb64u(str(k["y"])), "big"), ec.SECP384R1()).public_key()
    except (KeyError, ValueError, TypeError) as exc:
        raise CardError("not a P-384 public JWK", "bad_key") from exc
    return pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


# ── the key ──────────────────────────────────────────────────────────────────


class PemSigner:
    """A P-384 key held as an unencrypted PKCS#8 PEM. Every signature is verified under its own public key first."""

    def __init__(self, pem_bytes: bytes, *, source: str):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        try:
            key = serialization.load_pem_private_key(pem_bytes, password=None)
        except (ValueError, TypeError) as exc:
            raise CardError("a card key is an unencrypted PKCS#8 PEM private key", "bad_key") from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp384r1":
            raise CardError("a card key is ECDSA P-384 (ICDEV[OS] verifies ES384)", "bad_key")
        self._key, self.source = key, source

    def __repr__(self) -> str:          # never the key
        return f"PemSigner(source={self.source!r}, kid={kid(self.spki())})"

    def spki(self) -> bytes:
        from cryptography.hazmat.primitives import serialization

        return self._key.public_key().public_bytes(serialization.Encoding.DER,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo)

    def sign(self, message: bytes) -> bytes:
        """The raw r||s ES384 signature (96 bytes), checked under the public key before it is returned."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        r, s = decode_dss_signature(self._key.sign(message, ec.ECDSA(hashes.SHA384())))
        raw = r.to_bytes(48, "big") + s.to_bytes(48, "big")
        if not verify_raw(self.spki(), message, raw):
            raise CardError("the key returned a signature that does not verify under its own public key", "bad_key")
        return raw


def verify_raw(spki_der: bytes, message: bytes, raw: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 96:
        return False
    try:
        pub = _p384_public(spki_der)
        pub.verify(encode_dss_signature(int.from_bytes(raw[:48], "big"), int.from_bytes(raw[48:], "big")), message,
                   ec.ECDSA(hashes.SHA384()))
    except (InvalidSignature, CardError, ValueError, TypeError):
        return False
    return True


def _keyring_pem(service: str, name: str, backend=None) -> bytes:
    if backend is None:
        try:
            import keyring as backend
        except ImportError as exc:
            raise CardError("a keyring: key needs the keyring package (pip install keyring)", "keyring") from exc
    try:
        value = backend.get_password(service, name)
    except Exception as exc:  # noqa: BLE001 — never the key's text in a message
        raise CardError(f"the OS keyring could not be read ({type(exc).__name__})", "keyring") from exc
    if not value:
        raise CardError(f"the OS keyring holds no key at {service}/{name}: setting it is the site's or the owner's",
                        "no_key")
    return value.encode() if isinstance(value, str) else bytes(value)


def open_signer(spec, *, keyring_backend=None) -> PemSigner:
    """The publisher's card key from ``keyring:<service>/<name>``. No key is ``no_key``; any other spec is refused."""
    if not spec:
        raise CardError("no publisher key is configured (signing.key in args/store_publisher.yaml): a package is "
                        "refused until the site sets one", "no_key")
    m = _KEYRING.fullmatch(str(spec))
    if not m:
        raise CardError("signing.key is keyring:<service>/<name>", "bad_key")
    return PemSigner(_keyring_pem(m.group(1), m.group(2), keyring_backend), source=f"keyring:{m.group(1)}/{m.group(2)}")


# ── RFC 8785 (JCS, the one shared copy in tools/common/jcs.py) and the card's payload ──


def jcs(v) -> str:
    """RFC 8785: keys by UTF-16 code units, ECMAScript number and string forms, no whitespace."""
    return _jcs.jcs(v, CardError)


def _prune(v, *, verbatim: bool = False):
    """Default-valued fields go (empty lists and strings), except inside free-form Struct values (A2A 8.4.1)."""
    if verbatim:
        return v
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            x = _prune(x, verbatim=k in STRUCT_FIELDS)
            if (x == [] or x == "") and k not in STRUCT_FIELDS:
                continue
            out[k] = x
        return out
    if isinstance(v, list):
        return [_prune(x) for x in v]
    return v


def payload(card: dict) -> bytes:
    return jcs(_prune({k: v for k, v in card.items() if k != "signatures"})).encode("utf-8")


def sign_card(card: dict, signer) -> dict:
    """The card signed by ``signer``'s key (one signature; any earlier one is replaced), verified before it returns."""
    spki = signer.spki()
    header = {"alg": ALG, "typ": "JOSE", "kid": kid(spki)}
    protected = b64u(json.dumps(header, separators=(",", ":")).encode())
    sig = signer.sign(f"{protected}.{b64u(payload(card))}".encode())
    out = {**{k: v for k, v in card.items() if k != "signatures"},
           "signatures": [{"protected": protected, "signature": b64u(sig)}]}
    verify_card(out, {kid(spki): spki})
    return out


def verify_card(card: dict, pinned: dict) -> str:
    """The kid of the first signature that verifies under a PINNED key ({kid: spki}), else CardError. A key the card
    names (``jku``) is never trusted."""
    sigs = card.get("signatures") if isinstance(card, dict) else None
    if not sigs or not isinstance(sigs, list):
        raise CardError("the card is not signed")
    body = b64u(payload(card))
    for s in sigs:
        try:
            h = json.loads(unb64u(s["protected"]))
            raw = unb64u(s["signature"])
        except (KeyError, ValueError, TypeError):
            continue
        if not isinstance(h, dict) or h.get("alg") != ALG:
            continue
        spki = pinned.get(str(h.get("kid", "")))
        if spki is not None and verify_raw(spki, f"{s['protected']}.{body}".encode(), raw):
            return str(h["kid"])
    raise CardError("no signature on the card verifies under the publisher's key")


__all__ = ["CardError", "PemSigner", "jcs", "jwk", "kid", "open_signer", "payload", "sign_card", "spki_from_jwk",
           "verify_card"]
