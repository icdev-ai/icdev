#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D -- Authorized DoD Personnel Only
# POC: ICDEV System Administrator
"""Dependency Mapper — ICDEV Cross-Language Translation (Phase 43, D246)

Maps cross-language package equivalents from a declarative JSON table.
When a mapping is unknown, optionally queries LLM for an advisory suggestion.

Mappings stored in context/translation/dependency_mappings.json (D26 pattern).
Add new mappings without code changes.
"""

import argparse
import json
import sys
import textwrap
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MAPPINGS_PATH = BASE_DIR / "context" / "translation" / "dependency_mappings.json"

CUI_BANNER = "CUI // SP-CTI"

SUPPORTED_LANGUAGES = ("python", "java", "javascript", "typescript", "go", "rust", "csharp")


def _normalize_lang(lang):
    """Normalize language name."""
    lang = lang.lower().strip()
    aliases = {
        "js": "javascript",
        "ts": "typescript",
        "c#": "csharp",
        "cs": "csharp",
        "py": "python",
    }
    return aliases.get(lang, lang)


def load_mappings(path=None):
    """Load dependency mappings from JSON file."""
    mappings_path = path or MAPPINGS_PATH
    if not mappings_path.exists():
        return {}
    with open(mappings_path, encoding="utf-8") as f:
        return json.load(f)


def resolve_import(import_name, source_lang, target_lang, mappings=None):
    """Resolve a single import to its target language equivalent.

    Returns dict with:
        source_import, target_import, mapping_source, confidence, domain, notes
    """
    source_lang = _normalize_lang(source_lang)
    target_lang = _normalize_lang(target_lang)

    if mappings is None:
        mappings = load_mappings()

    result = {
        "source_import": import_name,
        "source_language": source_lang,
        "target_language": target_lang,
        "target_import": None,
        "mapping_source": "unmapped",
        "confidence": 0.0,
        "domain": None,
        "notes": "",
    }

    # Search through domain mappings
    domains = mappings.get("domains", {})
    for domain_name, domain_data in domains.items():
        packages = domain_data.get("packages", {})
        source_packages = packages.get(source_lang, [])

        # Check if import_name matches any source package
        if isinstance(source_packages, list):
            matched = import_name in source_packages
        elif isinstance(source_packages, str):
            matched = import_name == source_packages
        else:
            matched = False

        if matched:
            target_packages = packages.get(target_lang, [])
            if target_packages:
                target_pkg = target_packages[0] if isinstance(target_packages, list) else target_packages
                result["target_import"] = target_pkg
                result["mapping_source"] = "table"
                result["confidence"] = 1.0
                result["domain"] = domain_name
                result["notes"] = domain_data.get("notes", "")
                return result

    # No mapping found — check if it's a stdlib module
    stdlib_modules = {
        "python": [
            "os", "sys", "json", "re", "math", "datetime", "pathlib",
            "collections", "itertools", "functools", "typing", "abc",
            "hashlib", "uuid", "logging", "argparse", "textwrap",
            "subprocess", "threading", "asyncio", "sqlite3", "io",
            "copy", "time", "random", "shutil", "glob", "tempfile",
        ],
        "java": [
            "java.util", "java.io", "java.nio", "java.lang",
            "java.math", "java.time", "java.util.stream",
            "java.util.concurrent", "java.security",
        ],
        "go": [
            "fmt", "os", "io", "strings", "strconv", "math",
            "time", "encoding/json", "net/http", "path/filepath",
            "sync", "context", "log", "crypto", "regexp", "sort",
        ],
        "rust": [
            "std::collections", "std::io", "std::fs", "std::path",
            "std::fmt", "std::sync", "std::thread", "std::time",
        ],
        "csharp": [
            "System", "System.IO", "System.Linq",
            "System.Collections.Generic", "System.Threading.Tasks",
            "System.Text.Json", "System.Net.Http",
        ],
        "javascript": [
            "fs", "path", "os", "http", "https", "url",
            "crypto", "util", "events", "stream",
        ],
        "typescript": [
            "fs", "path", "os", "http", "https", "url",
            "crypto", "util", "events", "stream",
        ],
    }

    if import_name in stdlib_modules.get(source_lang, []):
        result["notes"] = f"stdlib module in {source_lang} — manual mapping required"
        result["mapping_source"] = "stdlib"
        result["confidence"] = 0.3

    return result


def resolve_imports(import_list, source_lang, target_lang, mappings=None):
    """Resolve a list of imports.

    Returns list of resolution dicts.
    """
    if mappings is None:
        mappings = load_mappings()
    return [
        resolve_import(imp.strip(), source_lang, target_lang, mappings)
        for imp in import_list
        if imp.strip()
    ]


def get_all_domains(mappings=None):
    """List all dependency domains available in the mapping table."""
    if mappings is None:
        mappings = load_mappings()
    return list(mappings.get("domains", {}).keys())


def get_domain_coverage(source_lang, target_lang, mappings=None):
    """Calculate mapping coverage for a language pair."""
    if mappings is None:
        mappings = load_mappings()

    source_lang = _normalize_lang(source_lang)
    target_lang = _normalize_lang(target_lang)

    domains = mappings.get("domains", {})
    total = 0
    covered = 0

    for domain_name, domain_data in domains.items():
        packages = domain_data.get("packages", {})
        if source_lang in packages:
            total += 1
            if target_lang in packages:
                covered += 1

    return {
        "source_language": source_lang,
        "target_language": target_lang,
        "total_domains": total,
        "covered_domains": covered,
        "coverage_pct": round((covered / total * 100) if total > 0 else 0, 1),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description=(
            f"{CUI_BANNER}\n"
            "ICDEV Dependency Mapper — Cross-Language Package Equivalents (D246)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(f"""\
            Examples:
              python tools/translation/dependency_mapper.py \\
                --source-language python --target-language go \\
                --imports "flask,requests,sqlalchemy" --json

              python tools/translation/dependency_mapper.py \\
                --source-language java --target-language rust \\
                --coverage --json

            {CUI_BANNER}
        """),
    )
    parser.add_argument("--source-language", required=True, help="Source language")
    parser.add_argument("--target-language", required=True, help="Target language")
    parser.add_argument("--imports", help="Comma-separated list of imports to resolve")
    parser.add_argument("--coverage", action="store_true", help="Show mapping coverage")
    parser.add_argument("--list-domains", action="store_true", help="List all mapping domains")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    mappings = load_mappings()

    if args.list_domains:
        domains = get_all_domains(mappings)
        if args.json_output:
            print(json.dumps({"domains": domains, "count": len(domains)}, indent=2))
        else:
            print(f"Dependency mapping domains ({len(domains)}):")
            for d in domains:
                print(f"  - {d}")
        return

    if args.coverage:
        cov = get_domain_coverage(args.source_language, args.target_language, mappings)
        if args.json_output:
            print(json.dumps(cov, indent=2))
        else:
            print(f"Coverage {cov['source_language']} -> {cov['target_language']}: "
                  f"{cov['covered_domains']}/{cov['total_domains']} domains "
                  f"({cov['coverage_pct']}%)")
        return

    if args.imports:
        import_list = [i.strip() for i in args.imports.split(",")]
        results = resolve_imports(import_list, args.source_language, args.target_language, mappings)

        mapped = [r for r in results if r["mapping_source"] == "table"]
        unmapped = [r for r in results if r["mapping_source"] not in ("table",)]

        if args.json_output:
            print(json.dumps({
                "resolutions": results,
                "summary": {
                    "total": len(results),
                    "mapped": len(mapped),
                    "unmapped": len(unmapped),
                },
            }, indent=2))
        else:
            print(f"Resolved {len(mapped)}/{len(results)} imports:")
            for r in results:
                status = "OK" if r["mapping_source"] == "table" else "UNMAPPED"
                target = r["target_import"] or "?"
                print(f"  [{status}] {r['source_import']} -> {target} ({r['domain'] or 'unknown'})")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
