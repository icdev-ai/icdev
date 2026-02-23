#!/usr/bin/env python3
# CUI // SP-CTI
"""Test file translation — translate test files between frameworks.

Architecture Decision D250: Test translation as separate tool.
BDD .feature files copied unchanged; only step definitions translated.
Framework mappings: pytest↔JUnit5↔testing↔cargo_test↔xUnit↔Jest."""

import argparse
import json
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Test framework mappings
FRAMEWORK_MAPPINGS = {
    "python": "pytest",
    "java": "junit5",
    "go": "testing",
    "rust": "cargo_test",
    "csharp": "xunit",
    "typescript": "jest",
    "javascript": "jest",
}

BDD_MAPPINGS = {
    "python": "behave",
    "java": "cucumber",
    "go": "godog",
    "rust": "cucumber-rs",
    "csharp": "specflow",
    "typescript": "cucumber-js",
    "javascript": "cucumber-js",
}

# Common assertion mappings per source→target framework
ASSERTION_MAPPINGS = {
    ("pytest", "junit5"): [
        {"source": "assert x == y", "target": "assertEquals(y, x)"},
        {"source": "assert x != y", "target": "assertNotEquals(y, x)"},
        {"source": "assert x is None", "target": "assertNull(x)"},
        {"source": "assert x is not None", "target": "assertNotNull(x)"},
        {"source": "assert x is True", "target": "assertTrue(x)"},
        {"source": "assert x is False", "target": "assertFalse(x)"},
        {"source": "with pytest.raises(E)", "target": "assertThrows(E.class, () -> { ... })"},
        {"source": "assert x in y", "target": "assertTrue(y.contains(x))"},
    ],
    ("pytest", "testing"): [
        {"source": "assert x == y", "target": "if x != y { t.Errorf(...) }"},
        {"source": "assert x is None", "target": "if x != nil { t.Errorf(...) }"},
        {"source": "with pytest.raises(E)", "target": "defer func() { recover() }()"},
    ],
    ("pytest", "jest"): [
        {"source": "assert x == y", "target": "expect(x).toBe(y)"},
        {"source": "assert x != y", "target": "expect(x).not.toBe(y)"},
        {"source": "assert x is None", "target": "expect(x).toBeNull()"},
        {"source": "assert x is True", "target": "expect(x).toBeTruthy()"},
        {"source": "with pytest.raises(E)", "target": "expect(() => { ... }).toThrow(E)"},
        {"source": "assert x in y", "target": "expect(y).toContain(x)"},
    ],
    ("pytest", "xunit"): [
        {"source": "assert x == y", "target": "Assert.Equal(y, x)"},
        {"source": "assert x != y", "target": "Assert.NotEqual(y, x)"},
        {"source": "assert x is None", "target": "Assert.Null(x)"},
        {"source": "assert x is True", "target": "Assert.True(x)"},
        {"source": "with pytest.raises(E)", "target": "Assert.Throws<E>(() => { ... })"},
    ],
    ("pytest", "cargo_test"): [
        {"source": "assert x == y", "target": "assert_eq!(x, y)"},
        {"source": "assert x != y", "target": "assert_ne!(x, y)"},
        {"source": "assert x is True", "target": "assert!(x)"},
        {"source": "with pytest.raises(E)", "target": "#[should_panic]"},
    ],
}

# Test file patterns per language
TEST_PATTERNS = {
    "python": ["test_*.py", "*_test.py"],
    "java": ["*Test.java", "*Tests.java"],
    "go": ["*_test.go"],
    "rust": ["*.rs"],  # tests module or tests/ dir
    "csharp": ["*Test.cs", "*Tests.cs"],
    "typescript": ["*.test.ts", "*.spec.ts"],
    "javascript": ["*.test.js", "*.spec.js"],
}

# BDD file patterns
BDD_PATTERNS = {
    "python": ["steps/*.py"],
    "java": ["steps/*.java", "stepdefs/*.java"],
    "go": ["*_test.go"],  # godog uses _test.go
    "csharp": ["Steps/*.cs"],
    "typescript": ["steps/*.ts"],
    "javascript": ["steps/*.js"],
}


def _get_assertion_mappings(source_lang, target_lang):
    """Get assertion mappings for the given language pair."""
    src_fw = FRAMEWORK_MAPPINGS.get(source_lang, "pytest")
    tgt_fw = FRAMEWORK_MAPPINGS.get(target_lang, "jest")
    return ASSERTION_MAPPINGS.get((src_fw, tgt_fw), [])


def _build_test_prompt(source_test_code, source_language, target_language,
                       ir_data=None, bdd_mode=False):
    """Build the test translation prompt."""
    prompt_path = BASE_DIR / "hardprompts" / "translation" / "test_translation.md"

    src_fw = FRAMEWORK_MAPPINGS.get(source_language, "unknown")
    tgt_fw = FRAMEWORK_MAPPINGS.get(target_language, "unknown")
    assertions = _get_assertion_mappings(source_language, target_language)

    if prompt_path.exists():
        template = prompt_path.read_text(encoding="utf-8")
    else:
        template = (
            "Translate these {source_language} tests ({source_framework}) "
            "to {target_language} ({target_framework}):\n\n{source_test_code}"
        )

    from tools.translation.code_translator import CUI_HEADERS, NAMING_CONVENTIONS, PROVENANCE_TEMPLATES

    cui_header = CUI_HEADERS.get(target_language, "// CUI // SP-CTI")
    provenance = PROVENANCE_TEMPLATES.get(target_language, "// Translated by ICDEV").format(
        source_lang=source_language
    )

    replacements = {
        "{{ source_language }}": source_language,
        "{{ target_language }}": target_language,
        "{{ source_framework }}": src_fw,
        "{{ target_framework }}": tgt_fw,
        "{{ source_test_code }}": source_test_code,
        "{{ production_ir }}": json.dumps(ir_data, indent=2) if ir_data else "Not available.",
        "{{ translated_signatures }}": "",
        "{{ dependency_mappings }}": "",
        "{{ target_naming }}": NAMING_CONVENTIONS.get(target_language, "camelCase"),
        "{{ cui_header }}": cui_header,
        "{{ provenance_comment }}": provenance,
        "{{ source_bdd_framework }}": BDD_MAPPINGS.get(source_language, "unknown"),
        "{{ target_bdd_framework }}": BDD_MAPPINGS.get(target_language, "unknown"),
    }

    prompt = template
    for key, value in replacements.items():
        prompt = prompt.replace(key, str(value))

    # Handle assertion mappings block
    if "{% for mapping in assertion_mappings %}" in prompt:
        start = prompt.index("{% for mapping in assertion_mappings %}")
        end = prompt.index("{% endfor %}", start) + len("{% endfor %}")
        assertion_text = ""
        for a in assertions:
            assertion_text += f"- `{a['source']}` → `{a['target']}`\n"
        prompt = prompt[:start] + assertion_text + prompt[end:]

    # Handle BDD block
    if "{% if bdd_mode %}" in prompt:
        if_start = prompt.index("{% if bdd_mode %}")
        endif_idx = prompt.index("{% endif %}", if_start) + len("{% endif %}")
        if bdd_mode:
            block = prompt[if_start:endif_idx]
            block = block.replace("{% if bdd_mode %}", "").replace("{% endif %}", "")
            prompt = prompt[:if_start] + block + prompt[endif_idx:]
        else:
            prompt = prompt[:if_start] + prompt[endif_idx:]

    return prompt


def translate_tests(source_test_dir, source_language, target_language,
                    output_dir, ir_data=None, project_id=None, job_id=None):
    """Translate test files from source to target language.

    Returns dict with translated_tests, copied_features, stats.
    """
    src_dir = Path(source_test_dir)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    translated_tests = []
    copied_features = []
    failed_tests = []

    # 1. Copy BDD .feature files unchanged (D250)
    for feature_file in src_dir.rglob("*.feature"):
        rel = feature_file.relative_to(src_dir)
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(feature_file, dest)
        copied_features.append(str(rel))

    # 2. Find and translate test files
    patterns = TEST_PATTERNS.get(source_language, [])
    bdd_patterns = BDD_PATTERNS.get(source_language, [])

    ext_map = {
        "python": ".py", "java": ".java", "go": ".go",
        "rust": ".rs", "csharp": ".cs",
        "typescript": ".ts", "javascript": ".js",
    }
    target_ext = ext_map.get(target_language, ".txt")

    for pattern in patterns:
        for test_file in src_dir.rglob(pattern):
            source_code = test_file.read_text(encoding="utf-8")

            # Determine if this is a BDD step definition
            is_bdd = any(
                test_file.match(bp) for bp in bdd_patterns
            )

            # Build prompt
            prompt = _build_test_prompt(
                source_code, source_language, target_language,
                ir_data=ir_data, bdd_mode=is_bdd,
            )

            # Invoke LLM
            translated = None
            try:
                from tools.translation.code_translator import _invoke_llm, _load_config
                config = _load_config()
                translated = _invoke_llm(prompt, config, "test_translation")
            except Exception:
                pass

            if translated and translated.strip():
                # Write translated test file
                rel = test_file.relative_to(src_dir)
                dest_name = rel.stem + target_ext
                if is_bdd:
                    dest = out_dir / "steps" / dest_name
                else:
                    dest = out_dir / dest_name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(translated.strip(), encoding="utf-8")

                translated_tests.append({
                    "source_file": str(rel),
                    "target_file": str(dest.relative_to(out_dir)),
                    "is_bdd": is_bdd,
                    "status": "translated",
                })
            else:
                failed_tests.append({
                    "source_file": str(test_file.relative_to(src_dir)),
                    "status": "failed",
                    "error": "LLM returned empty response",
                })

    # Audit trail
    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="translation.unit_translated",
            actor="test_translator",
            action=f"Translated {len(translated_tests)} test files from "
                   f"{source_language} to {target_language}",
            project_id=project_id,
            details={
                "translated_count": len(translated_tests),
                "failed_count": len(failed_tests),
                "feature_files_copied": len(copied_features),
            },
        )
    except Exception:
        pass

    return {
        "translated_tests": translated_tests,
        "copied_features": copied_features,
        "failed_tests": failed_tests,
        "stats": {
            "translated_count": len(translated_tests),
            "failed_count": len(failed_tests),
            "feature_files_copied": len(copied_features),
            "source_framework": FRAMEWORK_MAPPINGS.get(source_language, "unknown"),
            "target_framework": FRAMEWORK_MAPPINGS.get(target_language, "unknown"),
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Phase 43 — Test file translation (D250)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source-test-dir", required=True,
                        help="Directory containing source test files")
    parser.add_argument("--source-language", required=True, help="Source language")
    parser.add_argument("--target-language", required=True, help="Target language")
    parser.add_argument("--output-dir", required=True, help="Output directory for translated tests")
    parser.add_argument("--ir-file", help="Source IR JSON for context")
    parser.add_argument("--project-id", help="Project ID")
    parser.add_argument("--job-id", help="Translation job ID")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    ir_data = None
    if args.ir_file:
        ir_path = Path(args.ir_file)
        if ir_path.exists():
            with open(ir_path, "r", encoding="utf-8") as f:
                ir_data = json.load(f)

    result = translate_tests(
        source_test_dir=args.source_test_dir,
        source_language=args.source_language,
        target_language=args.target_language,
        output_dir=args.output_dir,
        ir_data=ir_data,
        project_id=args.project_id,
        job_id=args.job_id,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        stats = result["stats"]
        print(f"Test Translation: {args.source_language} → {args.target_language}")
        print(f"  Framework:  {stats['source_framework']} → {stats['target_framework']}")
        print(f"  Translated: {stats['translated_count']}")
        print(f"  Failed:     {stats['failed_count']}")
        print(f"  Features:   {stats['feature_files_copied']} (copied unchanged)")


if __name__ == "__main__":
    main()
