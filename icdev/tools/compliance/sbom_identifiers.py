#!/usr/bin/env python3
# CUI // SP-CTI
"""Component Identifiers for the 2026 SBOM Minimum Elements (sbx-fld-05).

The 2026 standard renamed the 2021 "Other Unique Identifiers" element to
**Component Identifiers** and changed its obligation: an author must supply at
least one common software identifier, and where MULTIPLE identifiers exist the
author should include ALL of them. The named types are CPE, PURL (ECMA-427),
UUIDs (RFC 9562), organization-specific identifiers, commit hashes, and the
intrinsic identifiers OmniBOR and SWHID (ISO/IEC 18670:2025).

`sbom_generator.py` emitted `purl` only. This module derives the full set from
the coordinates a manifest parser already produces, emits them into a CycloneDX
component in the shape each spec version actually supports, parses them back,
and validates their syntax. It is a pure library plus a small `--validate` CLI;
it never executes, deserializes or shells out.

Design notes
------------
* **CPE is the operationally important addition** — it is the NVD lookup key.
  What is derived here is a CPE 2.3 *match string*, not an assertion that the
  string exists in the NVD dictionary. Attributes that cannot be derived with
  confidence are left as the ANY wildcard `*`, which is legal in a match string
  and widens rather than narrows a CVE join. Guessing a vendor would silently
  narrow it, and a missed CVE is worse than an extra candidate.
* **Escaping** follows real NVD data rather than a maximal reading of NIST
  IR 7695: everything outside ``[A-Za-z0-9._-]`` is backslash-escaped, and
  ``.`` / ``-`` are left bare because that is how NVD writes them
  (``cpe:2.3:a:node-red:node-red:1.0.0:...``). Escaping them would break the
  string match this element exists to enable.
* **Nothing is fabricated.** OmniBOR and SWHID are intrinsic identifiers
  computed over content, so they are emitted only when a caller supplies the
  underlying artifact digest or a full git revision. A 12-hex Go pseudo-version
  suffix is a real commit hash but an abbreviated one, so it yields a
  `commit_hash` and deliberately not a SWHID, which requires the full 40 hex.
* **Every component gets at least one identifier**: the organization-specific
  identifier is derived from coordinates alone and is therefore always present,
  including for a component whose version is unknown and whose purl is absent.
"""

import argparse
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import unquote

# Identifier types named by the 2026 standard, in emission order. The order is
# fixed so that derive_identifiers() output is deterministic and two SBOMs of
# the same input diff cleanly.
IDENTIFIER_TYPES = (
    "purl",
    "cpe",
    "swhid",
    "omnibor",
    "commit_hash",
    "uuid",
    "organization",
)

# RFC 9562 v5 namespace for ICDEV component UUIDs. Deterministic: the same
# coordinates always yield the same UUID, in this checkout and in any other.
ICDEV_SBOM_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "sbom.icdev.ai")

# Prefix for the organization-specific identifier.
ORG_IDENTIFIER_PREFIX = "icdev:component:"

# CycloneDX gained first-class `swhid` and `omniborId` arrays in 1.6. Below
# that they go into properties rather than being dropped.
INTRINSIC_ID_MIN_SPEC = (1, 6)

# Property name prefix for identifiers with no native CycloneDX field.
PROPERTY_PREFIX = "icdev:identifier:"

# Version strings the manifest parsers emit when a version is unresolved.
# sbx-fld-06 replaces these with machine-interpretable unknown markers; until
# then they must not leak into a CPE version field as a literal.
UNRESOLVED_VERSIONS = frozenset({"", "unspecified", "managed", "unknown", "*"})

# purl type -> CPE part. Everything ICDEV parses today is application-layer;
# the map exists so an os/firmware component from a future ingester is correct.
_COMPONENT_TYPE_TO_CPE_PART = {
    "library": "a",
    "framework": "a",
    "application": "a",
    "service": "a",
    "container": "a",
    "os": "o",
    "operating-system": "o",
    "firmware": "h",
    "device": "h",
}

# Go pseudo-version: vX.Y.Z-yyyymmddhhmmss-<12 hex abbreviated commit>.
_GO_PSEUDO_VERSION = re.compile(r"-\d{14}-([0-9a-f]{12})$")

_CPE_UNRESERVED = re.compile(r"[A-Za-z0-9._-]")
_HEX_ONLY = re.compile(r"^[0-9a-f]+$")

_PURL_RE = re.compile(r"^pkg:[A-Za-z][A-Za-z0-9+.\-]*/[^@#?\s]+(@[^?#\s]+)?")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_SWHID_RE = re.compile(r"^swh:1:(cnt|dir|rev|rel|snp):[0-9a-f]{40}$")
_OMNIBOR_RE = re.compile(r"^gitoid:blob:sha(1:[0-9a-f]{40}|256:[0-9a-f]{64})$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")


# ---------------------------------------------------------------------------
# CPE 2.3 formatted-string helpers
# ---------------------------------------------------------------------------


def cpe_escape(value):
    """Escape a CPE 2.3 attribute value for the formatted-string binding.

    Characters outside ``[A-Za-z0-9._-]`` are backslash-escaped. See the module
    docstring for why ``.`` and ``-`` are left bare.
    """
    out = []
    for ch in str(value):
        out.append(ch if _CPE_UNRESERVED.match(ch) else "\\" + ch)
    return "".join(out)


def split_cpe(value):
    """Split a CPE 2.3 formatted string on unescaped colons.

    A bare ``str.split(':')`` would tear an escaped ``\\:`` inside an attribute
    in half and report a well-formed CPE as malformed.
    """
    parts, buf, escaped = [], [], False
    for ch in str(value):
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\":
            buf.append(ch)
            escaped = True
            continue
        if ch == ":":
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def _cpe_vendor_product(component):
    """Derive (vendor, product) for a component, using ``*`` where unknown.

    Returns unescaped values; the caller escapes.
    """
    name = str(component.get("name") or "").strip()
    group = str(component.get("group") or "").strip()
    purl = str(component.get("purl") or "")
    ecosystem = purl[4:].split("/", 1)[0].lower() if purl.startswith("pkg:") else ""

    if not name:
        return "*", "*"

    lowered = name.lower()

    if ecosystem == "maven" and group:
        # Reverse-DNS group ids carry the vendor in the second label:
        # org.apache.logging.log4j -> apache. A non-DNS group id (a bare
        # "mygroup") has its last segment as the closest thing to a vendor.
        segments = [s for s in group.lower().split(".") if s]
        if len(segments) >= 2 and segments[0] in ("com", "org", "net", "io", "edu", "gov", "dev"):
            return segments[1], lowered
        if segments:
            return segments[-1], lowered
        return "*", lowered

    if ecosystem == "golang":
        # Module path github.com/spf13/cobra -> vendor spf13, product cobra.
        module = str(component.get("name") or "")
        segments = [s for s in module.lower().split("/") if s]
        if len(segments) >= 3 and "." in segments[0]:
            return segments[1], segments[-1]
        return "*", segments[-1] if segments else lowered

    if ecosystem == "npm" and group.startswith("@"):
        # Scoped package @babel/core -> vendor babel, product core.
        return group[1:].lower(), lowered

    if ecosystem == "nuget" and "." in lowered:
        # Newtonsoft.Json -> vendor newtonsoft, product json — the shape NVD
        # uses for dotted .NET package ids.
        head, _, tail = lowered.partition(".")
        return head, tail

    return "*", lowered


def derive_cpe(component):
    """Derive a CPE 2.3 match string, or None when no product name is known."""
    vendor, product = _cpe_vendor_product(component)
    if product == "*":
        return None

    part = _COMPONENT_TYPE_TO_CPE_PART.get(str(component.get("type") or "library").lower(), "a")

    version = str(component.get("version") or "").strip()
    version_field = "*" if version.lower() in UNRESOLVED_VERSIONS else cpe_escape(version)

    vendor_field = "*" if vendor == "*" else cpe_escape(vendor)

    fields = [
        "cpe",
        "2.3",
        part,
        vendor_field,
        cpe_escape(product),
        version_field,
        "*",  # update
        "*",  # edition
        "*",  # language
        "*",  # sw_edition
        "*",  # target_sw — left ANY on purpose; pinning it drops NVD matches
        "*",  # target_hw
        "*",  # other
    ]
    return ":".join(fields)


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def component_key(component):
    """Stable coordinate key for a component: ``group/name@version``."""
    return f"{component.get('group', '') or ''}/{component.get('name', '')}@{component.get('version', '')}"


def component_id(component):
    """Deterministic 16-hex component id — the ``sbom_components.id`` primary
    key and the CycloneDX ``bom-ref``, derived from coordinates alone."""
    return hashlib.sha256(component_key(component).encode("utf-8")).hexdigest()[:16]


def derive_commit_hash(component):
    """Return a commit hash when one is recoverable from the coordinates.

    Honours an explicit ``commit_hash`` from the parser first; otherwise reads
    the abbreviated hash out of a Go pseudo-version.
    """
    explicit = str(component.get("commit_hash") or "").strip().lower()
    if explicit and _COMMIT_RE.match(explicit):
        return explicit

    match = _GO_PSEUDO_VERSION.search(str(component.get("version") or "").strip())
    if match:
        return match.group(1)
    return None


def derive_identifiers(component):
    """Derive every identifier derivable for one component.

    Args:
        component: a parser component dict — ``name``, ``version``, ``purl``,
            ``group``, ``type``, and optionally ``commit_hash``, ``gitoid``,
            ``swhid`` and a pre-existing ``identifiers`` list.

    Returns:
        A list of ``{"type": ..., "value": ...}`` dicts, deduplicated and in
        ``IDENTIFIER_TYPES`` order. Never empty: the organization-specific
        identifier is always derivable.
    """
    found = []

    def add(id_type, value):
        if value:
            found.append({"type": id_type, "value": str(value)})

    # Anything a parser already attached wins a place in the set.
    for existing in component.get("identifiers") or []:
        if isinstance(existing, dict):
            add(existing.get("type"), existing.get("value"))

    add("purl", component.get("purl"))
    add("cpe", derive_cpe(component))

    commit = derive_commit_hash(component)
    add("commit_hash", commit)

    # SWHID is an intrinsic identifier over a git revision; only a full 40-hex
    # sha1 is one. An abbreviated Go pseudo-version hash is not.
    swhid = str(component.get("swhid") or "").strip()
    if swhid:
        add("swhid", swhid)
    elif commit and len(commit) == 40 and _HEX_ONLY.match(commit):
        add("swhid", f"swh:1:rev:{commit}")

    # OmniBOR is computed over artifact bytes, which this generator does not
    # read (sbx-fld-03 / sbx-cov-01). Pass through only what a caller supplies.
    gitoid = str(component.get("gitoid") or "").strip()
    if gitoid:
        add("omnibor", gitoid if gitoid.startswith("gitoid:") else f"gitoid:blob:sha256:{gitoid}")

    add("uuid", str(uuid.uuid5(ICDEV_SBOM_NAMESPACE, component_key(component))))
    add("organization", f"{ORG_IDENTIFIER_PREFIX}{component_id(component)}")

    return _dedupe_sorted(found)


def _dedupe_sorted(identifiers):
    """Deduplicate on (type, value) and order by IDENTIFIER_TYPES then value."""
    order = {name: index for index, name in enumerate(IDENTIFIER_TYPES)}
    seen = set()
    unique = []
    for entry in identifiers:
        key = (entry.get("type"), entry.get("value"))
        if key in seen:
            continue
        seen.add(key)
        unique.append({"type": entry["type"], "value": entry["value"]})
    unique.sort(key=lambda e: (order.get(e["type"], len(order)), e["value"]))
    return unique


# ---------------------------------------------------------------------------
# CycloneDX emission and parsing
# ---------------------------------------------------------------------------


def _spec_tuple(spec_version):
    try:
        major, _, minor = str(spec_version or "1.4").partition(".")
        return (int(major), int(minor or 0))
    except ValueError:
        return (1, 4)


def apply_identifiers_to_cyclonedx(cdx_component, identifiers, spec_version="1.4"):
    """Write every identifier into a CycloneDX component dict.

    Uses the native field where the active spec version has one — ``purl``,
    ``cpe``, and (1.6+) the ``swhid`` / ``omniborId`` arrays — and properties
    for the rest, so that no identifier is dropped on any supported version.

    Mutates and returns ``cdx_component``.
    """
    supports_intrinsic = _spec_tuple(spec_version) >= INTRINSIC_ID_MIN_SPEC

    by_type = {}
    for entry in identifiers:
        by_type.setdefault(entry["type"], []).append(entry["value"])

    properties = []

    for id_type in IDENTIFIER_TYPES:
        values = by_type.get(id_type)
        if not values:
            continue

        if id_type == "purl":
            cdx_component["purl"] = values[0]
            extras = values[1:]
        elif id_type == "cpe":
            # CycloneDX `cpe` is a single string on every version through 1.7.
            cdx_component["cpe"] = values[0]
            extras = values[1:]
        elif id_type in ("swhid", "omnibor") and supports_intrinsic:
            cdx_component["swhid" if id_type == "swhid" else "omniborId"] = list(values)
            extras = []
        else:
            extras = values

        for value in extras:
            properties.append({"name": f"{PROPERTY_PREFIX}{id_type}", "value": value})

    # Any type the caller passed that is not in IDENTIFIER_TYPES still ships.
    for id_type, values in by_type.items():
        if id_type in IDENTIFIER_TYPES:
            continue
        for value in values:
            properties.append({"name": f"{PROPERTY_PREFIX}{id_type}", "value": value})

    if properties:
        cdx_component.setdefault("properties", []).extend(properties)

    return cdx_component


def parse_identifiers_from_cyclonedx(cdx_component):
    """Read the identifier set back out of a CycloneDX component dict.

    The inverse of :func:`apply_identifiers_to_cyclonedx` for every spec
    version, so an SBOM ICDEV wrote round-trips to the same (type, value) set
    it was built from.
    """
    found = []

    if cdx_component.get("purl"):
        found.append({"type": "purl", "value": cdx_component["purl"]})
    if cdx_component.get("cpe"):
        found.append({"type": "cpe", "value": cdx_component["cpe"]})

    for field, id_type in (("swhid", "swhid"), ("omniborId", "omnibor")):
        values = cdx_component.get(field) or []
        if isinstance(values, str):
            values = [values]
        for value in values:
            found.append({"type": id_type, "value": value})

    for prop in cdx_component.get("properties") or []:
        name = str(prop.get("name") or "")
        if name.startswith(PROPERTY_PREFIX):
            found.append({"type": name[len(PROPERTY_PREFIX):], "value": prop.get("value")})

    return _dedupe_sorted([entry for entry in found if entry.get("value")])


# ---------------------------------------------------------------------------
# sbom_components.identifiers_json round-trip
# ---------------------------------------------------------------------------


def identifiers_to_json(identifiers):
    """Serialize an identifier list for ``sbom_components.identifiers_json``.

    The column defaults to ``'{}'``, so the envelope is an object with an
    ``identifiers`` array rather than a bare array — the default stays valid
    and parses to an empty set.
    """
    return json.dumps({"identifiers": _dedupe_sorted(identifiers)}, sort_keys=True)


def identifiers_from_json(raw):
    """Parse ``sbom_components.identifiers_json`` back to an identifier list.

    Tolerates the ``'{}'`` column default, NULL, and a bare JSON array.
    """
    if not raw:
        return []
    if isinstance(raw, (list, dict)):
        payload = raw
    else:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return []

    if isinstance(payload, dict):
        entries = payload.get("identifiers") or []
    elif isinstance(payload, list):
        entries = payload
    else:
        return []

    return _dedupe_sorted(
        [e for e in entries if isinstance(e, dict) and e.get("type") and e.get("value")]
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_identifier(identifier):
    """Validate one identifier. Returns an error string, or None if valid."""
    id_type = identifier.get("type")
    value = str(identifier.get("value") or "")

    if not id_type:
        return "identifier has no type"
    if not value:
        return f"{id_type}: empty value"

    if id_type == "purl":
        if not _PURL_RE.match(value):
            return f"purl: not a valid ECMA-427 package URL: {value!r}"
    elif id_type == "cpe":
        fields = split_cpe(value)
        if len(fields) != 13:
            return f"cpe: expected 13 colon-separated fields, got {len(fields)}: {value!r}"
        if fields[0] != "cpe" or fields[1] != "2.3":
            return f"cpe: not a CPE 2.3 formatted string: {value!r}"
        if fields[2] not in ("a", "o", "h", "*", "-"):
            return f"cpe: invalid part {fields[2]!r}: {value!r}"
        if any(not field for field in fields[3:]):
            return f"cpe: empty attribute field: {value!r}"
    elif id_type == "uuid":
        if not _UUID_RE.match(value):
            return f"uuid: not an RFC 9562 UUID: {value!r}"
    elif id_type == "swhid":
        if not _SWHID_RE.match(value):
            return f"swhid: not an ISO/IEC 18670 SWHID core identifier: {value!r}"
    elif id_type == "omnibor":
        if not _OMNIBOR_RE.match(value):
            return f"omnibor: not a gitoid identifier: {value!r}"
    elif id_type == "commit_hash":
        if not _COMMIT_RE.match(value):
            return f"commit_hash: not a hexadecimal commit hash: {value!r}"
    elif id_type == "organization":
        if " " in value:
            return f"organization: identifier must not contain whitespace: {value!r}"

    return None


def validate_identifiers(identifiers, component_label="component"):
    """Validate a component's identifier set against the 2026 element.

    The element requires at least one identifier; every identifier present must
    be syntactically valid for its type.

    Returns:
        ``{"valid": bool, "errors": [...], "counts": {type: n}, "count": n}``
    """
    entries = list(identifiers or [])
    errors = []

    if not entries:
        errors.append(f"{component_label}: no component identifier (the 2026 element requires at least one)")

    counts = {}
    for entry in entries:
        counts[entry.get("type") or "?"] = counts.get(entry.get("type") or "?", 0) + 1
        error = validate_identifier(entry)
        if error:
            errors.append(f"{component_label}: {error}")

    return {"valid": not errors, "errors": errors, "counts": counts, "count": len(entries)}


def validate_sbom_identifiers(sbom):
    """Validate the Component Identifiers element across a CycloneDX document.

    Returns a report: overall validity, per-component errors, how many
    components carry each identifier type, and how many carry a CPE — the
    number that decides whether the SBOM can be joined to NVD.
    """
    components = sbom.get("components") or []
    errors = []
    type_totals = {}
    with_cpe = 0
    multi = 0

    for component in components:
        label = f"{component.get('name', '?')}@{component.get('version', '?')}"
        identifiers = parse_identifiers_from_cyclonedx(component)
        result = validate_identifiers(identifiers, component_label=label)
        errors.extend(result["errors"])
        for id_type, count in result["counts"].items():
            type_totals[id_type] = type_totals.get(id_type, 0) + count
        if result["counts"].get("cpe"):
            with_cpe += 1
        if result["count"] > 1:
            multi += 1

    return {
        "valid": not errors,
        "component_count": len(components),
        "components_with_cpe": with_cpe,
        "components_with_multiple_identifiers": multi,
        "identifier_totals": type_totals,
        "errors": errors,
    }


def _component_from_args(args):
    """Build a parser-shaped component dict from the --component CLI arguments.

    A purl already carries name, version and namespace, so it is split rather
    than re-typed; anything the caller states explicitly still wins, because a
    purl cannot express the CycloneDX component type that decides the CPE part.
    """
    value = args.component.strip()
    component = {"type": args.component_type or "library"}

    if value.startswith("pkg:"):
        component["purl"] = value
        remainder = value[4:].split("?", 1)[0].split("#", 1)[0]
        _, _, path = remainder.partition("/")
        coordinates, _, version = path.partition("@")
        namespace, _, name = coordinates.rpartition("/")
        component["name"] = unquote(name)
        component["version"] = unquote(version)
        component["group"] = unquote(namespace)
    else:
        component["name"] = value
        component["version"] = ""
        component["group"] = ""

    for key, supplied in (("name", args.name), ("version", args.version), ("group", args.group)):
        if supplied:
            component[key] = supplied
    return component


def _report_component(args):
    """Print every identifier derivable for one component. Returns an exit code."""
    component = _component_from_args(args)
    identifiers = derive_identifiers(component)
    result = validate_identifiers(identifiers, component_label=component.get("name") or "component")
    errors = result["errors"]

    if args.json_output:
        print(
            json.dumps(
                {
                    "component": component,
                    "identifiers": identifiers,
                    "has_cpe": any(entry["type"] == "cpe" for entry in identifiers),
                    "errors": errors,
                    "valid": not errors,
                },
                indent=2,
            )
        )
    else:
        print(f"Component: {component.get('name')} {component.get('version') or '(no version)'}")
        for entry in identifiers:
            print(f"  {entry['type']:<13} {entry['value']}")
        if not any(entry["type"] == "cpe" for entry in identifiers):
            print("  cpe           (not derivable from these coordinates)")
        for error in errors:
            print(f"  ERROR: {error}")

    return 0 if not errors else 1


def main():
    parser = argparse.ArgumentParser(
        description="Validate the Component Identifiers element of a CycloneDX SBOM (2026 Minimum Elements, sbx-fld-05)"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate", help="Path to a CycloneDX JSON SBOM")
    mode.add_argument(
        "--component",
        help="Inspect one component instead of an SBOM: a purl, or a bare name. "
        "Prints every identifier derivable from those coordinates, CPE included.",
    )
    parser.add_argument("--name", help="Component name, when --component is not a purl")
    parser.add_argument("--version", dest="version", help="Component version")
    parser.add_argument("--group", help="Component group / namespace")
    parser.add_argument(
        "--type", dest="component_type", default="library", help="CycloneDX component type"
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.component:
        sys.exit(_report_component(args))

    path = Path(args.validate)
    if not path.exists():
        print(f"ERROR: SBOM not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        sbom = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        print(f"ERROR: {path} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    report = validate_sbom_identifiers(sbom)

    if args.json_output:
        print(json.dumps(report, indent=2))
    else:
        print(f"Components:                 {report['component_count']}")
        print(f"With a CPE (NVD-joinable):  {report['components_with_cpe']}")
        print(f"With >1 identifier:         {report['components_with_multiple_identifiers']}")
        print(f"Identifier totals:          {report['identifier_totals']}")
        if report["errors"]:
            print(f"\nERRORS ({len(report['errors'])}):")
            for error in report["errors"][:50]:
                print(f"  - {error}")
        print(f"\nComponent Identifiers element: {'PASS' if report['valid'] else 'FAIL'}")

    sys.exit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
