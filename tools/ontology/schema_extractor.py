#!/usr/bin/env python3
# CUI // SP-CTI
"""Ontology Schema Extractor — extract ontology concepts from ICDEV™ modules.

Scans registered ontology sources (tools/ai_game_engine/ontology.py,
apps/forge_academy/ontology.py) and emits a unified concept catalog.
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


def _extract_concepts(mod: Any, source_name: str) -> list[dict]:
    concepts: list[dict] = []
    for name, obj in inspect.getmembers(mod):
        if name.endswith("_ONTOLOGY") and isinstance(obj, dict):
            for key, val in obj.items():
                if isinstance(val, dict) and "classes" in val:
                    for cls in val.get("classes", []):
                        concepts.append(
                            {
                                "source": source_name,
                                "domain": name.replace("_ONTOLOGY", "").lower(),
                                "key": key,
                                "iri": cls.get("iri", ""),
                                "concept": cls.get("concept", ""),
                                "prefix": cls.get("prefix", ""),
                            }
                        )
                elif isinstance(val, str) and ":" in val:
                    concepts.append(
                        {
                            "source": source_name,
                            "domain": name.replace("_ONTOLOGY", "").lower(),
                            "key": key,
                            "iri": val,
                            "concept": val.split(":")[-1],
                            "prefix": val.split(":")[0],
                        }
                    )
        elif name in ("MISSION_TYPE_ONTOLOGY", "TOPIC_ONTOLOGY", "TIER_COMPETENCY", "TITLE_ONTOLOGY_OVERRIDES", "STEP_TYPE_ONTOLOGY"):
            if isinstance(obj, dict):
                for key, val in obj.items():
                    if isinstance(val, str) and ":" in val:
                        concepts.append(
                            {
                                "source": source_name,
                                "domain": name.replace("_ONTOLOGY", "").replace("_OVERRIDES", "").lower(),
                                "key": str(key),
                                "iri": val,
                                "concept": val.split(":")[-1],
                                "prefix": val.split(":")[0],
                            }
                        )
    return concepts


def extract_all(dry_run: bool = False) -> dict[str, Any]:
    results: list[dict] = []
    errors: list[str] = []
    for src in ONTOLOGY_SOURCES:
        if not src.exists():
            errors.append(f"missing:{src}")
            continue
        mod = _load_module(src)
        if mod is None:
            errors.append(f"load_failed:{src}")
            continue
        results.extend(_extract_concepts(mod, str(src)))
    out = {
        "success": True,
        "dry_run": dry_run,
        "total_concepts": len(results),
        "sources_scanned": len(ONTOLOGY_SOURCES),
        "errors": errors,
        "concepts": results if not dry_run else [],
    }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract ontology concepts from ICDEV™ modules")
    parser.add_argument("--dry-run", action="store_true", help="Count concepts without returning full list")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result = extract_all(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Extracted {result['total_concepts']} concepts from {result['sources_scanned']} sources")
        if result["errors"]:
            for e in result["errors"]:
                print(f"  error: {e}", file=sys.stderr)
    sys.exit(0 if not result["errors"] else 1)


if __name__ == "__main__":
    main()
