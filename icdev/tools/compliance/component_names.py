#!/usr/bin/env python3
# CUI // SP-CTI
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""Component Name alternates — 2026 SBOM Minimum Elements (sbx-fld-06).

The 2026 standard defines Component Name as *the name assigned by the component
producer* and adds one sentence the 2021 document did not have: **"data formats
implementing this element must allow multiple entries to capture alternate
names."**

That sentence exists because a component genuinely has more than one name, and
which one a recipient holds depends on where they got it:

* PyPI's index is case-insensitive and separator-insensitive (PEP 503), so the
  producer writes ``Flask``, ``zope.interface`` or ``ruamel.yaml`` while tooling
  keyed on the normalized form holds ``flask``, ``zope-interface``,
  ``ruamel-yaml``. A recipient matching an advisory against either spelling must
  hit the same component.
* npm scoped packages are published as ``@scope/name``; CycloneDX splits that
  into ``group`` + ``name``, so the name the producer actually published under
  appears in no single field.
* Maven artifacts are universally cited as ``groupId:artifactId``; the
  ``artifactId`` alone is ambiguous across groups.
* A Go module past v1 carries its major version *in the module path*
  (``github.com/foo/bar/v2``), and the same project is cited both ways.

ICDEV emitted exactly one name per component and, worse, emitted the name it had
derived rather than the one the producer assigned — every Python component was
lower-cased and its separators rewritten before it reached the document. This
module keeps the derived name as the primary (purl, ``bom-ref`` and
deduplication all key on it and must stay stable) and states the producer's own
spelling, and every other name the component is legitimately known by, as
**alternates** alongside it. Nothing is invented: every alternate is a
mechanical transform of evidence already in the component record, so the module
is pure and works identically inside an air-gapped enclave.

CYCLONEDX MAPPING
=================

CycloneDX has no alias array on ``component`` in any supported spec version, so
the alternates travel in properties — the same seam the Component Producer
(sbx-fld-02) and the unknown/withheld convention (sbx-prc-01) use::

    {"name": "icdev:component:alternate-name",                 "value": "Flask"}
    {"name": "icdev:component:alternate-name-source:Flask",    "value": "producer-declared-spelling"}

The bare ``icdev:component:alternate-name`` property is the payload a consumer
reads to match on a name; it repeats, once per alternate, which is precisely the
"multiple entries" the element requires. The paired ``-source:`` property says
*why* that string is a name for this component, so an alternate is a traceable
derivation rather than a bare assertion. Properties are emitted in sorted order
so regenerating an unchanged SBOM is byte-stable.

INTERACTION WITH THE DISCLOSURE CONVENTION
==========================================

A component whose ``name`` the operator withholds (sbx-prc-01's
``icdev:withheld:name``) gets **no** alternates: publishing ``@scope/thing`` as
an alternate of a name that was redacted would hand back the value the redaction
removed. :func:`alternate_names` is therefore called only where the name is
disclosed, and :func:`validate_document` fails a document that breaks that rule.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Run by path (`python tools/compliance/component_names.py`), sys.path[0] is this
# directory, not the repo root, so the absolute import below cannot resolve. The
# CLI is documented in docs/reference/commands.md, so it has to work that way.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# PEP 503 normalization is the Coverage element's own (sbx-cov-01) — a second
# copy here could drift and start claiming two different names are canonical.
from tools.compliance.dependency_resolver import _normalize_pypi_name  # noqa: E402

# --- Alternate-name sources ----------------------------------------------------------

#: The spelling the producer wrote, before ICDEV normalized case/separators.
SOURCE_PRODUCER_SPELLING = "producer-declared-spelling"

#: The registry-normalized form, where the emitted name is not already it.
SOURCE_NORMALIZED = "pep-503-normalized"

#: An npm scoped package's published name, ``@scope/name``.
SOURCE_SCOPED = "registry-scoped-name"

#: The ``groupId:artifactId`` coordinate Maven and Gradle artifacts are cited by.
SOURCE_COORDINATE = "ecosystem-coordinate"

#: A Go module path with its ``/vN`` major-version suffix removed.
SOURCE_GO_BASE_PATH = "go-module-path-without-major-version"

#: Closed vocabulary. An alternate carrying anything else is a defect, not a name.
SOURCES = frozenset(
    {
        SOURCE_PRODUCER_SPELLING,
        SOURCE_NORMALIZED,
        SOURCE_SCOPED,
        SOURCE_COORDINATE,
        SOURCE_GO_BASE_PATH,
    }
)

# --- CycloneDX property names --------------------------------------------------------

#: Repeats, once per alternate. The payload a consumer matches names against.
PROPERTY_ALTERNATE_NAME = "icdev:component:alternate-name"

#: ``icdev:component:alternate-name-source:<the alternate>`` -> a code from SOURCES.
PROPERTY_ALTERNATE_NAME_SOURCE_PREFIX = "icdev:component:alternate-name-source:"

# --- Ecosystems ----------------------------------------------------------------------

#: purl type -> the ecosystem name ``dependency_resolver`` uses. Declared-manifest
#: parsers do not set ``ecosystem``, so it is recovered from the purl they do set.
_PURL_TYPE_TO_ECOSYSTEM = {
    "pypi": "python",
    "npm": "npm",
    "maven": "maven",
    "golang": "golang",
    "cargo": "cargo",
    "nuget": "nuget",
}

_GO_MAJOR_SUFFIX = re.compile(r"/v([2-9]|[1-9][0-9]+)$")


def ecosystem_of(component):
    """The ecosystem of a component, from its own field or from its purl."""
    stated = str((component or {}).get("ecosystem") or "").strip().lower()
    if stated:
        return stated
    purl = str((component or {}).get("purl") or "")
    match = re.match(r"^pkg:([a-zA-Z0-9.+-]+)/", purl)
    if not match:
        return ""
    return _PURL_TYPE_TO_ECOSYSTEM.get(match.group(1).lower(), "")


def _add(alternates, seen, primary, name, source):
    """Record one alternate, unless it is empty, the primary, or already held."""
    candidate = str(name or "").strip()
    if not candidate or candidate == primary or candidate in seen:
        return
    seen.add(candidate)
    alternates.append({"name": candidate, "source": source})


def alternate_names(component, declared=None):
    """Every additional name ``component`` is legitimately known by.

    Args:
        component: a component record as the parsers and ``dependency_resolver``
            emit it — ``name``, ``group``, ``purl`` and optionally ``ecosystem``
            and ``declared_name``.
        declared: extra producer-declared spellings observed for this component,
            for instance from sibling instances that deduplication collapsed.
            The point of the element is not to lose a name, so a spelling seen on
            a collapsed twin still reaches the document.

    Returns:
        A list of ``{"name": ..., "source": ...}``, sorted by name so the
        document is byte-stable across regeneration. Empty for a component with
        exactly one name, which is the common case and not a defect.
    """
    component = component or {}
    primary = str(component.get("name") or "").strip()
    if not primary:
        return []

    ecosystem = ecosystem_of(component)
    group = str(component.get("group") or "").strip()

    alternates = []
    seen = set()

    # The producer's own spelling, whatever the ecosystem: this is literally the
    # name the element is defined as, so it is never dropped.
    spellings = [component.get("declared_name")] + list(declared or [])
    for spelling in spellings:
        _add(alternates, seen, primary, spelling, SOURCE_PRODUCER_SPELLING)

    if ecosystem == "python":
        # `flask` from `Flask` is already the primary. `zope.interface` is not:
        # the parsers rewrite `_` but not `.`, so the emitted name can still be
        # un-normalized and a recipient keyed on PEP 503 would miss it.
        _add(alternates, seen, primary, _normalize_pypi_name(primary), SOURCE_NORMALIZED)
        for spelling in spellings:
            if spelling:
                _add(alternates, seen, primary, _normalize_pypi_name(spelling), SOURCE_NORMALIZED)

    elif ecosystem == "npm":
        # CycloneDX splits `@scope/name`; neither half is the published name.
        if group.startswith("@"):
            _add(alternates, seen, primary, f"{group}/{primary}", SOURCE_SCOPED)

    elif ecosystem in ("maven", "gradle"):
        if group:
            _add(alternates, seen, primary, f"{group}:{primary}", SOURCE_COORDINATE)

    elif ecosystem == "golang":
        # `github.com/foo/bar/v2` and `github.com/foo/bar` name the same project.
        base = _GO_MAJOR_SUFFIX.sub("", primary)
        _add(alternates, seen, primary, base, SOURCE_GO_BASE_PATH)

    return sorted(alternates, key=lambda entry: entry["name"])


def component_names(component, declared=None):
    """Every name for a component, primary first, then alternates in sorted order."""
    primary = str((component or {}).get("name") or "").strip()
    names = [primary] if primary else []
    names.extend(entry["name"] for entry in alternate_names(component, declared=declared))
    return names


# =====================================================================================
# CycloneDX
# =====================================================================================


def alternate_name_properties(alternates):
    """Render alternates as CycloneDX properties, sorted for byte-stability."""
    properties = []
    for entry in sorted(alternates or [], key=lambda item: item["name"]):
        properties.append({"name": PROPERTY_ALTERNATE_NAME, "value": entry["name"]})
        properties.append(
            {
                "name": f"{PROPERTY_ALTERNATE_NAME_SOURCE_PREFIX}{entry['name']}",
                "value": entry["source"],
            }
        )
    return properties


def apply_to_cyclonedx(cdx_component, alternates):
    """Append a component's alternate names to its CycloneDX properties."""
    if not alternates:
        return cdx_component
    cdx_component.setdefault("properties", []).extend(alternate_name_properties(alternates))
    return cdx_component


def alternate_names_from_cyclonedx(cdx_component):
    """Read alternates back out of a CycloneDX component.

    The inverse of :func:`apply_to_cyclonedx`: an alternate written into a
    document comes back as the same ``{"name", "source"}`` record, so a consumer
    that re-emits the component does not silently drop the extra names. An
    alternate whose source property is missing round-trips with an empty source
    rather than being discarded — :func:`validate_document` is what reports it.
    """
    sources = {}
    names = []
    for prop in (cdx_component or {}).get("properties") or []:
        key = str((prop or {}).get("name") or "")
        value = str((prop or {}).get("value") or "")
        if key == PROPERTY_ALTERNATE_NAME:
            if value not in names:
                names.append(value)
        elif key.startswith(PROPERTY_ALTERNATE_NAME_SOURCE_PREFIX):
            sources[key[len(PROPERTY_ALTERNATE_NAME_SOURCE_PREFIX) :]] = value
    return [{"name": name, "source": sources.get(name, "")} for name in sorted(names)]


def all_names_from_cyclonedx(cdx_component):
    """Every name a CycloneDX component states — its ``name`` plus its alternates."""
    primary = str((cdx_component or {}).get("name") or "")
    names = [primary] if primary else []
    names.extend(entry["name"] for entry in alternate_names_from_cyclonedx(cdx_component))
    return names


# =====================================================================================
# Validation
# =====================================================================================

# Imported lazily inside the validator so this module stays importable on its own.
_WITHHELD_PREFIX = "icdev:withheld:"
_UNKNOWN_PREFIX = "icdev:unknown:"


def _name_is_disclosed(cdx_component):
    """Whether the component states its own name, rather than withholding it."""
    for prop in (cdx_component or {}).get("properties") or []:
        key = str((prop or {}).get("name") or "")
        if key in (f"{_WITHHELD_PREFIX}name", f"{_UNKNOWN_PREFIX}name"):
            return False
    return True


def validate_component(cdx_component, index=0):
    """Defects in one CycloneDX component's alternate names. Empty list is a pass."""
    defects = []
    label = f"components[{index}] ({cdx_component.get('name', '?')})"
    alternates = alternate_names_from_cyclonedx(cdx_component)
    if not alternates:
        return defects

    if not _name_is_disclosed(cdx_component):
        defects.append(
            f"{label}: states alternate names while its name is undisclosed — "
            f"an alternate would hand back the value the disclosure removed"
        )
        return defects

    primary = str(cdx_component.get("name") or "")
    raw_values = [
        str(prop.get("value") or "")
        for prop in (cdx_component.get("properties") or [])
        if str(prop.get("name") or "") == PROPERTY_ALTERNATE_NAME
    ]
    if len(raw_values) != len(set(raw_values)):
        defects.append(f"{label}: repeats an alternate name")

    for entry in alternates:
        name = entry["name"]
        if not name.strip():
            defects.append(f"{label}: an alternate name is empty")
            continue
        if name == primary:
            defects.append(f"{label}: alternate {name!r} repeats the primary name")
        if not entry["source"]:
            defects.append(f"{label}: alternate {name!r} states no source")
        elif entry["source"] not in SOURCES:
            defects.append(
                f"{label}: alternate {name!r} has source {entry['source']!r}, "
                f"which is not one of {sorted(SOURCES)}"
            )
    return defects


def validate_document(document):
    """Check every component's alternate names in a CycloneDX document.

    Returns ``{"conformant": bool, "components": int, "with_alternates": int,
    "alternate_names": int, "defects": [...]}``.
    """
    components = (document or {}).get("components") or []
    defects = []
    with_alternates = 0
    total = 0

    metadata_component = ((document or {}).get("metadata") or {}).get("component")
    scanned = list(components)
    if isinstance(metadata_component, dict):
        scanned.append(metadata_component)

    for index, component in enumerate(scanned):
        if not isinstance(component, dict):
            continue
        alternates = alternate_names_from_cyclonedx(component)
        if alternates:
            with_alternates += 1
            total += len(alternates)
        defects.extend(validate_component(component, index))

    return {
        "conformant": not defects,
        "components": len([c for c in scanned if isinstance(c, dict)]),
        "with_alternates": with_alternates,
        "alternate_names": total,
        "defects": defects,
    }


# =====================================================================================
# CLI
# =====================================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Component Name alternates — 2026 SBOM Minimum Elements (sbx-fld-06)"
    )
    parser.add_argument("--validate", metavar="SBOM", help="Check a CycloneDX SBOM's alternate names")
    parser.add_argument("--names", metavar="SBOM", help="List every name each component is known by")
    parser.add_argument("--vocabulary", action="store_true", help="The closed alternate-source vocabulary")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()

    if args.vocabulary:
        payload = {
            "sources": sorted(SOURCES),
            "property": PROPERTY_ALTERNATE_NAME,
            "source_property_prefix": PROPERTY_ALTERNATE_NAME_SOURCE_PREFIX,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print("Alternate-name sources:")
            for source in payload["sources"]:
                print(f"  {source}")
            print(f"\nProperty: {PROPERTY_ALTERNATE_NAME} (repeats, once per alternate)")
        return 0

    target = args.validate or args.names
    if not target:
        parser.print_help()
        return 1

    try:
        with open(target, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except Exception as exc:
        print(f"Cannot read {target}: {exc}", file=sys.stderr)
        return 1

    if args.names:
        listing = []
        for component in document.get("components") or []:
            if isinstance(component, dict):
                listing.append({"bom-ref": component.get("bom-ref"), "names": all_names_from_cyclonedx(component)})
        if args.json:
            print(json.dumps({"components": listing}, indent=2))
        else:
            for entry in listing:
                print(f"{entry['bom-ref']}: {', '.join(entry['names'])}")
        return 0

    report = validate_document(document)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Components:        {report['components']}")
        print(f"With alternates:   {report['with_alternates']}")
        print(f"Alternate names:   {report['alternate_names']}")
        print(f"Conformant:        {'YES' if report['conformant'] else 'NO'}")
        for defect in report["defects"]:
            print(f"  DEFECT: {defect}")
    return 0 if report["conformant"] else 1


if __name__ == "__main__":
    sys.exit(main())
