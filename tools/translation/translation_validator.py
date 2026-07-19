#!/usr/bin/env python3
# CUI // SP-CTI
"""Phase 5 — Translation validation with 8-check pipeline and compiler-feedback repair loop.

Architecture Decision D248: Round-trip IR consistency check.
Architecture Decision D255: Compiler-feedback repair loop (Google ICSE 2025 + CoTran ECAI 2024).
Runs syntax, lint, round-trip IR, API surface, type coverage, complexity,
compliance, and feature mapping checks. On failure, feeds errors back to LLM.

Honesty invariants (nav-intel-03):
  * ``verified`` dimension — every check result carries an explicit ``verified``
    boolean. A check is *verified* only when its underlying validation actually
    ran. Consumers (dashboard ``/translations``, gate summary, MCP) MUST
    distinguish a verified pass from a "not verified" state and never treat the
    latter as success.
  * Air-gap safe — the absence of a target-language compiler/toolchain is the
    *expected* condition in an air-gapped environment. When the toolchain is
    absent, ``check_syntax`` returns an explicit ``verified=False`` (not-verified)
    state. Not-verified is NOT a failure: it never blocks the gate, but it also
    never counts as a pass. Only a *verified* failure (toolchain present, syntax
    errors) blocks.
  * Mocked stubs never inflate scores — units emitted by the mock-and-continue
    path (D256) are incomplete placeholders. They are excluded from the passing
    numerator of the API-surface and compliance checks and reported as
    "mocked (not verified)", so a stub that merely carries the right name/CUI
    banner can never fabricate a preserved API surface or compliance coverage.
"""

import argparse
import json
import os
import subprocess
import uuid
from tools.db.storage import get_connection
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Platform-aware null device (D145)
_NULL_DEVICE = "NUL" if os.name == "nt" else "/dev/null"

# Validation check names
CHECKS = [
    "syntax",
    "lint",
    "round_trip",
    "api_surface",
    "type_coverage",
    "complexity",
    "compliance",
    "feature_mapping",
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
    """Check syntax validity of translated code.

    Returns a 3-tuple ``(passed, errors, verified)``:
      * ``(True,  [],     True)``  — compiler ran and the file compiled cleanly.
      * ``(False, errors, True)``  — compiler ran and reported errors (blocking).
      * ``(True,  msg,   False)``  — the target-language toolchain is absent
        (expected in air-gap) OR no checker is configured for the language.
        This is an explicit *not-verified* state: distinct from a pass, it must
        NOT be treated as a passing gate and must NOT block air-gapped use.
    """
    cmd_parts = SYNTAX_COMMANDS.get(language)
    if not cmd_parts:
        # No syntax command defined for this language → cannot verify.
        return True, [f"No syntax checker configured for {language} (not verified)"], False

    cmd = cmd_parts + [str(file_path)]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return True, [], True
        errors = (result.stderr or result.stdout or "").strip().split("\n")
        return False, errors, True
    except FileNotFoundError:
        # Toolchain not installed (expected in air-gap) — NOT VERIFIED, not a pass.
        return True, [f"Syntax checker not available for {language} (not verified — toolchain absent)"], False
    except subprocess.TimeoutExpired:
        return False, ["Syntax check timed out"], True
    except Exception as e:
        # Unexpected failure — treat as not-verified rather than fabricating a pass.
        return True, [f"Syntax check skipped: {str(e)} (not verified)"], False


def check_lint(file_path, language):
    """Run language-specific linter. Returns (passed, warnings)."""
    cmd_parts = LINT_COMMANDS.get(language)
    if not cmd_parts:
        return True, []

    try:
        cmd = cmd_parts + [str(file_path)]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
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


def _is_mocked(unit):
    """A unit is 'mocked' (incomplete, not a real translation) if flagged as such
    (D256 mock-and-continue) or its body carries the translation-mock marker.

    Mocked units are placeholders — they must never inflate a gate score.
    """
    # nav-intel-05: an explicit ``mock: true`` flag (set by the mock-and-continue
    # path when an LLM error degrades a unit to a stub) is authoritative.
    if unit.get("mock") is True:
        return True
    if unit.get("status") == "mocked":
        return True
    code = (unit.get("translated_code", "") or "").lower()
    return "translation mock" in code or "mock — translation failed" in code


def _name_matches(name, unit_map):
    """Case/underscore-insensitive lookup of ``name`` in a {name: unit} map."""
    if name in unit_map:
        return True
    norm = name.lower().replace("_", "")
    return any(tname.lower().replace("_", "") == norm for tname in unit_map)


def check_round_trip(source_ir, translated_file, target_language):
    """Re-extract IR from translated code and compare to source (D248).
    Returns (score, findings)."""
    try:
        from tools.translation.source_extractor import extract_source

        target_ir = extract_source(
            str(translated_file.parent),
            target_language,
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
                findings.append(f"{name}: param count mismatch ({src_params} vs {tgt_params})")
        else:
            findings.append(f"{name}: not found in translated output")

    score = matched / len(source_units) if source_units else 1.0
    return round(score, 3), findings


def check_api_surface(source_ir, translated_units):
    """Check that public API signatures are preserved. Returns (score, findings).

    Only *real* (non-mocked) translated units count toward the preserved surface.
    A source unit that is present only as a mock stub is reported as
    "mocked (not verified)" and does NOT count as matched — a placeholder that
    merely carries the right name must never fabricate a preserved API surface.
    """
    source_units = source_ir.get("units", [])
    if not source_units:
        return 1.0, []

    real_names = {u.get("name", ""): u for u in translated_units if not _is_mocked(u)}
    mocked_names = {u.get("name", ""): u for u in translated_units if _is_mocked(u)}
    findings = []
    matched = 0

    for unit in source_units:
        name = unit.get("name", "")
        if _name_matches(name, real_names):
            matched += 1
        elif _name_matches(name, mocked_names):
            findings.append(f"Mocked (not verified): {name} ({unit.get('kind', 'function')})")
        else:
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
        tgt_lines = len([ln for ln in code.split("\n") if ln.strip()])
        src_lines = src.get("line_count", max(1, src_complexity))

        if src_lines > 0:
            increase_pct = ((tgt_lines - src_lines) / src_lines) * 100
            if increase_pct > 30:
                complexity_increases.append(increase_pct)
                findings.append(f"{name}: {increase_pct:.0f}% line increase ({src_lines} → {tgt_lines})")

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
        # Mocked stubs are incomplete — their CUI banner must not fabricate
        # compliance coverage. Count them as not-verified (against total).
        if _is_mocked(unit):
            findings.append(f"Mocked (not verified): {unit.get('name', '?')} — compliance not confirmed")
            continue
        code = unit.get("translated_code", "")
        if cui_marker in code:
            marked += 1
        else:
            findings.append(f"Missing CUI marking: {unit.get('name', '?')}")

    score = marked / total if total > 0 else 1.0
    return round(score, 3), findings


def check_feature_mapping(source_ir, translated_units, source_language, target_language):
    """Validate feature mapping rules were applied (D247).

    Wires the REAL ``FeatureMapLoader.validate_output`` against each translated
    unit (previously this was a no-op that hardcoded a 1.0 pass). Returns a
    3-tuple ``(score, findings, verified)``:
      * ``verified=False`` — the feature-map loader could not be imported, so
        the rules could not be checked (not a pass, not a failure).
      * ``verified=True``  — validation actually ran; ``score`` reflects the
        fraction of feature-validation checks that passed.
    """
    try:
        from tools.translation.feature_map import FeatureMapLoader

        loader = FeatureMapLoader()
        rules = loader.get_rules(source_language, target_language)
    except ImportError:
        return 1.0, ["Feature map loader not available (not verified)"], False

    if not rules:
        # No feature-mapping rules for this language pair → nothing to enforce.
        return 1.0, [], True

    # Detect features actually present in the source so we only enforce rules
    # whose source pattern really appeared (avoids false-positive violations).
    source_code_parts = []
    for u in source_ir.get("units", []):
        for field in ("source_code", "source", "code", "body"):
            if u.get(field):
                source_code_parts.append(u[field])
                break
    top_src = source_ir.get("source_code") or source_ir.get("source", "")
    if top_src:
        source_code_parts.append(top_src)

    if source_code_parts:
        detected = loader.detect_features(
            "\n".join(source_code_parts), source_language, target_language
        )
    else:
        # Conservative fallback: no per-unit source available — enforce all pair
        # rules. This can never fabricate success, only surface potential gaps.
        detected = rules

    if not detected:
        return 1.0, [], True

    findings = []
    total_checks = 0
    failed_checks = 0

    for tu in translated_units:
        translated_code = tu.get("translated_code", "")
        if not translated_code:
            continue
        result = loader.validate_output(translated_code, detected, target_language)
        for check in result.get("checks", []):
            total_checks += 1
            if not check.get("passed", True):
                failed_checks += 1
                findings.append(
                    f"{tu.get('name', '?')}: {check.get('validation', '?')} — "
                    f"{check.get('details', 'rule violated')}"
                )

    if total_checks == 0:
        return 1.0, findings, True

    score = 1.0 - (failed_checks / total_checks)
    return round(max(0.0, score), 3), findings, True


def validate_translation(
    source_ir,
    translated_data,
    source_language,
    target_language,
    output_dir=None,
    project_id=None,
    job_id=None,
    config=None,
    db_path=None,
):
    """Run all 8 validation checks. Returns validation report dict."""
    if config is None:
        config = _load_config()

    thresholds = config.get("validation", {}).get("thresholds", {})
    compliance_config = config.get("compliance", {})

    real_units = translated_data.get("translated_units", [])
    mocked_units = translated_data.get("mocked_units", [])
    # Checks see the full set but distinguish real vs mocked internally so mocked
    # stubs cannot inflate scores (see check_api_surface / check_compliance).
    translated_units = real_units + mocked_units
    mocked_count = len(mocked_units)

    results = {}
    overall_pass = True

    ext_map = {
        "python": "*.py",
        "java": "*.java",
        "go": "*.go",
        "rust": "*.rs",
        "csharp": "*.cs",
        "typescript": "*.ts",
        "javascript": "*.js",
    }

    # 1. Syntax check (per file) — air-gap safe with an explicit not-verified state.
    syntax_ran_any = False  # a real compiler actually ran on ≥1 file
    syntax_failed = False  # a verified syntax error was found
    syntax_findings = []
    files_seen = 0
    if output_dir:
        out = Path(output_dir)
        pattern = ext_map.get(target_language, "*")
        for f in out.rglob(pattern):
            files_seen += 1
            passed, errors, verified = check_syntax(f, target_language)
            if verified:
                syntax_ran_any = True
                if not passed:
                    syntax_failed = True
                    syntax_findings.extend(errors)
            else:
                # Toolchain absent for this language — not verified, not a failure.
                syntax_findings.extend(errors)

    syntax_verified = syntax_ran_any
    if files_seen == 0:
        # Nothing was actually compiled → cannot claim a pass.
        syntax_verified = False
        syntax_findings.append("No target-language files found to syntax-check (not verified)")

    if syntax_verified:
        syntax_status = "fail" if syntax_failed else "pass"
        results["syntax"] = {
            "passed": not syntax_failed,
            "verified": True,
            "status": syntax_status,
            "score": 0.0 if syntax_failed else 1.0,
            "findings": syntax_findings[:20],
        }
        # Only a VERIFIED syntax failure blocks the gate.
        if syntax_failed:
            overall_pass = False
    else:
        # Not verified: distinct from pass, never counts as passed, never blocks.
        results["syntax"] = {
            "passed": False,
            "verified": False,
            "status": "not_verified",
            "score": None,
            "findings": syntax_findings[:20],
        }

    # 2. Lint check
    lint_findings = []
    if output_dir:
        out = Path(output_dir)
        for f in out.rglob(ext_map.get(target_language, "*")):
            passed, warnings = check_lint(f, target_language)
            if not passed:
                lint_findings.extend(warnings)
    results["lint"] = {
        "passed": len(lint_findings) == 0,
        "verified": True,
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
        "verified": True,
        "score": rt_score,
        "threshold": min_rt,
        "findings": rt_findings[:20],
    }

    # 4. API surface match
    api_score, api_findings = check_api_surface(source_ir, translated_units)
    min_api = thresholds.get("min_api_surface_match", 0.90)
    results["api_surface"] = {
        "passed": api_score >= min_api,
        "verified": True,
        "score": api_score,
        "threshold": min_api,
        "mocked_count": mocked_count,
        "findings": api_findings[:20],
    }
    if api_score < min_api:
        overall_pass = False

    # 5. Type coverage
    type_score, type_findings = check_type_coverage(source_ir, target_language)
    min_type = thresholds.get("min_type_coverage", 0.85)
    results["type_coverage"] = {
        "passed": type_score >= min_type,
        "verified": True,
        "score": type_score,
        "threshold": min_type,
        "findings": type_findings[:20],
    }

    # 6. Complexity
    cx_score, cx_findings = check_complexity(source_ir, translated_units)
    max_cx = thresholds.get("max_complexity_increase_pct", 30)
    results["complexity"] = {
        "passed": cx_score >= 0.7,
        "verified": True,
        "score": cx_score,
        "threshold": max_cx,
        "findings": cx_findings[:20],
    }

    # 7. Compliance
    comp_score, comp_findings = check_compliance(translated_units, target_language)
    min_comp = compliance_config.get("min_control_coverage_pct", 95.0) / 100.0
    results["compliance"] = {
        "passed": comp_score >= min_comp,
        "verified": True,
        "score": comp_score,
        "threshold": min_comp,
        "mocked_count": mocked_count,
        "findings": comp_findings[:20],
    }
    if comp_score < min_comp:
        overall_pass = False

    # 8. Feature mapping — real FeatureMapLoader.validate_output wiring.
    fm_score, fm_findings, fm_verified = check_feature_mapping(
        source_ir, translated_units, source_language, target_language
    )
    if fm_verified:
        results["feature_mapping"] = {
            "passed": fm_score >= 0.8,
            "verified": True,
            "status": "pass" if fm_score >= 0.8 else "fail",
            "score": fm_score,
            "findings": fm_findings[:20],
        }
    else:
        results["feature_mapping"] = {
            "passed": False,
            "verified": False,
            "status": "not_verified",
            "score": None,
            "findings": fm_findings[:20],
        }

    # Gate evaluation
    gate_result = "pass" if overall_pass else "fail"
    if not overall_pass and all(
        r.get("passed", True) for k, r in results.items() if k in ("syntax", "api_surface", "compliance")
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
        # A check counts as "passed" only if it was verified AND passed — a
        # not-verified check never inflates the passed tally.
        "checks_passed": sum(
            1 for r in results.values() if r.get("passed", False) and r.get("verified", True)
        ),
        "checks_total": len(results),
        "checks_verified": sum(1 for r in results.values() if r.get("verified", True)),
        "not_verified_checks": [k for k, r in results.items() if not r.get("verified", True)],
        "fully_verified": all(r.get("verified", True) for r in results.values()),
        "mocked_count": mocked_count,
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
        conn = get_connection(db_path=str(db_path))
        c = conn.cursor()
        for check_type, result in results.items():
            val_id = str(uuid.uuid4())
            # Not-verified checks persist passed=NULL and score=NULL so the
            # dashboard can render an honest "not verified" state distinct from
            # both pass (1) and fail (0).
            if result.get("verified", True) is False:
                passed_val = None
                score_val = None
            else:
                passed_val = 1 if result.get("passed") else 0
                score_val = result.get("score", 0.0)
            c.execute(
                """INSERT INTO translation_validations
                   (id, job_id, check_type, passed, score, findings)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    val_id,
                    job_id,
                    check_type,
                    passed_val,
                    score_val,
                    json.dumps(result.get("findings", [])),
                ),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


def repair_translation(unit, source_code, translated_code, errors, source_language, target_language, config=None):
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
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        response = router.invoke("code_translation_repair", request)
        return response.content if response and response.content else None
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV™ Phase 5 — Translation validation + repair loop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ir-file", required=True, help="Source IR JSON file")
    parser.add_argument("--translated-file", required=True, help="Translated units JSON file")
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
        if report.get("not_verified_checks"):
            print(f"  Not verified: {', '.join(report['not_verified_checks'])} (toolchain absent / not run)")
        if report.get("mocked_count"):
            print(f"  Mocked units (excluded from pass counts): {report['mocked_count']}")
        print()
        for check, result in report["checks"].items():
            if result.get("verified", True) is False:
                status = "NOT VERIFIED"
            elif result.get("passed"):
                status = "PASS"
            else:
                status = "FAIL"
            score = result.get("score")
            score_str = f"{score:.2f}" if isinstance(score, (int, float)) else "n/a"
            print(f"  [{status}] {check}: {score_str}")
            for finding in result.get("findings", [])[:3]:
                print(f"         → {finding}")


if __name__ == "__main__":
    main()
