#!/usr/bin/env python3
# CUI // SP-CTI
# CANONICAL: backs the Component Hash Value and Component Hash Algorithm elements of the
# 2026 SBOM Minimum Elements for tools/compliance/sbom_generator.py.
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""Component Hash Value and Component Hash Algorithm (2026 SBOM Minimum Elements, sbx-fld-03).

The standard defines the two elements as:

    Component Hash Value      An ASCII, hexadecimal-encoded output of a cryptographic
                              hash function over the executable component artifact.
                              Where the artifact is not accessible to the author, that
                              must be stated explicitly rather than the field omitted.

    Component Hash Algorithm  The name of the hash function that produced the value,
                              expressed with an IANA Hash Function Textual Name, and
                              approved by a relevant authority such as NIST.

Both are NEW in 2026 and both were absent from ICDEV output: the generator read declared
manifests and never touched an artifact, so no hash was computable at all. sbx-cov-01
changed the input — `dependency_resolver` now reads each ecosystem's **resolved** source,
and a resolved source is the first thing in the pipeline that knows either where an
artifact is or what its producer said its digest was. This module turns that into the
two elements.

WHAT IS AND IS NOT A COMPONENT HASH

The element is a digest **of an artifact**, not any cryptographic value associated with a
package. That distinction is the whole design:

  * Adopted — a digest whose subject is one distributable artifact and whose algorithm is
    an approved IANA-named function. npm's `integrity`, Cargo.lock's `checksum`, NuGet's
    `sha512`/`contentHash` and a Python lock's single `sha256:` file hash all qualify.
  * Refused — a value that is cryptographic but is not that. Go's `go.sum` `h1:` is a
    SHA-256 over a synthesized listing of per-file hashes, not over the module zip;
    emitting it under the name `sha-256` would tell a recipient that hashing the artifact
    they hold reproduces it, which is false. A Maven `.sha1` sidecar is a digest of the
    right thing under an algorithm no authority still approves for integrity.

Nothing refused is dropped. It travels on `icdev:component-hash-unadopted` beside the
reason it was not adopted, so a recipient who wants the go.sum line still has it and
cannot mistake it for the element.

RESOLUTION ORDER

  1. Recompute from a local artifact — `component["artifact_path"]`, when it names a
     file that exists. Preferred outright: it is the only path where ICDEV states a
     digest it produced itself over bytes it read, rather than repeating a claim.
  2. Adopt the strongest approved, well-formed digest the resolved source declared —
     `component["declared_hashes"]`.
  3. Otherwise the explicit unknown marker, with a machine-readable reason.

Step 3 is the common outcome for this repository and that is the correct answer, not a
shortfall to paper over. An installed Python environment is an unpacked tree of files;
the wheel it came from is not on disk, so there is no artifact to hash and the standard
says to say so.

ALGORITHM NAMES

`IANA_HASH_FUNCTION_TEXTUAL_NAMES` is the IANA registry of that name, vendored rather
than fetched — ICDEV runs air-gapped, and the set of algorithm names an SBOM generator
considers valid must not vary with network reachability. Validation is allow-list only,
so a stale copy can only refuse a name, never invent one.

Being *registered* and being *approved* are separate questions and this module keeps them
separate. `md2`, `md5` and `sha-1` are in the registry and are not approved: a component
whose only available digest uses one of them is reported unknown, because a value the
recipient must not rely on is worse than a stated absence. `shake128`/`shake256` are
approved functions (FIPS 202) but extendable-output ones — a digest under them means
nothing without the output length the registry name does not carry — so they validate as
names and are never emitted.

CycloneDX spells the same names with different punctuation (`SHA-256`), and SPDX 2.3
differently again (`SHA256`); `spdx_writer.HASH_ALGORITHMS` already maps the second hop,
so emitting the CycloneDX spelling gives both serializations the element.

UNKNOWN VS WITHHELD (sbx-prc-01)

An inaccessible artifact is *unknown to the author*; a digest the operator declines to
publish is *withheld by the author*. Neither is expressed with a marker of this module's
own — both go through `unknown_information`, with the local reason carried as the
disclosure *detail* so precision survives without a competing vocabulary.

A withheld hash suppresses the CycloneDX `hashes` array and every supporting property,
including the unadopted digest: an artifact digest left in place beside a withheld marker
would publish exactly what was withheld.
"""

import base64
import hashlib
import json
import re
from pathlib import Path

from tools.compliance.dependency_resolver import RESOLUTION_DECLARED
from tools.compliance.unknown_information import (
    FIELD_HASH_ALGORITHM,
    FIELD_HASH_VALUE,
    REASON_ARTIFACT_NOT_ACCESSIBLE,
    UNKNOWN,
    WITHHELD,
    Disclosure,
)

# ---------------------------------------------------------------------------
# The IANA Hash Function Textual Names registry (vendored)
# ---------------------------------------------------------------------------

#: The registry the 2026 Component Hash Algorithm element points at, verbatim.
#:
#: `hex_length` is the length of the element's own encoding — an ASCII, hexadecimal
#: digest — and is what makes a declared digest checkable: a value labelled `sha-256`
#: that decodes to 20 bytes is not a SHA-256, and adopting it would hand the recipient a
#: verification that cannot succeed. `None` marks an extendable-output function, whose
#: digest length is a parameter the name does not carry.
#:
#: `approved` is the NIST question, not the IANA one. A name can be registered and
#: unfit: SP 800-131A Rev. 2 disallows MD5 and SHA-1 for integrity, and MD2 predates the
#: question. Registered-but-unapproved names exist here so a declared digest under one is
#: recognised and *refused with its reason*, rather than failing to parse.
#: `reference` is the registry's OWN citation for the name, not the RFC that specifies
#: the function. IANA cites RFC 3279 and RFC 4055 — the PKIX algorithm documents the
#: names were registered from — rather than RFC 1321 or RFC 6234, which define MD5 and
#: SHA-2. Copying a vendored registry means copying what it says.
IANA_HASH_FUNCTION_TEXTUAL_NAMES = {
    "md2": {"reference": "RFC 3279", "hex_length": 32, "approved": False},
    "md5": {"reference": "RFC 3279", "hex_length": 32, "approved": False},
    "sha-1": {"reference": "RFC 3279", "hex_length": 40, "approved": False},
    "sha-224": {"reference": "RFC 4055", "hex_length": 56, "approved": True},
    "sha-256": {"reference": "RFC 4055", "hex_length": 64, "approved": True},
    "sha-384": {"reference": "RFC 4055", "hex_length": 96, "approved": True},
    "sha-512": {"reference": "RFC 4055", "hex_length": 128, "approved": True},
    "shake128": {"reference": "RFC 8692", "hex_length": None, "approved": True},
    "shake256": {"reference": "RFC 8692", "hex_length": None, "approved": True},
}

#: Every registered name. Membership here is what "validates against the IANA Hash
#: Function Textual Names registry" means, and it is the only test a name emitted as the
#: Component Hash Algorithm has to pass.
IANA_HASH_NAMES = frozenset(IANA_HASH_FUNCTION_TEXTUAL_NAMES)

#: The names this module will emit: registered, NIST-approved, and fixed-length. The
#: fixed-length condition is what excludes the SHAKE pair — approved functions whose
#: registry name underspecifies the digest.
EMITTABLE_ALGORITHMS = frozenset(
    name
    for name, spec in IANA_HASH_FUNCTION_TEXTUAL_NAMES.items()
    if spec["approved"] and spec["hex_length"] is not None
)

#: What recomputation uses. The house rule is sha256 over md5; this is where that rule
#: meets the element, and `hashlib` is the only implementation involved.
DEFAULT_ALGORITHM = "sha-256"

#: Strongest first. Adoption is deterministic: two runs over one lockfile that declares
#: several digests for a component pick the same one, because an SBOM that churns on
#: regeneration cannot be diffed.
_ADOPTION_PREFERENCE = ("sha-512", "sha-384", "sha-256", "sha-224")

#: `hashlib` constructors for the emittable set. Keyed by IANA name so no call site has
#: to translate a name into an implementation.
_HASHLIB_CONSTRUCTORS = {
    "sha-224": hashlib.sha224,
    "sha-256": hashlib.sha256,
    "sha-384": hashlib.sha384,
    "sha-512": hashlib.sha512,
}


def _build_aliases():
    """Every spelling of a registered name that resolves to it, and nothing else.

    Built from the registry rather than by stripping punctuation off an arbitrary
    input: `sha3-256` differs from `sha-256` only in punctuation and is a DIFFERENT
    function that is not in this registry at all, so a normalizer that deleted
    separators would silently relabel a SHA3 digest as SHA-2.
    """
    aliases = {}
    for name in IANA_HASH_FUNCTION_TEXTUAL_NAMES:
        for spelling in (name, name.replace("-", ""), name.replace("-", "_")):
            aliases[spelling] = name
    return aliases


_ALIASES = _build_aliases()

#: `sha512-<base64>`, the Subresource Integrity form npm and yarn v1 write.
_SRI_ENTRY = re.compile(r"^(?P<alg>[A-Za-z0-9_-]+)-(?P<digest>[A-Za-z0-9+/=]+)$")

_HEX_ONLY = re.compile(r"^[0-9a-fA-F]+$")


def normalize_algorithm(raw):
    """An IANA Hash Function Textual Name, or `""` when the input names no registered one.

    Case-insensitive and tolerant of the two punctuations real manifests use
    (`SHA256`, `sha_256`). Deliberately intolerant of everything else: an unrecognised
    name is not guessed at.
    """
    if not raw:
        return ""
    return _ALIASES.get(str(raw).strip().casefold(), "")


def is_iana_hash_name(name):
    """True when `name` is exactly a registered IANA Hash Function Textual Name."""
    return name in IANA_HASH_NAMES


def is_approved(name):
    """True when a relevant authority still approves `name` for integrity.

    Registration is IANA's answer; approval is NIST's. The element requires both.
    """
    spec = IANA_HASH_FUNCTION_TEXTUAL_NAMES.get(name)
    return bool(spec and spec["approved"])


def hex_length_of(name):
    """Expected hexadecimal digest length, or `None` for an extendable-output function."""
    spec = IANA_HASH_FUNCTION_TEXTUAL_NAMES.get(name)
    return spec["hex_length"] if spec else None


def cyclonedx_algorithm(name):
    """An IANA name in CycloneDX's `hashes[].alg` spelling — `sha-256` -> `SHA-256`.

    The two vocabularies are the same names under different punctuation, which is what
    lets `spdx_writer.HASH_ALGORITHMS` complete the hop to SPDX's `SHA256` without this
    module knowing anything about SPDX.
    """
    return name.upper() if name in IANA_HASH_NAMES else ""


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# UNKNOWN and WITHHELD come from `unknown_information`, not restated here: the standard
# forbids omitting either element, so one of those two markers is what gets emitted when
# no digest can be shown, and a second spelling of "unknown" is exactly the drift the
# shared convention exists to prevent.

#: Resolved from bytes ICDEV read itself.
METHOD_RECOMPUTED = "recomputed-from-artifact"

#: Repeated from a digest the resolved source declared. Weaker than recomputation: it
#: states what the producer's lockfile claims, which is the claim the recipient would be
#: checking anyway.
METHOD_DECLARED = "declared-by-resolved-source"

# Local reasons. Every one of these maps to the shared REASON_ARTIFACT_NOT_ACCESSIBLE and
# travels as the disclosure DETAIL, so `icdev:unknown:hash_value` stays readable by a
# validator that knows only the shared vocabulary while the specifics survive.
REASON_NO_ARTIFACT = "artifact-not-on-disk"
REASON_SOURCE_HAS_NO_DIGEST = "resolved-source-carries-no-digest"
REASON_NOT_RESOLVED = "component-came-from-a-declared-manifest"
REASON_WEAK_ALGORITHM = "declared-digest-algorithm-not-approved"
REASON_UNREGISTERED_ALGORITHM = "declared-digest-algorithm-not-in-the-iana-registry"
REASON_MALFORMED = "declared-digest-malformed"
REASON_NOT_AN_ARTIFACT_DIGEST = "declared-digest-is-not-over-a-single-artifact"
REASON_MANY_ARTIFACTS = "source-lists-several-artifacts-and-names-no-installed-one"
REASON_ARTIFACT_UNREADABLE = "artifact-present-but-unreadable"
REASON_TARGET_NOT_BUILT = "target-component-is-not-built-at-generation-time"

#: What the digest is over. Supplied by whichever resolver read it, because only that
#: resolver knows — the standard's "executable component artifact" is a tarball for npm,
#: a `.crate` for Cargo and a jar for Maven.
SUBJECT_UNKNOWN = "unspecified-artifact"

PROPERTY_HASH = "icdev:component-hash"
PROPERTY_ALGORITHM = "icdev:component-hash-algorithm"
PROPERTY_METHOD = "icdev:component-hash-method"
PROPERTY_SUBJECT = "icdev:component-hash-subject"
PROPERTY_EVIDENCE = "icdev:component-hash-evidence"
PROPERTY_UNADOPTED = "icdev:component-hash-unadopted"
PROPERTY_UNADOPTED_REASON = "icdev:component-hash-unadopted-reason"

#: Read a whole artifact without holding it in memory. Jars and crates are small; a
#: container layer is not, and this module has no say in what it is pointed at.
_CHUNK_BYTES = 1024 * 1024


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def unknown_hash(reason, unadopted=None):
    """A component whose artifact digest could not be established, and why."""
    return {
        "state": UNKNOWN,
        "algorithm": "",
        "value": "",
        "method": "",
        "subject": "",
        "evidence": "",
        "reason": reason,
        "unadopted": list(unadopted or []),
    }


def _known_hash(algorithm, value, method, subject="", evidence="", unadopted=None):
    return {
        "state": "known",
        "algorithm": algorithm,
        "value": value,
        "method": method,
        "subject": subject or SUBJECT_UNKNOWN,
        "evidence": str(evidence or ""),
        "reason": "",
        "unadopted": list(unadopted or []),
    }


def is_unknown(result):
    return result.get("state") != "known"


# ---------------------------------------------------------------------------
# Reading declared digests
# ---------------------------------------------------------------------------


def _decode_to_hex(raw_value, algorithm, encoding=""):
    """`(hex, error)` for one declared digest under a known-length `algorithm`.

    The length check is what disambiguates the two encodings a lockfile might use
    without either being labelled: SHA-512 is 128 hex characters or 88 base64 ones, and
    no value is both. It is also the correctness check — `sha512` over 20 decoded bytes
    is a mislabelled SHA-1, and adopting it would publish a verification that cannot
    succeed.
    """
    value = str(raw_value or "").strip()
    if not value:
        return "", REASON_MALFORMED

    expected = hex_length_of(algorithm)
    if expected is None:
        # An extendable-output function: nothing about the value can confirm the length
        # its producer chose, so there is no digest here that can be checked.
        return "", REASON_NOT_AN_ARTIFACT_DIGEST

    if encoding != "base64" and _HEX_ONLY.match(value) and len(value) == expected:
        return value.lower(), ""

    if encoding != "hex":
        try:
            # `binascii.Error` is a ValueError; padding is restored because several
            # lockfile formats strip it.
            decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
        except ValueError:
            decoded = None
        if decoded is not None and len(decoded) * 2 == expected:
            return decoded.hex(), ""

    return "", REASON_MALFORMED


def _classify_declared(entry):
    """One declared digest -> `(adoptable, record)`.

    `record` always describes what was read, so a refusal can be reported rather than
    silently discarded. `adoptable` is `None` whenever anything at all is wrong with it.
    """
    if not isinstance(entry, dict):
        return None, {"declared": str(entry), "reason": REASON_MALFORMED}

    raw_algorithm = entry.get("algorithm") or ""
    raw_value = entry.get("value") or ""
    encoding = entry.get("encoding") or ""

    sri = str(entry.get("sri") or "").strip()
    if sri:
        # An SRI field may carry several space-separated digests. The first is what npm
        # writes and what a recipient checks; the rest are the same artifact under other
        # algorithms and add nothing the element can hold.
        parsed = _SRI_ENTRY.match(sri.split()[0])
        if not parsed:
            return None, {"declared": sri, "reason": REASON_MALFORMED}
        raw_algorithm = parsed.group("alg")
        raw_value = parsed.group("digest")
        encoding = "base64"

    declared_label = f"{raw_algorithm}:{raw_value}" if raw_algorithm else str(raw_value)
    record = {
        "declared": declared_label,
        "algorithm": raw_algorithm,
        "subject": entry.get("subject") or "",
        "source": str(entry.get("source") or ""),
    }

    # A source that says outright that its digest is not over an artifact — go.sum's
    # `h1:` module-directory hash — is refused before its name is even looked up. It
    # may well be a SHA-256; it is a SHA-256 of something else.
    if entry.get("artifact_digest") is False:
        record["reason"] = REASON_NOT_AN_ARTIFACT_DIGEST
        return None, record

    # Several artifact digests where the source does not say which artifact was
    # installed — a Python lock listing one sdist and fourteen platform wheels. Each is
    # a real digest of a real artifact and picking one would be a coin toss that names
    # the wrong file for thirteen recipients out of fourteen.
    if entry.get("ambiguous"):
        record["reason"] = REASON_MANY_ARTIFACTS
        return None, record

    algorithm = normalize_algorithm(raw_algorithm)
    if not algorithm:
        record["reason"] = REASON_UNREGISTERED_ALGORITHM
        return None, record
    if not is_approved(algorithm):
        record["reason"] = REASON_WEAK_ALGORITHM
        return None, record

    hex_value, error = _decode_to_hex(raw_value, algorithm, encoding)
    if error:
        record["reason"] = error
        return None, record

    record["algorithm"] = algorithm
    adoptable = {
        "algorithm": algorithm,
        "value": hex_value,
        "subject": entry.get("subject") or "",
        "source": str(entry.get("source") or ""),
    }
    return adoptable, record


def _adopt_declared(entries):
    """Pick the strongest well-formed digest, and report every one that was refused."""
    candidates = []
    refused = []
    for entry in entries:
        adoptable, record = _classify_declared(entry)
        if adoptable:
            candidates.append(adoptable)
        else:
            refused.append(record)

    if not candidates:
        return None, refused

    def rank(candidate):
        try:
            return _ADOPTION_PREFERENCE.index(candidate["algorithm"])
        except ValueError:  # emittable but unranked — after everything ranked
            return len(_ADOPTION_PREFERENCE)

    candidates.sort(key=lambda c: (rank(c), c["value"]))
    return candidates[0], refused


# ---------------------------------------------------------------------------
# Recomputation
# ---------------------------------------------------------------------------


def hash_file(path, algorithm=DEFAULT_ALGORITHM):
    """The hexadecimal digest of a file, or `""` when it cannot be read.

    Returns rather than raises on I/O failure: an artifact that vanished between
    resolution and generation is an unknown hash, which the element has a way to state.
    """
    constructor = _HASHLIB_CONSTRUCTORS.get(algorithm)
    if constructor is None:
        return ""
    digest = constructor()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_hash(component):
    """Resolve both elements for one parsed component. Always returns a result.

    Recomputation from a local artifact wins over any declared digest, because it is the
    one outcome where ICDEV states something it verified rather than something it was
    told. Where neither is available the result is the explicit unknown marker carrying
    a machine-readable reason — never an omission, and never a digest of something that
    is not the artifact.
    """
    declared_entries = component.get("declared_hashes") or []
    artifact_path = component.get("artifact_path") or ""

    if artifact_path:
        path = Path(artifact_path)
        if path.is_file():
            value = hash_file(path, DEFAULT_ALGORITHM)
            if value:
                refused = _adopt_declared(declared_entries)[1]
                return _known_hash(
                    DEFAULT_ALGORITHM,
                    value,
                    METHOD_RECOMPUTED,
                    subject=component.get("artifact_subject") or SUBJECT_UNKNOWN,
                    evidence=path,
                    unadopted=refused,
                )
            # The path resolved to a real file that would not read. Fall through to the
            # declared digest rather than reporting unknown outright: the producer's
            # claim is still the better answer, and the reason below records why we are
            # repeating it instead of confirming it.
            fallback_reason = REASON_ARTIFACT_UNREADABLE
        else:
            fallback_reason = REASON_NO_ARTIFACT
    else:
        fallback_reason = ""

    adopted, refused = _adopt_declared(declared_entries)
    if adopted:
        return _known_hash(
            adopted["algorithm"],
            adopted["value"],
            METHOD_DECLARED,
            subject=adopted["subject"],
            evidence=adopted["source"],
            unadopted=refused,
        )

    if refused:
        # Every digest the source carried was refused, and the first refusal's reason is
        # the specific, actionable one — "the sidecar is SHA-1" beats "no artifact".
        return unknown_hash(refused[0].get("reason") or REASON_MALFORMED, unadopted=refused)

    if fallback_reason:
        return unknown_hash(fallback_reason)
    if component.get("resolution") == RESOLUTION_DECLARED:
        return unknown_hash(REASON_NOT_RESOLVED)
    return unknown_hash(REASON_SOURCE_HAS_NO_DIGEST)


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


def record_hash_disclosure(result, disclosure=None):
    """State an unknown hash through the shared convention (sbx-prc-01).

    Both elements are recorded, because they stand or fall together: the algorithm that
    produced a digest nobody has is not a fact about the component. A resolution that
    DID produce a digest records nothing — a known field is not a disclosure — and a
    field the policy already withheld is left alone, because "we are not telling you"
    outranks "we could not find it".
    """
    if disclosure is None:
        disclosure = Disclosure()
    if not is_unknown(result):
        return disclosure

    detail = result.get("reason") or REASON_NO_ARTIFACT
    for field in (FIELD_HASH_VALUE, FIELD_HASH_ALGORITHM):
        if disclosure.state_of(field) != WITHHELD:
            disclosure.unknown(field, REASON_ARTIFACT_NOT_ACCESSIBLE, detail=detail)
    return disclosure


def hash_entries(result, disclosure=None):
    """Render a resolution as a CycloneDX `hashes` array.

    Empty when the digest is unknown or withheld: CycloneDX has no way to say either
    inside `hashes`, and an invented entry there is worse than none — a recipient
    verifies against it. The ICDEV properties carry the explicit marker in that case, so
    the elements are still present on the component.
    """
    if is_unknown(result):
        return []
    if disclosure is not None and (
        disclosure.state_of(FIELD_HASH_VALUE) or disclosure.state_of(FIELD_HASH_ALGORITHM)
    ):
        return []
    alg = cyclonedx_algorithm(result["algorithm"])
    if not alg or not result.get("value"):
        return []
    return [{"alg": alg, "content": result["value"]}]


def hash_properties(result, disclosure=None):
    """Render a resolution as CycloneDX `properties` entries.

    `icdev:component-hash` and `icdev:component-hash-algorithm` are always present. That
    pair is what keeps the two elements from being silently dropped from a component:
    an unknown digest leaves `hashes` empty, and the empty array is indistinguishable
    from a generator that never considered the question.

    A withheld value reports the withheld marker and stops there. The method, the
    subject, the evidence path and any unadopted digest are all facts *about* the
    artifact, and an artifact digest published beside a withheld marker would disclose
    what was withheld.
    """
    state_value = disclosure.state_of(FIELD_HASH_VALUE) if disclosure is not None else None
    state_algorithm = disclosure.state_of(FIELD_HASH_ALGORITHM) if disclosure is not None else None

    properties = [
        {
            "name": PROPERTY_HASH,
            "value": state_value if state_value else (result.get("value") or UNKNOWN),
        },
        {
            "name": PROPERTY_ALGORITHM,
            "value": state_algorithm if state_algorithm else (result.get("algorithm") or UNKNOWN),
        },
    ]

    if state_value == WITHHELD or state_algorithm == WITHHELD:
        return properties

    if not is_unknown(result):
        properties.append({"name": PROPERTY_METHOD, "value": result["method"]})
        properties.append({"name": PROPERTY_SUBJECT, "value": result["subject"]})
        if result.get("evidence"):
            properties.append({"name": PROPERTY_EVIDENCE, "value": result["evidence"]})

    for record in result.get("unadopted") or []:
        declared = record.get("declared")
        if not declared:
            continue
        properties.append({"name": PROPERTY_UNADOPTED, "value": declared})
        properties.append(
            {"name": PROPERTY_UNADOPTED_REASON, "value": record.get("reason") or REASON_MALFORMED}
        )

    return properties


def apply_hash_to_cyclonedx(cdx_component, result, disclosure=None):
    """Write the elements onto a built CycloneDX component. Returns it.

    `hashes` is CycloneDX's native slot for both elements at once — `alg` is the
    Component Hash Algorithm and `content` is the Component Hash Value — and
    `spdx_writer` translates it into SPDX `checksums`, so one emission covers both
    serializations.
    """
    entries = hash_entries(result, disclosure)
    if entries:
        cdx_component["hashes"] = entries
    cdx_component.setdefault("properties", []).extend(hash_properties(result, disclosure))
    return cdx_component


def hash_db_value(result, disclosure=None):
    """`(hash_value, hash_algorithm)` for `sbom_components`, never NULL.

    The columns landed unwritten in migration `20260808030213`. What goes in them is
    what the document says, including the two undisclosed states, so the row and the
    recipient's copy cannot come to differ.
    """
    properties = {p["name"]: p["value"] for p in hash_properties(result, disclosure)}
    return properties.get(PROPERTY_HASH, UNKNOWN), properties.get(PROPERTY_ALGORITHM, UNKNOWN)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _component_properties(component):
    return {p.get("name"): p.get("value") for p in component.get("properties") or [] if isinstance(p, dict)}


def _native_statement(component):
    """`(value, algorithm)` read from CycloneDX's own `hashes` array, or `(None, None)`.

    The ICDEV properties exist because CycloneDX cannot spell "unknown" inside `hashes`.
    Where a document *can* say what it means natively it does, and a component carrying a
    well-formed `hashes` entry has stated both elements whether or not ICDEV wrote it.
    Validating only the properties would report every conformant third-party CycloneDX
    document — and this generator's own output read back by a consumer that keeps only
    the standard fields — as stating nothing at all.
    """
    for entry in component.get("hashes") or []:
        if not isinstance(entry, dict):
            continue
        algorithm = normalize_algorithm(entry.get("alg"))
        content = str(entry.get("content") or "").strip()
        if algorithm and content:
            return content.lower(), algorithm
    return None, None


def validate_sbom_hashes(sbom):
    """Check a built CycloneDX document against both elements.

    Conformance here is not "every component has a digest" — the standard explicitly
    allows an inaccessible artifact — it is that every component *states* one of the
    permitted answers, and that every algorithm name it does state is a registered IANA
    Hash Function Textual Name.
    """
    components = list(sbom.get("components") or [])
    metadata_component = (sbom.get("metadata") or {}).get("component")
    if isinstance(metadata_component, dict):
        components.append(metadata_component)

    errors = []
    stated = 0
    undisclosed = 0

    for component in components:
        if not isinstance(component, dict):
            continue
        label = component.get("purl") or component.get("bom-ref") or component.get("name") or "?"
        properties = _component_properties(component)

        value = properties.get(PROPERTY_HASH)
        algorithm = properties.get(PROPERTY_ALGORITHM)
        if value is None or algorithm is None:
            # No ICDEV property pair — fall back to what CycloneDX itself says.
            value, algorithm = _native_statement(component)
        if value is None or algorithm is None:
            errors.append(f"{label}: Component Hash Value/Algorithm not stated at all")
            continue

        if value in (UNKNOWN, WITHHELD) or algorithm in (UNKNOWN, WITHHELD):
            undisclosed += 1
            if value == UNKNOWN and not Disclosure.from_cyclonedx(component).reason_for(FIELD_HASH_VALUE):
                errors.append(f"{label}: hash is unknown but no reason is recorded")
            continue

        if not is_iana_hash_name(algorithm):
            errors.append(
                f"{label}: {algorithm!r} is not an IANA Hash Function Textual Name "
                f"({sorted(IANA_HASH_NAMES)})"
            )
            continue
        if not is_approved(algorithm):
            errors.append(f"{label}: {algorithm!r} is a registered but unapproved algorithm")
            continue

        expected = hex_length_of(algorithm)
        if not _HEX_ONLY.match(value) or (expected is not None and len(value) != expected):
            errors.append(f"{label}: {value!r} is not a {expected}-character hexadecimal {algorithm} digest")
            continue

        for entry in component.get("hashes") or []:
            if not isinstance(entry, dict):
                continue
            if normalize_algorithm(entry.get("alg")) != algorithm or entry.get("content") != value:
                errors.append(f"{label}: hashes[] disagrees with the stated element")
        stated += 1

    return {
        "valid": not errors,
        "component_count": len(components),
        "hashes_stated": stated,
        "hashes_undisclosed": undisclosed,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Component Hash Value / Algorithm — 2026 SBOM Minimum Elements (sbx-fld-03)"
    )
    parser.add_argument("--registry", action="store_true", help="list the IANA Hash Function Textual Names")
    parser.add_argument("--validate", metavar="SBOM", help="check a CycloneDX document against both elements")
    parser.add_argument("--file", metavar="PATH", help="hexadecimal sha-256 digest of one artifact")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    if args.registry:
        payload = {
            "registry": "IANA Hash Function Textual Names",
            "names": {
                name: dict(spec, emittable=name in EMITTABLE_ALGORITHMS)
                for name, spec in sorted(IANA_HASH_FUNCTION_TEXTUAL_NAMES.items())
            },
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for name, spec in sorted(IANA_HASH_FUNCTION_TEXTUAL_NAMES.items()):
                emittable = "emitted" if name in EMITTABLE_ALGORITHMS else "not emitted"
                approval = "approved" if spec["approved"] else "NOT approved"
                print(f"{name:<10} {spec['reference']:<10} {approval:<13} {emittable}")
        return 0

    if args.file:
        value = hash_file(args.file)
        if not value:
            print(f"Error: could not read {args.file}", file=sys.stderr)
            return 1
        result = {"algorithm": DEFAULT_ALGORITHM, "value": value, "path": str(args.file)}
        print(json.dumps(result, indent=2) if args.json else f"{DEFAULT_ALGORITHM} {value}")
        return 0

    if args.validate:
        try:
            with open(args.validate, encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            print(f"Error: could not read {args.validate}: {exc}", file=sys.stderr)
            return 2
        report = validate_sbom_hashes(document)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(
                f"{report['component_count']} components — {report['hashes_stated']} with a digest, "
                f"{report['hashes_undisclosed']} explicitly undisclosed"
            )
            for error in report["errors"]:
                print(f"  ERROR {error}")
        return 0 if report["valid"] else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
