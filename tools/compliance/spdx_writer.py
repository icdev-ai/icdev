#!/usr/bin/env python3
# CUI // SP-CTI
# CANONICAL: the only SPDX producer in the tree. Authored in both
# tools/compliance/ and icdev/tools/compliance/ -- keep the two byte-identical.
"""Emit an SBOM as SPDX 2.3 JSON (sbx-fmt-01).

The 2026 Minimum Elements name **SPDX (ISO/IEC 5962:2021)** and **CycloneDX
(ECMA-424)** as the two data formats widely used to generate and consume SBOMs,
and define minimum automation support as supporting all such formats. ICDEV
emitted CycloneDX only, so half of that element was missing.

**The SPDX document is derived from the CycloneDX document, not built beside
it.** That is the whole design. The acceptance criterion for this task is that
the same project scores identically in both formats, and the only way to keep
that true as the remaining `sbx` element tasks land is for there to be one
producer of the elements and one translation of them. A second independent
builder would drift the first time someone added a field to one of them.

**Version.** SPDX 2.3, serialized as JSON. ISO/IEC 5962:2021 standardizes SPDX
2.2.1; 2.3 is its backward-compatible successor and is what current tooling
reads. SPDX 3.0 is deliberately not used yet -- the standard asks for widely
used formats, and 3.0's JSON-LD serialization is not yet what consumers ingest.

**What travels natively and what travels in an annotation.** Everything SPDX
2.3 models directly is written into the native field: name, `versionInfo`,
`originator` (Component Producer), `licenseDeclared`, `checksums`,
`externalRefs` (purl, CPE) and `relationships`. ICDEV's `icdev:*` properties
have no native home -- SPDX 2.3 has no extension point equivalent to CycloneDX
`properties` -- so they travel losslessly in a single `annotations` entry per
element whose `comment` is a JSON object. Annotations are SPDX's own mechanism
for a statement made about an element by the SBOM author, which is exactly what
these are. `spdx_property_index()` reads them back, so a validator can score an
SPDX document with the same logic it scores a CycloneDX one.

**Relationships are translated, never invented.** Dependency edges come from the
CycloneDX `dependencies` array (sbx-cov-02). Where that array is absent the SPDX
document states no dependency relationships either, rather than synthesizing a
root-depends-on-everything graph that the CycloneDX document does not claim --
inventing edges on one side is precisely how the two formats would stop scoring
identically.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

#: Official SPDX 2.3 JSON schema, vendored so validation works air-gapped.
#: Source: https://github.com/spdx/spdx-spec/blob/v2.3/schemas/spdx-schema.json
SCHEMA_PATH = BASE_DIR / "context" / "compliance" / "schemas" / "spdx-2.3.schema.json"

SPDX_VERSION = "SPDX-2.3"

#: Mandated by the SPDX specification for the document's own metadata.
SPDX_DATA_LICENSE = "CC0-1.0"

DOCUMENT_SPDX_ID = "SPDXRef-DOCUMENT"

#: SPDX's explicit "the author makes no assertion" marker. It is *not* the same
#: statement as the 2026 element "unknown to the author" -- that distinction is
#: carried by the `icdev:` properties (sbx-prc-01), which travel intact.
NOASSERTION = "NOASSERTION"

#: Key under which the CycloneDX `properties` list is carried in an annotation.
ANNOTATION_PROPERTIES_KEY = "icdev:properties"

#: Key under which the CycloneDX `compositions` array (the Coverage element's
#: per-assembly complete/incomplete split) is carried on the document.
ANNOTATION_COMPOSITIONS_KEY = "icdev:compositions"

#: CycloneDX `dependencies[].dependsOn` is the only edge kind CycloneDX 1.4-1.7
#: expresses. SPDX names 45 relationship types; DEPENDS_ON is the one with the
#: matching definition. sbx-cov-02 owns the shared vocabulary; anything it emits
#: that is not a plain dependency edge maps through RELATIONSHIP_TYPES.
DEFAULT_RELATIONSHIP = "DEPENDS_ON"
RELATIONSHIP_TYPES = {
    "dependsOn": "DEPENDS_ON",
    "depends_on": "DEPENDS_ON",
    "contains": "CONTAINS",
    "devDependsOn": "DEV_DEPENDENCY_OF",
    "optionalDependsOn": "OPTIONAL_DEPENDENCY_OF",
    "providedDependsOn": "PROVIDED_DEPENDENCY_OF",
    "testDependsOn": "TEST_DEPENDENCY_OF",
}

#: CycloneDX component `type` -> SPDX `primaryPackagePurpose`. Anything without
#: a counterpart is OTHER rather than dropped.
PACKAGE_PURPOSES = {
    "application": "APPLICATION",
    "framework": "FRAMEWORK",
    "library": "LIBRARY",
    "container": "CONTAINER",
    "operating-system": "OPERATING_SYSTEM",
    "device": "DEVICE",
    "firmware": "FIRMWARE",
    "file": "FILE",
}

#: CycloneDX hash algorithm names -> the SPDX 2.3 `checksums[].algorithm` enum.
#: Both derive from the IANA Hash Function Textual Names the 2026 Component Hash
#: Algorithm element points at; they differ only in punctuation.
HASH_ALGORITHMS = {
    "MD5": "MD5",
    "SHA-1": "SHA1",
    "SHA-224": "SHA224",
    "SHA-256": "SHA256",
    "SHA-384": "SHA384",
    "SHA-512": "SHA512",
    "SHA3-256": "SHA3-256",
    "SHA3-384": "SHA3-384",
    "SHA3-512": "SHA3-512",
    "BLAKE2b-256": "BLAKE2b-256",
    "BLAKE2b-384": "BLAKE2b-384",
    "BLAKE2b-512": "BLAKE2b-512",
    "BLAKE3": "BLAKE3",
    "ADLER32": "ADLER32",
}

_ID_ILLEGAL = re.compile(r"[^A-Za-z0-9.\-]+")


# =====================================================================================
# Identifiers
# =====================================================================================


def _sanitize_id(raw):
    """SPDX identifiers admit letters, digits, ``.`` and ``-`` only."""
    cleaned = _ID_ILLEGAL.sub("-", str(raw or "")).strip("-")
    return cleaned or "unnamed"


def _spdx_id(bom_ref, taken):
    """Derive a unique ``SPDXRef-`` identifier from a CycloneDX ``bom-ref``."""
    candidate = f"SPDXRef-{_sanitize_id(bom_ref)}"
    if candidate not in taken:
        taken.add(candidate)
        return candidate
    suffix = 2
    while f"{candidate}-{suffix}" in taken:
        suffix += 1
    unique = f"{candidate}-{suffix}"
    taken.add(unique)
    return unique


# =====================================================================================
# Field translation
# =====================================================================================


def _entity_name(entity):
    """Read an organization name out of a CycloneDX organizational entity."""
    if isinstance(entity, dict):
        name = str(entity.get("name") or "").strip()
        return name or None
    if isinstance(entity, str):
        return entity.strip() or None
    return None


def _originator(cdx_component):
    """Component Producer -> SPDX ``originator``.

    SPDX distinguishes ``originator`` ("originally created the package") from
    ``supplier`` ("the immediate supplier"), and the 2026 standard replaced
    *Supplier Name* with **Component Producer** precisely because the supplier
    reading was ambiguous. So the producer is the originator, never the
    supplier. A component with no identifiable producer says NOASSERTION here
    and carries its machine-readable unknown reason in the properties.
    """
    # 1.6+ writes `manufacturer`; 1.4/1.5 have only `supplier` (see
    # component_producer.apply_producer_to_cyclonedx). Either is the producer.
    name = _entity_name(cdx_component.get("manufacturer")) or _entity_name(cdx_component.get("supplier"))
    return f"Organization: {name}" if name else NOASSERTION


def _external_refs(cdx_component):
    """purl and CPE -> SPDX ``externalRefs``."""
    refs = []
    purl = cdx_component.get("purl")
    if purl:
        refs.append(
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": purl,
            }
        )
    cpe = cdx_component.get("cpe")
    if cpe:
        refs.append(
            {
                "referenceCategory": "SECURITY",
                "referenceType": "cpe23Type",
                "referenceLocator": cpe,
            }
        )
    return refs


def _checksums(cdx_component):
    """CycloneDX ``hashes`` -> SPDX ``checksums`` (Component Hash Value/Algorithm)."""
    checksums = []
    for entry in cdx_component.get("hashes") or []:
        if not isinstance(entry, dict):
            continue
        algorithm = HASH_ALGORITHMS.get(str(entry.get("alg") or "").strip())
        content = str(entry.get("content") or "").strip()
        if algorithm and content:
            checksums.append({"algorithm": algorithm, "checksumValue": content.lower()})
    return checksums


def _declared_license(cdx_component):
    """CycloneDX ``licenses`` -> SPDX ``licenseDeclared`` (Component License).

    ``licenseConcluded`` stays NOASSERTION: ICDEV reports the license the
    component declares, it does not conclude one on the recipient's behalf.
    """
    entries = cdx_component.get("licenses") or []
    identifiers = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("expression"):
            identifiers.append(str(entry["expression"]).strip())
            continue
        license_obj = entry.get("license") or {}
        value = license_obj.get("id") or license_obj.get("name")
        if value:
            identifiers.append(str(value).strip())
    identifiers = [i for i in identifiers if i]
    if not identifiers:
        return NOASSERTION
    if len(identifiers) == 1:
        return identifiers[0]
    return " AND ".join(f"({i})" if " " in i else i for i in identifiers)


# =====================================================================================
# Annotations -- the carrier for ICDEV's `icdev:*` properties
# =====================================================================================


def _normalize_properties(properties):
    """Normalize a CycloneDX ``properties`` array to ``[{name, value}]``."""
    normalized = []
    for entry in properties or []:
        if not isinstance(entry, dict) or "name" not in entry:
            continue
        normalized.append({"name": str(entry["name"]), "value": str(entry.get("value", ""))})
    return normalized


def _annotation(payload, annotator, created):
    """One SPDX annotation carrying a JSON payload.

    ``annotationType`` OTHER is correct: this is neither a review nor a legal
    notice, it is machine-readable data about the element contributed by the
    SBOM author.
    """
    return {
        "annotationDate": created,
        "annotationType": "OTHER",
        "annotator": annotator,
        "comment": json.dumps(payload, sort_keys=True),
    }


def _read_annotation_payloads(element):
    """Every JSON-object annotation comment on an SPDX element."""
    payloads = []
    for annotation in (element or {}).get("annotations") or []:
        comment = (annotation or {}).get("comment")
        if not isinstance(comment, str):
            continue
        try:
            parsed = json.loads(comment)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


# =====================================================================================
# Conversion
# =====================================================================================


def _creators(cdx):
    """SPDX ``creationInfo.creators``.

    The tool vendor is **not** mapped to ``Organization:``. The 2026 standard is
    explicit that the entity operating the tool is the SBOM Author and the tool
    vendor is not, and an ``Organization:`` creator would state exactly the
    conflation the element was rewritten to remove. Where a real author is
    present (sbx-fld-01) it is added as the Organization creator.
    """
    creators = []
    for tool in (cdx.get("metadata") or {}).get("tools") or []:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        version = str(tool.get("version") or "").strip()
        creators.append(f"Tool: {name}-{version}" if version else f"Tool: {name}")

    for entry in _normalize_properties((cdx.get("metadata") or {}).get("properties")):
        if entry["name"] == "icdev:sbom:author" and entry["value"]:
            creators.append(f"Organization: {entry['value']}")

    return creators or [f"Tool: {NOASSERTION}"]


def _creation_comment(cdx):
    vendors = sorted(
        {
            str(tool.get("vendor")).strip()
            for tool in (cdx.get("metadata") or {}).get("tools") or []
            if isinstance(tool, dict) and str(tool.get("vendor") or "").strip()
        }
    )
    parts = [
        "Translated from the CycloneDX document of the same build so that both "
        "formats carry the identical SBOM 2026 minimum elements.",
    ]
    if vendors:
        parts.append(
            "Tool vendor: " + ", ".join(vendors) + " (recorded as the vendor of the "
            "generating tool, which the 2026 standard distinguishes from the SBOM Author)."
        )
    return " ".join(parts)


def _package(cdx_component, spdx_id, annotator, created):
    """One CycloneDX component -> one SPDX package."""
    package = {
        "SPDXID": spdx_id,
        "name": str(cdx_component.get("name") or "unnamed"),
        # We read package metadata, never the distribution artifact's origin,
        # so there is nothing honest to put here.
        "downloadLocation": NOASSERTION,
        # No file-level analysis was performed, which is what lets the package
        # omit packageVerificationCode.
        "filesAnalyzed": False,
        "licenseConcluded": NOASSERTION,
        "licenseDeclared": _declared_license(cdx_component),
        "copyrightText": NOASSERTION,
        "originator": _originator(cdx_component),
    }

    version = str(cdx_component.get("version") or "").strip()
    if version:
        package["versionInfo"] = version

    purpose = PACKAGE_PURPOSES.get(str(cdx_component.get("type") or "").strip())
    if purpose:
        package["primaryPackagePurpose"] = purpose

    external_refs = _external_refs(cdx_component)
    if external_refs:
        package["externalRefs"] = external_refs

    checksums = _checksums(cdx_component)
    if checksums:
        package["checksums"] = checksums

    properties = _normalize_properties(cdx_component.get("properties"))
    if properties:
        package["annotations"] = [
            _annotation({ANNOTATION_PROPERTIES_KEY: properties}, annotator, created)
        ]

    return package


def _relationships(cdx, ref_to_id, root_id):
    """CycloneDX ``dependencies`` -> SPDX ``relationships``.

    DESCRIBES is document structure, not a dependency claim, so it is always
    emitted. Everything else comes from an edge the CycloneDX document already
    asserts.
    """
    relationships = [
        {
            "spdxElementId": DOCUMENT_SPDX_ID,
            "relatedSpdxElement": root_id,
            "relationshipType": "DESCRIBES",
        }
    ]

    seen = {(DOCUMENT_SPDX_ID, root_id, "DESCRIBES")}
    for entry in cdx.get("dependencies") or []:
        if not isinstance(entry, dict):
            continue
        parent = ref_to_id.get(entry.get("ref"))
        if not parent:
            continue
        # CycloneDX 1.4-1.7 express only "dependsOn". `relationshipType` is the
        # hook for sbx-cov-02's richer vocabulary; absent it, an edge is a
        # dependency edge.
        kind = RELATIONSHIP_TYPES.get(entry.get("relationshipType"), DEFAULT_RELATIONSHIP)
        for child_ref in entry.get("dependsOn") or []:
            child = ref_to_id.get(child_ref)
            if not child or (parent, child, kind) in seen:
                continue
            seen.add((parent, child, kind))
            relationships.append(
                {
                    "spdxElementId": parent,
                    "relatedSpdxElement": child,
                    "relationshipType": kind,
                }
            )

    return relationships


def to_spdx(cyclonedx_document, document_name=None, namespace=None):
    """Translate a CycloneDX document into an SPDX 2.3 document.

    Args:
        cyclonedx_document: the CycloneDX JSON document, as a dict.
        document_name: override for the SPDX document name.
        namespace: override for ``documentNamespace``. Defaults to the
            CycloneDX ``serialNumber``, which is already a URN and already
            unique per document -- reusing it keeps the two serializations of
            one build identifiable as the same SBOM.

    Returns:
        dict: the SPDX document.
    """
    cdx = cyclonedx_document or {}
    metadata = cdx.get("metadata") or {}
    target = metadata.get("component") or {}

    created = str(metadata.get("timestamp") or "").strip() or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    creators = _creators(cdx)
    annotator = next((c for c in creators if c.startswith("Tool:")), f"Tool: {NOASSERTION}")

    taken = set()
    ref_to_id = {}

    root_ref = target.get("bom-ref") or "root"
    root_id = _spdx_id(root_ref, taken)
    ref_to_id[root_ref] = root_id
    packages = [_package(target, root_id, annotator, created)]

    for component in cdx.get("components") or []:
        if not isinstance(component, dict):
            continue
        ref = component.get("bom-ref")
        spdx_id = _spdx_id(ref or component.get("name") or "component", taken)
        if ref:
            ref_to_id[ref] = spdx_id
        packages.append(_package(component, spdx_id, annotator, created))

    document = {
        "spdxVersion": SPDX_VERSION,
        "dataLicense": SPDX_DATA_LICENSE,
        "SPDXID": DOCUMENT_SPDX_ID,
        "name": str(document_name or target.get("name") or "sbom"),
        "documentNamespace": str(namespace or cdx.get("serialNumber") or f"urn:uuid:{root_ref}"),
        "creationInfo": {
            "created": created,
            "creators": creators,
            "comment": _creation_comment(cdx),
        },
        "packages": packages,
        "documentDescribes": [root_id],
        "relationships": _relationships(cdx, ref_to_id, root_id),
    }

    # Document-level ICDEV properties, plus the Coverage element's per-assembly
    # aggregate. SPDX 2.3 has no `compositions` concept, and dropping it would
    # make the SPDX document score lower on Coverage than the CycloneDX one.
    payload = {}
    document_properties = _normalize_properties(metadata.get("properties"))
    if document_properties:
        payload[ANNOTATION_PROPERTIES_KEY] = document_properties
    if cdx.get("compositions"):
        payload[ANNOTATION_COMPOSITIONS_KEY] = cdx["compositions"]
    if payload:
        document["annotations"] = [_annotation(payload, annotator, created)]

    return document


# =====================================================================================
# Element parity -- the executable form of this task's acceptance criterion
# =====================================================================================


def cyclonedx_property_index(document):
    """``{spdx_id: [{name, value}, ...]}`` for a CycloneDX document.

    Keyed by the SPDX identifier each element *would* receive, so the two
    indexes are directly comparable.
    """
    cdx = document or {}
    metadata = cdx.get("metadata") or {}
    target = metadata.get("component") or {}

    index = {}
    document_properties = _normalize_properties(metadata.get("properties"))
    if document_properties:
        index[DOCUMENT_SPDX_ID] = document_properties

    taken = set()
    root_id = _spdx_id(target.get("bom-ref") or "root", taken)
    target_properties = _normalize_properties(target.get("properties"))
    if target_properties:
        index[root_id] = target_properties

    for component in cdx.get("components") or []:
        if not isinstance(component, dict):
            continue
        spdx_id = _spdx_id(component.get("bom-ref") or component.get("name") or "component", taken)
        properties = _normalize_properties(component.get("properties"))
        if properties:
            index[spdx_id] = properties

    return index


def spdx_property_index(document):
    """``{spdx_id: [{name, value}, ...]}`` read back out of an SPDX document."""
    spdx = document or {}
    index = {}

    for payload in _read_annotation_payloads(spdx):
        properties = payload.get(ANNOTATION_PROPERTIES_KEY)
        if properties:
            index.setdefault(DOCUMENT_SPDX_ID, []).extend(_normalize_properties(properties))

    for package in spdx.get("packages") or []:
        if not isinstance(package, dict):
            continue
        for payload in _read_annotation_payloads(package):
            properties = payload.get(ANNOTATION_PROPERTIES_KEY)
            if properties:
                index.setdefault(package.get("SPDXID"), []).extend(_normalize_properties(properties))

    return index


def _flatten(index):
    return {
        f"{element}::{entry['name']}={entry['value']}"
        for element, entries in (index or {}).items()
        for entry in entries
    }


def compare_element_coverage(cyclonedx_document, spdx_document):
    """Do the two serializations of one build carry the same elements?

    This is the acceptance criterion for sbx-fmt-01 in a form sbx-sig-02's
    conformance validator can call directly: if the two documents disagree on a
    single element statement they cannot score identically, whatever the
    scoring rules turn out to be.
    """
    cdx_flat = _flatten(cyclonedx_property_index(cyclonedx_document))
    spdx_flat = _flatten(spdx_property_index(spdx_document))
    missing_in_spdx = sorted(cdx_flat - spdx_flat)
    missing_in_cyclonedx = sorted(spdx_flat - cdx_flat)
    return {
        "parity": not missing_in_spdx and not missing_in_cyclonedx,
        "element_count": len(cdx_flat),
        "missing_in_spdx": missing_in_spdx,
        "missing_in_cyclonedx": missing_in_cyclonedx,
    }


# =====================================================================================
# Validation against the official schema
# =====================================================================================


#: Where the schema can live, in order. The checkout, the `icdev/` mirror and a
#: pip install (which lands the FORGE context layer under `icdev/data/`) each
#: resolve BASE_DIR differently, and a validator that cannot find its schema
#: fails every document.
def _schema_candidates():
    relative = Path("context") / "compliance" / "schemas" / "spdx-2.3.schema.json"
    return [
        SCHEMA_PATH,
        BASE_DIR / "data" / relative,
        Path(__file__).resolve().parents[3] / relative,
    ]


def load_schema(path=None):
    """Load the vendored official SPDX 2.3 JSON schema."""
    candidates = [Path(path)] if path else _schema_candidates()
    schema_path = next((c for c in candidates if c.exists()), candidates[0])
    with open(schema_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_spdx(document, schema_path=None):
    """Validate a document against the official SPDX 2.3 schema.

    Returns ``{"valid": bool, "errors": [...]}``. A missing ``jsonschema`` is
    reported as an error rather than a pass -- a validator that silently
    approves everything is worse than no validator.
    """
    try:
        import jsonschema
    except ImportError:
        return {"valid": False, "errors": ["jsonschema is not installed; cannot validate"]}

    try:
        schema = load_schema(schema_path)
    except OSError as exc:
        return {"valid": False, "errors": [f"SPDX schema unavailable: {exc}"]}

    validator = jsonschema.Draft7Validator(schema)
    errors = [
        f"{'/'.join(str(p) for p in error.absolute_path) or '<document>'}: {error.message}"
        for error in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    ]
    return {"valid": not errors, "errors": errors}


def write_spdx(cyclonedx_document, output_path, document_name=None, namespace=None):
    """Translate and write an SPDX document. Returns the path written."""
    spdx = to_spdx(cyclonedx_document, document_name=document_name, namespace=namespace)
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as handle:
        json.dump(spdx, handle, indent=2)
    return str(out_file)


# =====================================================================================
# CLI
# =====================================================================================


def _load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main():
    parser = argparse.ArgumentParser(description="Translate CycloneDX to SPDX 2.3 and validate it")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--convert", metavar="CYCLONEDX_JSON", help="CycloneDX document to translate")
    group.add_argument("--validate", metavar="SPDX_JSON", help="SPDX document to validate")
    group.add_argument(
        "--compare",
        nargs=2,
        metavar=("CYCLONEDX_JSON", "SPDX_JSON"),
        help="Report element parity between the two serializations of one build",
    )
    parser.add_argument("--output", help="Output path for --convert (default: stdout)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    try:
        if args.convert:
            spdx = to_spdx(_load(args.convert))
            result = validate_spdx(spdx)
            if args.output:
                out_file = Path(args.output)
                out_file.parent.mkdir(parents=True, exist_ok=True)
                with open(out_file, "w", encoding="utf-8") as handle:
                    json.dump(spdx, handle, indent=2)
                payload = {"output": str(out_file), **result}
            else:
                payload = {"document": spdx, **result}
            print(json.dumps(payload, indent=2) if args.json_output else json.dumps(spdx, indent=2))
            return 0 if result["valid"] else 1

        if args.validate:
            result = validate_spdx(_load(args.validate))
            if args.json_output:
                print(json.dumps(result, indent=2))
            else:
                print("VALID" if result["valid"] else "INVALID")
                for error in result["errors"]:
                    print(f"  {error}")
            return 0 if result["valid"] else 1

        result = compare_element_coverage(_load(args.compare[0]), _load(args.compare[1]))
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print(f"parity: {result['parity']} ({result['element_count']} element statements)")
            for missing in result["missing_in_spdx"]:
                print(f"  missing in SPDX: {missing}")
            for missing in result["missing_in_cyclonedx"]:
                print(f"  missing in CycloneDX: {missing}")
        return 0 if result["parity"] else 1

    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
