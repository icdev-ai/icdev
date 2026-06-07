#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
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
import os
import subprocess
import sys
from tools.db.storage import get_connection
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

logger = get_logger("mcp.gap_handlers")


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
        ("--source-path", "source_path"),
        ("--source-language", "source_language"),
        ("--target-language", "target_language"),
        ("--output-dir", "output_dir"),
        ("--project-id", "project_id"),
        ("--candidates", "candidates"),
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
        ("--source-path", "source_path"),
        ("--language", "language"),
        ("--output-ir", "output_ir"),
        ("--project-id", "project_id"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/translation/source_extractor.py", cli_args)


def handle_translate_unit(args: dict) -> dict:
    """Phase 3: LLM-based code translation from IR."""
    cli_args = []
    for flag, key in [
        ("--ir-file", "ir_file"),
        ("--source-language", "source_language"),
        ("--target-language", "target_language"),
        ("--output-dir", "output_dir"),
        ("--candidates", "candidates"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/translation/code_translator.py", cli_args, timeout=600)


def handle_map_dependencies(args: dict) -> dict:
    """Cross-language dependency equivalence lookup."""
    cli_args = []
    for flag, key in [
        ("--source-language", "source_language"),
        ("--target-language", "target_language"),
        ("--imports", "imports"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/translation/dependency_mapper.py", cli_args, timeout=60)


def handle_check_types(args: dict) -> dict:
    """Phase 2: Type system compatibility pre-check."""
    cli_args = []
    for flag, key in [
        ("--ir-file", "ir_file"),
        ("--source-language", "source_language"),
        ("--target-language", "target_language"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/translation/type_checker.py", cli_args)


def handle_assemble_project(args: dict) -> dict:
    """Phase 4: Assemble translated units into project structure."""
    cli_args = []
    for flag, key in [
        ("--ir-list", "ir_list"),
        ("--output-dir", "output_dir"),
        ("--project-template", "project_template"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/translation/project_assembler.py", cli_args)


def handle_validate_translation(args: dict) -> dict:
    """Phase 5: Validate translated output + repair loop."""
    cli_args = []
    for flag, key in [
        ("--output-dir", "output_dir"),
        ("--source-language", "source_language"),
        ("--target-language", "target_language"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/translation/translation_validator.py", cli_args)


def handle_translate_tests(args: dict) -> dict:
    """Translate test suites across languages."""
    cli_args = []
    for flag, key in [
        ("--source-test-dir", "source_test_dir"),
        ("--source-language", "source_language"),
        ("--target-language", "target_language"),
        ("--output-dir", "output_dir"),
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
        ("--source", "source"),
        ("--output", "output"),
        ("--language", "language"),
        ("--from", "from_version"),
        ("--to", "to_version"),
    ]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/modernization/version_migrator.py", cli_args, timeout=300)


def handle_framework_migrate(args: dict) -> dict:
    """Migrate between application frameworks."""
    cli_args = []
    for flag, key in [
        ("--source", "source"),
        ("--output", "output"),
        ("--from", "from_framework"),
        ("--to", "to_framework"),
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
        conn = get_connection()
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
            args["project_id"],
            args["name"],
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
                conn,
                args["project_id"],
                args["plan_name"],
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
                conn,
                args["project_id"],
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
                conn,
                args["project_id"],
                args["appellant"],
                args["ai_system"],
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
                conn,
                args["appeal_id"],
                args["resolution"],
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
                conn,
                args["project_id"],
                args["review_type"],
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
            args["project_id"],
            args["incident_type"],
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
            args["project_id"],
            args["ai_system"],
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


# ============================================================
# CODE INTELLIGENCE (Phase 52, D331-D337)
# ============================================================


def handle_code_analyze(args: dict) -> dict:
    """Run AST-based code quality analysis."""
    try:
        from tools.analysis.code_analyzer import CodeAnalyzer

        project_dir = args.get("project_dir", str(BASE_DIR / "tools"))
        project_id = args.get("project_id")
        analyzer = CodeAnalyzer(
            project_dir=project_dir,
            project_id=project_id,
            db_path=DB_PATH,
        )
        result = analyzer.scan_directory()
        if args.get("store"):
            try:
                stored = analyzer.store_metrics(
                    result.get("metrics", []),
                    result.get("scan_id", ""),
                    db_path=DB_PATH,
                )
                result["stored_rows"] = stored
            except Exception:
                result["stored_rows"] = 0
        # Summarize (don't return full metrics in MCP response)
        metrics = result.pop("metrics", [])
        result["function_count"] = len([m for m in metrics if m.get("function_name")])
        return result
    except Exception as exc:
        return {"error": str(exc)}


def handle_code_quality_report(args: dict) -> dict:
    """Get code quality trend report."""
    try:
        from tools.analysis.code_analyzer import CodeAnalyzer

        analyzer = CodeAnalyzer(project_id=args.get("project_id"), db_path=DB_PATH)
        trend = analyzer.get_trend(args.get("project_id"), db_path=DB_PATH)
        return {"project_id": args.get("project_id"), "trend": trend, "scan_count": len(trend)}
    except Exception as exc:
        return {"error": str(exc)}


def handle_ironbank_generate(args: dict) -> dict:
    """Generate Iron Bank hardening manifest and container approval record."""
    try:
        from tools.infra.ironbank_metadata_generator import generate_hardening_manifest

        return generate_hardening_manifest(
            project_id=args["project_id"],
            project_dir=args.get("project_dir"),
            output_dir=args.get("output_dir"),
        )
    except Exception as exc:
        return {"error": str(exc)}


def handle_ironbank_validate(args: dict) -> dict:
    """Validate Iron Bank hardening manifest."""
    try:
        from tools.infra.ironbank_metadata_generator import validate_hardening_manifest

        return validate_hardening_manifest(
            project_id=args["project_id"],
            manifest_path=args.get("manifest_path"),
        )
    except Exception as exc:
        return {"error": str(exc)}


def handle_eu_ai_act_classify(args: dict) -> dict:
    """Run EU AI Act risk classification and compliance assessment."""
    try:
        from tools.compliance.eu_ai_act_classifier import EUAIActClassifier

        assessor = EUAIActClassifier()
        project = {"id": args["project_id"]}
        result = assessor.assess(project, project_dir=args.get("project_dir"))
        return result
    except Exception as exc:
        return {"error": str(exc)}


def handle_runtime_feedback_collect(args: dict) -> dict:
    """Collect runtime feedback from test results."""
    try:
        from tools.analysis.runtime_feedback import RuntimeFeedbackCollector

        collector = RuntimeFeedbackCollector(
            project_id=args.get("project_id"),
            db_path=DB_PATH,
        )
        xml_path = Path(args["xml_path"])
        return collector.collect_from_xml(
            xml_path,
            run_id=args.get("run_id"),
            db_path=DB_PATH,
        )
    except Exception as exc:
        return {"error": str(exc)}


# ============================================================
# EVOLUTION DAEMON & REGISTRY EXTENSIONS (D-EVO-1, D-NC-4/5/6)
# ============================================================


def handle_evolution_daemon_status(args: dict) -> dict:
    """Get evolution daemon status including all 7 reflex states."""
    return _run_cli("tools/registry/evolution_daemon.py", ["--status"])


def handle_egress_monitor_evaluate(args: dict) -> dict:
    """Evaluate child egress traffic against parent policies (D-NC-6)."""
    cli_args = []
    if args.get("child_id"):
        cli_args.extend(["--evaluate", "--child-id", str(args["child_id"])])
    if args.get("endpoint"):
        cli_args.extend(["--endpoint", str(args["endpoint"])])
    return _run_cli("tools/registry/egress_monitor.py", cli_args)


def handle_propagation_verify(args: dict) -> dict:
    """Verify post-propagation integrity (D-NC-5)."""
    cli_args = ["--verify"]
    if args.get("propagation_id"):
        cli_args.extend(["--propagation-id", str(args["propagation_id"])])
    return _run_cli("tools/registry/propagation_verifier.py", cli_args)


def handle_sandbox_score(args: dict) -> dict:
    """Compute isolation posture score (D-NC-4)."""
    cli_args = ["--score"]
    if args.get("capability_id"):
        cli_args.extend(["--capability-id", str(args["capability_id"])])
    if args.get("source_metadata"):
        import json as _json

        cli_args.extend(["--source-metadata", _json.dumps(args["source_metadata"])])
    return _run_cli("tools/registry/sandbox_scorer.py", cli_args)


# ============================================================
# BAYESIAN TEACHING INTELLIGENCE (D-BT-1 through D-BT-6)
# ============================================================


def handle_bayesian_score_pairs(args: dict) -> dict:
    """Score fine-tuning pairs by information gain (D-BT-1)."""
    cli_args = ["--score-pairs"]
    if args.get("dataset_id"):
        cli_args.extend(["--dataset-id", str(args["dataset_id"])])
    return _run_cli("tools/intelligence/bayesian_teacher.py", cli_args)


def handle_bayesian_optimal_order(args: dict) -> dict:
    """Compute optimal compliance teaching order (D-BT-5)."""
    cli_args = ["--optimal-order"]
    if args.get("project_id"):
        cli_args.extend(["--project-id", str(args["project_id"])])
    return _run_cli("tools/intelligence/bayesian_teacher.py", cli_args)


def handle_bayesian_teaching_dim(args: dict) -> dict:
    """Compute teaching dimension for item set (D-BT-3)."""
    cli_args = ["--teaching-dim"]
    if args.get("items"):
        cli_args.extend(["--items", str(args["items"])])
    return _run_cli("tools/intelligence/bayesian_teacher.py", cli_args)


def handle_bayesian_smart_encode(args: dict) -> dict:
    """Apply SmartEncoding tag compression (D-BT-4)."""
    cli_args = ["--smart-encode"]
    if args.get("project_id"):
        cli_args.extend(["--project-id", str(args["project_id"])])
    return _run_cli("tools/intelligence/bayesian_teacher.py", cli_args)


# ============================================================
# WORKFLOW DISCIPLINE ENGINE (D-WF-1 through D-WF-7)
# ============================================================


def handle_workflow_loop_create(args: dict) -> dict:
    """Create a new PLAN-APPLY-UNIFY workflow loop (D-WF-1)."""
    cli_args = ["--create"]
    for flag, key in [("--project-id", "project_id"), ("--phase", "phase")]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/workflow/loop_engine.py", cli_args)


def handle_workflow_loop_status(args: dict) -> dict:
    """Get workflow loop status."""
    cli_args = ["--status"]
    for flag, key in [("--loop-id", "loop_id"), ("--project-id", "project_id")]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/workflow/loop_engine.py", cli_args)


def handle_workflow_next_action(args: dict) -> dict:
    """Recommend single next action (D-WF-2)."""
    cli_args = ["--recommend"]
    if args.get("project_id"):
        cli_args.extend(["--project-id", str(args["project_id"])])
    return _run_cli("tools/workflow/next_action.py", cli_args)


def handle_workflow_reconcile(args: dict) -> dict:
    """Run UNIFY phase reconciliation (D-WF-5)."""
    cli_args = ["--reconcile"]
    if args.get("loop_id"):
        cli_args.extend(["--loop-id", str(args["loop_id"])])
    return _run_cli("tools/workflow/reconciler.py", cli_args)


# ============================================================
# NEMOCLAW SECURITY (D-NC-1 through D-NC-3)
# ============================================================


def handle_credential_broker_request(args: dict) -> dict:
    """Request scoped credential token (D-NC-1)."""
    cli_args = ["--request"]
    for flag, key in [("--agent-id", "agent_id"), ("--function", "function")]:
        if args.get(key):
            cli_args.extend([flag, str(args[key])])
    return _run_cli("tools/security/credential_broker.py", cli_args)


def handle_credential_broker_status(args: dict) -> dict:
    """Get credential broker status."""
    return _run_cli("tools/security/credential_broker.py", ["--status"])


def handle_blueprint_verify(args: dict) -> dict:
    """Compute or verify directory digest (D-NC-3)."""
    cli_args = []
    if args.get("expected"):
        cli_args.extend(["--verify", "--path", str(args["path"]), "--expected", str(args["expected"])])
    else:
        cli_args.extend(["--compute", "--path", str(args["path"])])
    if args.get("entity_type"):
        cli_args.extend(["--entity-type", str(args["entity_type"])])
    if args.get("entity_id"):
        cli_args.extend(["--entity-id", str(args["entity_id"])])
    return _run_cli("tools/security/blueprint_verifier.py", cli_args)


def handle_egress_policy_resolve(args: dict) -> dict:
    """Resolve effective egress policy for agent role (D-NC-2)."""
    cli_args = ["--resolve"]
    if args.get("role"):
        cli_args.extend(["--role", str(args["role"])])
    return _run_cli("tools/security/egress_policy_manager.py", cli_args)


# ── Autoresearch (Phase 67, D-AR-1 through D-AR-10) ─────────────────────────


def handle_autoresearch_create(args: dict) -> dict:
    """Create a new autoresearch experiment candidate."""
    cli_args = [
        "--create",
        "--domain",
        str(args.get("domain", "compliance")),
        "--hypothesis",
        str(args.get("hypothesis", "")),
    ]
    return _run_cli("tools/autoresearch/experiment_engine.py", cli_args)


def handle_autoresearch_loop(args: dict) -> dict:
    """Run autonomous Bayesian Autoresearch loop for a domain."""
    cli_args = [
        "--loop",
        "--domain",
        str(args.get("domain", "compliance")),
        "--max-experiments",
        str(args.get("max_experiments", 5)),
    ]
    if args.get("overnight"):
        cli_args.append("--overnight")
    return _run_cli("tools/autoresearch/experiment_engine.py", cli_args)


def handle_autoresearch_status(args: dict) -> dict:
    """Get current autoresearch status across all domains."""
    return _run_cli("tools/autoresearch/experiment_engine.py", ["--status"])


def handle_autoresearch_select(args: dict) -> dict:
    """Select next experiment via Bayesian scoring + Thompson Sampling."""
    cli_args = ["--select", "--domain", str(args.get("domain", "compliance"))]
    return _run_cli("tools/autoresearch/bayesian_selector.py", cli_args)


def handle_autoresearch_evaluate(args: dict) -> dict:
    """Evaluate a single domain fitness metric."""
    cli_args = ["--evaluate", str(args.get("domain", "compliance"))]
    if args.get("project_id"):
        cli_args.extend(["--project-id", str(args["project_id"])])
    return _run_cli("tools/autoresearch/fitness_evaluator.py", cli_args)


def handle_autoresearch_health(args: dict) -> dict:
    """Health check for autoresearch subsystem."""
    return _run_cli("tools/autoresearch/experiment_engine.py", ["--health"])


# ── Redaction & Data Protection (Phase 70 — D-RDT-1) ─────────────────


def handle_redaction_detect(args: dict) -> dict:
    """Detect PII/sensitive data in text."""
    cli_args = ["--detect", str(args.get("text", ""))]
    return _run_cli("tools/redaction/detector.py", cli_args)


def handle_redaction_anonymize(args: dict) -> dict:
    """Anonymize PII in text."""
    cli_args = ["--anonymize", str(args.get("text", "")), "--show-text"]
    if args.get("impact_level"):
        cli_args.extend(["--il", str(args["impact_level"])])
    if args.get("session_id"):
        cli_args.extend(["--session", str(args["session_id"])])
    return _run_cli("tools/redaction/anonymizer.py", cli_args)


def handle_redaction_sanitize_proposal(args: dict) -> dict:
    """Sanitize proposal content for LLM."""
    cli_args = ["--sanitize", str(args.get("text", "")), "--show-text"]
    if args.get("function_name"):
        cli_args.extend(["--function", str(args["function_name"])])
    if args.get("impact_level"):
        cli_args.extend(["--il", str(args["impact_level"])])
    return _run_cli("tools/redaction/govcon_sanitizer.py", cli_args)


def handle_redaction_scan_db(args: dict) -> dict:
    """Scan proposal DB tables for PII."""
    cli_args = ["--scan"]
    if args.get("table"):
        cli_args.extend(["--table", str(args["table"])])
    if args.get("sample_size"):
        cli_args.extend(["--sample-size", str(args["sample_size"])])
    return _run_cli("tools/redaction/db_scanner.py", cli_args)


# ── GovCon Proposals Security — Aggregation Guard (prop-sec-04 through prop-sec-08) ──


def handle_guard_result(args: dict) -> dict:
    """Run mosaic aggregation guard on a result set (prop-sec-06)."""
    try:
        from tools.security.aggregation_guard import guard_result

        result_set = args.get("result_set", [])
        surface = str(args.get("surface", ""))
        ctx = {
            "user_id": args.get("user_id"),
            "clearance_level": args.get("clearance_level"),
            "surface_ceiling": args.get("surface_ceiling"),
        }
        result = guard_result(result_set, ctx, surface)
        if args.get("gate") and result.get("action") == "block":
            return {"error": "aggregation_guard: block — derived classification exceeds surface ceiling", **result}
        return result
    except ImportError:
        return _run_cli(
            "tools/security/aggregation_guard.py",
            ["--guard", "--surface", str(args.get("surface", "")), "--json"],
        )
    except Exception as exc:
        return {"error": str(exc)}


def handle_evaluate_aggregation_rules(args: dict) -> dict:
    """Evaluate SCG aggregation rules and return fired rules + derived classification (prop-sec-03)."""
    try:
        from tools.security.aggregation_guard import evaluate_rules

        result_set = args.get("result_set", [])
        fired = evaluate_rules(result_set)
        return {"fired_rules": fired, "count": len(fired)}
    except ImportError:
        cli_args = ["--evaluate-rules", "--json"]
        if args.get("rules_file"):
            cli_args.extend(["--rules-file", str(args["rules_file"])])
        return _run_cli("tools/security/aggregation_guard.py", cli_args)
    except Exception as exc:
        return {"error": str(exc)}


# ── Oracle Anticipatory Agent ─────────────────────────────────────────────


def handle_oracle_predictions_list(args: dict) -> dict:
    """Query oracle_predictions with optional lens/confidence filters."""
    lens = args.get("lens")
    min_confidence = float(args.get("min_confidence", 0.0))
    limit = int(args.get("limit", 50))
    try:
        conn = get_connection()
        sql = "SELECT id, lens, title, confidence, tags, created_at FROM oracle_predictions WHERE confidence >= ?"
        params = [min_confidence]
        if lens:
            sql += " AND lens = ?"
            params.append(lens)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        predictions = [
            {"id": r[0], "lens": r[1], "title": r[2], "confidence": r[3], "tags": r[4], "created_at": r[5]}
            for r in rows
        ]
        return {"predictions": predictions, "count": len(predictions)}
    except Exception as exc:
        logger.warning("handle_oracle_predictions_list: %s", exc)
        return {"error": str(exc), "predictions": []}


def handle_oracle_lens_status(args: dict) -> dict:
    """Return per-lens health: prediction count, avg_confidence, latest run."""
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT lens,
                   COUNT(*) AS total,
                   AVG(confidence) AS avg_conf,
                   MAX(created_at) AS last_run,
                   SUM(CASE WHEN confidence >= 0.7 THEN 1 ELSE 0 END) AS high_count,
                   SUM(CASE WHEN confidence >= 0.4 AND confidence < 0.7 THEN 1 ELSE 0 END) AS med_count,
                   SUM(CASE WHEN confidence < 0.4 THEN 1 ELSE 0 END) AS low_count
            FROM oracle_predictions
            GROUP BY lens
            ORDER BY lens
            """
        ).fetchall()
        conn.close()
        lenses = [
            {
                "lens": r[0],
                "total_predictions": r[1],
                "avg_confidence": round(r[2], 3) if r[2] else 0.0,
                "last_run": r[3],
                "high": r[4],
                "medium": r[5],
                "low": r[6],
            }
            for r in rows
        ]
        return {"lenses": lenses}
    except Exception as exc:
        logger.warning("handle_oracle_lens_status: %s", exc)
        return {"error": str(exc), "lenses": []}


def handle_oracle_kanban_bridge_sync(args: dict) -> dict:
    """Batch-sync promoted anticipation_report GKPs to suggested kanban tasks."""
    min_confidence = float(args.get("min_confidence", 0.80))
    cli_args = ["--sync", "--min-confidence", str(min_confidence)]
    return _run_cli("tools/oracle/kanban_bridge.py", cli_args)


def handle_oracle_kanban_bridge_gate(args: dict) -> dict:
    """Gate check for the Oracle kanban bridge."""
    return _run_cli("tools/oracle/kanban_bridge.py", ["--gate"])


# ---------------------------------------------------------------------------
# FathomDeskNews Pipeline (ADN) — phases A–H
# ---------------------------------------------------------------------------


def handle_news_ingest_once(args: dict) -> dict:
    """Run the RSS ingestor once across all configured feeds."""
    return _run_cli("tools/trading/news/rss_ingestor.py", ["--run-once"])


def handle_news_classify(args: dict) -> dict:
    """Classify pending news items using the rule-based classifier."""
    return _run_cli("tools/trading/news/classifier.py", ["--run"])


def handle_news_scenario_match(args: dict) -> dict:
    """Match classified items to macro scenarios."""
    return _run_cli("tools/trading/news/scenario_matcher.py", ["--run"])


def handle_news_aggregate(args: dict) -> dict:
    """Aggregate and promote news clusters to the dashboard."""
    return _run_cli("tools/trading/news/aggregator.py", ["--run"])


def handle_news_reason(args: dict) -> dict:
    """Run LLM-backed reasoning over top news clusters."""
    return _run_cli("tools/trading/news/news_reasoner.py", ["--run"])


def handle_news_db_migrate(args: dict) -> dict:
    """Run ADN database migrations (ad_news_* tables)."""
    return _run_cli("tools/trading/news/db.py", ["--migrate"])


# ---------------------------------------------------------------------------
# System Graph (federated Sigma.js graph — 3 500+ nodes, 6 sources)
# ---------------------------------------------------------------------------


def handle_system_graph_get(args: dict) -> dict:
    """Return the full federated ICDEV system graph payload."""
    try:
        from tools.system_graph.graph_builder import build_graph
        sources = args.get("sources") or None
        if isinstance(sources, list) and len(sources) == 0:
            sources = None
        data = build_graph(
            sources=sources,
            filter_type=args.get("filter_type") or None,
            filter_health=args.get("filter_health") or None,
            search=args.get("search") or None,
        )
        return {
            "node_count": len(data.get("nodes", [])),
            "edge_count": len(data.get("edges", [])),
            "stats": data.get("stats", {}),
            "nodes": data.get("nodes", [])[:500],   # cap for MCP transport
            "edges": data.get("edges", [])[:500],
            "truncated": len(data.get("nodes", [])) > 500,
        }
    except Exception as exc:
        logger.warning("handle_system_graph_get: %s", exc)
        return {"error": str(exc)}


def handle_system_graph_node_detail(args: dict) -> dict:
    """Return full detail for a single graph node."""
    try:
        from tools.system_graph.graph_builder import get_node_detail
        node_id = args.get("node_id", "")
        detail = get_node_detail(node_id)
        if detail is None:
            return {"error": f"node '{node_id}' not found"}
        return detail
    except Exception as exc:
        logger.warning("handle_system_graph_node_detail: %s", exc)
        return {"error": str(exc)}


def handle_system_graph_stats(args: dict) -> dict:
    """Return high-level stats for the federated system graph."""
    try:
        from tools.system_graph.graph_builder import build_graph
        data = build_graph()
        return data.get("stats", {})
    except Exception as exc:
        logger.warning("handle_system_graph_stats: %s", exc)
        return {"error": str(exc)}


# ===========================================================================
# Category: chain_orchestration (CoT / CoD)
# ===========================================================================


def handle_cot_invoke(args: dict) -> dict:
    """Invoke Chain of Thought via ChainOrchestrator."""
    try:
        from tools.llm.chain_orchestrator import ChainOrchestrator
        from tools.llm.provider import LLMRequest

        orchestrator = ChainOrchestrator()
        request = LLMRequest(
            messages=[{"role": "user", "content": args.get("prompt", "")}],
            system_prompt=args.get("system_prompt", ""),
        )
        result = orchestrator.invoke_chain_of_thought(
            args.get("function", "default"),
            request,
        )
        return {
            "content": result.content,
            "chain_mode": result.chain_mode,
            "models_used": result.models_used,
            "total_cost_usd": result.total_cost_usd,
            "total_input_tokens": result.total_input_tokens,
            "total_output_tokens": result.total_output_tokens,
            "total_duration_ms": result.total_duration_ms,
            "stop_reason": result.stop_reason,
            "trace_id": result.trace_id,
            "confidence": result.confidence,
            "rounds": result.rounds,
        }
    except Exception as exc:
        logger.warning("handle_cot_invoke: %s", exc)
        return {"error": str(exc)}


def handle_reasoned_codegen_advise(args: dict) -> dict:
    """Advise whether reasoned codegen pays off for a task (enable + mode)."""
    try:
        from tools.llm.reasoned_codegen_advisor import recommend

        return recommend(
            args.get("function", "code_generation"),
            args.get("spec", ""),
            context={
                "file_count": int(args.get("file_count", 0) or 0),
                "past_failures": int(args.get("past_failures", 0) or 0),
            },
            use_llm=bool(args.get("use_llm", False)),
        )
    except Exception as exc:
        logger.warning("handle_reasoned_codegen_advise: %s", exc)
        return {"error": str(exc), "recommended": False, "mode": "off"}


# ---------------------------------------------------------------------------
# NOC CANVAS (NOCC)
# ---------------------------------------------------------------------------


def noc_alarm_ingest(args: dict) -> dict:
    """Ingest an alarm into NOCC and correlate with existing alarms."""
    try:
        from tools.noc_canvas.db.init_db import get_connection
        from tools.noc_canvas.alarm_correlator import ingest_alarm
        conn = get_connection()
        try:
            result = ingest_alarm(conn, args)
            return result
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("noc_alarm_ingest: %s", exc)
        return {"error": str(exc)}


def noc_incident_create(args: dict) -> dict:
    """Create a P1–P4 incident in the NOC Operations Canvas."""
    try:
        from tools.noc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            title = args.get("title", "")
            severity = args.get("severity", "p3")
            affected_circuit = args.get("affected_circuit", "")
            affected_carrier = args.get("affected_carrier", "")
            root_cause = args.get("root_cause", "")
            sla_breach = 1 if args.get("sla_breach") else 0
            opened_by = args.get("opened_by", "mcp-gateway")
            assigned_to = args.get("assigned_to", "")
            import time as _time
            incident_number = f"INC-MCP-{int(_time.time())}"
            try:
                conn.execute(
                    "INSERT INTO noc_incidents (incident_number, title, severity, status, "
                    "affected_circuit, affected_carrier, root_cause, sla_breach, opened_by, "
                    "assigned_to, classification) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (incident_number, title, severity, "open", affected_circuit,
                     affected_carrier, root_cause, sla_breach, opened_by, assigned_to,
                     "CUI // SP-CTI"),
                )
            except Exception:
                conn.execute(
                    "INSERT INTO noc_incidents (incident_number, title, severity, status, "
                    "affected_circuit, affected_carrier, root_cause, sla_breach, opened_by, "
                    "assigned_to, classification) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (incident_number, title, severity, "open", affected_circuit,
                     affected_carrier, root_cause, sla_breach, opened_by, assigned_to,
                     "CUI // SP-CTI"),
                )
            try:
                conn.commit()
            except Exception:
                pass
            return {"status": "created", "incident_number": incident_number, "severity": severity}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("noc_incident_create: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# PMC CANVAS
# ---------------------------------------------------------------------------


def pmc_peer_evaluate(args: dict) -> dict:
    """Run the 6-dimension peering decision engine on a BGP peer."""
    try:
        from tools.pmc_canvas.peering_decision_engine import evaluate_peer
        peer = {
            "asn": args.get("asn"),
            "org_name": args.get("org_name", f"AS{args.get('asn')}"),
            "traffic_ratio": args.get("traffic_ratio", 0.5),
            "ipv4_prefix_count": args.get("ipv4_prefix_count", 0),
            "ipv6_prefix_count": args.get("ipv6_prefix_count", 0),
            "irr_as_set": args.get("irr_as_set", ""),
        }
        our_asn = args.get("our_asn", 0)
        our_ix_ids = args.get("our_ix_ids", [])
        prefixes = args.get("prefixes", [])
        # Inject optional scored dimensions
        if "rpki_valid_pct" in args:
            peer["rpki_valid_pct"] = args["rpki_valid_pct"]
        if "irr_registered_pct" in args:
            peer["irr_registered_pct"] = args["irr_registered_pct"]
        if "noc_responsiveness" in args:
            peer["noc_responsiveness"] = args["noc_responsiveness"]
        return evaluate_peer(peer, our_asn, our_ix_ids, prefixes)
    except Exception as exc:
        logger.warning("pmc_peer_evaluate: %s", exc)
        return {"error": str(exc)}


def pmc_rpki_validate(args: dict) -> dict:
    """Validate a BGP prefix against Cloudflare RPKI API."""
    try:
        from tools.pmc_canvas.rpki_validator import validate_prefix
        prefix = args.get("prefix", "")
        origin_asn = int(args.get("origin_asn", 0))
        if not prefix or not origin_asn:
            return {"error": "prefix and origin_asn are required"}
        return validate_prefix(prefix, origin_asn)
    except Exception as exc:
        logger.warning("pmc_rpki_validate: %s", exc)
        return {"error": str(exc)}


def ccc_circuit_ingest(args: dict) -> dict:
    """Add or update a circuit record in the CCC inventory."""
    try:
        from tools.ccc_canvas.db.init_db import get_connection
        conn = get_connection()
        circuit_id = args.get("circuit_id", "")
        circuit_type = args.get("circuit_type", "other")
        carrier = args.get("carrier", "")
        if not circuit_id or not carrier:
            return {"error": "circuit_id and carrier are required"}
        fields = {
            "circuit_id": circuit_id,
            "circuit_type": circuit_type,
            "carrier": carrier,
            "bandwidth_gbps": float(args.get("bandwidth_gbps", 0)),
            "utilization_pct": float(args.get("utilization_pct", 0)),
            "mrr_usd": float(args.get("mrr_usd", 0)),
        }
        values = tuple(fields.values())
        try:
            conn.execute(
                "INSERT OR REPLACE INTO ccc_circuits"
                " (circuit_id, circuit_type, carrier, bandwidth_gbps, utilization_pct, mrr_usd)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                values,
            )
        except Exception:
            conn.execute(
                "INSERT INTO ccc_circuits"
                " (circuit_id, circuit_type, carrier, bandwidth_gbps, utilization_pct, mrr_usd)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (circuit_id) DO UPDATE SET circuit_type=EXCLUDED.circuit_type",
                values,
            )
        conn.commit()
        conn.close()
        return {"status": "ok", "circuit_id": circuit_id}
    except Exception as exc:
        logger.warning("ccc_circuit_ingest: %s", exc)
        return {"error": str(exc)}


def ccc_capacity_analyze(args: dict) -> dict:
    """Run capacity analysis for a single circuit by primary key."""
    try:
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.ccc_canvas.capacity_engine import analyze_circuit
        circuit_pk = int(args.get("circuit_pk", 0))
        if not circuit_pk:
            return {"error": "circuit_pk required"}
        conn = get_connection()
        result = analyze_circuit(conn, circuit_pk)
        conn.close()
        return result
    except Exception as exc:
        logger.warning("ccc_capacity_analyze: %s", exc)
        return {"error": str(exc)}


def ccc_loa_create(args: dict) -> dict:
    """Create an LOA request for a cross-connect."""
    try:
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.ccc_canvas.loa_workflow import create_loa_request
        if not args.get("facility"):
            return {"error": "facility required"}
        conn = get_connection()
        result = create_loa_request(conn, args)
        conn.close()
        return result
    except Exception as exc:
        logger.warning("ccc_loa_create: %s", exc)
        return {"error": str(exc)}


def dsoc_rtbh_trigger(args: dict) -> dict:
    """Trigger RTBH null-route for a target prefix."""
    try:
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.rtbh_manager import trigger_rtbh
        if not args.get("prefix"):
            return {"error": "prefix required"}
        if not args.get("trigger_reason"):
            return {"error": "trigger_reason required"}
        conn = get_connection()
        try:
            result = trigger_rtbh(
                conn,
                prefix=args["prefix"],
                reason=args["trigger_reason"],
                triggered_by=args.get("triggered_by", "mcp"),
                auto_withdraw_minutes=int(args.get("auto_withdraw_minutes", 60)),
            )
            conn.commit()
        finally:
            conn.close()
        return result
    except Exception as exc:
        logger.warning("dsoc_rtbh_trigger: %s", exc)
        return {"error": str(exc)}


def dsoc_flowspec_activate(args: dict) -> dict:
    """Activate a BGP flowspec rule by ID."""
    try:
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.flowspec_engine import activate_rule
        rule_id = args.get("rule_id")
        if rule_id is None:
            return {"error": "rule_id required"}
        conn = get_connection()
        try:
            result = activate_rule(conn, int(rule_id))
            conn.commit()
        finally:
            conn.close()
        return result
    except Exception as exc:
        logger.warning("dsoc_flowspec_activate: %s", exc)
        return {"error": str(exc)}


def dsoc_hijack_report(args: dict) -> dict:
    """Return open BGP hijack and route-leak events from DSOC."""
    try:
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.bgp_hijack_detector import get_active_hijacks
        conn = get_connection()
        try:
            return {"hijacks": get_active_hijacks(conn)}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("dsoc_hijack_report: %s", exc)
        return {"error": str(exc)}


def routinator_validate(args: dict) -> dict:
    """Validate a prefix+ASN pair via on-premises Routinator RPKI validator."""
    prefix = args.get("prefix", "").strip()
    origin_asn = args.get("origin_asn")
    if not prefix or origin_asn is None:
        return {"error": "prefix and origin_asn required"}
    try:
        from tools.databridge.connectors.routinator_connector import validate_prefix
        return validate_prefix(prefix, int(origin_asn))
    except Exception as exc:
        logger.warning("routinator_validate: %s", exc)
        return {"error": str(exc)}


def pmacct_ingest(args: dict) -> dict:
    """Test pmacct connector health and return connection status."""
    try:
        from tools.databridge.connectors.pmacct_connector import test_connection
        return test_connection()
    except Exception as exc:
        logger.warning("pmacct_ingest: %s", exc)
        return {"error": str(exc)}


def dsoc_overview(args: dict) -> dict:
    """Return DSOC overview: active mitigations, RTBH, scrubbing utilization, threats."""
    try:
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.dsoc_aggregator import get_dsoc_overview
        conn = get_connection()
        try:
            return get_dsoc_overview(conn)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("dsoc_overview: %s", exc)
        return {"error": str(exc)}


# ============================================================
# ANVIL CO-WORKER ENGINE (ACE)
# ============================================================


def handle_ace_launch(args: dict) -> dict:
    """Launch an ACE co-worker session via ACEController.launch()."""
    problem_text = args.get("problem_text", "")
    trigger_source = args.get("trigger_source", "api")
    trigger_ref = args.get("trigger_ref", "")
    if not problem_text:
        return {"error": "problem_text is required"}
    try:
        from icdev.tools.ace.controller import ACEController

        controller = ACEController.get_instance()
        instance_id = controller.launch(problem_text, trigger_source, trigger_ref)
        return {"instance_id": instance_id, "state": "assembling"}
    except ImportError:
        return {
            "error": "ACEController not yet available (ace-runtime not shipped)",
            "instance_id": None,
            "state": "unavailable",
        }
    except Exception as exc:
        logger.warning("handle_ace_launch: %s", exc)
        return {"error": str(exc)}


def handle_ace_status(args: dict) -> dict:
    """Return full status of an ACE co-worker instance including co-worker states."""
    instance_id = args.get("instance_id", "")
    if not instance_id:
        return {"error": "instance_id is required"}
    try:
        from icdev.tools.ace.controller import ACEController

        controller = ACEController.get_instance()
        return controller.status(instance_id)
    except ImportError:
        return {
            "error": "ACEController not yet available (ace-runtime not shipped)",
            "instance_id": instance_id,
            "state": "unavailable",
        }
    except Exception as exc:
        logger.warning("handle_ace_status: %s", exc)
        return {"error": str(exc)}


def handle_cod_invoke(args: dict) -> dict:
    """Invoke Chain of Debate via ChainOrchestrator."""
    try:
        from tools.llm.chain_orchestrator import ChainOrchestrator
        from tools.llm.provider import LLMRequest

        orchestrator = ChainOrchestrator()
        request = LLMRequest(
            messages=[{"role": "user", "content": args.get("prompt", "")}],
            system_prompt=args.get("system_prompt", ""),
        )
        result = orchestrator.invoke_chain_of_debate(
            args.get("function", "default"),
            request,
        )
        return {
            "content": result.content,
            "chain_mode": result.chain_mode,
            "models_used": result.models_used,
            "total_cost_usd": result.total_cost_usd,
            "total_input_tokens": result.total_input_tokens,
            "total_output_tokens": result.total_output_tokens,
            "total_duration_ms": result.total_duration_ms,
            "stop_reason": result.stop_reason,
            "trace_id": result.trace_id,
            "confidence": result.confidence,
            "rounds": result.rounds,
        }
    except Exception as exc:
        logger.warning("handle_cod_invoke: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# SIPA — Software Integrity & Provenance Assessor (sipa-mcp-01)
# ---------------------------------------------------------------------------


def handle_integrity_assess(args: dict) -> dict:
    """Run a static SIPA integrity assessment of a source artifact.

    Pattern A (direct import) wrapper around tools.integrity.engine.assess.
    Deterministic, JSON in/out, never executes the target (static-only:
    quarantine copy/clone, isolated scanner subprocesses, AST parsing).
    """
    source = args.get("source")
    if not source:
        return {"error": "integrity_assess: 'source' is required"}
    try:
        from tools.integrity import engine

        return engine.assess(
            str(source),
            mode=str(args.get("mode", "auto")),
            project_id=args.get("project_id"),
            session_id=args.get("session_id"),
            declared_purpose=args.get("declared_purpose"),
        )
    except Exception as exc:
        logger.warning("handle_integrity_assess: %s", exc)
        return {"error": str(exc)}


def handle_integrity_list_assessments(args: dict) -> dict:
    """List SIPA assessments (id, source, mode, status, verdict, risk_score).

    Read-only RLS-aware query against integrity_assessments with optional
    status / verdict filters (mirrors the dashboard list view).
    """
    status = args.get("status")
    verdict = args.get("verdict")
    try:
        limit = int(args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    try:
        conn = get_connection()
        try:
            sql = (
                "SELECT id, source_type, source_ref, mode, project_id, session_id, "
                "status, verdict, risk_score, created_at, updated_at "
                "FROM integrity_assessments"
            )
            where, params = [], []
            if status:
                where.append("status = ?")
                params.append(status)
            if verdict:
                where.append("verdict = ?")
                params.append(verdict)
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
        cols = (
            "id", "source_type", "source_ref", "mode", "project_id", "session_id",
            "status", "verdict", "risk_score", "created_at", "updated_at",
        )
        assessments = []
        for r in rows:
            try:
                assessments.append({c: r[c] for c in cols})
            except (TypeError, KeyError, IndexError):
                assessments.append({c: r[i] for i, c in enumerate(cols)})
        return {"assessments": assessments, "count": len(assessments)}
    except Exception as exc:
        logger.warning("handle_integrity_list_assessments: %s", exc)
        return {"error": str(exc), "assessments": []}


# ---------------------------------------------------------------------------
# ACF — Autonomous Capability Foundry (acf-mcp-01)
# ---------------------------------------------------------------------------


def handle_foundry_run(args: dict) -> dict:
    """Trigger one ACF foundry cycle (harvest -> synth -> novelty -> score -> CoD -> emit).

    Pattern A (direct import) wrapper around tools.foundry.engine.run_cycle.
    Deterministic, JSON in/out, no shell. Rate limits from
    args/foundry_config.yaml are enforced inside run_cycle. With dry_run=True the
    full pipeline runs but the seeder does NOT write to kanban_tasks.

    Returns the engine roll-up: run_id, harvested, concepts_proposed,
    concepts_approved, tasks_emitted, active_projects, status, dry_run.
    """
    try:
        from tools.foundry import engine

        max_concepts = args.get("max_concepts")
        result = engine.run_cycle(
            dry_run=bool(args.get("dry_run", False)),
            max_concepts=int(max_concepts) if max_concepts is not None else None,
        )
        return result
    except Exception as exc:
        logger.warning("handle_foundry_run: %s", exc)
        return {"error": str(exc)}


def handle_foundry_status(args: dict) -> dict:
    """Return ACF pipeline status: recent runs, active projects, concept counts.

    Pattern A (direct import) wrapper around tools.foundry.engine.status.
    Read-only, RLS-aware, JSON in/out, no shell. Returns recent_runs,
    active_projects, pipeline (concept counts by status), and rate_limits.
    """
    try:
        from tools.foundry import engine

        try:
            limit = int(args.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        return engine.status(limit=limit)
    except Exception as exc:
        logger.warning("handle_foundry_status: %s", exc)
        return {"error": str(exc)}
