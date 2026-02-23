#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D -- Authorized DoD Personnel Only
# POC: ICDEV System Administrator
"""Feature Map Loader — ICDEV Cross-Language Translation (Phase 43, D247)

Loads and applies 3-part feature mapping rules adopted from Amazon Oxidizer
research (PLDI 2025). Each rule has:
  (a) syntactic pattern — regex for detection in source code
  (b) NL description — injected into LLM prompt for translation guidance
  (c) static validation — check expression for verifying translated output

Feature maps are stored in args/translation_config.yaml under feature_maps.
"""

import json
import re
import textwrap
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = BASE_DIR / "args" / "translation_config.yaml"

CUI_BANNER = "CUI // SP-CTI"

# ---------------------------------------------------------------------------
# Built-in feature maps (fallback when config not available)
# ---------------------------------------------------------------------------
BUILTIN_FEATURE_MAPS = {
    "python_to_java": [
        {
            "id": "py_list_comprehension",
            "pattern": r"\[.*\bfor\b.*\bin\b.*\]",
            "description": "Python list comprehension — translate to Java Stream .map().collect(Collectors.toList()) or explicit for-loop.",
            "validation": "no_list_comprehension_syntax",
        },
        {
            "id": "py_context_manager",
            "pattern": r"\bwith\b\s+\w+.*\bas\b",
            "description": "Python context manager (with/as) — translate to Java try-with-resources (AutoCloseable).",
            "validation": "uses_try_with_resources_or_finally",
        },
        {
            "id": "py_generator",
            "pattern": r"\byield\b",
            "description": "Python generator function — translate to Java Iterator/Stream or custom Iterable.",
            "validation": "no_yield_keyword",
        },
        {
            "id": "py_decorator",
            "pattern": r"@\w+",
            "description": "Python decorator — translate to Java annotation or wrapper pattern (AOP, proxy).",
            "validation": "no_at_decorator_syntax",
        },
        {
            "id": "py_multiple_return",
            "pattern": r"return\s+\w+\s*,\s*\w+",
            "description": "Python multiple return values (tuple) — translate to Java record, Pair, or custom DTO.",
            "validation": "single_return_type",
        },
        {
            "id": "py_dict_comprehension",
            "pattern": r"\{.*:\s*.*\bfor\b.*\bin\b.*\}",
            "description": "Python dict comprehension — translate to Java Stream .collect(Collectors.toMap()).",
            "validation": "no_dict_comprehension_syntax",
        },
    ],
    "python_to_go": [
        {
            "id": "py_exception_handling",
            "pattern": r"\btry\b.*\bexcept\b",
            "description": "Python try/except — translate to Go multi-value return (val, err) pattern. Check err != nil after each call.",
            "validation": "uses_error_return_pattern",
        },
        {
            "id": "py_class",
            "pattern": r"\bclass\b\s+\w+",
            "description": "Python class — translate to Go struct with methods (receiver functions). Use interfaces for polymorphism.",
            "validation": "uses_struct_not_class",
        },
        {
            "id": "py_async",
            "pattern": r"\basync\s+def\b|\bawait\b",
            "description": "Python async/await — translate to Go goroutines with channels or sync.WaitGroup.",
            "validation": "uses_goroutines_or_channels",
        },
    ],
    "python_to_rust": [
        {
            "id": "py_none_check",
            "pattern": r"\bis\s+None\b|\bis\s+not\s+None\b",
            "description": "Python None checks — translate to Rust Option<T> with .is_some()/.is_none() or pattern matching.",
            "validation": "uses_option_type",
        },
        {
            "id": "py_mutable_list",
            "pattern": r"\w+\.append\(|\w+\.extend\(",
            "description": "Python mutable list operations — translate to Rust Vec<T> with .push() or .extend(). Ensure proper ownership/borrowing.",
            "validation": "uses_vec_type",
        },
        {
            "id": "py_string_format",
            "pattern": r'f".*\{.*\}"',
            "description": "Python f-string — translate to Rust format!() macro.",
            "validation": "uses_format_macro",
        },
    ],
    "java_to_python": [
        {
            "id": "java_optional",
            "pattern": r"Optional<\w+>",
            "description": "Java Optional<T> — translate to Python Optional[T] with None checks or pattern matching.",
            "validation": "no_optional_wrapper",
        },
        {
            "id": "java_stream",
            "pattern": r"\.stream\(\)\.(?:map|filter|collect|reduce)",
            "description": "Java Stream API — translate to Python list comprehension, generator expression, or itertools.",
            "validation": "uses_pythonic_iteration",
        },
    ],
    "java_to_go": [
        {
            "id": "java_class_hierarchy",
            "pattern": r"\bextends\b|\bimplements\b",
            "description": "Java class hierarchy — translate to Go struct embedding (extends) and interface satisfaction (implements). Go uses implicit interface implementation.",
            "validation": "no_extends_implements",
        },
    ],
    "go_to_rust": [
        {
            "id": "go_goroutine",
            "pattern": r"\bgo\s+\w+\(",
            "description": "Go goroutine — translate to Rust tokio::spawn() or std::thread::spawn() with proper ownership transfer.",
            "validation": "uses_spawn_or_async",
        },
        {
            "id": "go_channel",
            "pattern": r"\bmake\(chan\b|<-\s*\w+|\w+\s*<-",
            "description": "Go channel — translate to Rust tokio::sync::mpsc or std::sync::mpsc channels.",
            "validation": "uses_mpsc_channel",
        },
    ],
    "csharp_to_java": [
        {
            "id": "cs_linq",
            "pattern": r"\.Where\(|\.Select\(|\.OrderBy\(|\.GroupBy\(",
            "description": "C# LINQ — translate to Java Stream API with .filter(), .map(), .sorted(), .collect(groupingBy()).",
            "validation": "uses_stream_api",
        },
        {
            "id": "cs_async_await",
            "pattern": r"\basync\b\s+Task|\bawait\b",
            "description": "C# async/await — translate to Java CompletableFuture or reactive streams (Project Reactor).",
            "validation": "uses_completable_future",
        },
    ],
    "typescript_to_python": [
        {
            "id": "ts_interface",
            "pattern": r"\binterface\b\s+\w+",
            "description": "TypeScript interface — translate to Python Protocol (typing) or ABC. Prefer Protocol for structural typing.",
            "validation": "uses_protocol_or_abc",
        },
    ],
}


class FeatureMapLoader:
    """Load and apply 3-part feature mapping rules (D247)."""

    def __init__(self, config_path=None):
        self.config_path = config_path or CONFIG_PATH
        self._maps = {}
        self._load_maps()

    def _load_maps(self):
        """Load feature maps from config, falling back to built-in."""
        try:
            import yaml

            if self.config_path.exists():
                with open(self.config_path) as f:
                    cfg = yaml.safe_load(f) or {}
                self._maps = cfg.get("feature_maps", {})
        except ImportError:
            pass

        # Merge built-in maps for any missing pairs
        for pair_key, rules in BUILTIN_FEATURE_MAPS.items():
            if pair_key not in self._maps:
                self._maps[pair_key] = rules

    def get_pair_key(self, source_lang, target_lang):
        """Normalize language pair to lookup key."""
        src = source_lang.lower().replace("javascript", "typescript")
        tgt = target_lang.lower().replace("javascript", "typescript")
        return f"{src}_to_{tgt}"

    def get_rules(self, source_lang, target_lang):
        """Get feature mapping rules for a language pair."""
        key = self.get_pair_key(source_lang, target_lang)
        return self._maps.get(key, [])

    def detect_features(self, source_code, source_lang, target_lang):
        """Detect which features are present in source code.

        Returns list of matched rule dicts with 'matched_lines' added.
        """
        rules = self.get_rules(source_lang, target_lang)
        matched = []
        lines = source_code.split("\n")

        for rule in rules:
            pattern = rule.get("pattern", "")
            if not pattern:
                continue
            try:
                regex = re.compile(pattern)
            except re.error:
                continue

            matched_lines = []
            for i, line in enumerate(lines, 1):
                if regex.search(line):
                    matched_lines.append(i)

            if matched_lines:
                match_entry = dict(rule)
                match_entry["matched_lines"] = matched_lines
                match_entry["match_count"] = len(matched_lines)
                matched.append(match_entry)

        return matched

    def generate_prompt_context(self, detected_features):
        """Generate LLM prompt context from detected features.

        Returns a formatted string to inject into translation prompts.
        """
        if not detected_features:
            return ""

        lines = ["## Language-Specific Feature Guidance\n"]
        lines.append(
            "The following source language patterns were detected. "
            "Apply these translation rules:\n"
        )
        for feat in detected_features:
            lines.append(f"- **{feat.get('id', 'unknown')}** ({feat.get('match_count', 0)} occurrences):")
            lines.append(f"  {feat.get('description', 'No guidance available.')}")
            lines.append("")

        return "\n".join(lines)

    def validate_output(self, translated_code, detected_features, target_lang):
        """Validate translated output against feature validation checks.

        Returns dict with 'passed', 'failed', 'checks' lists.
        """
        results = {"passed": [], "failed": [], "checks": []}

        for feat in detected_features:
            validation = feat.get("validation", "")
            if not validation:
                continue

            check = {
                "rule_id": feat.get("id", "unknown"),
                "validation": validation,
                "passed": True,
                "details": "",
            }

            # Basic validation checks (extensible)
            if validation == "no_list_comprehension_syntax":
                if re.search(r"\[.*\bfor\b.*\bin\b.*\]", translated_code):
                    check["passed"] = False
                    check["details"] = "List comprehension syntax found in target code"
            elif validation == "uses_try_with_resources_or_finally":
                if not re.search(r"\btry\s*\(|finally\s*\{", translated_code):
                    check["passed"] = False
                    check["details"] = "Missing try-with-resources or finally block"
            elif validation == "no_yield_keyword":
                if re.search(r"\byield\b", translated_code):
                    check["passed"] = False
                    check["details"] = "yield keyword found in target code"
            elif validation == "uses_error_return_pattern":
                if target_lang.lower() == "go" and not re.search(r"\berr\b\s*!=\s*nil", translated_code):
                    check["passed"] = False
                    check["details"] = "Missing Go error return pattern (err != nil)"
            elif validation == "uses_struct_not_class":
                if re.search(r"\bclass\b", translated_code):
                    check["passed"] = False
                    check["details"] = "class keyword found in Go target code"
            elif validation == "uses_option_type":
                if target_lang.lower() == "rust" and not re.search(r"Option<", translated_code):
                    check["passed"] = False
                    check["details"] = "Missing Rust Option<T> for None handling"
            elif validation == "uses_vec_type":
                if target_lang.lower() == "rust" and not re.search(r"Vec<", translated_code):
                    check["passed"] = False
                    check["details"] = "Missing Rust Vec<T> for list operations"
            elif validation == "uses_format_macro":
                if target_lang.lower() == "rust" and not re.search(r"format!\(", translated_code):
                    check["passed"] = False
                    check["details"] = "Missing Rust format!() macro"

            results["checks"].append(check)
            if check["passed"]:
                results["passed"].append(check)
            else:
                results["failed"].append(check)

        return results

    def list_supported_pairs(self):
        """List all language pairs with feature mapping rules."""
        return list(self._maps.keys())

    def to_dict(self):
        """Return all feature maps as dict."""
        return dict(self._maps)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description=f"{CUI_BANNER}\nICDEV Feature Map Loader (D247)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source-language", help="Source language")
    parser.add_argument("--target-language", help="Target language")
    parser.add_argument("--source-file", help="Source file to scan for features")
    parser.add_argument("--list-pairs", action="store_true", help="List supported pairs")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    loader = FeatureMapLoader()

    if args.list_pairs:
        pairs = loader.list_supported_pairs()
        if args.json_output:
            print(json.dumps({"supported_pairs": pairs, "count": len(pairs)}, indent=2))
        else:
            print(f"Supported feature map pairs ({len(pairs)}):")
            for p in pairs:
                print(f"  - {p}")
        return

    if args.source_file and args.source_language and args.target_language:
        source_path = Path(args.source_file)
        if not source_path.exists():
            print(f"[ERROR] File not found: {source_path}", file=sys.stderr)
            sys.exit(1)
        source_code = source_path.read_text(encoding="utf-8", errors="replace")
        detected = loader.detect_features(source_code, args.source_language, args.target_language)
        if args.json_output:
            print(json.dumps({
                "source_file": str(source_path),
                "source_language": args.source_language,
                "target_language": args.target_language,
                "detected_features": detected,
                "count": len(detected),
                "prompt_context": loader.generate_prompt_context(detected),
            }, indent=2))
        else:
            print(f"Detected {len(detected)} features in {source_path}:")
            for feat in detected:
                print(f"  [{feat['id']}] {feat['match_count']} occurrences — {feat['description'][:80]}")
        return

    if args.source_language and args.target_language:
        rules = loader.get_rules(args.source_language, args.target_language)
        if args.json_output:
            print(json.dumps({"rules": rules, "count": len(rules)}, indent=2))
        else:
            print(f"Feature rules for {args.source_language} -> {args.target_language}: {len(rules)}")
            for r in rules:
                print(f"  [{r['id']}] {r['description'][:80]}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
