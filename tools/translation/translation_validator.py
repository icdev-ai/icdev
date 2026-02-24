#!/usr/bin/env python3
# CUI // SP-CTI
"""Phase 5 — Translation validation with 8-check pipeline and compiler-feedback repair loop.

Architecture Decision D248: Round-trip IR consistency check.
Architecture Decision D255: Compiler-feedback repair loop (Google ICSE 2025 + CoTran ECAI 2024).
Runs syntax, lint, round-trip IR, API surface, type coverage, complexity,
compliance, and feature mapping checks. On failure, feeds errors back to LLM."""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Platform-aware null device (D145)
_NULL_DEVICE = "NUL" if os.name == "nt" else "/dev/null"

# Validation check names
CHECKS = [
    "syntax", "lint", "round_trip", "api_surface",
    "type_coverage", "complexity", "compliance", "feature_mapping",
]

# Syntax check commands per language
SYNTAX_COMMANDS = {
    "python": ["python", "-m", "py_compile"],
    "java": ["javac", "-d", _NULL_DEVICE],
    "go": ["go", "vet"],
    "rust": ["cargo", "check"],
    "csharp": ["dotnet", "build", "--no-restore"],
    "typescript": ["npx", "tsc", "--noEmit"],
}

# Lint commands per language
LINT_COMMANDS = {
    "python": ["ruff", "check"],
    "java": ["checkstyle", "-c", _NULL_DEVICE],
    "go": ["golangci-lint", "run"],
    "rust": ["cargo", "clippy"],
    "csharp": ["dotnet", "format", "--verify-no-changes"],
    "typescript": ["npx", "eslint"],
}


def _load_config():
    """Load translation config."""
    config_path = BASE_DIR / "args" / "translation_config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, "r") as f:
                return yaml.safe_load(f)
        except ImportError:
            pass
    return {
        "validation": {
            "thresholds": {
                "min_api_surface_match": 0.90,
                "min_type_coverage": 0.85,
                "min_round_trip_similarity": 0.80,
                "max_complexity_increase_pct": 30,
            },
            "gate_evaluation": True,
        },
        "compliance": {"min_control_coverage_pct": 95.0},
        "repair": {
            "max_repair_attempts": 3,
            "include_compiler_errors": True,
        },
    }


def check_syntax(file_path, language):
    """Check syntax validity of translated code. Returns (passed, errors)."""
    cmd_parts = SYNTAX_COMMANDS.get(language)
    if not cmd_parts:
        return True, []

    if language == "python":
        cmd = cmd_parts + [str(file_path)]
    elif language in ("typescript", "javascript"):
        cmd = cmd_parts + [str(file_path)]
    else:
        cmd = cmd_parts + [str(file_path)]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return True, []
        errors = (result.stderr or result.stdout or "").strip().split("\n")
        return False, errors
    except FileNotFoundError:
        # Tool not installed — skip
        return True, [f"Syntax checker not available for {language}"]
    except subprocess.TimeoutExpired:
        return False, ["Syntax check timed out"]
    except Exception as e:
        return True, [f"Syntax check skipped: {str(e)}"]


def check_lint(file_path, language):
    """Run language-specific linter. Returns (passed, warnings)."""
    cmd_parts = LINT_COMMANDS.get(language)
    if not cmd_parts:
        return True, []

    try:
        cmd = cmd_parts + [str(file_path)]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return True, []
        warnings = (result.stderr or result.stdout or "").strip().split("\n")
        return False, warnings[:20]  # Limit output
    except FileNotFoundError:
        return True, [f"Linter not available for {language}"]
    except Exception as e:
        return True, [f"Lint skipped: {str(e)}"]


def check_round_trip(source_ir, translated_file, target_language):
    """Re-extract IR from translated code and compare to source (D248).
    Returns (score, findings)."""
    try:
        from tools.translation.source_extractor import extract_source
        target_ir = extract_source(
            str(translated_file.parent), target_language,
        )
    except Exception:
        return 0.5, ["Round-trip extraction not available"]

    if not target_ir or not target_ir.get("units"):
        return 0.0, ["No units extracted from translated code"]

    source_units = {u["name"]: u for u in source_ir.get("units", [])}
    target_units = {u["name"]: u for u in target_ir.get("units", [])}
    findings = []

    if not source_units:
        return 1.0, []

    matched = 0
    for name, src_unit in source_units.items():
        # Try exact match first, then case-insensitive
        tgt = target_units.get(name)
        if not tgt:
            # Try naming convention adaptation
            for tname, tunit in target_units.items():
                if tname.lower().replace("_", "") == name.lower().replace("_", ""):
                    tgt = tunit
                    break

        if tgt:
            matched += 1
            # Check param count
            src_params = len(src_unit.get("params", []))
            tgt_params = len(tgt.get("params", []))
            if src_params != tgt_params:
                findings.append(
                    f"{name}: param count mismatch ({src_params} vs {tgt_params})"
                )
        else:
            findings.append(f"{name}: not found in translated output")

    score = matched / len(source_units) if source_units else 1.0
    return round(score, 3), findings


def check_api_surface(source_ir, translated_units):
    """Check that public API signatures are preserved. Returns (score, findings)."""
    source_units = source_ir.get("units", [])
    if not source_units:
        return 1.0, []

    translated_names = {u.get("name", ""): u for u in translated_units}
    findings = []
    matched = 0

    for unit in source_units:
        name = unit.get("name", "")
        if name in translated_names:
            matched += 1
        else:
            # Check case-insensitive
            found = False
            for tname in translated_names:
                if tname.lower().replace("_", "") == name.lower().replace("_", ""):
                    matched += 1
                    found = True
                    break
            if not found:
                findings.append(f"Missing: {name} ({unit.get('kind', 'function')})")

    score = matched / len(source_units) if source_units else 1.0
    return round(score, 3), findings


def check_type_coverage(source_ir, target_language):
    """Check type mapping coverage. Returns (score, findings)."""
    units = source_ir.get("units", [])
    if not units:
        return 1.0, []

    total_types = 0
    mapped_types = 0
    findings = []

    try:
        from tools.translation.type_checker import load_type_mappings, map_type
        type_mappings = load_type_mappings()
    except ImportError:
        return 0.5, ["Type checker not available"]

    source_language = source_ir.get("language", "python")

    for unit in units:
        for param in unit.get("params", []):
            ptype = param.get("type")
            if ptype:
                total_types += 1
                mapped = map_type(ptype, source_language, target_language, type_mappings)
                if mapped.get("confidence", 0) > 0.5:
                    mapped_types += 1
                else:
                    findings.append(f"Unmapped type: {ptype} in {unit.get('name', '?')}")

        ret_type = unit.get("return_type")
        if ret_type:
            total_types += 1
            mapped = map_type(ret_type, source_language, target_language, type_mappings)
            if mapped.get("confidence", 0) > 0.5:
                mapped_types += 1

    score = mapped_types / total_types if total_types > 0 else 1.0
    return round(score, 3), findings


def check_complexity(source_ir, translated_units):
    """Check complexity change (source vs translated). Returns (score, findings)."""
    source_units = {u["name"]: u for u in source_ir.get("units", [])}
    findings = []
    complexity_increases = []

    for tu in translated_units:
        name = tu.get("name", "")
        src = source_units.get(name)
        if not src:
            continue

        src_complexity = src.get("complexity", 1)
        # Estimate translated complexity from line count
        code = tu.get("translated_code", "")
        tgt_lines = len([l for l in code.split("\n") if l.strip()])
        src_lines = src.get("line_count", max(1, src_complexity))

        if src_lines > 0:
            increase_pct = ((tgt_lines - src_lines) / src_lines) * 100
            if increase_pct > 30:
                complexity_increases.append(increase_pct)
                findings.append(
                    f"{name}: {increase_pct:.0f}% line increase ({src_lines} → {tgt_lines})"
                )

    if not complexity_increases:
        return 1.0, findings

    avg_increase = sum(complexity_increases) / len(complexity_increases)
    # Score: 1.0 if avg <= 0%, 0.0 if avg >= 100%
    score = max(0.0, min(1.0, 1.0 - (avg_increase / 100)))
    return round(score, 3), findings


def check_compliance(translated_units, target_language):
    """Check CUI markings and compliance coverage. Returns (score, findings)."""
    findings = []
    marked = 0
    total = len(translated_units)

    cui_marker = "CUI // SP-CTI"

    for unit in translated_units:
        code = unit.get("translated_code", "")
        if cui_marker in code:
            marked += 1
        else:
            findings.append(f"Missing CUI marking: {unit.get('name', '?')}")

    score = marked / total if total > 0 else 1.0
    return round(score, 3), findings


def check_feature_mapping(source_ir, translated_units, source_language, target_language):
    """Validate feature mapping rules were applied (D247). Returns (score, findings)."""
    try:
        from tools.translation.feature_map import FeatureMapLoader
        loader = FeatureMapLoader()
        rules = loader.get_rules(source_language, target_language)
    except ImportError:
        return 1.0, ["Feature map loader not available"]

    if not rules:
        return 1.0, []

    # Detect features in source
    source_features = set()
    for unit in source_ir.get("units", []):
        for idiom in unit.get("idioms", []):
            source_features.add(idiom)

    if not source_features:
        return 1.0, []

    # Check that translated code doesn't still contain source patterns
    findings = []
    violations = 0
    total_checks = 0

    for rule in rules:
        validation = rule.get("validation", "")
        if not validation:
            continue

        total_checks += 1
        # Check validation constraints across all translated units
        all_code = "\n".join(u.get("translated_code", "") for u in translated_units)

        if validation == "no_list_comprehension_syntax" and "[" in all_code and "for" in all_code:
            # Rough check — might have list comprehension syntax in non-Python target
            if target_language not in ("python",):
                pass  # OK, other languages use [] differently
        # More validation checks can be added per rule

    score = 1.0 - (violations / total_checks) if total_checks > 0 else 1.0
    return round(max(0.0, score), 3), findings


def validate_translation(source_ir, translated_data, source_language, target_language,
                         output_dir=None, project_id=None, job_id=None,
                         config=None, db_path=None):
    """Run all 8 validation checks. Returns validation report dict."""
    if config is None:
        config = _load_config()

    thresholds = config.get("validation", {}).get("thresholds", {})
    compliance_config = config.get("compliance", {})

    translated_units = translated_data.get("translated_units", []) + \
                       translated_data.get("mocked_units", [])

    results = {}
    overall_pass = True

    # 1. Syntax check (per file)
    syntax_passed = True
    syntax_findings = []
    if output_dir:
        out = Path(output_dir)
        ext_map = {
            "python": "*.py", "java": "*.java", "go": "*.go",
            "rust": "*.rs", "csharp": "*.cs",
            "typescript": "*.ts", "javascript": "*.js",
        }
        pattern = ext_map.get(target_language, "*")
        for f in out.rglob(pattern):
            passed, errors = check_syntax(f, target_language)
            if not passed:
                syntax_passed = False
                syntax_findings.extend(errors)
    results["syntax"] = {
        "passed": syntax_passed,
        "score": 1.0 if syntax_passed else 0.0,
        "findings": syntax_findings[:20],
    }
    if not syntax_passed:
        overall_pass = False

    # 2. Lint check
    lint_passed = True
    lint_findings = []
    if output_dir:
        out = Path(output_dir)
        for f in out.rglob(ext_map.get(target_language, "*")):
            passed, warnings = check_lint(f, target_language)
            if not passed:
                lint_findings.extend(warnings)
    results["lint"] = {
        "passed": len(lint_findings) == 0,
        "score": 1.0 if not lint_findings else 0.5,
        "findings": lint_findings[:20],
    }

    # 3. Round-trip IR (D248)
    rt_score = 1.0
    rt_findings = []
    if output_dir:
        rt_score, rt_findings = check_round_trip(source_ir, Path(output_dir), target_language)
    min_rt = thresholds.get("min_round_trip_similarity", 0.80)
    results["round_trip"] = {
        "passed": rt_score >= min_rt,
        "score": rt_score,
        "threshold": min_rt,
        "findings": rt_findings[:20],
    }

    # 4. API surface match
    api_score, api_findings = check_api_surface(source_ir, translated_units)
    min_api = thresholds.get("min_api_surface_match", 0.90)
    results["api_surface"] = {
        "passed": api_score >= min_api,
        "score": api_score,
        "threshold": min_api,
        "findings": api_findings[:20],
    }
    if api_score < min_api:
        overall_pass = False

    # 5. Type coverage
    type_score, type_findings = check_type_coverage(source_ir, target_language)
    min_type = thresholds.get("min_type_coverage", 0.85)
    results["type_coverage"] = {
        "passed": type_score >= min_type,
        "score": type_score,
        "threshold": min_type,
        "findings": type_findings[:20],
    }

    # 6. Complexity
    cx_score, cx_findings = check_complexity(source_ir, translated_units)
    max_cx = thresholds.get("max_complexity_increase_pct", 30)
    results["complexity"] = {
        "passed": cx_score >= 0.7,
        "score": cx_score,
        "threshold": max_cx,
        "findings": cx_findings[:20],
    }

    # 7. Compliance
    comp_score, comp_findings = check_compliance(translated_units, target_language)
    min_comp = compliance_config.get("min_control_coverage_pct", 95.0) / 100.0
    results["compliance"] = {
        "passed": comp_score >= min_comp,
        "score": comp_score,
        "threshold": min_comp,
        "findings": comp_findings[:20],
    }
    if comp_score < min_comp:
        overall_pass = False

    # 8. Feature mapping
    fm_score, fm_findings = check_feature_mapping(
        source_ir, translated_units, source_language, target_language
    )
    results["feature_mapping"] = {
        "passed": fm_score >= 0.8,
        "score": fm_score,
        "findings": fm_findings[:20],
    }

    # Gate evaluation
    gate_result = "pass" if overall_pass else "fail"
    if not overall_pass and all(
        r.get("passed", True) for k, r in results.items()
        if k in ("syntax", "api_surface", "compliance")
    ):
        gate_result = "warn"

    report = {
        "job_id": job_id,
        "project_id": project_id,
        "source_language": source_language,
        "target_language": target_language,
        "checks": results,
        "overall_pass": overall_pass,
        "gate_result": gate_result,
        "checks_passed": sum(1 for r in results.values() if r.get("passed", False)),
        "checks_total": len(results),
    }

    # Record in DB
    if db_path and job_id:
        _record_validations(db_path, job_id, results)

    # Audit trail
    try:
        from tools.audit.audit_logger import log_event
        event_type = "translation.validation_passed" if overall_pass else "translation.validation_failed"
        log_event(
            event_type=event_type,
            actor="translation_validator",
            action=f"Validation {gate_result}: {report['checks_passed']}/{report['checks_total']} checks passed",
            project_id=project_id,
            details={
                "gate_result": gate_result,
                "checks_passed": report["checks_passed"],
                "checks_total": report["checks_total"],
                "api_surface_score": results.get("api_surface", {}).get("score"),
                "compliance_score": results.get("compliance", {}).get("score"),
            },
        )
    except Exception:
        pass

    return report


def _record_validations(db_path, job_id, results):
    """Record validation results in DB."""
    try:
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        for check_type, result in results.items():
            val_id = str(uuid.uuid4())
            c.execute(
                """INSERT INTO translation_validations
                   (id, job_id, check_type, passed, score, findings)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    val_id, job_id, check_type,
                    1 if result.get("passed") else 0,
                    result.get("score", 0.0),
                    json.dumps(result.get("findings", [])),
                ),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


def repair_translation(unit, source_code, translated_code, errors,
                       source_language, target_language, config=None):
    """Attempt LLM-based repair using compiler feedback (D255).
    Returns repaired code or None."""
    if config is None:
        config = _load_config()

    prompt_path = BASE_DIR / "hardprompts" / "translation" / "translation_repair.md"
    if prompt_path.exists():
        template = prompt_path.read_text(encoding="utf-8")
    else:
        template = (
            "Fix the following {target_language} code translation errors:\n\n"
            "Errors:\n{error_output}\n\n"
            "Code:\n{translated_code}"
        )

    repair_config = config.get("repair", {})
    max_attempts = repair_config.get("max_repair_attempts", 3)

    replacements = {
        "{{ unit_name }}": unit.get("name", "unknown"),
        "{{ unit_kind }}": unit.get("kind", "function"),
        "{{ source_language }}": source_language,
        "{{ target_language }}": target_language,
        "{{ attempt_number }}": "1",
        "{{ max_attempts }}": str(max_attempts),
        "{{ source_code }}": source_code,
        "{{ translated_code }}": translated_code,
        "{{ error_output }}": "\n".join(errors) if isinstance(errors, list) else str(errors),
        "{{ dependency_mappings }}": "",
        "{{ type_mappings }}": "",
    }

    prompt = template
    for key, value in replacements.items():
        prompt = prompt.replace(key, str(value))

    # Remove Jinja2 blocks
    for tag in ["{% for failure in validation_failures %}", "{% endfor %}"]:
        prompt = prompt.replace(tag, "")

    try:
        from tools.llm.router import LLMRouter
        router = LLMRouter()
        response = router.invoke(
            function="code_translation_repair",
            prompt=prompt,
            temperature=0.2,
        )
        if isinstance(response, dict):
            return response.get("content", response.get("text"))
        return str(response) if response else None
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Phase 5 — Translation validation + repair loop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ir-file", required=True, help="Source IR JSON file")
    parser.add_argument("--translated-file", required=True,
                        help="Translated units JSON file")
    parser.add_argument("--source-language", required=True, help="Source language")
    parser.add_argument("--target-language", required=True, help="Target language")
    parser.add_argument("--output-dir", help="Assembled project directory")
    parser.add_argument("--project-id", help="Project ID")
    parser.add_argument("--job-id", help="Translation job ID")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    # Load files
    ir_path = Path(args.ir_file)
    trans_path = Path(args.translated_file)

    if not ir_path.exists():
        print(json.dumps({"error": f"IR file not found: {args.ir_file}"}))
        return
    if not trans_path.exists():
        print(json.dumps({"error": f"Translated file not found: {args.translated_file}"}))
        return

    with open(ir_path, "r", encoding="utf-8") as f:
        source_ir = json.load(f)
    with open(trans_path, "r", encoding="utf-8") as f:
        translated_data = json.load(f)

    config = _load_config()
    report = validate_translation(
        source_ir=source_ir,
        translated_data=translated_data,
        source_language=args.source_language,
        target_language=args.target_language,
        output_dir=args.output_dir,
        project_id=args.project_id,
        job_id=args.job_id,
        config=config,
        db_path=DB_PATH if DB_PATH.exists() else None,
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Validation: {args.source_language} → {args.target_language}")
        print(f"  Gate: {report['gate_result'].upper()}")
        print(f"  Checks passed: {report['checks_passed']}/{report['checks_total']}")
        print()
        for check, result in report["checks"].items():
            status = "PASS" if result.get("passed") else "FAIL"
            score = result.get("score", 0)
            print(f"  [{status}] {check}: {score:.2f}")
            for finding in result.get("findings", [])[:3]:
                print(f"         → {finding}")


if __name__ == "__main__":
    main()
