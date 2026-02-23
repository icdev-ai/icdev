#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D -- Authorized DoD Personnel Only
# POC: ICDEV System Administrator
"""Type Checker — ICDEV Cross-Language Translation (Phase 43, D253)

Phase 2 of the 5-phase hybrid translation pipeline.
Validates type-compatibility of function signatures between source/target
type systems BEFORE LLM translation (adopted from Amazon Oxidizer, PLDI 2025).

Catches:
  - Nullable/non-nullable mismatches
  - Generic type parameter differences
  - Trait/interface incompatibilities
  - Ownership model differences (Rust)
  - Error handling model differences (Go error returns vs exceptions)

Type mappings stored in context/translation/type_mappings.json.
"""

import argparse
import json
import sys
import textwrap
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TYPE_MAPPINGS_PATH = BASE_DIR / "context" / "translation" / "type_mappings.json"

CUI_BANNER = "CUI // SP-CTI"

# ---------------------------------------------------------------------------
# Built-in type mappings (fallback when config not available)
# ---------------------------------------------------------------------------
BUILTIN_TYPE_MAPPINGS = {
    "primitives": {
        "python": {
            "int": {"java": "int", "go": "int", "rust": "i64", "csharp": "int", "typescript": "number"},
            "float": {"java": "double", "go": "float64", "rust": "f64", "csharp": "double", "typescript": "number"},
            "str": {"java": "String", "go": "string", "rust": "String", "csharp": "string", "typescript": "string"},
            "bool": {"java": "boolean", "go": "bool", "rust": "bool", "csharp": "bool", "typescript": "boolean"},
            "bytes": {"java": "byte[]", "go": "[]byte", "rust": "Vec<u8>", "csharp": "byte[]", "typescript": "Buffer"},
            "None": {"java": "void", "go": "", "rust": "()", "csharp": "void", "typescript": "void"},
        },
        "java": {
            "int": {"python": "int", "go": "int32", "rust": "i32", "csharp": "int", "typescript": "number"},
            "long": {"python": "int", "go": "int64", "rust": "i64", "csharp": "long", "typescript": "number"},
            "double": {"python": "float", "go": "float64", "rust": "f64", "csharp": "double", "typescript": "number"},
            "String": {"python": "str", "go": "string", "rust": "String", "csharp": "string", "typescript": "string"},
            "boolean": {"python": "bool", "go": "bool", "rust": "bool", "csharp": "bool", "typescript": "boolean"},
            "void": {"python": "None", "go": "", "rust": "()", "csharp": "void", "typescript": "void"},
        },
        "go": {
            "int": {"python": "int", "java": "long", "rust": "isize", "csharp": "long", "typescript": "number"},
            "int32": {"python": "int", "java": "int", "rust": "i32", "csharp": "int", "typescript": "number"},
            "int64": {"python": "int", "java": "long", "rust": "i64", "csharp": "long", "typescript": "number"},
            "float64": {"python": "float", "java": "double", "rust": "f64", "csharp": "double", "typescript": "number"},
            "string": {"python": "str", "java": "String", "rust": "String", "csharp": "string", "typescript": "string"},
            "bool": {"python": "bool", "java": "boolean", "rust": "bool", "csharp": "bool", "typescript": "boolean"},
            "error": {"python": "Exception", "java": "Exception", "rust": "Result", "csharp": "Exception", "typescript": "Error"},
        },
        "rust": {
            "i32": {"python": "int", "java": "int", "go": "int32", "csharp": "int", "typescript": "number"},
            "i64": {"python": "int", "java": "long", "go": "int64", "csharp": "long", "typescript": "number"},
            "f64": {"python": "float", "java": "double", "go": "float64", "csharp": "double", "typescript": "number"},
            "String": {"python": "str", "java": "String", "go": "string", "csharp": "string", "typescript": "string"},
            "bool": {"python": "bool", "java": "boolean", "go": "bool", "csharp": "bool", "typescript": "boolean"},
        },
    },
    "collections": {
        "python": {
            "list": {"java": "List", "go": "[]", "rust": "Vec", "csharp": "List", "typescript": "Array"},
            "dict": {"java": "Map", "go": "map", "rust": "HashMap", "csharp": "Dictionary", "typescript": "Record"},
            "set": {"java": "Set", "go": "map[K]struct{}", "rust": "HashSet", "csharp": "HashSet", "typescript": "Set"},
            "tuple": {"java": "record", "go": "struct", "rust": "tuple", "csharp": "ValueTuple", "typescript": "tuple"},
        },
    },
    "nullable": {
        "python": "Optional[T]",
        "java": "Optional<T> / @Nullable",
        "go": "*T (pointer)",
        "rust": "Option<T>",
        "csharp": "T?",
        "typescript": "T | null | undefined",
    },
    "error_handling": {
        "python": "try/except (exceptions)",
        "java": "try/catch (checked+unchecked exceptions)",
        "go": "(value, error) multi-return",
        "rust": "Result<T, E> / Option<T>",
        "csharp": "try/catch (exceptions)",
        "typescript": "try/catch (exceptions) / Promise rejection",
    },
    "type_system_warnings": {
        "python_to_rust": [
            "Python is dynamically typed; Rust is statically typed with ownership. All types must be explicit.",
            "Python None -> Rust Option<T>. Every nullable value must be wrapped in Option.",
            "Python mutable-by-default -> Rust immutable-by-default. Use mut for mutable variables.",
        ],
        "python_to_go": [
            "Python exceptions -> Go (value, error) returns. Every function that can fail must return error.",
            "Python classes -> Go structs + interfaces. No inheritance; use composition and interface satisfaction.",
        ],
        "java_to_rust": [
            "Java null references -> Rust Option<T>. No null in Rust.",
            "Java garbage collection -> Rust ownership/borrowing. Memory managed at compile time.",
            "Java class hierarchy -> Rust traits + generics. No inheritance.",
        ],
        "java_to_go": [
            "Java class hierarchy -> Go interfaces (implicit) + struct embedding.",
            "Java checked exceptions -> Go (value, error) multi-return.",
        ],
        "go_to_rust": [
            "Go goroutines -> Rust async/await or std::thread with ownership transfer.",
            "Go channels -> Rust mpsc channels.",
            "Go error returns -> Rust Result<T, E>. Use ? operator for propagation.",
        ],
    },
}


def load_type_mappings(path=None):
    """Load type mappings from JSON file."""
    mappings_path = path or TYPE_MAPPINGS_PATH
    if mappings_path.exists():
        with open(mappings_path, encoding="utf-8") as f:
            return json.load(f)
    return BUILTIN_TYPE_MAPPINGS


def map_type(source_type, source_lang, target_lang, mappings=None):
    """Map a single type from source to target language.

    Returns dict with: source_type, target_type, confidence, warnings.
    """
    if mappings is None:
        mappings = load_type_mappings()

    source_lang = source_lang.lower()
    target_lang = target_lang.lower()

    result = {
        "source_type": source_type,
        "source_language": source_lang,
        "target_language": target_lang,
        "target_type": None,
        "confidence": 0.0,
        "warnings": [],
    }

    if not source_type:
        result["target_type"] = "auto"
        result["confidence"] = 0.5
        result["warnings"].append("No type annotation in source — target type must be inferred")
        return result

    # Check primitive mappings
    primitives = mappings.get("primitives", {}).get(source_lang, {})
    clean_type = source_type.strip()

    if clean_type in primitives:
        target_map = primitives[clean_type]
        if target_lang in target_map:
            result["target_type"] = target_map[target_lang]
            result["confidence"] = 1.0
            return result

    # Check collection mappings
    collections = mappings.get("collections", {}).get(source_lang, {})
    for coll_name, coll_map in collections.items():
        if coll_name.lower() in clean_type.lower():
            if target_lang in coll_map:
                result["target_type"] = coll_map[target_lang]
                result["confidence"] = 0.8
                result["warnings"].append(f"Collection type mapping — inner types must be translated separately")
                return result

    # Check nullable patterns
    nullable_patterns = {
        "python": r"Optional\[",
        "java": r"Optional<|@Nullable",
        "rust": r"Option<",
        "csharp": r"\w+\?",
        "typescript": r"\|\s*null|\|\s*undefined",
    }
    src_nullable_pat = nullable_patterns.get(source_lang, "")
    if src_nullable_pat and __import__("re").search(src_nullable_pat, clean_type):
        nullable_target = mappings.get("nullable", {}).get(target_lang, "nullable")
        result["target_type"] = nullable_target
        result["confidence"] = 0.7
        result["warnings"].append("Nullable type — ensure null safety in target language")
        return result

    # Unknown type — needs LLM or manual mapping
    result["target_type"] = f"/* TODO: map {clean_type} */"
    result["confidence"] = 0.2
    result["warnings"].append(f"Unknown type mapping for '{clean_type}' — requires manual review")

    return result


def check_signature_compatibility(unit, source_lang, target_lang, mappings=None):
    """Check type-compatibility of a function signature.

    Returns dict with: compatible, issues, parameter_mappings, return_mapping.
    """
    if mappings is None:
        mappings = load_type_mappings()

    issues = []
    param_mappings = []

    # Check parameters
    for param in unit.get("parameters", []):
        param_type = param.get("type")
        mapped = map_type(param_type, source_lang, target_lang, mappings)
        param_mappings.append({
            "param_name": param.get("name", "?"),
            "source_type": param_type,
            "target_type": mapped["target_type"],
            "confidence": mapped["confidence"],
            "warnings": mapped["warnings"],
        })
        if mapped["confidence"] < 0.5:
            issues.append(
                f"Parameter '{param.get('name', '?')}': low confidence type mapping "
                f"({param_type} -> {mapped['target_type']})"
            )

    # Check return type
    return_type = unit.get("return_type")
    return_mapped = map_type(return_type, source_lang, target_lang, mappings)
    if return_mapped["confidence"] < 0.5 and return_type:
        issues.append(
            f"Return type: low confidence mapping ({return_type} -> {return_mapped['target_type']})"
        )

    # Check error handling model differences
    pair_key = f"{source_lang}_to_{target_lang}"
    type_warnings = mappings.get("type_system_warnings", {}).get(pair_key, [])

    # Go-specific: functions need error returns
    if target_lang == "go" and unit.get("kind") == "function":
        issues.append("Go target: ensure function returns (value, error) for fallible operations")

    # Rust-specific: ownership considerations
    if target_lang == "rust":
        if any(p.get("type") and "list" in str(p.get("type", "")).lower() for p in unit.get("parameters", [])):
            issues.append("Rust target: mutable collection parameters need &mut or owned Vec<T>")

    compatible = len([i for i in issues if "low confidence" in i]) == 0

    return {
        "unit_name": unit.get("name", "?"),
        "unit_kind": unit.get("kind", "?"),
        "compatible": compatible,
        "issues": issues,
        "type_system_warnings": type_warnings,
        "parameter_mappings": param_mappings,
        "return_mapping": {
            "source_type": return_type,
            "target_type": return_mapped["target_type"],
            "confidence": return_mapped["confidence"],
            "warnings": return_mapped["warnings"],
        },
    }


def check_all_units(units, source_lang, target_lang, mappings=None):
    """Check type-compatibility for all IR units.

    Returns dict with: total, compatible, incompatible, results.
    """
    if mappings is None:
        mappings = load_type_mappings()

    results = []
    for unit in units:
        if unit.get("kind") in ("function", "class"):
            result = check_signature_compatibility(unit, source_lang, target_lang, mappings)
            results.append(result)

    compatible_count = sum(1 for r in results if r["compatible"])
    incompatible_count = len(results) - compatible_count

    return {
        "source_language": source_lang,
        "target_language": target_lang,
        "total_checked": len(results),
        "compatible": compatible_count,
        "incompatible": incompatible_count,
        "compatibility_pct": round((compatible_count / len(results) * 100) if results else 0, 1),
        "results": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description=(
            f"{CUI_BANNER}\n"
            "ICDEV Type Checker — Phase 2: Type-Compatibility Pre-Check (D253)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(f"""\
            Examples:
              python tools/translation/type_checker.py \\
                --ir-file ir.json --source-language python \\
                --target-language java --json

            {CUI_BANNER}
        """),
    )
    parser.add_argument("--ir-file", required=True, help="IR JSON file from source_extractor")
    parser.add_argument("--source-language", required=True, help="Source language")
    parser.add_argument("--target-language", required=True, help="Target language")
    parser.add_argument("--project-id", default="", help="Project ID for audit trail")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    ir_path = Path(args.ir_file)
    if not ir_path.exists():
        print(f"[ERROR] IR file not found: {ir_path}", file=sys.stderr)
        sys.exit(1)

    with open(ir_path, encoding="utf-8") as f:
        ir_data = json.load(f)

    units = ir_data.get("units", [])
    result = check_all_units(units, args.source_language, args.target_language)

    # Audit trail
    if args.project_id:
        try:
            sys.path.insert(0, str(BASE_DIR))
            from tools.audit.audit_logger import log_event
            log_event(
                event_type="translation.type_check",
                actor="type-checker",
                action=f"Type-checked {result['total_checked']} units: {result['compatibility_pct']}% compatible",
                project_id=args.project_id,
                details={
                    "source_language": args.source_language,
                    "target_language": args.target_language,
                    "compatible": result["compatible"],
                    "incompatible": result["incompatible"],
                },
                classification="CUI",
            )
        except Exception:
            pass

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"[INFO] Type-check: {result['compatible']}/{result['total_checked']} units compatible "
              f"({result['compatibility_pct']}%)")
        if result["incompatible"] > 0:
            print(f"[WARN] {result['incompatible']} units have type compatibility issues")
            for r in result["results"]:
                if not r["compatible"]:
                    print(f"  - {r['unit_name']}: {', '.join(r['issues'][:2])}")


if __name__ == "__main__":
    main()
