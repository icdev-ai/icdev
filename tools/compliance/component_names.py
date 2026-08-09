#!/usr/bin/env python3
# CUI // SP-CTI
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""Component Name — 2026 SBOM Minimum Elements (sbx-fld-06).

The 2026 minor update to this element changed one thing: a data format
implementing Component Name **must allow MULTIPLE entries, to capture alternate
names**. One component is routinely known by several names, and a recipient
matching an SBOM against an advisory, a registry or another SBOM matches on
whichever name that source happened to use. A document carrying one name forces
every consumer to re-derive the others, and each does it slightly differently.

ICDEV emitted exactly one name per component: whatever spelling survived its
parser's normalization. This module states the rest of them.

WHAT AN ALTERNATE NAME IS HERE
==============================

Every alternate is **derived from evidence the manifest already gave us** — the
spelling as written, the namespace the manifest itself supplied, or the purl
this generator built from those same coordinates. Nothing is guessed, no
registry is consulted, and no network call is made:

``declared``
    The spelling as it appears in the manifest, before the parser normalized it.
    ``Flask_Login`` in a ``requirements.txt`` becomes the component named
    ``flask-login``; both are real names for the same package and an advisory
    may use either.
``normalized``
    The ecosystem's canonical form, where that ecosystem defines one. Python's
    is PEP 503: lowercase with runs of ``-``, ``_`` and ``.`` collapsed to a
    single ``-``.
``qualified``
    Namespace and name joined the way the ecosystem writes a coordinate —
    ``com.example:alpha`` for Maven and Gradle, ``@babel/core`` for a scoped npm
    package. This is the form humans and advisories usually quote, and it is the
    one ICDEV's own ``name``/``group`` split destroys.
``short``
    The last path segment of a path-shaped name. A Go module
    ``github.com/spf13/cobra`` is universally called ``cobra``.
``purl``
    The name segment of the purl, decoded. Normally identical to the primary
    name and therefore dropped; it is emitted when the purl encoding round-trips
    to something different, which is precisely the case worth stating.

THE PRIMARY NAME DOES NOT MOVE
------------------------------

Alternates are added; the primary ``name`` is left exactly as it was. It feeds
``component_id`` — which is the bom-ref, the ``sbom_components`` primary key and
the organization-specific identifier all at once (sbx-fld-05) — so changing
which spelling wins would renumber every component in every historical document
to say nothing new. The element asks for the alternates to be *available*, not
for a different one to be *preferred*.

DEDUPLICATION IS BY VALUE, AND THE PRIMARY IS NOT AN ALTERNATE
--------------------------------------------------------------

Two derivations frequently produce the same string — an npm ``declared`` name
and its ``qualified`` reconstruction are both ``@babel/core``. The first kind to
produce a value keeps it, in the fixed order of :data:`NAME_KINDS`, so the
output is deterministic and two runs over one input diff clean. A derivation
that reproduces the primary name is dropped rather than emitted as an alternate
of itself.

A WITHHELD NAME HAS NO ALTERNATES
---------------------------------

If the disclosure policy withholds ``name``, the alternates are suppressed
entirely. Publishing four other names for the component whose name was redacted
would undo the redaction — the same reason ``component_licenser`` suppresses its
evidence and URL rather than only its identifier. The same applies to a name
that is *unknown*: there is no known spelling to have alternates of.

CARRIER
=======

CycloneDX has no native alternate-name array in any spec version ICDEV emits, so
alternates are properties: ``icdev:component-name-alternate:<kind>``, value the
name. CycloneDX ``properties`` is an array and does not require unique names, so
this genuinely allows MULTIPLE entries — which is the obligation. The pair
survives a round trip through :func:`parse_names_from_cyclonedx`.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.compliance.unknown_information import FIELD_NAME  # noqa: E402

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: Kind of alternate name. Also the derivation order, which fixes which kind
#: keeps a string that two derivations both produce.
NAME_DECLARED = "declared"
NAME_NORMALIZED = "normalized"
NAME_QUALIFIED = "qualified"
NAME_SHORT = "short"
NAME_PURL = "purl"

NAME_KINDS = (NAME_DECLARED, NAME_NORMALIZED, NAME_QUALIFIED, NAME_SHORT, NAME_PURL)

#: One property per alternate. The kind is in the property name and the name is
#: the value, so a reader that understands only the prefix still gets every name.
PROPERTY_ALTERNATE_PREFIX = "icdev:component-name-alternate:"

#: Ecosystems that write a coordinate as ``namespace:name`` rather than
#: ``namespace/name``. Maven and Gradle share a coordinate system.
_COLON_QUALIFIED = frozenset({"maven", "gradle"})

#: purl type -> ecosystem, for the few types whose purl name is not the
#: ecosystem name ICDEV uses internally.
_PURL_TYPE_TO_ECOSYSTEM = {
    "pypi": "python",
    "npm": "npm",
    "maven": "maven",
    "golang": "golang",
    "cargo": "cargo",
    "nuget": "nuget",
    "gem": "ruby",
}

_PURL_HEAD = re.compile(r"^pkg:([A-Za-z][A-Za-z0-9+.\-]*)/(.+)$")

#: PEP 503: runs of ``-``, ``_`` and ``.`` collapse to a single ``-``.
_PEP503_SEPARATORS = re.compile(r"[-_.]+")


def normalize_pypi(name):
    """The PEP 503 normalized form of a Python distribution name."""
    return _PEP503_SEPARATORS.sub("-", str(name or "").strip()).lower()


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def purl_ecosystem(purl):
    """The ecosystem a purl names, or ``""``."""
    match = _PURL_HEAD.match(str(purl or "").strip())
    if not match:
        return ""
    purl_type = match.group(1).lower()
    return _PURL_TYPE_TO_ECOSYSTEM.get(purl_type, purl_type)


def purl_name(purl):
    """The decoded name segment of a purl, or ``""``.

    The name is the LAST path segment before any ``@version``; everything before
    it is the namespace. Qualifiers and the subpath are cut first so a
    ``?arch=`` or ``#sub/dir`` cannot be mistaken for one.
    """
    match = _PURL_HEAD.match(str(purl or "").strip())
    if not match:
        return ""
    remainder = match.group(2)
    for cut in ("#", "?"):
        remainder = remainder.split(cut, 1)[0]
    # Split the version off the LAST segment only: an `@` can legally appear in
    # an encoded npm scope (`%40babel` decodes to one, but a raw `@` shows up in
    # hand-written purls too), and cutting on the first would eat the namespace.
    remainder = remainder.rsplit("/", 1)[-1]
    remainder = remainder.split("@", 1)[0]
    return unquote(remainder).strip()


def _ecosystem(component):
    """The ecosystem for a parser component dict — purl first, then the field."""
    return purl_ecosystem(component.get("purl")) or str(
        component.get("ecosystem") or ""
    ).strip().lower()


def derive_names(component):
    """Every name this component is known by, from its own coordinates.

    Returns ``{"primary": str, "alternates": [{"name": str, "kind": str}, ...]}``.
    ``alternates`` is deterministic, free of duplicates, and never contains the
    primary name.
    """
    component = component or {}
    primary = str(component.get("name") or "").strip()
    ecosystem = _ecosystem(component)
    group = str(component.get("group") or "").strip()
    declared = str(component.get("declared_name") or "").strip()

    candidates = []

    if declared:
        candidates.append((NAME_DECLARED, declared))

    if ecosystem == "python":
        # Only meaningful against the declared spelling: normalizing the primary
        # name reproduces it, and a component with no declared spelling recorded
        # has nothing to normalize.
        if declared:
            candidates.append((NAME_NORMALIZED, normalize_pypi(declared)))

    if group and primary:
        separator = ":" if ecosystem in _COLON_QUALIFIED else "/"
        candidates.append((NAME_QUALIFIED, f"{group}{separator}{primary}"))

    if "/" in primary:
        candidates.append((NAME_SHORT, primary.rsplit("/", 1)[-1]))

    candidates.append((NAME_PURL, purl_name(component.get("purl"))))

    alternates = []
    seen = {primary}
    for kind, value in candidates:
        value = str(value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        alternates.append({"name": value, "kind": kind})

    return {"primary": primary, "alternates": alternates}


def all_names(names):
    """Primary first, then every alternate — the flat list a matcher wants."""
    ordered = [names.get("primary", "")] if names.get("primary") else []
    ordered.extend(entry["name"] for entry in names.get("alternates") or [])
    return ordered


# ---------------------------------------------------------------------------
# CycloneDX
# ---------------------------------------------------------------------------


def name_properties(names, disclosure=None):
    """CycloneDX ``properties`` entries for a component's alternate names.

    Empty when the name is undisclosed: see the module docstring on why a
    withheld name cannot be allowed to carry alternates.
    """
    if disclosure is not None and disclosure.state_of(FIELD_NAME) is not None:
        return []
    return [
        {"name": f"{PROPERTY_ALTERNATE_PREFIX}{entry['kind']}", "value": entry["name"]}
        for entry in (names or {}).get("alternates") or []
    ]


def apply_names_to_cyclonedx(cdx_component, names, disclosure=None):
    """Append the alternate-name properties to a CycloneDX component."""
    properties = name_properties(names, disclosure)
    if properties:
        cdx_component.setdefault("properties", []).extend(properties)
    return cdx_component


def parse_names_from_cyclonedx(cdx_component):
    """Read back what :func:`apply_names_to_cyclonedx` wrote.

    Order is the document's, not :data:`NAME_KINDS`' — a round trip has to return
    what the document actually says, including an unrecognised kind, so that
    :func:`validate_sbom_names` can report it rather than silently drop it.
    """
    cdx_component = cdx_component or {}
    alternates = []
    for prop in cdx_component.get("properties") or []:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name") or "")
        if not name.startswith(PROPERTY_ALTERNATE_PREFIX):
            continue
        alternates.append(
            {
                "name": str(prop.get("value") or "").strip(),
                "kind": name[len(PROPERTY_ALTERNATE_PREFIX) :],
            }
        )
    return {"primary": str(cdx_component.get("name") or "").strip(), "alternates": alternates}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def names_to_json(names):
    """Serialize for storage. Sorted, so a re-run produces a byte-equal value."""
    alternates = sorted(
        ({"name": e["name"], "kind": e["kind"]} for e in (names or {}).get("alternates") or []),
        key=lambda e: (e["kind"], e["name"]),
    )
    return json.dumps(alternates, sort_keys=True)


def names_from_json(raw, primary=""):
    """Inverse of :func:`names_to_json`. Unreadable JSON degrades to no alternates."""
    if isinstance(raw, list):
        loaded = raw
    else:
        try:
            loaded = json.loads(raw or "[]")
        except (TypeError, ValueError):
            loaded = []
    alternates = [
        {"name": str(e.get("name") or "").strip(), "kind": str(e.get("kind") or "").strip()}
        for e in loaded
        if isinstance(e, dict) and str(e.get("name") or "").strip()
    ]
    return {"primary": primary, "alternates": alternates}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_names(names, component_label="component"):
    """Structural checks on one component's names. Returns a list of errors."""
    errors = []
    names = names or {}
    primary = str(names.get("primary") or "").strip()
    if not primary:
        errors.append(f"{component_label}: no component name")

    seen = set()
    for entry in names.get("alternates") or []:
        value = str(entry.get("name") or "").strip()
        kind = str(entry.get("kind") or "").strip()
        if not value:
            errors.append(f"{component_label}: alternate name of kind {kind!r} is empty")
            continue
        if kind not in NAME_KINDS:
            errors.append(f"{component_label}: {value!r} has unrecognised alternate-name kind {kind!r}")
        if value == primary:
            errors.append(f"{component_label}: alternate name {value!r} repeats the primary name")
        if value in seen:
            errors.append(f"{component_label}: alternate name {value!r} is listed twice")
        seen.add(value)

    return errors


def validate_sbom_names(sbom):
    """Check every component in a CycloneDX document. Returns ``(errors, summary)``.

    ``components_with_alternates`` is the number the element is actually about:
    a document where it is zero allows multiple entries in principle and states
    one name in practice.
    """
    errors = []
    components = [c for c in (sbom.get("components") or []) if isinstance(c, dict)]
    target = ((sbom.get("metadata") or {}).get("component")) or None
    if isinstance(target, dict):
        components = components + [target]

    with_alternates = 0
    total_alternates = 0
    for index, component in enumerate(components):
        names = parse_names_from_cyclonedx(component)
        label = component.get("bom-ref") or component.get("name") or f"components[{index}]"
        errors.extend(validate_names(names, label))
        if names["alternates"]:
            with_alternates += 1
            total_alternates += len(names["alternates"])

    return errors, {
        "components": len(components),
        "components_with_alternates": with_alternates,
        "alternate_names": total_alternates,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Component Name (2026 SBOM Minimum Elements) — alternate names"
    )
    parser.add_argument("--validate", help="Path to a CycloneDX JSON SBOM to check")
    parser.add_argument("--name", help="Derive the names of one component")
    parser.add_argument("--group", default="", help="Namespace/group for --name")
    parser.add_argument("--purl", default="", help="purl for --name")
    parser.add_argument("--declared-name", dest="declared_name", default="", help="Manifest spelling")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.name:
        names = derive_names(
            {
                "name": args.name,
                "group": args.group,
                "purl": args.purl,
                "declared_name": args.declared_name,
            }
        )
        if args.json_output:
            print(json.dumps(names, indent=2))
        else:
            print(f"primary: {names['primary']}")
            for entry in names["alternates"]:
                print(f"  {entry['kind']:<11} {entry['name']}")
        return 0

    if not args.validate:
        parser.error("one of --validate or --name is required")

    try:
        with open(args.validate, "r", encoding="utf-8") as handle:
            sbom = json.load(handle)
    except Exception as exc:
        print(f"ERROR: could not read SBOM: {args.validate}: {exc}", file=sys.stderr)
        return 1

    errors, summary = validate_sbom_names(sbom)
    payload = {"valid": not errors, "errors": errors, **summary}
    if args.json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Components:               {summary['components']}")
        print(f"With alternate names:     {summary['components_with_alternates']}")
        print(f"Alternate names total:    {summary['alternate_names']}")
        for error in errors:
            print(f"  ERROR {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
