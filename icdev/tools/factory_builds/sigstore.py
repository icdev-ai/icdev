# CUI // SP-CTI
"""Sigstore bundles, in-toto statements, DSSE, OpenSSF model-signing and OCI referrers for one build (icx-ent-03).

The formats are the ones ICDEV[OS] verifies (osx-ent-05 ``icdev_os/release/bundle.py`` and ``oci.py``, osx-ent-06
``artefact.py``), and the ones cosign v3 writes:

* a **Sigstore bundle v0.3** (``application/vnd.dev.sigstore.bundle.v0.3+json``; sigstore/protobuf-specs
  ``sigstore_bundle.proto``) holding a DSSE envelope with exactly ONE signature over
  ``PAE(payloadType, payload)`` (DSSE protocol.md). Key-based: the verification material is a ``publicKey`` hint,
  base64 of SHA-256 of the key's SubjectPublicKeyInfo DER, and the signature carries no keyid. Keyless: the material is
  the single leaf ``certificate`` (``rawBytes``, DER), the form v0.3 requires for keyless. ``tlogEntries`` holds what a
  transparency log returned, and is empty when the log is off (the default);
* an **OpenSSF model-signing** bundle (sigstore/model-transparency ``model_signing`` 1.x): predicate
  ``https://model_signing/signature/v1.0``, serialization ``files`` with ``sha256`` and no symlinks, each resource a
  relative path with its digest, the subject digest SHA-256 over each resource digest's RAW bytes in path order, and
  (key-based) the hint the hex SHA-256 of the key's PEM;
* an **OCI image layout** with one referrer manifest per bundle, as cosign's ``WriteAttestationNewBundleFormat``
  (OCI image-spec v1.1.1): artifactType the bundle media type, config the OCI empty descriptor, one layer, subject the
  artefact, annotations ``dev.sigstore.bundle.content`` and ``dev.sigstore.bundle.predicateType``.

:func:`open_bundle` is ICDEV[IT]'s own check of what it wrote (a build that does not verify under its own key is never
recorded); ICDEV[OS] verifies again against the keys a system's owner pinned, never a key a bundle names.
"""
from __future__ import annotations

import base64
import hashlib
import json

from tools.crypto import artefact_signer as asig
from tools.factory_builds.constants import BUNDLE_MEDIA_TYPE, MODEL_PREDICATE, PAYLOAD_TYPE, STATEMENT

OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
EMPTY_MT = "application/vnd.oci.empty.v1+json"
EMPTY = b"{}"
EMPTY_DIGEST = "sha256:" + hashlib.sha256(EMPTY).hexdigest()


class BundleError(ValueError):
    def __init__(self, code: str, why: str):
        super().__init__(why)
        self.code, self.why = code, why


def canonical(obj) -> bytes:
    """The one serialisation statements and OCI manifests are signed and hashed in: keys sorted, no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def text(obj) -> bytes:
    """A JSON file of the artefact: keys sorted, two-space indent, one trailing newline (as ICDEV[OS] writes them)."""
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def pae(payload_type: str, body: bytes) -> bytes:
    t = payload_type.encode()
    return b"DSSEv1 %d %s %d %s" % (len(t), t, len(body), body)


def statement(subjects: list[dict], predicate_type: str, predicate: dict) -> dict:
    return {"_type": STATEMENT, "subject": subjects, "predicateType": predicate_type, "predicate": predicate}


def hint(spki_der: bytes) -> str:
    return base64.b64encode(hashlib.sha256(spki_der).digest()).decode()


def pem_hint(spki_der: bytes) -> str:
    return hashlib.sha256(asig.pem(spki_der).encode()).hexdigest()


def _material(signer) -> dict:
    if getattr(signer, "mode", "key") == "keyless":
        return {"certificate": {"rawBytes": base64.b64encode(signer.certificate_der).decode()}, "tlogEntries": []}
    return {"publicKey": {"hint": hint(signer.spki())}, "tlogEntries": []}


def envelope(stmt: dict, signer) -> dict:
    body = canonical(stmt)
    sig = signer.sign(pae(PAYLOAD_TYPE, body))
    return {"payload": base64.b64encode(body).decode(), "payloadType": PAYLOAD_TYPE,
            "signatures": [{"sig": base64.b64encode(sig).decode()}]}


def sign(stmt: dict, signer) -> dict:
    """A Sigstore bundle v0.3 holding ``stmt`` in a DSSE envelope signed by ``signer``."""
    return {"mediaType": BUNDLE_MEDIA_TYPE, "verificationMaterial": _material(signer),
            "dsseEnvelope": envelope(stmt, signer)}


def open_bundle(b, spki_der: bytes, *, model_hint: bool = False) -> dict:
    """The statement a bundle carries if it verifies under ``spki_der`` (the build's own key); else raise."""
    if not isinstance(b, dict) or b.get("mediaType") != BUNDLE_MEDIA_TYPE:
        raise BundleError("media_type", "not a Sigstore bundle v0.3")
    env = b.get("dsseEnvelope")
    if not isinstance(env, dict) or env.get("payloadType") != PAYLOAD_TYPE:
        raise BundleError("content", "the bundle carries no in-toto DSSE envelope")
    sigs = env.get("signatures")
    if not isinstance(sigs, list) or len(sigs) != 1 or not isinstance(sigs[0], dict):
        raise BundleError("signatures", "a bundle's envelope holds exactly one signature")
    vm = b.get("verificationMaterial") if isinstance(b.get("verificationMaterial"), dict) else {}
    if "certificate" in vm:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        try:
            cert = x509.load_der_x509_certificate(base64.b64decode(vm["certificate"]["rawBytes"], validate=True))
        except (KeyError, TypeError, ValueError) as exc:
            raise BundleError("key", "the bundle's certificate does not parse") from exc
        if cert.public_key().public_bytes(serialization.Encoding.DER,
                                          serialization.PublicFormat.SubjectPublicKeyInfo) != spki_der:
            raise BundleError("key", "the bundle's certificate is not for the build's key")
    else:
        h = ((vm.get("publicKey") or {}) if isinstance(vm.get("publicKey"), dict) else {}).get("hint")
        want = pem_hint(spki_der) if model_hint else hint(spki_der)
        if h != want:
            raise BundleError("key", "the bundle names another key")
    try:
        body = base64.b64decode(str(env["payload"]), validate=True)
        sig = base64.b64decode(str(sigs[0]["sig"]), validate=True)
    except (KeyError, ValueError, TypeError) as exc:
        raise BundleError("encoding", "the payload or signature is not base64") from exc
    if not asig.verify(spki_der, pae(PAYLOAD_TYPE, body), sig):
        raise BundleError("signature", "the signature does not verify")
    try:
        stmt = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise BundleError("statement", "the payload is not JSON") from exc
    if not isinstance(stmt, dict) or stmt.get("_type") != STATEMENT or not isinstance(stmt.get("subject"), list):
        raise BundleError("statement", "the payload is not an in-toto Statement v1")
    return stmt


# ── OpenSSF model-signing ───────────────────────────────────────────────────


def model_root(resources: list[dict]) -> str:
    """model-signing's subject digest: SHA-256 over each resource digest's RAW bytes, in path order."""
    h = hashlib.sha256()
    for r in sorted(resources, key=lambda r: tuple(str(r["name"]).split("/"))):
        h.update(bytes.fromhex(r["digest"]))
    return h.hexdigest()


def sign_model(resources: list[dict], name: str, signer) -> bytes:
    """An OpenSSF model-signing bundle over ``resources`` ([{name, digest}] of the weights files)."""
    res = [{"name": r["name"], "algorithm": "sha256", "digest": r["digest"]}
           for r in sorted(resources, key=lambda r: tuple(r["name"].split("/")))]
    st = statement([{"name": name, "digest": {"sha256": model_root(res)}}], MODEL_PREDICATE,
                   {"serialization": {"method": "files", "hash_type": "sha256", "allow_symlinks": False},
                    "resources": res})
    env = envelope(st, signer)
    env["signatures"][0]["keyid"] = ""
    if getattr(signer, "mode", "key") == "keyless":
        vm = _material(signer)
    else:
        vm = {"publicKey": {"hint": pem_hint(signer.spki())}, "tlogEntries": []}
    return text({"mediaType": BUNDLE_MEDIA_TYPE, "verificationMaterial": vm, "dsseEnvelope": env})


# ── OCI referrers in an image layout ────────────────────────────────────────


def oci_digest(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def referrer(subject: dict, bundle_bytes: bytes, predicate_type: str, created: str) -> bytes:
    mt = BUNDLE_MEDIA_TYPE
    return canonical({"schemaVersion": 2, "mediaType": OCI_MANIFEST, "artifactType": mt,
                      "config": {"mediaType": EMPTY_MT, "artifactType": mt, "digest": EMPTY_DIGEST, "size": len(EMPTY)},
                      "layers": [{"mediaType": mt, "digest": oci_digest(bundle_bytes), "size": len(bundle_bytes)}],
                      "subject": {k: subject[k] for k in ("mediaType", "digest", "size")},
                      "annotations": {"org.opencontainers.image.created": created,
                                      "dev.sigstore.bundle.content": "dsse-envelope",
                                      "dev.sigstore.bundle.predicateType": predicate_type}})


def layout(subject: dict, bundles: list[tuple[bytes, str]], created: str) -> dict[str, bytes]:
    """An OCI image layout (path -> bytes) holding a referrer per (bundle bytes, predicate type)."""
    files: dict[str, bytes] = {"oci-layout": canonical({"imageLayoutVersion": "1.0.0"})}
    blobs: dict[str, bytes] = {EMPTY_DIGEST: EMPTY}
    descs = []
    for b, pt in bundles:
        blobs[oci_digest(b)] = b
        m = referrer(subject, b, pt, created)
        blobs[oci_digest(m)] = m
        descs.append({"mediaType": OCI_MANIFEST, "artifactType": BUNDLE_MEDIA_TYPE, "digest": oci_digest(m),
                      "size": len(m), "annotations": {"dev.sigstore.bundle.predicateType": pt}})
    for d, b in blobs.items():
        files[f"blobs/sha256/{d.split(':', 1)[1]}"] = b
    files["index.json"] = canonical({"schemaVersion": 2, "mediaType": OCI_INDEX,
                                     "manifests": sorted(descs, key=lambda x: x["digest"])})
    return files


# ── one document to carry an artefact (ICDEV[OS]'s import) ──────────────────


def transfer(files: dict[str, bytes]) -> dict:
    """Every file as UTF-8 text in one ``icos-release-transfer/v1`` document (each file's digest still holds)."""
    from tools.factory_builds.constants import TRANSFER

    return {"schema": TRANSFER, "files": {p: b.decode("utf-8") for p, b in sorted(files.items())}}


def from_transfer(doc) -> dict[str, bytes]:
    from tools.factory_builds.constants import TRANSFER

    if not isinstance(doc, dict) or doc.get("schema") != TRANSFER or not isinstance(doc.get("files"), dict):
        raise BundleError("schema", f"not an {TRANSFER} document")
    out = {}
    for p, t in doc["files"].items():
        if not isinstance(p, str) or not isinstance(t, str) or p.startswith("/") or ".." in p.split("/"):
            raise BundleError("schema", "a file path must be relative and inside the artefact")
        out[p] = t.encode("utf-8")
    return out
