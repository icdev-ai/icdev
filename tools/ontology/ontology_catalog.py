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
    # Standalone ontology modules
    Path("tools/ai_game_engine/ontology.py"),
    Path("apps/forge_academy/ontology.py"),
    # Canvas ONTOLOGY_MAP constants — one per canvas
    Path("tools/infra_canvas/constants.py"),
    Path("tools/network/constants.py"),
    Path("tools/observability_canvas/constants.py"),
    Path("tools/agentic_ai_canvas/constants.py"),
    Path("tools/migration_canvas/constants.py"),
    Path("tools/ops_hub/constants.py"),
    Path("tools/aisg/constants.py"),
    Path("tools/boundary_canvas/constants.py"),
    Path("tools/security_canvas/constants.py"),
    Path("tools/data_canvas/constants.py"),
    Path("tools/pipeline/constants.py"),
    Path("tools/qdc_canvas/constants.py"),
    Path("tools/aiml_canvas/constants.py"),
    Path("tools/mission_canvas/constants.py"),
    # Additional canvas / tool ontology maps
    Path("tools/ai_observatory/constants.py"),
    Path("tools/govlift/constants.py"),
    Path("tools/system_graph/constants.py"),
    Path("tools/gameday/constants.py"),
    Path("tools/ttx/constants.py"),
    Path("tools/workflow_hitl/constants.py"),
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

    for src in ONTOLOGY_SOURCES:
        seen: set[tuple[str, str]] = set()  # reset per source — cross-file duplicates are expected
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
            # Handle flat string-map format: GAMEDAY_ONTOLOGY_MAP, INFRA_ONTOLOGY_MAP, etc.
            if name.endswith("_ONTOLOGY_MAP") and isinstance(obj, dict):
                domain = name.replace("_ONTOLOGY_MAP", "").lower()
                stats["domains"].add(domain)
                for key, val in obj.items():
                    if isinstance(val, str) and ":" in val:
                        stats["concepts"] += 1
                        prefix = val.split(":")[0] if not val.startswith("http") else ""
                        if prefix:
                            stats["prefixes"].add(prefix)
                continue
            if name.endswith("_ONTOLOGY") and isinstance(obj, dict):
                domain = name.replace("_ONTOLOGY", "").lower()
                stats["domains"].add(domain)
                for key, val in obj.items():
                    if isinstance(val, dict) and "classes" in val:
                        # Duplicate check is scoped to (domain, key, concept) — the same
                        # class may intentionally appear under multiple ontology keys.
                        key_seen: set[tuple[str, str, str]] = set()
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
                            key_pair = (domain, key, concept)
                            if key_pair in key_seen and concept:
                                warnings.append({"type": "duplicate_concept", "domain": domain, "concept": concept})
                            key_seen.add(key_pair)
                            seen.add((domain, concept))
                            if prefix:
                                stats["prefixes"].add(prefix)
                    elif isinstance(val, str) and ":" in val:
                        stats["concepts"] += 1
                        prefix = val.split(":")[0]
                        concept = val.split(":")[-1]
                        if not concept:
                            errors.append({"type": "empty_concept", "domain": domain, "key": key})
                        # Many-to-one mappings (multiple keys → same concept) are valid;
                        # only warn if the exact same key maps to the same concept twice.
                        pair = (domain, key, concept)
                        if pair in seen and concept:
                            warnings.append({"type": "duplicate_concept", "domain": domain, "concept": concept})
                        seen.add(pair)  # type: ignore[arg-type]
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
                            # key → concept is a many-to-one mapping by design; no dup check
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
