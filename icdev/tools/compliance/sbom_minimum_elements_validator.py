#!/usr/bin/env python3
# CUI // SP-CTI
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""Score an SBOM against the 2026 Minimum Elements.

Standard: *2026 Minimum Elements for a Software Bill of Materials (SBOM)*,
published 2026-07-29 by CISA with NSA, FBI and 16 international partners,
document version 2.1, TLP:CLEAR. It **replaces** the 2021 NTIA minimum
elements rather than amending them.

Gap analysis: ``docs/compliance/sbom-2026-minimum-elements-gap-analysis.md``.

WHAT THIS IS
------------
The measurement instrument for the ``sbx`` card. It reads a CycloneDX or SPDX
document and returns a per-element verdict — ``met`` / ``partial`` / ``gap``
with a rationale — over the standard's **17 data fields** and **6 applicable
practices**, plus totals and a weighted score.

Three consumers, and the third is the one that is easy to forget:

1. ``sbx-gov-01`` gates on the score, so a conformance regression blocks a
   deploy instead of being noticed later.
2. ``sbx-fmt-02`` replaces the filename-glob presence checks in
   ``fedramp_assessor``/``sbd_assessor``/``cssp_assessor``/``ivv_assessor``
   with a real parse — today those four decide an SBOM exists because a file
   matched ``*sbom*``.
3. **Grading SBOMs received from vendors.** The standard is aimed at
   organizations that *procure* software at least as much as at those that
   produce it, so this runs against third-party documents ICDEV did not
   generate and knows nothing else about. That is why the reader is
   format-agnostic, why nothing here imports the generator, and why
   ``sbom_record_id`` is optional on a recorded assessment.

WHY THE VERDICT IS THREE-VALUED
-------------------------------
A binary pass/fail would collapse "absent" and "present but non-conforming"
into one bucket, and those need different work: a gap is a feature to build, a
partial is a defect to fix. The gap analysis is written in the same three
values, so its matrix and this tool's output are directly comparable — which
is the point, since the card's baseline is stated in that matrix.

UNKNOWN vs WITHHELD
-------------------
The 2026 "Explicitly Identifying Unknown Information" element splits what 2021
called "known unknowns" into two distinct states: **unknown to the author**
and **withheld by the author**. They are not interchangeable — a recipient can
ask about the second and has a documented process for doing so, while the
first is a limit on what the producer could determine at all.

So this validator never scores them the same, and it treats a value that
conflates them as *worse* than a stated unknown: ``"unspecified"`` and
``"managed"`` (the two literals the current generator emits) are AMBIGUOUS,
not unknown. ``UNKNOWN_MARKERS``, ``WITHHELD_MARKERS`` and
``AMBIGUOUS_PLACEHOLDERS`` below are the vocabulary; **sbx-prc-01 must import
them rather than restate them**, or the producer and the grader will drift.

KNOWN LIMITS
------------
- SPDX support is JSON, versions 2.2 and 2.3. SPDX 3.x (JSON-LD ``@graph``)
  and SPDX tag-value are rejected with a named error rather than parsed
  approximately — mis-scoring a vendor's document is worse than declining it.
- The verdict is over the **document**. Practices are organizational, and only
  the part of a practice a document can evidence is scored; see each
  practice's rationale for what that part is.
"""

import argparse
import hashlib
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

VALIDATOR_VERSION = "1.0.0"

STANDARD_NAME = "2026 Minimum Elements for a Software Bill of Materials (SBOM)"
STANDARD_VERSION = "2.1"
STANDARD_DATE = "2026-07-29"
STANDARD_PUBLISHER = "CISA with NSA, FBI and 16 international partners"
CLASSIFICATION = "CUI // SP-CTI"

# ── Verdict vocabulary ────────────────────────────────────────────────────
STATUS_MET = "met"
STATUS_PARTIAL = "partial"
STATUS_GAP = "gap"

# Weighted score contribution. A partial is worth half a met because it is
# half the work: the field exists and the plumbing carries it, but the value
# does not conform.
STATUS_WEIGHT = {STATUS_MET: 1.0, STATUS_PARTIAL: 0.5, STATUS_GAP: 0.0}

CATEGORY_DATA_FIELD = "data_field"
CATEGORY_PRACTICE = "practice"

# ── Explicit-unknown vocabulary (2026 "Explicitly Identifying Unknown
# Information"). sbx-prc-01 owns emitting these; it must import them. ───────
UNKNOWN_MARKERS = frozenset(
    {
        "noassertion",  # SPDX's own spelling, and the interoperable one
        "unknown",
        "unknown-to-author",
        "not-identifiable",
    }
)

WITHHELD_MARKERS = frozenset(
    {
        "withheld",
        "withheld-by-author",
        "redacted",
    }
)

# Values that say nothing at all while looking like they say something. These
# are the failure the standard's 2026 revision targets: they are not machine
# interpretable and a recipient cannot tell whether the producer could not
# determine the value or chose not to publish it.
#
# "unspecified" and "managed" are literals the current generator emits (for an
# unpinned requirement and a Maven dependency whose version comes from a parent
# POM respectively) — see gap analysis §3.2, Component Version.
AMBIGUOUS_PLACEHOLDERS = frozenset(
    {
        "",
        "unspecified",
        "managed",
        "n/a",
        "na",
        "none",
        "null",
        "tbd",
        "todo",
        "-",
        "?",
    }
)

# ── ICDEV property keys for elements neither format has a native field for.
# Named here so the producer tasks (sbx-fld-*, sbx-prc-*) and this grader
# agree on the key rather than each inventing one. ─────────────────────────
PROP_AUTHOR = "icdev:sbom:author"
PROP_GENERATION_CONTEXT = "icdev:sbom:generation-context"
PROP_SBOM_VERSION = "icdev:sbom:version"
PROP_SUPERSEDES = "icdev:sbom:supersedes"
PROP_UNKNOWN_CONTACT = "icdev:sbom:unknown-information-contact"
PROP_COVERAGE = "icdev:sbom:coverage"
PROP_ALTERNATE_NAMES = "icdev:sbom:component:alternate-names"
PROP_UNKNOWN_FIELDS = "icdev:sbom:component:unknown-fields"
PROP_WITHHELD_FIELDS = "icdev:sbom:component:withheld-fields"

# Component Producer's carrier, owned by tools/compliance/component_producer.py
# (sbx-fld-02). Imported rather than restated — the same rule this module asks
# sbx-prc-01 to follow for the unknown/withheld vocabulary.
#
# Reading these is not optional. That module writes the native CycloneDX field
# ONLY when a producer is identifiable (`manufacturer` from 1.6, `supplier`
# below it) and states in its own docstring that "the properties, not the
# native field, remain the authoritative statement". A component of unknown
# provenance therefore carries no supplier at all — so a reader that looks only
# at the native field scores an explicitly-marked unknown as an absent value,
# which is exactly the distinction the 2026 standard added.
#
# Soft import with literal fallbacks: grading a vendor's SBOM must not depend
# on ICDEV's producer resolver being importable.
try:
    from tools.compliance.component_producer import (
        PROPERTY_PRODUCER as PROP_COMPONENT_PRODUCER,
        PROPERTY_PROVENANCE as PROP_COMPONENT_PROVENANCE,
    )
except ImportError:  # pragma: no cover - fallback for a partial checkout
    PROP_COMPONENT_PRODUCER = "icdev:component-producer"
    PROP_COMPONENT_PROVENANCE = "icdev:component-provenance"

# ── Format currency. The standard says to avoid deprecated versions of any
# format and to reassess supported formats regularly. ─────────────────────
CYCLONEDX_CURRENT = frozenset({"1.6", "1.7"})  # ECMA-424, December 2025
CYCLONEDX_SUPERSEDED = frozenset({"1.0", "1.1", "1.2", "1.3", "1.4", "1.5"})
SPDX_CURRENT = frozenset({"SPDX-2.3", "SPDX-3.0", "SPDX-3.0.1"})
SPDX_SUPERSEDED = frozenset({"SPDX-1.0", "SPDX-1.1", "SPDX-1.2", "SPDX-2.0", "SPDX-2.1", "SPDX-2.2"})

# ── Lifecycle vocabulary for SBOM Generation Context. CycloneDX 1.5+
# `metadata.lifecycles[].phase` is the native carrier and its vocabulary is
# the superset of the standard's examples ("before build", "build",
# "after build"). ─────────────────────────────────────────────────────────
GENERATION_CONTEXTS = frozenset(
    {
        "design",
        "pre-build",
        "build",
        "post-build",
        "operations",
        "discovery",
        "decommission",
        # The standard's own phrasing, accepted as written.
        "before build",
        "after build",
        "source",
    }
)

# ── Hash algorithms. The standard: named per IANA Hash Function Textual
# Names AND approved by a relevant authority such as NIST. The two are
# different tests and a broken algorithm passes the first. ────────────────
IANA_HASH_NAMES = frozenset(
    {
        "MD5",
        "SHA1",
        "SHA224",
        "SHA256",
        "SHA384",
        "SHA512",
        "SHA512224",
        "SHA512256",
        "SHA3224",
        "SHA3256",
        "SHA3384",
        "SHA3512",
        "BLAKE2B256",
        "BLAKE2B384",
        "BLAKE2B512",
        "BLAKE3",
    }
)

# NIST-approved for digital-signature/integrity use (FIPS 180-4, FIPS 202).
# MD5 and SHA-1 are named in IANA's registry and are collision-broken.
NIST_APPROVED_HASHES = frozenset(
    {
        "SHA224",
        "SHA256",
        "SHA384",
        "SHA512",
        "SHA512224",
        "SHA512256",
        "SHA3224",
        "SHA3256",
        "SHA3384",
        "SHA3512",
    }
)

# ── Signature algorithms. The standard requires approval by a relevant
# authority: NIST DSS (FIPS 186-5), ISO/IEC 14888-4:2024, or ENISA's Agreed
# Cryptographic Mechanisms. JOSE/JSF names, which is what CycloneDX carries.
APPROVED_SIGNATURE_ALGORITHMS = frozenset(
    {
        "RS256", "RS384", "RS512",
        "PS256", "PS384", "PS512",
        "ES256", "ES384", "ES512",
        "ED25519", "EDDSA", "ED448",
        "ML-DSA-44", "ML-DSA-65", "ML-DSA-87",  # FIPS 204
        "SLH-DSA",  # FIPS 205
    }
)

# Tool versions that are indistinguishable from an unset default. The current
# generator hardcodes "1.0.0" and has never changed it (gap analysis §3.1).
PLACEHOLDER_TOOL_VERSIONS = frozenset({"1.0.0", "0.0.0", "1.0", "0.0", "0", "1"})

# ── Timestamp shapes. RFC 9557 (IXDTF) is RFC 3339 plus a bracketed
# time-zone annotation. A bare RFC 3339 string is *permitted* by RFC 9557 but
# does not evidence it, and the standard names 9557 specifically. ─────────
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:?\d{2})$"
)
_RFC9557_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:?\d{2})"
    r"(?:\[[^\]]+\])+$"
)

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_UUID_URN_RE = re.compile(r"^urn:uuid:[0-9a-fA-F-]{36}$")

# Identifier classes the standard names. "At least one common software
# identifier"; if multiple exist, include all of them.
COMMON_IDENTIFIER_TYPES = frozenset({"purl", "cpe"})
INTRINSIC_IDENTIFIER_TYPES = frozenset({"swhid", "omnibor", "gitoid", "commit", "uuid"})


class UnsupportedFormatError(ValueError):
    """The document is not a format this validator can grade honestly."""


# ─────────────────────────────────────────────────────────────────────────
# Normalized model
#
# Both formats are read into one shape so the 23 scorers never branch on
# format. That matters for third-party grading: the verdict for a vendor's
# SPDX file and for ICDEV's CycloneDX file has to mean the same thing.
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class NormalizedComponent:
    """One component, with every field's *state* preserved, not just its value.

    ``unknown_fields`` / ``withheld_fields`` carry field names the document
    explicitly marked, which is why an absent value and an explicitly-unknown
    value can score differently.
    """

    name: str = ""
    alternate_names: list = field(default_factory=list)
    declares_alternate_names: bool = False
    version: str = ""
    producer: str = ""
    licenses: list = field(default_factory=list)
    hashes: list = field(default_factory=list)  # [{"algorithm": str, "value": str}]
    identifiers: list = field(default_factory=list)  # [{"type": str, "value": str}]
    ref: str = ""
    unknown_fields: set = field(default_factory=set)
    withheld_fields: set = field(default_factory=set)


@dataclass
class NormalizedSbom:
    """A format-agnostic view of an SBOM document."""

    format_name: str = ""
    format_version: str = ""
    author: str = ""
    author_is_tool: bool = False
    signature_value: str = ""
    signature_algorithm: str = ""
    generation_context: str = ""
    timestamp: str = ""
    tool_name: str = ""
    tool_version: str = ""
    sbom_version: str = ""  # semantic/string version if one exists
    revision_counter: str = ""  # CycloneDX integer `version`
    serial_number: str = ""
    supersedes: str = ""
    target_name: str = ""
    target_version: str = ""
    components: list = field(default_factory=list)
    dependency_refs: set = field(default_factory=set)  # refs appearing in the graph
    dependency_edges: int = 0
    coverage_aggregate: str = ""
    coverage_statement: str = ""
    distribution_urls: list = field(default_factory=list)
    unknown_contact: str = ""
    unknown_fields: set = field(default_factory=set)
    withheld_fields: set = field(default_factory=set)


# ─────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────


def _norm(value):
    """Lowercase, stripped string form of any scalar. '' for None."""
    if value is None:
        return ""
    return str(value).strip().lower()


def _marker_state(value):
    """Classify a raw field value into a state.

    Returns one of ``present`` / ``unknown`` / ``withheld`` / ``ambiguous`` /
    ``absent``. This single function is why the unknown-vs-withheld
    distinction is applied uniformly across all 23 elements instead of being
    re-decided per scorer.
    """
    text = _norm(value)
    if text in UNKNOWN_MARKERS:
        return "unknown"
    if text in WITHHELD_MARKERS:
        return "withheld"
    if text in AMBIGUOUS_PLACEHOLDERS:
        return "ambiguous" if text else "absent"
    return "present"


def _normalize_hash_name(algorithm):
    """Fold an algorithm name to its IANA comparison form.

    CycloneDX writes ``SHA-256``, SPDX writes ``SHA256``, and vendors write
    both plus ``sha_256``. The registry entry is the same one.
    """
    return re.sub(r"[-_\s]", "", str(algorithm or "")).upper()


def _pct(numerator, denominator):
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# ─────────────────────────────────────────────────────────────────────────
# Format detection and reading
# ─────────────────────────────────────────────────────────────────────────


def detect_format(document):
    """Return ``(format_name, format_version)`` for a parsed JSON document."""
    if not isinstance(document, dict):
        raise UnsupportedFormatError("SBOM document must be a JSON object")

    if document.get("bomFormat") == "CycloneDX" or "specVersion" in document:
        return "CycloneDX", str(document.get("specVersion", ""))

    if "spdxVersion" in document:
        return "SPDX", str(document.get("spdxVersion", ""))

    # SPDX 3.x drops spdxVersion for a JSON-LD graph. Detect it so the error
    # names the real reason instead of "unrecognised".
    if "@graph" in document or "@context" in document:
        raise UnsupportedFormatError(
            "SPDX 3.x JSON-LD is not supported by this validator. Supported: "
            "CycloneDX JSON (1.0-1.7) and SPDX JSON 2.2/2.3. Declining to score "
            "rather than grade a format it would read approximately."
        )

    raise UnsupportedFormatError(
        "Unrecognised SBOM format: no 'bomFormat', 'specVersion' or 'spdxVersion'. "
        "Supported: CycloneDX JSON and SPDX JSON 2.2/2.3."
    )


def _cdx_properties(container):
    """CycloneDX ``properties[]`` as a dict. Last value wins."""
    out = {}
    for prop in container.get("properties") or []:
        if isinstance(prop, dict) and prop.get("name"):
            out[str(prop["name"])] = prop.get("value", "")
    return out


def _cdx_component(raw):
    """Read one CycloneDX component into the normalized shape."""
    comp = NormalizedComponent()
    comp.name = str(raw.get("name") or "")
    comp.version = str(raw.get("version") or "")
    comp.ref = str(raw.get("bom-ref") or "")

    props = _cdx_properties(raw)

    # Producer: the 2026 element replaces Supplier Name outright, but the
    # producing entity can legitimately land in any of these depending on
    # spec version, so all four are read.
    supplier = raw.get("supplier") or {}
    manufacturer = raw.get("manufacturer") or {}
    # The ICDEV property is checked FIRST because component_producer.py declares
    # it authoritative over the native field, and because it is the only carrier
    # that survives when the producer is unknown.
    comp.producer = str(
        props.get(PROP_COMPONENT_PRODUCER)
        or props.get(PROP_COMPONENT_PROVENANCE)
        or (supplier.get("name") if isinstance(supplier, dict) else "")
        or (manufacturer.get("name") if isinstance(manufacturer, dict) else "")
        or raw.get("publisher")
        or raw.get("author")
        or ""
    )

    # Alternate names. Neither format has a native field, so the ICDEV
    # property is the carrier and its *presence* — even holding an empty
    # list — is what evidences that the format allows multiple entries.
    if PROP_ALTERNATE_NAMES in props:
        comp.declares_alternate_names = True
        raw_alts = props[PROP_ALTERNATE_NAMES]
        try:
            parsed = json.loads(raw_alts) if raw_alts else []
            comp.alternate_names = [str(a) for a in parsed] if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            comp.alternate_names = [a.strip() for a in str(raw_alts).split(",") if a.strip()]

    for prop_key, target in ((PROP_UNKNOWN_FIELDS, comp.unknown_fields),
                             (PROP_WITHHELD_FIELDS, comp.withheld_fields)):
        if prop_key in props:
            raw_list = props[prop_key]
            try:
                parsed = json.loads(raw_list) if raw_list else []
            except (ValueError, TypeError):
                parsed = [a.strip() for a in str(raw_list).split(",")]
            if isinstance(parsed, list):
                target.update(_norm(a) for a in parsed if _norm(a))

    for lic in raw.get("licenses") or []:
        if not isinstance(lic, dict):
            continue
        if lic.get("expression"):
            comp.licenses.append(str(lic["expression"]))
            continue
        inner = lic.get("license") or {}
        if isinstance(inner, dict):
            value = inner.get("id") or inner.get("name") or inner.get("url")
            if value:
                comp.licenses.append(str(value))

    for hsh in raw.get("hashes") or []:
        if isinstance(hsh, dict) and hsh.get("content"):
            comp.hashes.append(
                {"algorithm": str(hsh.get("alg") or ""), "value": str(hsh["content"])}
            )

    if raw.get("purl"):
        comp.identifiers.append({"type": "purl", "value": str(raw["purl"])})
    if raw.get("cpe"):
        comp.identifiers.append({"type": "cpe", "value": str(raw["cpe"])})
    if raw.get("omniborId"):
        for value in raw["omniborId"] if isinstance(raw["omniborId"], list) else [raw["omniborId"]]:
            comp.identifiers.append({"type": "omnibor", "value": str(value)})
    if raw.get("swhid"):
        for value in raw["swhid"] if isinstance(raw["swhid"], list) else [raw["swhid"]]:
            comp.identifiers.append({"type": "swhid", "value": str(value)})
    for ref in raw.get("externalReferences") or []:
        if isinstance(ref, dict) and _norm(ref.get("type")) == "vcs" and ref.get("url"):
            comp.identifiers.append({"type": "commit", "value": str(ref["url"])})

    return comp


def read_cyclonedx(document):
    """Read a CycloneDX JSON document into the normalized shape."""
    sbom = NormalizedSbom()
    sbom.format_name = "CycloneDX"
    sbom.format_version = str(document.get("specVersion") or "")

    metadata = document.get("metadata") or {}
    props = _cdx_properties(metadata)

    sbom.timestamp = str(metadata.get("timestamp") or "")

    # Author. The standard is explicit that the entity *operating* the tool is
    # the author and the tool vendor is not — so `tools[].vendor` is read only
    # to detect that mistake, never to satisfy the element.
    authors = metadata.get("authors") or []
    if isinstance(authors, list) and authors:
        first = authors[0]
        sbom.author = str(first.get("name") or "") if isinstance(first, dict) else str(first)
    if not sbom.author:
        for key in ("manufacture", "manufacturer", "supplier"):
            entity = metadata.get(key)
            if isinstance(entity, dict) and entity.get("name"):
                sbom.author = str(entity["name"])
                break
    if not sbom.author and props.get(PROP_AUTHOR):
        sbom.author = str(props[PROP_AUTHOR])

    # Tools: CycloneDX 1.4 array form and 1.5+ `{components:[], services:[]}`.
    tools = metadata.get("tools")
    tool_entries = []
    if isinstance(tools, list):
        tool_entries = [t for t in tools if isinstance(t, dict)]
    elif isinstance(tools, dict):
        tool_entries = [t for t in (tools.get("components") or []) if isinstance(t, dict)]
    if tool_entries:
        sbom.tool_name = str(tool_entries[0].get("name") or "")
        sbom.tool_version = str(tool_entries[0].get("version") or "")
        vendor = str(tool_entries[0].get("vendor") or tool_entries[0].get("publisher") or "")
        if vendor and not sbom.author:
            # A tool vendor and no author at all is the specific mistake the
            # standard calls out, so record it and let the scorer say why.
            #
            # Note what is NOT flagged: an author that happens to equal the tool
            # vendor. An organization operating a tool it also publishes is a
            # legitimate author, and the element is about who ran the tool.
            sbom.author_is_tool = True

    lifecycles = metadata.get("lifecycles") or []
    if isinstance(lifecycles, list) and lifecycles:
        first = lifecycles[0]
        sbom.generation_context = str(
            (first.get("phase") or first.get("name") or "") if isinstance(first, dict) else first
        )
    if not sbom.generation_context:
        sbom.generation_context = str(props.get(PROP_GENERATION_CONTEXT) or "")

    sbom.serial_number = str(document.get("serialNumber") or "")
    if document.get("version") is not None:
        sbom.revision_counter = str(document.get("version"))
    sbom.sbom_version = str(props.get(PROP_SBOM_VERSION) or "")
    sbom.supersedes = str(props.get(PROP_SUPERSEDES) or "")
    sbom.unknown_contact = str(props.get(PROP_UNKNOWN_CONTACT) or "")

    signature = document.get("signature") or {}
    if isinstance(signature, dict):
        sbom.signature_value = str(signature.get("value") or "")
        sbom.signature_algorithm = str(signature.get("algorithm") or "")
        # JSF also allows a `signers[]` array for multi-party signatures.
        if not sbom.signature_value:
            signers = signature.get("signers") or []
            if isinstance(signers, list) and signers and isinstance(signers[0], dict):
                sbom.signature_value = str(signers[0].get("value") or "")
                sbom.signature_algorithm = str(signers[0].get("algorithm") or "")

    target = metadata.get("component") or {}
    if isinstance(target, dict):
        sbom.target_name = str(target.get("name") or "")
        sbom.target_version = str(target.get("version") or "")

    for raw in document.get("components") or []:
        if isinstance(raw, dict):
            sbom.components.append(_cdx_component(raw))

    for entry in document.get("dependencies") or []:
        if not isinstance(entry, dict):
            continue
        ref = str(entry.get("ref") or "")
        if ref:
            sbom.dependency_refs.add(ref)
        children = entry.get("dependsOn") or entry.get("provides") or []
        for child in children:
            sbom.dependency_refs.add(str(child))
            sbom.dependency_edges += 1

    compositions = document.get("compositions") or []
    aggregates = [
        _norm(c.get("aggregate")) for c in compositions if isinstance(c, dict) and c.get("aggregate")
    ]
    if aggregates:
        # The document's completeness is its weakest assembly: one incomplete
        # composition makes the component set incomplete, whatever the rest say.
        for weakest in ("unknown", "not_specified", "incomplete_first_party_only",
                        "incomplete_third_party_only", "incomplete", "complete"):
            if weakest in aggregates:
                sbom.coverage_aggregate = weakest
                break
        else:
            sbom.coverage_aggregate = aggregates[0]
    elif props.get(PROP_COVERAGE):
        sbom.coverage_aggregate = _norm(props[PROP_COVERAGE])
    sbom.coverage_statement = str(props.get(f"{PROP_COVERAGE}:statement") or "")

    for ref in document.get("externalReferences") or []:
        if not isinstance(ref, dict):
            continue
        if _norm(ref.get("type")) in {"distribution", "distribution-intake", "bom"} and ref.get("url"):
            sbom.distribution_urls.append(str(ref["url"]))
    for ref in metadata.get("externalReferences") or []:
        if isinstance(ref, dict) and _norm(ref.get("type")) in {"distribution", "bom"} and ref.get("url"):
            sbom.distribution_urls.append(str(ref["url"]))

    return sbom


def _spdx_component(raw):
    """Read one SPDX package into the normalized shape."""
    comp = NormalizedComponent()
    comp.name = str(raw.get("name") or "")
    comp.version = str(raw.get("versionInfo") or "")
    comp.ref = str(raw.get("SPDXID") or "")

    # SPDX writes `Organization: Acme Inc (contact@acme.example)`. The prefix
    # is the entity *class*, not part of the name.
    producer = raw.get("supplier") or raw.get("originator") or ""
    producer = str(producer)
    for prefix in ("Organization:", "Person:", "Tool:"):
        if producer.startswith(prefix):
            producer = producer[len(prefix):].strip()
            break
    comp.producer = producer

    for key in ("licenseConcluded", "licenseDeclared"):
        value = raw.get(key)
        if value and _norm(value) not in AMBIGUOUS_PLACEHOLDERS:
            comp.licenses.append(str(value))
            break

    for checksum in raw.get("checksums") or []:
        if isinstance(checksum, dict) and checksum.get("checksumValue"):
            comp.hashes.append(
                {
                    "algorithm": str(checksum.get("algorithm") or ""),
                    "value": str(checksum["checksumValue"]),
                }
            )

    for ref in raw.get("externalRefs") or []:
        if not isinstance(ref, dict):
            continue
        ref_type = _norm(ref.get("referenceType"))
        locator = str(ref.get("referenceLocator") or "")
        if not locator:
            continue
        if ref_type == "purl":
            comp.identifiers.append({"type": "purl", "value": locator})
        elif ref_type.startswith("cpe"):
            comp.identifiers.append({"type": "cpe", "value": locator})
        elif ref_type in {"swh", "swhid"}:
            comp.identifiers.append({"type": "swhid", "value": locator})
        elif ref_type in {"gitoid", "omnibor"}:
            comp.identifiers.append({"type": "omnibor", "value": locator})

    # SPDX has no alternate-name field at all, so an SPDX document can only
    # evidence this through a comment convention. Read it, do not invent it.
    comment = str(raw.get("comment") or "")
    if "alternate-names:" in comment.lower():
        comp.declares_alternate_names = True
        tail = comment.lower().split("alternate-names:", 1)[1]
        comp.alternate_names = [a.strip() for a in tail.split(",") if a.strip()]

    return comp


def read_spdx(document):
    """Read an SPDX 2.2/2.3 JSON document into the normalized shape."""
    version = str(document.get("spdxVersion") or "")
    if version and not version.startswith(("SPDX-2.2", "SPDX-2.3")):
        if version.startswith("SPDX-3"):
            raise UnsupportedFormatError(
                f"SPDX {version} is not supported by this validator. Supported: SPDX-2.2 "
                "and SPDX-2.3 JSON. Declining to score rather than grade approximately."
            )
        # 2.0/2.1 read close enough to 2.2 to be graded, and grading them is
        # how a superseded-format finding gets raised at all.

    sbom = NormalizedSbom()
    sbom.format_name = "SPDX"
    sbom.format_version = version

    creation = document.get("creationInfo") or {}
    sbom.timestamp = str(creation.get("created") or "")

    for creator in creation.get("creators") or []:
        text = str(creator)
        if text.startswith(("Organization:", "Person:")):
            if not sbom.author:
                sbom.author = text.split(":", 1)[1].strip()
        elif text.startswith("Tool:"):
            tool = text.split(":", 1)[1].strip()
            # SPDX convention is `Tool: name-version`.
            if "-" in tool:
                name, _, tool_version = tool.rpartition("-")
                sbom.tool_name = name.strip() or tool
                sbom.tool_version = tool_version.strip()
            else:
                sbom.tool_name = tool
    if not sbom.author:
        sbom.author_is_tool = bool(sbom.tool_name)

    comment = str(creation.get("comment") or "") + " " + str(document.get("comment") or "")
    lowered = comment.lower()
    for key, attr in (
        ("generation-context:", "generation_context"),
        ("sbom-version:", "sbom_version"),
        ("supersedes:", "supersedes"),
        ("unknown-information-contact:", "unknown_contact"),
    ):
        if key in lowered:
            tail = comment[lowered.index(key) + len(key):]
            setattr(sbom, attr, tail.split("\n", 1)[0].strip())

    sbom.serial_number = str(document.get("documentNamespace") or "")
    sbom.target_name = str(document.get("name") or "")

    for raw in document.get("packages") or []:
        if isinstance(raw, dict):
            sbom.components.append(_spdx_component(raw))

    described = set()
    for rel in document.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        rel_type = str(rel.get("relationshipType") or "").upper()
        parent = str(rel.get("spdxElementId") or "")
        child = str(rel.get("relatedSpdxElement") or "")
        if rel_type == "DESCRIBES":
            described.add(child)
            sbom.dependency_refs.add(child)
            continue
        if rel_type in {"AMENDS", "VARIANT_OF", "SPECIFICATION_FOR"}:
            if rel_type == "AMENDS" and not sbom.supersedes:
                sbom.supersedes = child
            continue
        if rel_type in {
            "DEPENDS_ON", "DEPENDENCY_OF", "CONTAINS", "CONTAINED_BY",
            "STATIC_LINK", "DYNAMIC_LINK", "BUILD_DEPENDENCY_OF",
            "DEV_DEPENDENCY_OF", "OPTIONAL_DEPENDENCY_OF", "RUNTIME_DEPENDENCY_OF",
        }:
            sbom.dependency_refs.add(parent)
            sbom.dependency_refs.add(child)
            sbom.dependency_edges += 1

    # SPDX states completeness through PackageVerificationCode / filesAnalyzed
    # rather than a compositions array. A document that says nothing says
    # "unknown", which is what the aggregate has to report.
    target = None
    for raw in document.get("packages") or []:
        if isinstance(raw, dict) and raw.get("SPDXID") in described:
            target = raw
            break
    if target is not None:
        sbom.target_version = str(target.get("versionInfo") or "")

    if "sbom-coverage:" in lowered:
        tail = comment[lowered.index("sbom-coverage:") + len("sbom-coverage:"):]
        sbom.coverage_aggregate = _norm(tail.split("\n", 1)[0])
        sbom.coverage_statement = tail.split("\n", 1)[0].strip()

    for ref in document.get("externalDocumentRefs") or []:
        if isinstance(ref, dict) and ref.get("spdxDocument"):
            sbom.distribution_urls.append(str(ref["spdxDocument"]))
    if str(sbom.serial_number).startswith(("http://", "https://")):
        sbom.distribution_urls.append(sbom.serial_number)

    return sbom


def read_document(document):
    """Normalize any supported SBOM document."""
    format_name, _version = detect_format(document)
    if format_name == "CycloneDX":
        return read_cyclonedx(document)
    return read_spdx(document)


def load_document(path):
    """Load and normalize an SBOM file. Returns ``(raw_json, NormalizedSbom)``."""
    sbom_path = Path(path)
    if not sbom_path.is_file():
        raise FileNotFoundError(f"SBOM not found: {sbom_path}")
    text = sbom_path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise UnsupportedFormatError(
            f"{sbom_path} is not valid JSON ({exc}). SPDX tag-value is not supported."
        ) from exc
    return raw, read_document(raw)


# ─────────────────────────────────────────────────────────────────────────
# Element scorers
#
# Each returns (status, rationale). Rationale is written for the person who
# has to close the gap: it names the field and the expected value, not just
# the verdict.
# ─────────────────────────────────────────────────────────────────────────


def _score_sbom_author(s):
    if not s.author:
        if s.author_is_tool:
            return (
                STATUS_GAP,
                "No author. The document names a tool vendor only, and the standard is "
                "explicit that the author is the entity OPERATING the tool, not the tool "
                f"or its vendor. Emit metadata.authors[].name or the {PROP_AUTHOR} property.",
            )
        return STATUS_GAP, "No SBOM author recorded anywhere in the document."
    state = _marker_state(s.author)
    if state in {"unknown", "withheld", "ambiguous"}:
        return (
            STATUS_PARTIAL,
            f"Author present but not identifying ({s.author!r}). The author element has no "
            "unknown fallback — the entity creating the SBOM data always knows who it is.",
        )
    if len(s.author) <= 4 and s.author.isupper():
        return (
            STATUS_PARTIAL,
            f"Author {s.author!r} looks like an acronym; the standard requires full names.",
        )
    return STATUS_MET, f"Author is {s.author!r}, distinct from the tool and its vendor."


def _score_author_signature(s):
    if not s.signature_value:
        return (
            STATUS_GAP,
            "No digital signature attributable to the SBOM author. CycloneDX carries this in "
            "the top-level `signature` (JSF) object.",
        )
    algorithm = s.signature_algorithm.upper().replace("_", "-")
    if not algorithm:
        return STATUS_PARTIAL, "Signature present but no algorithm named, so it cannot be verified."
    if algorithm not in APPROVED_SIGNATURE_ALGORITHMS:
        return (
            STATUS_PARTIAL,
            f"Signature algorithm {s.signature_algorithm!r} is not on the approved list "
            "(NIST DSS / ISO/IEC 14888-4:2024 / ENISA Agreed Cryptographic Mechanisms).",
        )
    return STATUS_MET, f"Signature present, algorithm {s.signature_algorithm} is approved."


def _score_data_format_name(s):
    if not s.format_name:
        return STATUS_GAP, "No data format name declared."
    return STATUS_MET, f"Data format name is {s.format_name!r}."


def _score_data_format_version(s):
    if not s.format_version:
        return STATUS_GAP, "No data format version declared."
    if s.format_name == "CycloneDX":
        if s.format_version in CYCLONEDX_CURRENT:
            return STATUS_MET, f"CycloneDX {s.format_version} is current (ECMA-424, Dec 2025)."
        if s.format_version in CYCLONEDX_SUPERSEDED:
            return (
                STATUS_PARTIAL,
                f"CycloneDX {s.format_version} is superseded. The standard cites ECMA-424 "
                f"and warns against deprecated versions; use {sorted(CYCLONEDX_CURRENT)}.",
            )
        return STATUS_PARTIAL, f"Unrecognised CycloneDX version {s.format_version!r}."
    if s.format_version in SPDX_CURRENT:
        return STATUS_MET, f"{s.format_version} is current."
    if s.format_version in SPDX_SUPERSEDED:
        return (
            STATUS_PARTIAL,
            f"{s.format_version} is superseded; the standard warns against deprecated versions.",
        )
    return STATUS_PARTIAL, f"Unrecognised SPDX version {s.format_version!r}."


def _score_generation_context(s):
    if not s.generation_context:
        return (
            STATUS_GAP,
            "No lifecycle phase recorded. CycloneDX 1.5+ carries this in "
            f"metadata.lifecycles[].phase; otherwise use the {PROP_GENERATION_CONTEXT} property. "
            "An SBOM built from source manifests is 'pre-build' and that is knowable at "
            "generation time.",
        )
    if _norm(s.generation_context) not in GENERATION_CONTEXTS:
        return (
            STATUS_PARTIAL,
            f"Generation context {s.generation_context!r} is outside the lifecycle vocabulary "
            f"{sorted(GENERATION_CONTEXTS)}.",
        )
    return STATUS_MET, f"Generation context is {s.generation_context!r}."


def _score_timestamp(s):
    if not s.timestamp:
        return STATUS_GAP, "No timestamp."
    if _RFC9557_RE.match(s.timestamp):
        return STATUS_MET, f"Timestamp {s.timestamp!r} conforms to RFC 9557 (IXDTF)."
    if _RFC3339_RE.match(s.timestamp):
        return (
            STATUS_PARTIAL,
            f"Timestamp {s.timestamp!r} is RFC 3339 but carries no RFC 9557 time-zone "
            "annotation. The standard names RFC 9557 specifically; append the bracketed "
            "zone, e.g. '2026-08-08T03:42:20.123456+00:00[UTC]'.",
        )
    return STATUS_GAP, f"Timestamp {s.timestamp!r} parses as neither RFC 3339 nor RFC 9557."


def _score_tool_name(s):
    if not s.tool_name:
        return STATUS_GAP, "No generating tool named."
    return STATUS_MET, f"Tool name is {s.tool_name!r}."


def _score_tool_version(s):
    state = _marker_state(s.tool_version)
    if state == "unknown":
        return (
            STATUS_MET,
            f"Tool version explicitly marked unknown ({s.tool_version!r}), which the standard "
            "permits where the version is unavailable to the author.",
        )
    if state in {"absent", "ambiguous"}:
        return (
            STATUS_GAP,
            "No tool version. Where it is unavailable the author must indicate unknown "
            f"explicitly (one of {sorted(UNKNOWN_MARKERS)}), not leave it blank.",
        )
    if s.tool_version in PLACEHOLDER_TOOL_VERSIONS:
        return (
            STATUS_PARTIAL,
            f"Tool version {s.tool_version!r} is a common unset default and cannot be "
            "distinguished from a hardcoded constant, so it does not identify the code "
            "delivery. Derive it from the package version. If it is genuinely this "
            "version, no change is needed.",
        )
    return STATUS_MET, f"Tool version is {s.tool_version!r}."


def _score_sbom_version(s):
    if s.sbom_version:
        match = _SEMVER_RE.match(s.sbom_version)
        if match:
            major = int(match.group(1))
            if major != 1:
                return (
                    STATUS_PARTIAL,
                    f"SBOM version {s.sbom_version!r} has major version {major}. The standard "
                    "says an SBOM following these elements should carry major version 1.",
                )
            return STATUS_MET, f"SBOM version is {s.sbom_version!r} (SemVer, major 1)."
        if _UUID_URN_RE.match(s.sbom_version):
            return STATUS_MET, f"SBOM version is a serial identifier {s.sbom_version!r} (RFC 9562)."
        return (
            STATUS_PARTIAL,
            f"SBOM version {s.sbom_version!r} is neither SemVer nor an RFC 9562 serial number.",
        )
    if s.revision_counter:
        return (
            STATUS_PARTIAL,
            f"Only a bare revision counter ({s.revision_counter!r}) is present. It says nothing "
            "about the change from the previous version, and it does not reconcile with the "
            f"independently counting sbom_records.version. Emit a SemVer string in "
            f"{PROP_SBOM_VERSION} and keep the two in step.",
        )
    return STATUS_GAP, "No SBOM version identifier of any kind."


def _component_field_summary(components, getter, field_name):
    """Bucket components by the state of one field.

    Returns ``(present, explicit_unknown, withheld, ambiguous, absent)`` counts.
    The explicit-unknown bucket is separate from absent on purpose — under the
    2026 standard those are a conforming statement and a defect respectively.
    """
    counts = {"present": 0, "unknown": 0, "withheld": 0, "ambiguous": 0, "absent": 0}
    for comp in components:
        value = getter(comp)
        state = _marker_state(value)
        if state in {"absent", "ambiguous"} and field_name in comp.unknown_fields:
            state = "unknown"
        elif state in {"absent", "ambiguous"} and field_name in comp.withheld_fields:
            state = "withheld"
        counts[state] += 1
    return counts


def _conforming_component_verdict(counts, total, element, remedy):
    """Shared shape for the eight per-component elements."""
    if total == 0:
        return (
            STATUS_GAP,
            f"No components in the document, so {element} cannot be evidenced.",
        )
    conforming = counts["present"] + counts["unknown"] + counts["withheld"]
    if conforming == total:
        detail = f"{counts['present']}/{total} carry a value"
        if counts["unknown"]:
            detail += f", {counts['unknown']} explicitly unknown"
        if counts["withheld"]:
            detail += f", {counts['withheld']} explicitly withheld"
        return STATUS_MET, f"{element}: {detail}."
    if conforming == 0:
        return STATUS_GAP, f"{element}: absent on all {total} components. {remedy}"
    missing = total - conforming
    ambiguous_note = ""
    if counts["ambiguous"]:
        ambiguous_note = (
            f" {counts['ambiguous']} carry an ambiguous placeholder, which conflates "
            "unknown-to-author with withheld-by-author and is not machine interpretable."
        )
    return (
        STATUS_PARTIAL,
        f"{element}: {conforming}/{total} components conform, {missing} do not.{ambiguous_note} {remedy}",
    )


def _score_component_producer(s):
    counts = _component_field_summary(s.components, lambda c: c.producer, "producer")
    return _conforming_component_verdict(
        counts,
        len(s.components),
        "Component Producer",
        "Where no producer is identifiable the standard requires the component to be marked "
        f"explicitly as of unknown provenance (one of {sorted(UNKNOWN_MARKERS)}); silence is "
        "not permitted.",
    )


def _score_dependency_relationship(s):
    total = len(s.components)
    if total == 0:
        return STATUS_GAP, "No components, so no dependency relationships can be expressed."
    if s.dependency_edges == 0 and not s.dependency_refs:
        return (
            STATUS_GAP,
            "Flat component list: no dependency graph is expressed at all, so a recipient "
            "cannot determine which component requires which. CycloneDX carries this in the "
            "top-level `dependencies` array; SPDX in `relationships`.",
        )
    covered = sum(1 for c in s.components if c.ref and c.ref in s.dependency_refs)
    if covered == total and s.dependency_edges > 0:
        return (
            STATUS_MET,
            f"Dependency graph covers all {total} components across {s.dependency_edges} edges.",
        )
    if s.dependency_edges == 0:
        return (
            STATUS_PARTIAL,
            f"Components are referenced in the graph but it declares no edges, so no "
            f"relationship between any two of the {total} components is expressed.",
        )
    return (
        STATUS_PARTIAL,
        f"Dependency graph covers {covered}/{total} components across {s.dependency_edges} "
        "edges. Every component needs an entry, even one whose dependency set is empty — "
        "otherwise absence from the graph is indistinguishable from an unstated relationship.",
    )


def _score_component_hash_value(s):
    counts = _component_field_summary(
        s.components,
        lambda c: (c.hashes[0]["value"] if c.hashes else ""),
        "hash_value",
    )
    return _conforming_component_verdict(
        counts,
        len(s.components),
        "Component Hash Value",
        "The hash is taken over the executable component artifact. Where the author cannot "
        "access the artifact the value must be marked unknown explicitly.",
    )


def _score_component_hash_algorithm(s):
    hashed = [c for c in s.components if c.hashes]
    if not hashed:
        return (
            STATUS_GAP,
            "No component carries a hash, so no algorithm is named. This is consequent to the "
            "Component Hash Value gap, not independent of it.",
        )
    unnamed, unapproved, approved = [], [], 0
    for comp in hashed:
        for hsh in comp.hashes:
            name = _normalize_hash_name(hsh["algorithm"])
            if not name:
                unnamed.append(comp.name)
            elif name not in IANA_HASH_NAMES:
                unnamed.append(f"{comp.name} ({hsh['algorithm']})")
            elif name not in NIST_APPROVED_HASHES:
                unapproved.append(f"{comp.name} ({hsh['algorithm']})")
            else:
                approved += 1
    if unnamed:
        return (
            STATUS_PARTIAL,
            f"{len(unnamed)} hash entries name no algorithm or one absent from the IANA Hash "
            f"Function Textual Names registry (e.g. {unnamed[0]}).",
        )
    if unapproved:
        return (
            STATUS_PARTIAL,
            f"{len(unapproved)} hash entries use an IANA-named but non-NIST-approved algorithm "
            f"(e.g. {unapproved[0]}). MD5 and SHA-1 are collision-broken.",
        )
    if len(hashed) < len(s.components):
        return (
            STATUS_PARTIAL,
            f"All {approved} named algorithms are IANA-registered and NIST-approved, but only "
            f"{len(hashed)}/{len(s.components)} components are hashed at all.",
        )
    return (
        STATUS_MET,
        f"All {approved} hash entries name an IANA-registered, NIST-approved algorithm.",
    )


def _score_component_identifiers(s):
    total = len(s.components)
    if total == 0:
        return STATUS_GAP, "No components, so no identifiers."
    with_common = 0
    types_seen = set()
    for comp in s.components:
        comp_types = {_norm(i["type"]) for i in comp.identifiers if _marker_state(i["value"]) == "present"}
        types_seen |= comp_types
        if comp_types & COMMON_IDENTIFIER_TYPES:
            with_common += 1
    if with_common == 0:
        return (
            STATUS_GAP,
            f"No component carries a common software identifier. The standard requires at "
            f"least one of {sorted(COMMON_IDENTIFIER_TYPES)} per component.",
        )
    if with_common < total:
        return (
            STATUS_PARTIAL,
            f"{with_common}/{total} components carry a common software identifier (CPE or PURL); "
            f"{total - with_common} carry none.",
        )
    if len(types_seen) < 2:
        only = sorted(types_seen)[0] if types_seen else "none"
        return (
            STATUS_PARTIAL,
            f"Every component carries exactly one identifier type ({only}) and the document "
            "carries no other class anywhere. The standard requires that where multiple "
            "identifiers exist, ALL of them are included — CPE alongside PURL for NVD lookup, "
            f"plus any of {sorted(INTRINSIC_IDENTIFIER_TYPES)} that are known.",
        )
    return (
        STATUS_MET,
        f"All {total} components carry a common software identifier; "
        f"{len(types_seen)} identifier classes present ({', '.join(sorted(types_seen))}).",
    )


def _score_component_license(s):
    counts = _component_field_summary(
        s.components,
        lambda c: (c.licenses[0] if c.licenses else ""),
        "license",
    )
    return _conforming_component_verdict(
        counts,
        len(s.components),
        "Component License",
        "Use SPDX license identifiers where possible, otherwise a URL to the full details. "
        "Proprietary conditions must be indicated and unknown must be stated explicitly.",
    )


def _score_component_name(s):
    total = len(s.components)
    if total == 0:
        return STATUS_GAP, "No components, so no names."
    unnamed = [c for c in s.components if _marker_state(c.name) != "present"]
    if unnamed:
        return (
            STATUS_PARTIAL if len(unnamed) < total else STATUS_GAP,
            f"{len(unnamed)}/{total} components have no usable name.",
        )
    declaring = sum(1 for c in s.components if c.declares_alternate_names)
    if declaring < total:
        return (
            STATUS_PARTIAL,
            f"All {total} components are named, but {total - declaring} carry no alternate-name "
            "declaration. The standard requires the format to allow multiple entries so "
            "alternate names can be captured; neither CycloneDX nor SPDX has a native field, "
            f"so declare the list (empty is fine) in the {PROP_ALTERNATE_NAMES} property.",
        )
    alternates = sum(len(c.alternate_names) for c in s.components)
    return (
        STATUS_MET,
        f"All {total} components are named and declare an alternate-name list "
        f"({alternates} alternates recorded).",
    )


def _score_component_version(s):
    counts = _component_field_summary(s.components, lambda c: c.version, "version")
    return _conforming_component_verdict(
        counts,
        len(s.components),
        "Component Version",
        "Where the producer provides no version the author must indicate unknown explicitly. "
        f"The literals {sorted(AMBIGUOUS_PLACEHOLDERS - {''})} are not machine-interpretable "
        "unknown markers and collide with Explicitly Identifying Unknown Information.",
    )


# ── Practices ─────────────────────────────────────────────────────────────


def _score_accommodation_of_updates(s):
    if s.supersedes:
        return (
            STATUS_MET,
            f"The document identifies the version it supersedes ({s.supersedes!r}), so a "
            "recipient can order revisions and see that a correction happened.",
        )
    first_version = _SEMVER_RE.match(s.sbom_version or "")
    if first_version and s.sbom_version.startswith("1.0.0"):
        return (
            STATUS_MET,
            "The document declares itself the first version (1.0.0), which is the other "
            "conforming state for this element.",
        )
    if s.sbom_version or s.revision_counter:
        return (
            STATUS_PARTIAL,
            "A version identifier exists but nothing links this document to the one it "
            f"corrects or replaces. Emit {PROP_SUPERSEDES} (or an SPDX AMENDS relationship) "
            "so a correction is visible as a correction. Recipients may now weigh SBOM errors "
            "in risk decisions, so the 2021 tolerance for silent revision is withdrawn.",
        )
    return STATUS_GAP, "No revision lineage of any kind: no version and no supersedes link."


def _score_coverage(s):
    if not s.coverage_aggregate:
        return (
            STATUS_GAP,
            "The document makes no completeness statement, so a recipient cannot tell whether "
            "a component's absence means it is not present or merely not found. CycloneDX "
            "carries this in compositions[].aggregate.",
        )
    aggregate = _norm(s.coverage_aggregate)
    if aggregate == "complete":
        return (
            STATUS_MET,
            "Component set is declared complete, covering transitive dependencies. The "
            "standard sets no minimum depth.",
        )
    if aggregate in {"unknown", "not_specified"}:
        return (
            STATUS_PARTIAL,
            f"Completeness is declared {aggregate!r}. The statement is present and honest, "
            "which is what the element requires of the document, but the underlying set is "
            "not known to cover transitive dependencies.",
        )
    return (
        STATUS_PARTIAL,
        f"Completeness is declared {aggregate!r}. The declaration conforms; the component set "
        "does not yet reach every transitive dependency."
        + (f" Statement: {s.coverage_statement}" if s.coverage_statement else ""),
    )


def _score_distribution_and_delivery(s):
    if s.distribution_urls:
        versioned = [
            url
            for url in s.distribution_urls
            if (s.target_version and s.target_version in url)
            or (s.serial_number and s.serial_number.rsplit(":", 1)[-1] in url)
            or re.search(r"/\d+\.\d+", url)
        ]
        if versioned:
            return (
                STATUS_MET,
                f"A version-specific retrieval URL is declared ({versioned[0]}).",
            )
        return (
            STATUS_PARTIAL,
            f"A retrieval URL is declared ({s.distribution_urls[0]}) but it is not "
            "version-specific, so it cannot identify which build this SBOM describes.",
        )
    return (
        STATUS_GAP,
        "No retrieval mechanism is declared in the document: no version-specific URL, API "
        "endpoint or repository reference. A file path on the producer's disk is not one. "
        "CycloneDX carries this in externalReferences[type=distribution].",
    )


def _score_explicit_unknowns(s):
    ambiguous = []
    # Counted as (component, field) pairs rather than as running totals: a
    # field can be marked BOTH by its value ("withheld") and by the component's
    # withheld-fields list, and reporting that one field as two would make the
    # diagnostic disagree with the document it is describing.
    unknown_pairs = set()
    withheld_pairs = set()
    for index, comp in enumerate(s.components):
        key = comp.ref or f"{comp.name}#{index}"
        for field_name, value in (
            ("version", comp.version),
            ("producer", comp.producer),
            ("license", comp.licenses[0] if comp.licenses else ""),
        ):
            state = _marker_state(value)
            if state == "ambiguous":
                ambiguous.append(f"{comp.name}.{field_name}={value!r}")
            elif state == "unknown":
                unknown_pairs.add((key, field_name))
            elif state == "withheld":
                withheld_pairs.add((key, field_name))
        unknown_pairs.update((key, name) for name in comp.unknown_fields)
        withheld_pairs.update((key, name) for name in comp.withheld_fields)

    explicit_unknown = len(unknown_pairs)
    explicit_withheld = len(withheld_pairs)

    if ambiguous:
        return (
            STATUS_GAP,
            f"{len(ambiguous)} field values conflate unknown-to-author with withheld-by-author "
            f"(e.g. {ambiguous[0]}). The 2026 revision makes these distinct states; a single "
            f"placeholder cannot express either. Use {sorted(UNKNOWN_MARKERS)} and "
            f"{sorted(WITHHELD_MARKERS)}.",
        )
    if explicit_withheld and not s.unknown_contact:
        return (
            STATUS_PARTIAL,
            f"{explicit_withheld} fields are marked withheld, but the document names no process "
            "for a recipient to ask about the redacted information. The standard requires the "
            f"author to have one. Declare it in {PROP_UNKNOWN_CONTACT}.",
        )
    if not s.unknown_contact:
        return (
            STATUS_PARTIAL,
            "No ambiguous placeholders remain, but the document declares no process for "
            "recipients to query redacted security-related information, which the element "
            f"requires independently of whether anything is currently withheld. Declare it in "
            f"{PROP_UNKNOWN_CONTACT}.",
        )
    return (
        STATUS_MET,
        f"Unknown and withheld are distinctly marked ({explicit_unknown} unknown, "
        f"{explicit_withheld} withheld) and a query process is declared "
        f"({s.unknown_contact}).",
    )


def _score_frequency(s):
    if not s.timestamp:
        return STATUS_GAP, "Undated document: it cannot be tied to a build or a release."
    target_state = _marker_state(s.target_version)
    if target_state == "present" and s.target_version not in {"0.0.0", "0.0", "0"}:
        return (
            STATUS_MET,
            f"The document is bound to a specific version of the target "
            f"({s.target_name} {s.target_version}), so every release can carry its own SBOM.",
        )
    return (
        STATUS_PARTIAL,
        f"The target component's version is {s.target_version or 'absent'!r}, so this document "
        "is not bound to a particular build or release and successive SBOMs are "
        "indistinguishable. Every software version or update needs its own associated SBOM; a "
        "30-day staleness threshold is materially weaker than per-release.",
    )


def _score_machine_processable(s):
    if s.format_name not in {"CycloneDX", "SPDX"}:
        return (
            STATUS_GAP,
            f"Format {s.format_name!r} is not one the standard names. SPDX and CycloneDX are "
            "the two widely used machine-processable formats; SWID was removed in 2026.",
        )
    current = (
        s.format_version in CYCLONEDX_CURRENT
        if s.format_name == "CycloneDX"
        else s.format_version in SPDX_CURRENT
    )
    if not current:
        return (
            STATUS_PARTIAL,
            f"{s.format_name} {s.format_version} parses machine-processably but is a superseded "
            "version of the format, and the standard says to avoid deprecated versions and to "
            "reassess supported formats regularly.",
        )
    return (
        STATUS_MET,
        f"{s.format_name} {s.format_version} is a current version of one of the two formats "
        "the standard names.",
    )


# ─────────────────────────────────────────────────────────────────────────
# The element table
#
# Order and membership are fixed by the published standard: 9 SBOM Metadata
# fields + 8 Component Data fields = 17 data fields, and 6 practices. Access
# Control was REMOVED in 2026 and folded into Distribution and Delivery, so
# the practice count here is 6 of the 7 rows the 2021 document had.
# ─────────────────────────────────────────────────────────────────────────

ELEMENTS = (
    # --- SBOM Metadata (9) ---
    ("sbom_author", CATEGORY_DATA_FIELD, "SBOM Author", _score_sbom_author),
    ("sbom_author_signature", CATEGORY_DATA_FIELD, "SBOM Author Signature", _score_author_signature),
    ("sbom_data_format_name", CATEGORY_DATA_FIELD, "SBOM Data Format Name", _score_data_format_name),
    ("sbom_data_format_version", CATEGORY_DATA_FIELD, "SBOM Data Format Version", _score_data_format_version),
    ("sbom_generation_context", CATEGORY_DATA_FIELD, "SBOM Generation Context", _score_generation_context),
    ("sbom_timestamp", CATEGORY_DATA_FIELD, "SBOM Timestamp", _score_timestamp),
    ("sbom_tool_name", CATEGORY_DATA_FIELD, "SBOM Tool Name", _score_tool_name),
    ("sbom_tool_version", CATEGORY_DATA_FIELD, "SBOM Tool Version", _score_tool_version),
    ("sbom_version", CATEGORY_DATA_FIELD, "SBOM Version", _score_sbom_version),
    # --- Component Data (8) ---
    ("component_producer", CATEGORY_DATA_FIELD, "Component Producer", _score_component_producer),
    ("component_dependency_relationship", CATEGORY_DATA_FIELD, "Component Dependency Relationship", _score_dependency_relationship),
    ("component_hash_value", CATEGORY_DATA_FIELD, "Component Hash Value", _score_component_hash_value),
    ("component_hash_algorithm", CATEGORY_DATA_FIELD, "Component Hash Algorithm", _score_component_hash_algorithm),
    ("component_identifiers", CATEGORY_DATA_FIELD, "Component Identifiers", _score_component_identifiers),
    ("component_license", CATEGORY_DATA_FIELD, "Component License", _score_component_license),
    ("component_name", CATEGORY_DATA_FIELD, "Component Name", _score_component_name),
    ("component_version", CATEGORY_DATA_FIELD, "Component Version", _score_component_version),
    # --- Practices and Processes (6 applicable; Access Control removed) ---
    ("accommodation_of_updates", CATEGORY_PRACTICE, "Accommodation of Updates to SBOM Data", _score_accommodation_of_updates),
    ("coverage", CATEGORY_PRACTICE, "Coverage", _score_coverage),
    ("distribution_and_delivery", CATEGORY_PRACTICE, "Distribution and Delivery", _score_distribution_and_delivery),
    ("explicitly_identifying_unknown_information", CATEGORY_PRACTICE, "Explicitly Identifying Unknown Information", _score_explicit_unknowns),
    ("frequency", CATEGORY_PRACTICE, "Frequency", _score_frequency),
    ("machine_processable_data", CATEGORY_PRACTICE, "Machine-Processable Data", _score_machine_processable),
)

DATA_FIELD_COUNT = sum(1 for _, category, _, _ in ELEMENTS if category == CATEGORY_DATA_FIELD)
PRACTICE_COUNT = sum(1 for _, category, _, _ in ELEMENTS if category == CATEGORY_PRACTICE)

# The 2021 document listed 7 practices. Access Control was removed outright in
# 2026 and its considerations folded into Distribution and Delivery, so a
# "0 of 7" reading of a pre-2026 baseline and a "0 of 6" reading here are the
# same measurement. Reported so both readings reconcile without arithmetic.
PRACTICES_LISTED_2021 = 7
REMOVED_PRACTICES = ("Access Control",)


# ─────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────


def validate(document, document_path=None, document_bytes=None):
    """Score a parsed SBOM document against the 2026 Minimum Elements.

    Args:
        document: Parsed JSON dict, or an already-normalized ``NormalizedSbom``.
        document_path: Optional path, echoed into the report.
        document_bytes: Optional raw bytes, hashed into the report so an
            assessment can be tied to the exact document it graded.

    Returns:
        A JSON-serializable report dict.
    """
    normalized = document if isinstance(document, NormalizedSbom) else read_document(document)

    elements = []
    tally = {
        CATEGORY_DATA_FIELD: {STATUS_MET: 0, STATUS_PARTIAL: 0, STATUS_GAP: 0},
        CATEGORY_PRACTICE: {STATUS_MET: 0, STATUS_PARTIAL: 0, STATUS_GAP: 0},
    }
    weighted = 0.0

    for element_id, category, title, scorer in ELEMENTS:
        status, rationale = scorer(normalized)
        elements.append(
            {
                "id": element_id,
                "category": category,
                "title": title,
                "status": status,
                "rationale": rationale,
            }
        )
        tally[category][status] += 1
        weighted += STATUS_WEIGHT[status]

    data = tally[CATEGORY_DATA_FIELD]
    practices = tally[CATEGORY_PRACTICE]
    total_elements = DATA_FIELD_COUNT + PRACTICE_COUNT

    report = {
        "standard": STANDARD_NAME,
        "standard_version": STANDARD_VERSION,
        "standard_date": STANDARD_DATE,
        "standard_publisher": STANDARD_PUBLISHER,
        "validator_version": VALIDATOR_VERSION,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "document": {
            "path": str(document_path) if document_path else None,
            "sha256": _sha256_bytes(document_bytes) if document_bytes else None,
            "format_name": normalized.format_name,
            "format_version": normalized.format_version,
            "component_count": len(normalized.components),
        },
        "elements": elements,
        # Keyed projection of the same verdicts. The list above is canonical —
        # it preserves the standard's own ordering and the data-field/practice
        # split — but a consumer that only wants "what did element X score"
        # should not have to scan it. tools/compliance/sbom_conformance_gate.py
        # (sbx-gov-01) looks up by name and treats a non-dict as "no per-element
        # data available", so without this its delegation to this module would
        # silently never fire.
        "elements_by_id": {element["id"]: element for element in elements},
        # Aggregate over ALL 23 elements. sbx-gov-01 prefers this to counting
        # data fields itself, on the grounds that this module scores the six
        # practices too and owns the definition of what counts. It is right.
        "elements_met": data[STATUS_MET] + practices[STATUS_MET],
        "elements_total": total_elements,
        "data_fields": {
            "met": data[STATUS_MET],
            "partial": data[STATUS_PARTIAL],
            "gap": data[STATUS_GAP],
            "total": DATA_FIELD_COUNT,
        },
        "practices": {
            "met": practices[STATUS_MET],
            "partial": practices[STATUS_PARTIAL],
            "gap": practices[STATUS_GAP],
            "total": PRACTICE_COUNT,
            "listed_in_2021": PRACTICES_LISTED_2021,
            "removed_in_2026": list(REMOVED_PRACTICES),
        },
        "score": {
            "data_fields": f"{data[STATUS_MET]}/{DATA_FIELD_COUNT}",
            "practices": f"{practices[STATUS_MET]}/{PRACTICE_COUNT}",
            "weighted_pct": _pct(weighted, total_elements),
            "fully_met_pct": _pct(data[STATUS_MET] + practices[STATUS_MET], total_elements),
        },
        "conformant": data[STATUS_MET] == DATA_FIELD_COUNT and practices[STATUS_MET] == PRACTICE_COUNT,
        "classification": CLASSIFICATION,
    }
    return report


def validate_file(path):
    """Load, normalize and score an SBOM file."""
    sbom_path = Path(path)
    raw_bytes = sbom_path.read_bytes() if sbom_path.is_file() else None
    _raw, normalized = load_document(sbom_path)
    return validate(normalized, document_path=sbom_path, document_bytes=raw_bytes)


#: Entry point ``tools/compliance/sbom_conformance_gate.py`` (sbx-gov-01)
#: imports by name. That module was written against this one before either had
#: landed, and it delegates "the moment this module is importable" — so the
#: name it reaches for is part of this module's contract, not an accident.
#: Kept as an alias rather than a rename because ``validate_file`` is the name
#: this module's own CLI, tests and docs use.
validate_sbom = validate_file


# ─────────────────────────────────────────────────────────────────────────
# Optional persistence — sbx-gov-01 needs history to detect a regression
# ─────────────────────────────────────────────────────────────────────────


def record_assessment(report, project_id=None, sbom_record_id=None, db_path=None):
    """Append one assessment row to ``sbom_conformance_assessments``.

    Append-only: a re-score inserts a new row rather than updating the old
    one, because the old row is the basis of a past acceptance decision.

    The import is deferred so that importing this module — which the
    assessors in ``sbx-fmt-02`` do on every run — never requires a database.
    """
    from tools.db.storage import get_connection

    conn = get_connection(str(db_path)) if db_path else get_connection()
    try:
        document = report["document"]
        conn.execute(
            """INSERT INTO sbom_conformance_assessments
               (id, sbom_record_id, project_id, document_path, document_sha256,
                format_name, format_version, component_count,
                data_fields_met, data_fields_partial, data_fields_gap,
                practices_met, practices_partial, practices_gap,
                weighted_score, conformant, elements_json,
                validator_version, standard_version)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s)""",
            (
                str(uuid.uuid4()),
                sbom_record_id,
                project_id,
                document.get("path"),
                document.get("sha256") or "",
                document.get("format_name"),
                document.get("format_version"),
                document.get("component_count", 0),
                report["data_fields"]["met"],
                report["data_fields"]["partial"],
                report["data_fields"]["gap"],
                report["practices"]["met"],
                report["practices"]["partial"],
                report["practices"]["gap"],
                report["score"]["weighted_pct"],
                1 if report["conformant"] else 0,
                json.dumps(report["elements"]),
                report["validator_version"],
                report["standard_version"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return True


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

_STATUS_LABEL = {STATUS_MET: "MET    ", STATUS_PARTIAL: "PARTIAL", STATUS_GAP: "GAP    "}


def _print_human(report):
    document = report["document"]
    print(f"{CLASSIFICATION}\n")
    print(f"SBOM conformance — {report['standard']} (v{report['standard_version']})")
    print(f"  Document: {document['path'] or '<stdin>'}")
    print(f"  Format:   {document['format_name']} {document['format_version']}")
    print(f"  Components: {document['component_count']}\n")

    for category, heading in (
        (CATEGORY_DATA_FIELD, "DATA FIELDS"),
        (CATEGORY_PRACTICE, "PRACTICES AND PROCESSES"),
    ):
        print(heading)
        for element in report["elements"]:
            if element["category"] != category:
                continue
            print(f"  [{_STATUS_LABEL[element['status']]}] {element['title']}")
            print(f"            {element['rationale']}")
        print()

    data = report["data_fields"]
    practices = report["practices"]
    print(
        f"Data fields: {data['met']} met, {data['partial']} partial, {data['gap']} gap "
        f"(of {data['total']})"
    )
    print(
        f"Practices:   {practices['met']} met, {practices['partial']} partial, "
        f"{practices['gap']} gap (of {practices['total']} applicable; "
        f"{practices['listed_in_2021']} listed pre-2026, "
        f"{', '.join(practices['removed_in_2026'])} removed)"
    )
    print(f"Weighted score: {report['score']['weighted_pct']}%")
    print(f"Conformant: {'YES' if report['conformant'] else 'NO'}")
    print(f"\n{CLASSIFICATION}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Score a CycloneDX or SPDX SBOM against the 2026 Minimum Elements "
            "(CISA et al., 2026-07-29, v2.1). Works on third-party SBOMs."
        )
    )
    parser.add_argument("--sbom", "--file", dest="sbom", required=True, help="Path to the SBOM document")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Exit non-zero if the weighted score is below this percentage",
    )
    parser.add_argument(
        "--require-conformant",
        action="store_true",
        help="Exit non-zero unless every data field and practice is fully met",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Append the assessment to sbom_conformance_assessments (append-only)",
    )
    parser.add_argument("--project-id", dest="project_id", default=None, help="Project id for --record")
    parser.add_argument(
        "--sbom-record-id",
        dest="sbom_record_id",
        type=int,
        default=None,
        help="sbom_records.id this document came from, for --record",
    )
    parser.add_argument("--db", dest="db_path", default=None, help="Database path override")
    args = parser.parse_args(argv)

    try:
        report = validate_file(args.sbom)
    except (FileNotFoundError, UnsupportedFormatError) as exc:
        if args.json_output:
            print(json.dumps({"error": str(exc), "conformant": False}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.record:
        try:
            record_assessment(
                report,
                project_id=args.project_id,
                sbom_record_id=args.sbom_record_id,
                db_path=args.db_path,
            )
            report["recorded"] = True
        except Exception as exc:  # noqa: BLE001 — recording must not mask the score
            report["recorded"] = False
            report["record_error"] = str(exc)

    if args.json_output:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)

    if args.require_conformant and not report["conformant"]:
        return 1
    if args.min_score is not None and report["score"]["weighted_pct"] < args.min_score:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
