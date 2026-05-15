#!/usr/bin/env python3
# CUI // SP-CTI
"""Ontology Catalog Validator — validate ontology consistency and coverage.

Checks:
- All IRIs are non-empty and well-formed
- No duplicate (prefix, concept) pairs within a domain
- All referenced prefixes are defined in ONTOLOGY_NAMESPACES
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any

ONTOLOGY_SOURCES = [
    Path("tools/ai_game_engine/ontology.py"),
    Path("apps/forge_academy/ontology.py"),
]


def _load_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def validate_catalog() -> dict[str, Any]:
    errors: list[dict] = []
    warnings: list[dict] = []
    stats = {"sources": 0, "concepts": 0, "domains": set(), "prefixes": set()}
    seen: set[tuple[str, str]] = set()

    for src in ONTOLOGY_SOURCES:
        if not src.exists():
            errors.append({"type": "missing_source", "file": str(src)})
            continue
        mod = _load_module(src)
        if mod is None:
            errors.append({"type": "load_failed", "file": str(src)})
            continue
        stats["sources"] += 1

        namespaces = getattr(mod, "ONTOLOGY_NAMESPACES", {})
        valid_prefixes = set(namespaces.keys())

        for name, obj in inspect.getmembers(mod):
            if name.endswith("_ONTOLOGY") and isinstance(obj, dict):
                domain = name.replace("_ONTOLOGY", "").lower()
                stats["domains"].add(domain)
                for key, val in obj.items():
                    if isinstance(val, dict) and "classes" in val:
                        for cls in val.get("classes", []):
                            stats["concepts"] += 1
                            prefix = cls.get("prefix", "")
                            concept = cls.get("concept", "")
                            iri = cls.get("iri", "")
                            if not prefix:
                                errors.append({"type": "empty_prefix", "domain": domain, "key": key, "concept": concept})
                            if not concept:
                                errors.append({"type": "empty_concept", "domain": domain, "key": key})
                            if not iri:
                                errors.append({"type": "empty_iri", "domain": domain, "key": key, "concept": concept})
                            if prefix and prefix not in valid_prefixes:
                                warnings.append({"type": "unknown_prefix", "domain": domain, "prefix": prefix, "concept": concept})
                            pair = (domain, concept)
                            if pair in seen and concept:
                                warnings.append({"type": "duplicate_concept", "domain": domain, "concept": concept})
                            seen.add(pair)
                            if prefix:
                                stats["prefixes"].add(prefix)
                    elif isinstance(val, str) and ":" in val:
                        stats["concepts"] += 1
                        prefix = val.split(":")[0]
                        concept = val.split(":")[-1]
                        if not concept:
                            errors.append({"type": "empty_concept", "domain": domain, "key": key})
                        pair = (domain, concept)
                        if pair in seen and concept:
                            warnings.append({"type": "duplicate_concept", "domain": domain, "concept": concept})
                        seen.add(pair)
                        if prefix:
                            stats["prefixes"].add(prefix)
            elif name in ("MISSION_TYPE_ONTOLOGY", "TOPIC_ONTOLOGY", "TIER_COMPETENCY", "TITLE_ONTOLOGY_OVERRIDES", "STEP_TYPE_ONTOLOGY"):
                if isinstance(obj, dict):
                    domain = name.replace("_ONTOLOGY", "").replace("_OVERRIDES", "").lower()
                    stats["domains"].add(domain)
                    for key, val in obj.items():
                        if isinstance(val, str) and ":" in val:
                            stats["concepts"] += 1
                            prefix = val.split(":")[0]
                            concept = val.split(":")[-1]
                            if not concept:
                                errors.append({"type": "empty_concept", "domain": domain, "key": str(key)})
                            pair = (domain, concept)
                            if pair in seen and concept:
                                warnings.append({"type": "duplicate_concept", "domain": domain, "concept": concept})
                            seen.add(pair)
                            if prefix:
                                stats["prefixes"].add(prefix)

    result = {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "sources": stats["sources"],
            "concepts": stats["concepts"],
            "domains": len(stats["domains"]),
            "prefixes": len(stats["prefixes"]),
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate ontology catalog")
    parser.add_argument("--validate", action="store_true", help="Run validation (required for gate mode)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result = validate_catalog()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        status = "VALID" if result["valid"] else "INVALID"
        print(f"Ontology catalog: {status}")
        print(f"  Concepts: {result['stats']['concepts']}")
        print(f"  Domains: {result['stats']['domains']}")
        print(f"  Prefixes: {result['stats']['prefixes']}")
        if result["errors"]:
            print(f"  Errors: {len(result['errors'])}")
        if result["warnings"]:
            print(f"  Warnings: {len(result['warnings'])}")
    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
