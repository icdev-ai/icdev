#!/usr/bin/env python3
# CUI // SP-CTI
# CANONICAL: backs the Component Hash Value / Component Hash Algorithm elements of the
# 2026 SBOM Minimum Elements for tools/compliance/sbom_generator.py.
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""Component Hash Value and Component Hash Algorithm (2026 SBOM Minimum Elements, sbx-fld-03).

The standard defines the two elements as:

  Component Hash Value      ASCII, hexadecimal-encoded output of a cryptographic hash
                            over the *executable component artifact*. Where the author
                            cannot access the artifact the value must be marked unknown
                            — it may not be omitted.
  Component Hash Algorithm  Named using IANA Hash Function Textual Names, and approved
                            by a relevant authority such as NIST.

Resolution order, per the card:

  1. Recompute from the local artifact whenever one is present. A recomputed digest is
     the only one that is a hash of the bytes the recipient will actually run.
  2. Otherwise accept a digest *declared* by a resolved manifest, but only from a source
     that is a digest of the artifact itself: npm ``integrity`` (Subresource Integrity),
     an OCI-style ``<alg>:<hex>``, or any caller-supplied value carrying its algorithm.
  3. Otherwise mark the value unknown, with a machine-readable reason.

Deliberately NOT accepted as a Component Hash Value:

  * ``go.sum`` ``h1:`` lines. ``h1`` is a SHA-256 over a synthesized listing of the
    module's file names and their individual hashes (``golang.org/x/mod/sumdb/dirhash``),
    not over the module zip. Emitting it as ``sha-256`` would assert something false
    about the artifact, so a go component without a local artifact stays unknown.
  * A bare hex string with no algorithm label (e.g. ``Cargo.lock``'s ``checksum``). The
    algorithm is never inferred from digest length — a caller that knows the algorithm
    passes ``sha-256:<hex>``.

``sbom_generator.py`` reads declared dependency manifests and does not resolve artifacts,
so today the common outcome is an explicit unknown with reason ``artifact-not-located``.
``component_hash()`` consumes ``component["artifact_path"]`` the moment sbx-cov-01's
resolution work starts populating it; nothing else has to change.
"""

import base64
import binascii
import hashlib
from pathlib import Path

# ---------------------------------------------------------------------------
# IANA Hash Function Textual Names
# ---------------------------------------------------------------------------
# The registry the standard names, established by RFC 4572 section 8 and referenced by
# RFC 8122. Entries are lowercase and hyphenated ("sha-256", never "SHA256"/"sha256").
# md2, md5 and sha-1 are registry members but are NOT approved for new use — RFC 6149 /
# RFC 6151 declare md2/md5 historic, and the repo rule already mandates sha256 over md5.
# They stay in the registry set so a declared digest naming them is rejected as
# not-approved rather than misreported as not-a-registry-name.
IANA_HASH_FUNCTION_TEXTUAL_NAMES = frozenset(
    {
        "md2",
        "md5",
        "sha-1",
        "sha-224",
        "sha-256",
        "sha-384",
        "sha-512",
    }
)

# NIST-approved subset (FIPS 180-4). Approval is the second half of the element: the
# standard asks for an IANA name AND an algorithm approved by a relevant authority.
NIST_APPROVED_HASH_FUNCTIONS = frozenset({"sha-224", "sha-256", "sha-384", "sha-512"})

# What this module will compute locally. Same set — every approved name has a
# fixed-length hashlib constructor.
_HASHLIB_CONSTRUCTORS = {
    "sha-224": "sha224",
    "sha-256": "sha256",
    "sha-384": "sha384",
    "sha-512": "sha512",
}

# Expected digest length in bytes, used to reject a declared digest whose payload does
# not match the algorithm it claims.
_DIGEST_BYTES = {"sha-224": 28, "sha-256": 32, "sha-384": 48, "sha-512": 64}

DEFAULT_HASH_ALGORITHM = "sha-256"

# CycloneDX carries its own hash-alg vocabulary, which is not the IANA one. Only names
# in the CycloneDX 1.4+ enum may appear in a component's `hashes` array; sha-224 has no
# CycloneDX spelling, so such a digest travels in the ICDEV properties only.
_CYCLONEDX_ALG_NAMES = {
    "sha-1": "SHA-1",
    "sha-256": "SHA-256",
    "sha-384": "SHA-384",
    "sha-512": "SHA-512",
    "md5": "MD5",
}

# The explicit unknown marker. The standard forbids omitting the field.
UNKNOWN = "unknown"

# Where a known value came from.
SOURCE_ARTIFACT = "artifact"
SOURCE_DECLARED = "declared-digest"

# Machine-readable unknown reasons.
REASON_NO_ARTIFACT = "artifact-not-located"
REASON_ARTIFACT_MISSING = "artifact-not-accessible"
REASON_ARTIFACT_NOT_A_FILE = "artifact-not-a-file"
REASON_ARTIFACT_UNREADABLE = "artifact-read-failed"
REASON_DIGEST_MALFORMED = "declared-digest-malformed"
REASON_DIGEST_ALGORITHM_UNREGISTERED = "declared-digest-algorithm-not-iana-registered"
REASON_DIGEST_ALGORITHM_NOT_APPROVED = "declared-digest-algorithm-not-approved"

# CycloneDX property names carrying the two elements.
PROPERTY_HASH_VALUE = "icdev:component-hash-value"
PROPERTY_HASH_ALGORITHM = "icdev:component-hash-algorithm"
PROPERTY_HASH_SOURCE = "icdev:component-hash-source"
PROPERTY_HASH_UNKNOWN_REASON = "icdev:component-hash-unknown-reason"

_READ_CHUNK_BYTES = 1024 * 1024


def is_iana_hash_name(algorithm):
    """True when `algorithm` is spelled exactly as the IANA registry spells it."""
    return isinstance(algorithm, str) and algorithm in IANA_HASH_FUNCTION_TEXTUAL_NAMES


def is_approved_hash_name(algorithm):
    """True when `algorithm` is an IANA name AND approved by NIST for new use."""
    return isinstance(algorithm, str) and algorithm in NIST_APPROVED_HASH_FUNCTIONS


def to_cyclonedx_alg(algorithm):
    """Map an IANA textual name to its CycloneDX `hashes[].alg` spelling.

    Returns None when CycloneDX has no spelling for it (sha-224), in which case the
    digest is still reported through the ICDEV properties.
    """
    return _CYCLONEDX_ALG_NAMES.get(algorithm)


def unknown_hash(reason, detail=None):
    """Build the explicit unknown marker. Never returns an omitted/empty value."""
    return {
        "hash_value": UNKNOWN,
        "hash_algorithm": UNKNOWN,
        "source": None,
        "reason": reason,
        "detail": detail,
    }


def _known_hash(hex_value, algorithm, source):
    return {
        "hash_value": hex_value,
        "hash_algorithm": algorithm,
        "source": source,
        "reason": None,
        "detail": None,
    }


def is_unknown(result):
    """True when `result` is an unknown marker rather than a real digest."""
    return result.get("hash_value") == UNKNOWN


def hash_artifact(artifact_path, algorithm=DEFAULT_HASH_ALGORITHM):
    """Hash the bytes of an executable component artifact.

    Returns a known result with a lowercase ASCII hex value, or an unknown marker
    naming why the artifact could not be read. Never raises on a missing, unreadable
    or non-file path — inaccessibility is a documented outcome of this element, not an
    error condition.
    """
    if algorithm not in _HASHLIB_CONSTRUCTORS:
        raise ValueError(
            f"Refusing to compute with {algorithm!r}: not an IANA-named, NIST-approved "
            f"hash function. Supported: {sorted(_HASHLIB_CONSTRUCTORS)}"
        )

    path = Path(artifact_path)
    digest = hashlib.new(_HASHLIB_CONSTRUCTORS[algorithm])
    try:
        if not path.exists():
            return unknown_hash(REASON_ARTIFACT_MISSING, str(artifact_path))
        if not path.is_file():
            return unknown_hash(REASON_ARTIFACT_NOT_A_FILE, str(artifact_path))
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        return unknown_hash(REASON_ARTIFACT_UNREADABLE, f"{artifact_path}: {exc}")

    return _known_hash(digest.hexdigest(), algorithm, SOURCE_ARTIFACT)


def parse_declared_digest(declared):
    """Parse a digest declared by a resolved manifest.

    Accepts Subresource Integrity as emitted by `package-lock.json`
    (``sha512-<base64>``) and the OCI-style ``sha256:<hex>``. The algorithm is always
    taken from the string; it is never inferred from the digest length.
    """
    if not isinstance(declared, str) or not declared.strip():
        return unknown_hash(REASON_DIGEST_MALFORMED, repr(declared))

    declared = declared.strip()
    # npm records several digests separated by whitespace; the first is enough.
    declared = declared.split()[0]

    if ":" in declared:
        raw_alg, _, payload = declared.partition(":")
        encoding = "hex"
    elif "-" in declared:
        raw_alg, _, payload = declared.partition("-")
        encoding = "base64"
    else:
        return unknown_hash(REASON_DIGEST_MALFORMED, declared)

    algorithm = _normalize_algorithm_name(raw_alg)
    if not is_iana_hash_name(algorithm):
        return unknown_hash(REASON_DIGEST_ALGORITHM_UNREGISTERED, raw_alg)
    if not is_approved_hash_name(algorithm):
        return unknown_hash(REASON_DIGEST_ALGORITHM_NOT_APPROVED, algorithm)

    if encoding == "base64":
        try:
            digest_bytes = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            return unknown_hash(REASON_DIGEST_MALFORMED, declared)
        hex_value = digest_bytes.hex()
    else:
        hex_value = payload.strip().lower()
        try:
            digest_bytes = bytes.fromhex(hex_value)
        except ValueError:
            return unknown_hash(REASON_DIGEST_MALFORMED, declared)

    if len(digest_bytes) != _DIGEST_BYTES[algorithm]:
        return unknown_hash(REASON_DIGEST_MALFORMED, f"{declared} (wrong length for {algorithm})")

    return _known_hash(hex_value, algorithm, SOURCE_DECLARED)


def _normalize_algorithm_name(raw):
    """Fold a manifest's spelling onto the IANA textual name.

    npm writes ``sha512``; OCI writes ``sha256``; the registry writes ``sha-512``. Only
    this missing-hyphen difference is normalized — an unrecognized spelling is returned
    lowercased so the caller reports it as unregistered rather than guessing.
    """
    name = str(raw).strip().lower()
    hyphenated = {
        "sha1": "sha-1",
        "sha224": "sha-224",
        "sha256": "sha-256",
        "sha384": "sha-384",
        "sha512": "sha-512",
    }
    return hyphenated.get(name, name)


def component_hash(component, algorithm=DEFAULT_HASH_ALGORITHM):
    """Resolve the two elements for one parsed component.

    Reads ``artifact_path`` (a local executable artifact, preferred) and
    ``declared_digest`` (a manifest-declared digest of that artifact). Always returns a
    result: either a real digest or an explicit unknown marker carrying a reason.
    """
    artifact_path = component.get("artifact_path")
    declared = component.get("declared_digest")

    artifact_result = None
    if artifact_path:
        artifact_result = hash_artifact(artifact_path, algorithm=algorithm)
        if not is_unknown(artifact_result):
            return artifact_result

    if declared:
        declared_result = parse_declared_digest(declared)
        if not is_unknown(declared_result):
            return declared_result
        # An artifact we were told about but could not read is the more specific
        # failure; report it ahead of a malformed declared digest.
        return artifact_result or declared_result

    if artifact_result is not None:
        return artifact_result

    return unknown_hash(REASON_NO_ARTIFACT, component.get("purl") or component.get("name"))


def hash_properties(result):
    """Render a resolution result as CycloneDX `properties` entries.

    Both elements are always present — a known digest reports its source, an unknown
    one reports why. This is what keeps the field from being silently omitted.
    """
    properties = [
        {"name": PROPERTY_HASH_VALUE, "value": result["hash_value"]},
        {"name": PROPERTY_HASH_ALGORITHM, "value": result["hash_algorithm"]},
    ]
    if is_unknown(result):
        properties.append(
            {"name": PROPERTY_HASH_UNKNOWN_REASON, "value": result.get("reason") or UNKNOWN}
        )
    else:
        properties.append({"name": PROPERTY_HASH_SOURCE, "value": result["source"]})
    return properties


def cyclonedx_hashes(result):
    """Render a resolution result as a CycloneDX `hashes` array.

    Empty when the value is unknown or the algorithm has no CycloneDX spelling —
    CycloneDX cannot express "unknown" inside `hashes`, and a bogus entry there is worse
    than none. The ICDEV properties carry the unknown marker in that case.
    """
    if is_unknown(result):
        return []
    alg = to_cyclonedx_alg(result["hash_algorithm"])
    if not alg:
        return []
    return [{"alg": alg, "content": result["hash_value"]}]
