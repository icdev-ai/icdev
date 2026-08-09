#!/usr/bin/env python3
# CUI // SP-CTI
"""External Ontology Mappings — STIX 2.1, MITRE ATT&CK, GeoSPARQL, OSCAL, DCAT.

Provides local-only mappings from ICDEV ontology classes to external standards.
All URIs are stored as prefix aliases; no external HTTP resolution is performed.

Public API
----------
    export_to_stix(icdev_class=None)      → dict with STIX 2.1 mappings
    export_to_oscal(icdev_class=None)     → dict with OSCAL mappings
    export_to_geosparql(icdev_class=None) → dict with GeoSPARQL mappings
    load_mappings()                       → full in-memory mapping registry
    write_ttl(path=None)                  → regenerate args/ontology/external_mappings.ttl

Usage
-----
    python tools/ontology/external_mappings.py --export stix --json
    python tools/ontology/external_mappings.py --export oscal --json
    python tools/ontology/external_mappings.py --export geosparql --json
    python tools/ontology/external_mappings.py --write-ttl
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_TTL_PATH = _BASE_DIR / "args" / "ontology" / "external_mappings.ttl"

# Mapping registry — single source of truth for all external alignments.
# Format: icdev_class -> {
#     "stix"      : {"type": "...", "relation": "owl:equivalentClass|...", "target": "stix:..."},
#     "mitre"     : {"type": "...", "relation": "...", "target": "mitre:..."},
#     "oscal"     : {"type": "...", "relation": "...", "target": "osacr:..."},
#     "geosparql" : {"type": "...", "relation": "...", "target": "geo:..."},
#     "dcat"      : {"type": "...", "relation": "...", "target": "dcat:..."},
# }
_MAPPINGS: Dict[str, Dict[str, Dict[str, str]]] = {
    "security:CyberOperation": {
        "stix": {
            "type": "intrusion-set",
            "relation": "owl:equivalentClass",
            "target": "stix:IntrusionSet",
            "description": (
                "A cyber operation is conceptually equivalent to a STIX 2.1 IntrusionSet. "
                "Both represent a grouped set of adversarial behaviors and resources."
            ),
        },
    },
    "security:ThreatActor": {
        "stix": {
            "type": "threat-actor",
            "relation": "owl:equivalentClass",
            "target": "stix:ThreatActor",
            "description": (
                "A threat actor is conceptually equivalent to a STIX 2.1 ThreatActor. "
                "Both represent an individual, group, or organization with malicious intent."
            ),
        },
    },
    "war:CyberOperation": {
        "mitre": {
            "type": "attack-pattern",
            "relation": "hasTechnique",
            "target": "mitre:AttackPattern",
            "description": (
                "Links a war cyber operation to a MITRE ATT&CK AttackPattern via T-code mapping. "
                "The hasTechnique property is defined in the ICDEV core namespace."
            ),
        },
    },
    "geospatial:GeoEntity": {
        "geosparql": {
            "type": "Feature",
            "relation": "owl:equivalentClass",
            "target": "geo:Feature",
            "description": (
                "A geospatial entity is equivalent to a GeoSPARQL Feature. "
                "Both represent a real-world or abstract thing with spatial characteristics."
            ),
        },
    },
    "compliance:NISTControl": {
        "oscal": {
            "type": "control",
            "relation": "owl:equivalentClass",
            "target": "osacr:Control",
            "description": (
                "A NIST control is conceptually equivalent to an OSCAL Control. "
                "Both represent a security or privacy requirement statement."
            ),
        },
    },
    "icdev:Project": {
        "dcat": {
            "type": "Dataset",
            "relation": "owl:equivalentClass",
            "target": "dcat:Dataset",
            "description": (
                "An ICDEV project is aligned with a DCAT Dataset for data catalog interoperability. "
                "Both represent a curated collection of data or resources."
            ),
        },
    },
}

# Prefix definitions used when generating TTL
_PREFIXES: Dict[str, str] = {
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "icdev": "https://icdev.dev/ontology/core#",
    "security": "https://icdev.dev/ns/security#",
    "war": "https://icdev.dev/ns/war#",
    "geospatial": "https://icdev.dev/ns/geospatial#",
    "compliance": "https://icdev.dev/ns/compliance#",
    "stix": "http://docs.oasis-open.org/ns/cti/stix#",
    "mitre": "http://attack.mitre.org/ns#",
    "geo": "http://www.opengis.net/ont/geosparql#",
    "osacr": "http://csrc.nist.gov/ns/oscal/1.0#",
    "dcat": "http://www.w3.org/ns/dcat#",
    "dcterms": "http://purl.org/dc/terms/",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_mappings() -> Dict[str, Dict[str, Dict[str, str]]]:
    """Return the full external-mapping registry.

    No external HTTP calls are made; all data is stored locally in Python
    constants that mirror ``args/ontology/external_mappings.ttl``.
    """
    return dict(_MAPPINGS)


def export_to_stix(icdev_class: Optional[str] = None) -> Dict[str, Any]:
    """Export ICDEV-to-STIX 2.1 mappings.

    Parameters
    ----------
    icdev_class :
        Optional ICDEV class URI (e.g. ``"security:CyberOperation"``).
        If *None*, all STIX mappings are returned.

    Returns
    -------
    dict with keys ``mappings`` (list) and ``standard`` (``"stix"``).
    Each mapping contains: icdev_class, stix_type, relation, stix_id,
    description.
    """
    mappings: List[Dict[str, str]] = []
    for cls, targets in _MAPPINGS.items():
        if icdev_class is not None and cls != icdev_class:
            continue
        stix = targets.get("stix")
        if stix:
            mappings.append(
                {
                    "icdev_class": cls,
                    "stix_type": stix["type"],
                    "relation": stix["relation"],
                    "stix_id": stix["target"],
                    "description": stix["description"],
                }
            )
        # MITRE attack-pattern is closely related to STIX; surface it here
        mitre = targets.get("mitre")
        if mitre:
            mappings.append(
                {
                    "icdev_class": cls,
                    "stix_type": mitre["type"],
                    "relation": mitre["relation"],
                    "stix_id": mitre["target"],
                    "description": mitre["description"],
                }
            )
    return {"standard": "stix", "mappings": mappings}


def export_to_oscal(icdev_class: Optional[str] = None) -> Dict[str, Any]:
    """Export ICDEV-to-OSCAL mappings.

    Parameters
    ----------
    icdev_class :
        Optional ICDEV class URI. If *None*, all OSCAL mappings are returned.

    Returns
    -------
    dict with keys ``mappings`` (list) and ``standard`` (``"oscal"``).
    """
    mappings: List[Dict[str, str]] = []
    for cls, targets in _MAPPINGS.items():
        if icdev_class is not None and cls != icdev_class:
            continue
        oscal = targets.get("oscal")
        if oscal:
            mappings.append(
                {
                    "icdev_class": cls,
                    "oscal_type": oscal["type"],
                    "relation": oscal["relation"],
                    "oscal_id": oscal["target"],
                    "description": oscal["description"],
                }
            )
    return {"standard": "oscal", "mappings": mappings}


def export_to_geosparql(icdev_class: Optional[str] = None) -> Dict[str, Any]:
    """Export ICDEV-to-GeoSPARQL mappings.

    Parameters
    ----------
    icdev_class :
        Optional ICDEV class URI. If *None*, all GeoSPARQL mappings are returned.

    Returns
    -------
    dict with keys ``mappings`` (list) and ``standard`` (``"geosparql"``).
    """
    mappings: List[Dict[str, str]] = []
    for cls, targets in _MAPPINGS.items():
        if icdev_class is not None and cls != icdev_class:
            continue
        geosparql = targets.get("geosparql")
        if geosparql:
            mappings.append(
                {
                    "icdev_class": cls,
                    "geosparql_type": geosparql["type"],
                    "relation": geosparql["relation"],
                    "geosparql_id": geosparql["target"],
                    "description": geosparql["description"],
                }
            )
    return {"standard": "geosparql", "mappings": mappings}


# ---------------------------------------------------------------------------
# TTL generation / regeneration
# ---------------------------------------------------------------------------


def write_ttl(path: Optional[Path] = None) -> Path:
    """Regenerate the Turtle file from the in-memory mapping registry.

    This keeps ``args/ontology/external_mappings.ttl`` in sync with the
    Python constants.  No external HTTP calls are made.

    Parameters
    ----------
    path :
        Destination file path.  Defaults to ``args/ontology/external_mappings.ttl``.

    Returns
    -------
    Path of the written file.
    """
    out = path or _TTL_PATH
    out.parent.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines: List[str] = []
    for prefix, uri in _PREFIXES.items():
        lines.append(f'@prefix {prefix}: <{uri}> .')
    lines.append("")

    lines.append('<https://icdev.dev/ontology/external-mappings>')
    lines.append('    rdf:type owl:Ontology ;')
    lines.append('    owl:versionInfo "1.0.0" ;')
    lines.append('    dcterms:title "ICDEV External Standard Mappings" ;')
    lines.append('    dcterms:description "Mappings from ICDEV ontology to external standards: STIX 2.1, MITRE ATT&CK, GeoSPARQL, NIST OSCAL, DCAT. All URIs are stored locally; no external HTTP resolution is required." ;')
    lines.append(f'    dcterms:date "{today}"^^xsd:date .')
    lines.append("")
    lines.append("# ── External standard prefixes (local resolution only) ──")
    lines.append("# STIX 2.1       — OASIS Cyber Threat Intelligence ( conceptual alignment )")
    lines.append("# MITRE ATT&CK   — technique taxonomy (distributed via STIX bundles)")
    lines.append("# GeoSPARQL      — OGC spatial ontology")
    lines.append("# NIST OSCAL     — control catalog and assessment framework")
    lines.append("# DCAT           — W3C Data Catalog Vocabulary")
    lines.append("")

    # Group by relation type for readability
    equivalences: List[str] = []
    properties: List[str] = []
    property_assertions: List[str] = []

    for cls, targets in _MAPPINGS.items():
        for std, info in targets.items():
            rel = info["relation"]
            target = info["target"]
            if rel == "owl:equivalentClass":
                equivalences.append(f"{cls} owl:equivalentClass {target} .")
            elif rel == "hasTechnique":
                # Define property once
                if not properties:
                    properties.append("icdev:hasTechnique rdf:type owl:ObjectProperty ;")
                    properties.append('    rdfs:label "has technique" ;')
                    properties.append('    rdfs:comment "Links an operation to a MITRE ATT&CK technique pattern (T-code mapping). No external HTTP resolution is required; the mitre: prefix is a locally stored namespace alias." ;')
                    properties.append(f"    rdfs:domain {cls.split(':')[0]}:CyberOperation ;")
                    properties.append('    rdfs:range mitre:AttackPattern .')
                    properties.append("")
                property_assertions.append(f"{cls} icdev:hasTechnique {target} .")

    if equivalences:
        lines.append("# ── Class equivalences ──")
        lines.extend(equivalences)
        lines.append("")

    if properties:
        lines.append("# ── Property definitions for non-equivalence mappings ──")
        lines.extend(properties)
        lines.append("")

    if property_assertions:
        lines.append("# ── MITRE ATT&CK technique mapping ──")
        lines.extend(property_assertions)
        lines.append("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ICDEV External Ontology Mappings")
    p.add_argument(
        "--export",
        choices=["stix", "oscal", "geosparql", "all"],
        help="Export mappings for the selected standard",
    )
    p.add_argument(
        "--icdev-class",
        default=None,
        help="Filter to a single ICDEV class (e.g. security:CyberOperation)",
    )
    p.add_argument("--write-ttl", action="store_true", help="Regenerate external_mappings.ttl")
    p.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    p.add_argument("--validate", action="store_true", help="Validate that TTL file is in sync with Python constants")
    return p


def _validate() -> Dict[str, Any]:
    """Check that the on-disk TTL matches what Python would generate."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ttl", delete=False, encoding="utf-8") as fh:
        tmp = Path(fh.name)
    try:
        write_ttl(path=tmp)
        expected_text = tmp.read_text(encoding="utf-8")
    finally:
        tmp.unlink(missing_ok=True)

    if not _TTL_PATH.exists():
        return {"status": "error", "message": f"TTL file missing: {_TTL_PATH}"}

    actual_text = _TTL_PATH.read_text(encoding="utf-8")
    # Normalise line endings for cross-platform comparison
    expected_norm = expected_text.replace("\r\n", "\n").strip()
    actual_norm = actual_text.replace("\r\n", "\n").strip()

    if expected_norm == actual_norm:
        return {"status": "ok", "message": "TTL file is in sync with Python constants."}

    return {
        "status": "drift",
        "message": "TTL file differs from Python constants. Run --write-ttl to regenerate.",
    }


def main(argv: Optional[List[str]] = None) -> None:
    args = _build_parser().parse_args(argv)

    if args.validate:
        result = _validate()
        print(json.dumps(result, indent=2) if args.json else result["message"])
        sys.exit(0 if result["status"] == "ok" else 1)

    if args.write_ttl:
        written = write_ttl()
        msg = f"Written: {written}"
        print(json.dumps({"status": "ok", "path": str(written)}, indent=2) if args.json else msg)
        return

    if not args.export:
        _build_parser().print_help()
        return

    results: List[Dict[str, Any]] = []
    exports = {
        "stix": export_to_stix,
        "oscal": export_to_oscal,
        "geosparql": export_to_geosparql,
    }

    keys = list(exports.keys()) if args.export == "all" else [args.export]
    for key in keys:
        fn = exports[key]
        results.append(fn(icdev_class=args.icdev_class))

    if args.export == "all":
        payload: Dict[str, Any] = {"exports": results}
    else:
        payload = results[0]

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"[{payload['standard'].upper()}] {len(payload['mappings'])} mapping(s)")
        for m in payload["mappings"]:
            print(f"  {m['icdev_class']} -> {m.get('stix_id') or m.get('oscal_id') or m.get('geosparql_id')} ({m['relation']})")


if __name__ == "__main__":
    main()
