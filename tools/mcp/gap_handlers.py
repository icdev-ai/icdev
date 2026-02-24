#!/usr/bin/env python3
# CUI // SP-CTI
"""Gap handlers — MCP tool handlers for CLI tools not yet exposed via MCP.

These 55 handler functions bridge the gap between existing CLI tools and
the unified MCP gateway server.  Each handler follows one of two patterns:

  Pattern A (preferred): Direct Python import when tool has a clean API.
  Pattern B (fallback):  Subprocess wrapper invoking CLI with --json flag.

All handlers accept args: dict and return dict (JSON-serializable).
Organized by category matching the tool_registry.py categories.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

logger = logging.getLogger("mcp.gap_handlers")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_cli(script_path: str, cli_args: list = None, timeout: int = 300) -> dict:
    """Run a CLI tool as subprocess with --json output.

    Args:
        script_path: Relative path from BASE_DIR (e.g. "tools/testing/production_audit.py").
        cli_args: Additional CLI arguments.
        timeout: Subprocess timeout in seconds.

    Returns:
        Parsed JSON dict on success, or {"error": ...} on failure.
    """
    cmd = [sys.executable, str(BASE_DIR / script_path), "--json"]
    if cli_args:
        cmd.extend(cli_args)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError:
                return {"output": proc.stdout[:4000], "returncode": 0}
        return {
            "error": proc.stderr[:2000] if proc.stderr else f"Exit code {proc.returncode}",
            "stdout": proc.stdout[:2000] if proc.stdout else "",
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Timeout after {timeout}s", "script": script_path}
    except FileNotFoundError:
        return {"error": f"Script not found: {script_path}"}
    except Exception as exc:
        return {"error": str(exc)}


# ===========================================================================
# Category: translation (Phase 43 — Cross-Language Translation)
# ===========================================================================

def handle_translate_code(args: dict) -> dict:
    """Full 5-phase translation pipeline."""
    cli_args = []
    for flag, key in [
        ("--source-path", "source_path"), ("--source-language", "source_language"),
        ("--target-language", "target_language"), ("--output-dir", "output_dir"),
        ("--project-id", "project_id"), ("--candidates", "candidates"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    if args.get("validate"):
        cli_args.append("--validate")
    if args.get("dry_run"):
        cli_args.append("--dry-run")
    if args.get("compliance_bridge"):
        cli_args.append("--compliance-bridge")
    return _run_cli("tools/translation/translation_manager.py", cli_args, timeout=600)


def handle_extract_source_ir(args: dict) -> dict:
    """Phase 1: Extract source code to language-agnostic IR."""
    cli_args = []
    for flag, key in [
        ("--source-path", "source_path"), ("--language", "language"),
        ("--output-ir", "output_ir"), ("--project-id", "project_id"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/translation/source_extractor.py", cli_args)


def handle_translate_unit(args: dict) -> dict:
    """Phase 3: LLM-based code translation from IR."""
    cli_args = []
    for flag, key in [
        ("--ir-file", "ir_file"), ("--source-language", "source_language"),
        ("--target-language", "target_language"), ("--output-dir", "output_dir"),
        ("--candidates", "candidates"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/translation/code_translator.py", cli_args, timeout=600)


def handle_map_dependencies(args: dict) -> dict:
    """Cross-language dependency equivalence lookup."""
    cli_args = []
    for flag, key in [
        ("--source-language", "source_language"), ("--target-language", "target_language"),
        ("--imports", "imports"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/translation/dependency_mapper.py", cli_args, timeout=60)


def handle_check_types(args: dict) -> dict:
    """Phase 2: Type system compatibility pre-check."""
    cli_args = []
    for flag, key in [
        ("--ir-file", "ir_file"), ("--source-language", "source_language"),
        ("--target-language", "target_language"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/translation/type_checker.py", cli_args)


def handle_assemble_project(args: dict) -> dict:
    """Phase 4: Assemble translated units into project structure."""
    cli_args = []
    for flag, key in [
        ("--ir-list", "ir_list"), ("--output-dir", "output_dir"),
        ("--project-template", "project_template"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/translation/project_assembler.py", cli_args)


def handle_validate_translation(args: dict) -> dict:
    """Phase 5: Validate translated output + repair loop."""
    cli_args = []
    for flag, key in [
        ("--output-dir", "output_dir"), ("--source-language", "source_language"),
        ("--target-language", "target_language"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/translation/translation_validator.py", cli_args)


def handle_translate_tests(args: dict) -> dict:
    """Translate test suites across languages."""
    cli_args = []
    for flag, key in [
        ("--source-test-dir", "source_test_dir"), ("--source-language", "source_language"),
        ("--target-language", "target_language"), ("--output-dir", "output_dir"),
        ("--ir-file", "ir_file"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/translation/test_translator.py", cli_args, timeout=600)


def handle_map_features(args: dict) -> dict:
    """Feature mapping rules lookup for language pairs."""
    cli_args = []
    if args.get("rule_path"):
        cli_args.extend(["--rule-path", args["rule_path"]])
    if args.get("validate"):
        cli_args.append("--validate")
    return _run_cli("tools/translation/feature_map.py", cli_args, timeout=60)


# ===========================================================================
# Category: dx (Phase 34 — Universal AI Companion)
# ===========================================================================

def handle_companion_setup(args: dict) -> dict:
    """Auto-detect AI tools and generate instruction files + MCP configs."""
    cli_args = ["--setup"]
    if args.get("all_platforms"):
        cli_args.append("--all")
    if args.get("platforms"):
        cli_args.extend(["--platforms", args["platforms"]])
    if args.get("write"):
        cli_args.append("--write")
    if args.get("dry_run"):
        cli_args.append("--dry-run")
    return _run_cli("tools/dx/companion.py", cli_args)


def handle_detect_ai_tools(args: dict) -> dict:
    """Detect installed AI coding tools from environment."""
    return _run_cli("tools/dx/tool_detector.py", [], timeout=30)


def handle_generate_instructions(args: dict) -> dict:
    """Generate instruction files for AI coding tool platforms."""
    cli_args = []
    if args.get("all_platforms"):
        cli_args.append("--all")
    if args.get("platform"):
        cli_args.extend(["--platform", args["platform"]])
    if args.get("write"):
        cli_args.append("--write")
    return _run_cli("tools/dx/instruction_generator.py", cli_args)


def handle_generate_mcp_configs(args: dict) -> dict:
    """Generate MCP config files for AI coding tool platforms."""
    cli_args = []
    if args.get("all_platforms"):
        cli_args.append("--all")
    if args.get("platform"):
        cli_args.extend(["--platform", args["platform"]])
    if args.get("write"):
        cli_args.append("--write")
    return _run_cli("tools/dx/mcp_config_generator.py", cli_args)


def handle_translate_skills(args: dict) -> dict:
    """Translate Claude Code skills to other AI tool formats."""
    cli_args = []
    if args.get("all_platforms"):
        cli_args.append("--all")
    if args.get("platform"):
        cli_args.extend(["--platform", args["platform"]])
    if args.get("skills"):
        cli_args.extend(["--skills", args["skills"]])
    if args.get("write"):
        cli_args.append("--write")
    if args.get("list_skills"):
        cli_args.append("--list")
    return _run_cli("tools/dx/skill_translator.py", cli_args)


# ===========================================================================
# Category: cloud (Phase 38 — Cloud-Agnostic Architecture)
# ===========================================================================

def handle_csp_monitor_scan(args: dict) -> dict:
    """Scan CSP services for updates and changes."""
    cli_args = ["--scan"]
    if args.get("all_csps"):
        cli_args.append("--all")
    if args.get("csp"):
        cli_args.extend(["--csp", args["csp"]])
    return _run_cli("tools/cloud/csp_monitor.py", cli_args, timeout=120)


def handle_csp_changelog(args: dict) -> dict:
    """Generate CSP changelog with recommendations."""
    cli_args = ["--generate"]
    if args.get("days"):
        cli_args.extend(["--days", str(args["days"])])
    if args.get("summary_only"):
        cli_args = ["--summary"]
    return _run_cli("tools/cloud/csp_changelog.py", cli_args)


def handle_validate_region(args: dict) -> dict:
    """Validate CSP region compliance certifications."""
    cli_args = []
    action = args.get("action", "validate")
    cli_args.append(action)
    if args.get("csp"):
        cli_args.extend(["--csp", args["csp"]])
    if args.get("region"):
        cli_args.extend(["--region", args["region"]])
    if args.get("frameworks"):
        cli_args.extend(["--frameworks", args["frameworks"]])
    if args.get("impact_level"):
        cli_args.extend(["--impact-level", args["impact_level"]])
    return _run_cli("tools/cloud/region_validator.py", cli_args)


def handle_cloud_mode_status(args: dict) -> dict:
    """Check current cloud mode and configuration."""
    cli_args = []
    action = args.get("action", "status")
    cli_args.append(f"--{action}")
    return _run_cli("tools/cloud/cloud_mode_manager.py", cli_args, timeout=30)


def handle_csp_health_check(args: dict) -> dict:
    """Check health of all configured CSP services."""
    return _run_cli("tools/cloud/csp_health_checker.py", ["--check"], timeout=60)


# ===========================================================================
# Category: registry (Phase 36 — Evolutionary Intelligence)
# ===========================================================================

def handle_register_child(args: dict) -> dict:
    """Register a child application in the registry."""
    cli_args = ["--register"]
    if args.get("name"):
        cli_args.extend(["--name", args["name"]])
    if args.get("type"):
        cli_args.extend(["--type", args["type"]])
    return _run_cli("tools/registry/child_registry.py", cli_args)


def handle_list_children(args: dict) -> dict:
    """List all registered child applications."""
    return _run_cli("tools/registry/child_registry.py", ["--list"])


def handle_get_genome(args: dict) -> dict:
    """Get current capability genome version."""
    cli_args = []
    if args.get("history"):
        cli_args.append("--history")
    else:
        cli_args.append("--get")
    return _run_cli("tools/registry/genome_manager.py", cli_args)


def handle_evaluate_capability(args: dict) -> dict:
    """Evaluate a capability across 6 dimensions."""
    cli_args = ["--evaluate"]
    if args.get("data"):
        cli_args.extend(["--data", json.dumps(args["data"]) if isinstance(args["data"], dict) else args["data"]])
    return _run_cli("tools/registry/capability_evaluator.py", cli_args)


def handle_list_staging(args: dict) -> dict:
    """List capability staging environments."""
    return _run_cli("tools/registry/staging_manager.py", ["--list"])


def handle_list_propagations(args: dict) -> dict:
    """List capability propagation log."""
    return _run_cli("tools/registry/propagation_manager.py", ["--list"])


def handle_absorption_candidates(args: dict) -> dict:
    """Get capabilities ready for genome absorption."""
    return _run_cli("tools/registry/absorption_engine.py", ["--candidates"])


def handle_unevaluated_behaviors(args: dict) -> dict:
    """Get unevaluated learned behaviors from children."""
    return _run_cli("tools/registry/learning_collector.py", ["--unevaluated"])


def handle_cross_pollination_candidates(args: dict) -> dict:
    """Find cross-pollination candidates between children."""
    return _run_cli("tools/registry/cross_pollinator.py", ["--candidates"])


# ===========================================================================
# Category: security_agentic (Phase 45 — OWASP Agentic AI Security)
# ===========================================================================

def handle_scan_code_patterns(args: dict) -> dict:
    """Scan for dangerous code patterns across 6 languages."""
    try:
        from tools.security.code_pattern_scanner import CodePatternScanner
        scanner = CodePatternScanner()
        project_dir = args.get("project_dir")
        if project_dir:
            results = scanner.scan_directory(project_dir)
            if args.get("gate"):
                gate = scanner.evaluate_gate(results)
                return {"scan_results": results, "gate": gate}
            return results
        return {"error": "Provide 'project_dir'"}
    except ImportError:
        cli_args = []
        if args.get("project_dir"):
            cli_args.extend(["--project-dir", args["project_dir"]])
        if args.get("gate"):
            cli_args.append("--gate")
        return _run_cli("tools/security/code_pattern_scanner.py", cli_args)


def handle_validate_tool_chain(args: dict) -> dict:
    """Validate tool call chain against security rules."""
    cli_args = []
    if args.get("rules"):
        cli_args.append("--rules")
    if args.get("gate"):
        cli_args.append("--gate")
    if args.get("project_id"):
        cli_args.extend(["--project-id", args["project_id"]])
    return _run_cli("tools/security/tool_chain_validator.py", cli_args)


def handle_validate_agent_output(args: dict) -> dict:
    """Validate agent output for classification leaks and PII."""
    cli_args = []
    if args.get("text"):
        cli_args.extend(["--text", args["text"]])
    if args.get("gate"):
        cli_args.append("--gate")
    if args.get("project_id"):
        cli_args.extend(["--project-id", args["project_id"]])
    return _run_cli("tools/security/agent_output_validator.py", cli_args)


def handle_score_agent_trust(args: dict) -> dict:
    """Compute or check agent trust score."""
    cli_args = []
    if args.get("agent_id"):
        cli_args.extend(["--agent-id", args["agent_id"]])
    if args.get("score"):
        cli_args.append("--score")
    elif args.get("check"):
        cli_args.append("--check")
    elif args.get("all_agents"):
        cli_args.append("--all")
    if args.get("gate"):
        cli_args.append("--gate")
    if args.get("project_id"):
        cli_args.extend(["--project-id", args["project_id"]])
    return _run_cli("tools/security/agent_trust_scorer.py", cli_args)


def handle_check_mcp_authorization(args: dict) -> dict:
    """Check MCP per-tool RBAC authorization."""
    cli_args = []
    if args.get("check"):
        cli_args.append("--check")
    if args.get("list_permissions"):
        cli_args.append("--list")
    if args.get("validate"):
        cli_args.append("--validate")
    if args.get("role"):
        cli_args.extend(["--role", args["role"]])
    if args.get("tool"):
        cli_args.extend(["--tool", args["tool"]])
    return _run_cli("tools/security/mcp_tool_authorizer.py", cli_args)


def handle_ai_telemetry_summary(args: dict) -> dict:
    """Get AI usage telemetry summary."""
    cli_args = ["--summary"]
    return _run_cli("tools/security/ai_telemetry_logger.py", cli_args)


def handle_generate_ai_bom(args: dict) -> dict:
    """Generate AI Bill of Materials."""
    cli_args = []
    if args.get("project_id"):
        cli_args.extend(["--project-id", args["project_id"]])
    if args.get("project_dir"):
        cli_args.extend(["--project-dir", args["project_dir"]])
    if args.get("gate"):
        cli_args.append("--gate")
    return _run_cli("tools/security/ai_bom_generator.py", cli_args)


def handle_run_atlas_red_team(args: dict) -> dict:
    """Run ATLAS red teaming tests (opt-in)."""
    cli_args = []
    if args.get("project_id"):
        cli_args.extend(["--project-id", args["project_id"]])
    if args.get("technique"):
        cli_args.extend(["--technique", args["technique"]])
    if args.get("behavioral"):
        cli_args.append("--behavioral")
    if args.get("brt_technique"):
        cli_args.extend(["--brt-technique", args["brt_technique"]])
    return _run_cli("tools/security/atlas_red_team.py", cli_args)


def handle_detect_behavioral_drift(args: dict) -> dict:
    """Detect behavioral drift in agent telemetry."""
    cli_args = ["--drift"]
    if args.get("agent_id"):
        cli_args.extend(["--agent-id", args["agent_id"]])
    return _run_cli("tools/security/ai_telemetry_logger.py", cli_args)


# ===========================================================================
# Category: testing (Production Gates & Validation)
# ===========================================================================

def handle_production_audit(args: dict) -> dict:
    """Run 30-check production readiness audit."""
    cli_args = []
    if args.get("category"):
        cli_args.extend(["--category", args["category"]])
    if args.get("gate"):
        cli_args.append("--gate")
    return _run_cli("tools/testing/production_audit.py", cli_args, timeout=300)


def handle_production_remediate(args: dict) -> dict:
    """Auto-fix production audit blockers."""
    cli_args = []
    if args.get("auto"):
        cli_args.append("--auto")
    if args.get("dry_run"):
        cli_args.append("--dry-run")
    if args.get("check_id"):
        cli_args.extend(["--check-id", args["check_id"]])
    if args.get("skip_audit"):
        cli_args.append("--skip-audit")
    return _run_cli("tools/testing/production_remediate.py", cli_args, timeout=300)


def handle_validate_claude_dir(args: dict) -> dict:
    """Validate .claude directory governance alignment."""
    return _run_cli("tools/testing/claude_dir_validator.py", [], timeout=60)


def handle_health_check(args: dict) -> dict:
    """Run full system health check."""
    return _run_cli("tools/testing/health_check.py", [], timeout=60)


def handle_validate_screenshot(args: dict) -> dict:
    """Vision LLM screenshot validation."""
    cli_args = []
    if args.get("check"):
        cli_args.append("--check")
    if args.get("image"):
        cli_args.extend(["--image", args["image"]])
    if args.get("assertion"):
        cli_args.extend(["--assert", args["assertion"]])
    if args.get("batch_dir"):
        cli_args.extend(["--batch-dir", args["batch_dir"]])
    return _run_cli("tools/testing/screenshot_validator.py", cli_args, timeout=120)


def handle_run_e2e_tests(args: dict) -> dict:
    """Run E2E tests via Playwright."""
    cli_args = []
    if args.get("discover"):
        cli_args.append("--discover")
    if args.get("run_all"):
        cli_args.append("--run-all")
    if args.get("test_file"):
        cli_args.extend(["--test-file", args["test_file"]])
    if args.get("validate_screenshots"):
        cli_args.append("--validate-screenshots")
    return _run_cli("tools/testing/e2e_runner.py", cli_args, timeout=600)


# ===========================================================================
# Category: installer (Phase 33 — Modular Installation)
# ===========================================================================

def handle_install_modules(args: dict) -> dict:
    """Run modular installer."""
    cli_args = []
    if args.get("profile"):
        cli_args.extend(["--profile", args["profile"]])
    if args.get("compliance"):
        cli_args.extend(["--compliance", args["compliance"]])
    if args.get("platform"):
        cli_args.extend(["--platform", args["platform"]])
    if args.get("add_module"):
        cli_args.extend(["--add-module", args["add_module"]])
    if args.get("status"):
        cli_args.append("--status")
    if args.get("upgrade"):
        cli_args.append("--upgrade")
    return _run_cli("tools/installer/installer.py", cli_args)


def handle_validate_module_registry(args: dict) -> dict:
    """Validate module dependency resolution."""
    return _run_cli("tools/installer/module_registry.py", ["--validate"])


def handle_list_compliance_postures(args: dict) -> dict:
    """List available compliance posture configurations."""
    return _run_cli("tools/installer/compliance_configurator.py", ["--list-postures"])


def handle_generate_platform_artifacts(args: dict) -> dict:
    """Generate platform deployment artifacts."""
    cli_args = ["--generate"]
    if args.get("target"):
        cli_args.append(args["target"])
    if args.get("modules"):
        cli_args.extend(["--modules", args["modules"]])
    if args.get("output"):
        cli_args.extend(["--output", args["output"]])
    return _run_cli("tools/installer/platform_setup.py", cli_args)


# ===========================================================================
# Category: misc (Various uncategorized gaps)
# ===========================================================================

def handle_register_external_patterns(args: dict) -> dict:
    """Register external framework analysis as innovation signals."""
    cli_args = []
    if args.get("source"):
        cli_args.extend(["--source", args["source"]])
    if args.get("pattern_file"):
        cli_args.extend(["--pattern-file", args["pattern_file"]])
    return _run_cli("tools/security/code_pattern_scanner.py", cli_args)


def handle_analyze_legacy_ui(args: dict) -> dict:
    """Analyze legacy UI screenshots for modernization."""
    cli_args = []
    if args.get("image"):
        cli_args.extend(["--image", args["image"]])
    if args.get("image_dir"):
        cli_args.extend(["--image-dir", args["image_dir"]])
    if args.get("app_id"):
        cli_args.extend(["--app-id", args["app_id"]])
    if args.get("project_id"):
        cli_args.extend(["--project-id", args["project_id"]])
    if args.get("store"):
        cli_args.append("--store")
    if args.get("score_only"):
        cli_args.append("--score-only")
    return _run_cli("tools/modernization/ui_analyzer.py", cli_args, timeout=120)


def handle_generate_profile_md(args: dict) -> dict:
    """Generate PROFILE.md from dev profile."""
    cli_args = []
    if args.get("scope"):
        cli_args.extend(["--scope", args["scope"]])
    if args.get("scope_id"):
        cli_args.extend(["--scope-id", args["scope_id"]])
    if args.get("output"):
        cli_args.extend(["--output", args["output"]])
    if args.get("store"):
        cli_args.append("--store")
    return _run_cli("tools/builder/profile_md_generator.py", cli_args)


def handle_generate_claude_md(args: dict) -> dict:
    """Generate dynamic CLAUDE.md for child applications."""
    cli_args = []
    if args.get("blueprint"):
        cli_args.extend(["--blueprint", args["blueprint"]])
    if args.get("output"):
        cli_args.extend(["--output", args["output"]])
    return _run_cli("tools/builder/claude_md_generator.py", cli_args)


def handle_version_migrate(args: dict) -> dict:
    """Migrate code between language versions."""
    cli_args = []
    for flag, key in [
        ("--source", "source"), ("--output", "output"),
        ("--language", "language"), ("--from", "from_version"),
        ("--to", "to_version"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/modernization/version_migrator.py", cli_args, timeout=300)


def handle_framework_migrate(args: dict) -> dict:
    """Migrate between application frameworks."""
    cli_args = []
    for flag, key in [
        ("--source", "source"), ("--output", "output"),
        ("--from", "from_framework"), ("--to", "to_framework"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/modernization/framework_migrator.py", cli_args, timeout=300)


def handle_worktree_manage(args: dict) -> dict:
    """Manage git worktrees for parallel CI/CD."""
    cli_args = []
    if args.get("create"):
        cli_args.append("--create")
    if args.get("list"):
        cli_args.append("--list")
    if args.get("cleanup"):
        cli_args.append("--cleanup")
    if args.get("status"):
        cli_args.append("--status")
    if args.get("task_id"):
        cli_args.extend(["--task-id", args["task_id"]])
    if args.get("target_dir"):
        cli_args.extend(["--target-dir", args["target_dir"]])
    if args.get("worktree_name"):
        cli_args.extend(["--worktree-name", args["worktree_name"]])
    return _run_cli("tools/ci/modules/worktree.py", cli_args)


def handle_nlq_query(args: dict) -> dict:
    """Execute natural language compliance query."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        # Simple passthrough — NLQ requires LLM which is not invoked here.
        # Return available tables for the user to formulate queries.
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row["name"] for row in cursor.fetchall()]
        conn.close()
        return {
            "status": "info",
            "message": "NLQ queries require the dashboard (/query page) or direct SQL. Available tables listed.",
            "tables": tables,
            "table_count": len(tables),
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# AI Transparency & Accountability (Phase 48, D307-D315) — 10 tools
# ---------------------------------------------------------------------------

def handle_omb_m25_21_assess(args: dict) -> dict:
    """OMB M-25-21 High-Impact AI assessment."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from omb_m25_21_assessor import OMBM2521Assessor
        assessor = OMBM2521Assessor(db_path=DB_PATH)
        return assessor.assess(args["project_id"], project_dir=args.get("project_dir"))
    except Exception as exc:
        return {"error": str(exc)}


def handle_omb_m26_04_assess(args: dict) -> dict:
    """OMB M-26-04 Unbiased AI assessment."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from omb_m26_04_assessor import OMBM2604Assessor
        assessor = OMBM2604Assessor(db_path=DB_PATH)
        return assessor.assess(args["project_id"], project_dir=args.get("project_dir"))
    except Exception as exc:
        return {"error": str(exc)}


def handle_nist_ai_600_1_assess(args: dict) -> dict:
    """NIST AI 600-1 GenAI Profile assessment."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from nist_ai_600_1_assessor import NISTAI6001Assessor
        assessor = NISTAI6001Assessor(db_path=DB_PATH)
        return assessor.assess(args["project_id"], project_dir=args.get("project_dir"))
    except Exception as exc:
        return {"error": str(exc)}


def handle_gao_ai_assess(args: dict) -> dict:
    """GAO-21-519SP AI Accountability assessment."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from gao_ai_assessor import GAOAIAssessor
        assessor = GAOAIAssessor(db_path=DB_PATH)
        return assessor.assess(args["project_id"], project_dir=args.get("project_dir"))
    except Exception as exc:
        return {"error": str(exc)}


def handle_model_card_generate(args: dict) -> dict:
    """Generate model card per OMB M-26-04 / Google Model Cards format."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from model_card_generator import generate_model_card
        return generate_model_card(args["project_id"], args["model_name"], db_path=DB_PATH)
    except Exception as exc:
        return {"error": str(exc)}


def handle_system_card_generate(args: dict) -> dict:
    """Generate system-level AI card."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from system_card_generator import generate_system_card
        return generate_system_card(args["project_id"], db_path=DB_PATH)
    except Exception as exc:
        return {"error": str(exc)}


def handle_ai_transparency_audit(args: dict) -> dict:
    """Run cross-framework AI transparency audit."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from ai_transparency_audit import run_transparency_audit
        return run_transparency_audit(args["project_id"], args.get("project_dir"), db_path=DB_PATH)
    except Exception as exc:
        return {"error": str(exc)}


def handle_confabulation_check(args: dict) -> dict:
    """Check text for confabulation indicators."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "security"))
        from confabulation_detector import check_output
        return check_output(args["project_id"], args["text"], db_path=DB_PATH)
    except Exception as exc:
        return {"error": str(exc)}


def handle_ai_inventory_register(args: dict) -> dict:
    """Register an AI use case in the OMB M-25-21 inventory."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from ai_inventory_manager import register_ai_component
        return register_ai_component(
            args["project_id"], args["name"],
            purpose=args.get("purpose", ""),
            risk_level=args.get("risk_level", "minimal_risk"),
            db_path=DB_PATH,
        )
    except Exception as exc:
        return {"error": str(exc)}


def handle_fairness_assess(args: dict) -> dict:
    """Assess fairness and bias compliance per OMB M-26-04."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from fairness_assessor import assess_fairness
        return assess_fairness(args["project_id"], args.get("project_dir"), db_path=DB_PATH)
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# AI Accountability (Phase 49, D316-D321) — 8 tools
# ---------------------------------------------------------------------------

def handle_ai_oversight_plan_create(args: dict) -> dict:
    """Register a human oversight plan."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from accountability_manager import _get_connection, _ensure_tables, register_oversight_plan
        conn = _get_connection(DB_PATH)
        _ensure_tables(conn)
        try:
            return register_oversight_plan(
                conn, args["project_id"], args["plan_name"],
                description=args.get("plan_data", ""),
                created_by=args.get("approved_by", ""),
            )
        finally:
            conn.close()
    except Exception as exc:
        return {"error": str(exc)}


def handle_ai_caio_designate(args: dict) -> dict:
    """Designate a CAIO / responsible AI official."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from accountability_manager import _get_connection, _ensure_tables, designate_caio
        conn = _get_connection(DB_PATH)
        _ensure_tables(conn)
        try:
            return designate_caio(
                conn, args["project_id"],
                name=args.get("official_name", args.get("name", "")),
                role=args.get("official_role", args.get("role", "CAIO")),
                organization=args.get("organization", ""),
            )
        finally:
            conn.close()
    except Exception as exc:
        return {"error": str(exc)}


def handle_ai_appeal_file(args: dict) -> dict:
    """File an AI accountability appeal."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from accountability_manager import _get_connection, _ensure_tables, file_appeal
        conn = _get_connection(DB_PATH)
        _ensure_tables(conn)
        try:
            return file_appeal(
                conn, args["project_id"], args["appellant"], args["ai_system"],
                grievance=args.get("decision_contested", args.get("grievance", "")),
            )
        finally:
            conn.close()
    except Exception as exc:
        return {"error": str(exc)}


def handle_ai_appeal_resolve(args: dict) -> dict:
    """Resolve an AI accountability appeal."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from accountability_manager import _get_connection, _ensure_tables, resolve_appeal
        conn = _get_connection(DB_PATH)
        _ensure_tables(conn)
        try:
            return resolve_appeal(
                conn, args["appeal_id"], args["resolution"],
                status=args.get("resolved_by", "resolved"),
            )
        finally:
            conn.close()
    except Exception as exc:
        return {"error": str(exc)}


def handle_ai_ethics_review_submit(args: dict) -> dict:
    """Submit an ethics review for an AI system."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from accountability_manager import _get_connection, _ensure_tables, submit_ethics_review
        conn = _get_connection(DB_PATH)
        _ensure_tables(conn)
        try:
            return submit_ethics_review(
                conn, args["project_id"], args["review_type"],
                summary=args.get("ai_system", ""),
                findings=args.get("findings", ""),
                recommendation=args.get("reviewer", ""),
            )
        finally:
            conn.close()
    except Exception as exc:
        return {"error": str(exc)}


def handle_ai_incident_log(args: dict) -> dict:
    """Log an AI-specific incident."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from ai_incident_response import log_incident
        return log_incident(
            args["project_id"], args["incident_type"],
            ai_system=args.get("ai_system"),
            severity=args.get("severity", "medium"),
            description=args["description"],
            reported_by=args.get("reported_by"),
            db_path=DB_PATH,
        )
    except Exception as exc:
        return {"error": str(exc)}


def handle_ai_reassessment_schedule(args: dict) -> dict:
    """Create a reassessment schedule for an AI system."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from ai_reassessment_scheduler import create_schedule
        return create_schedule(
            args["project_id"], args["ai_system"],
            frequency=args.get("frequency", "annual"),
            next_due=args.get("next_due"),
            db_path=DB_PATH,
        )
    except Exception as exc:
        return {"error": str(exc)}


def handle_ai_accountability_audit(args: dict) -> dict:
    """Run cross-framework AI accountability audit."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from ai_accountability_audit import run_accountability_audit
        return run_accountability_audit(args["project_id"], db_path=DB_PATH)
    except Exception as exc:
        return {"error": str(exc)}
