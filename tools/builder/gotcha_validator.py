#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""GOTCHA Framework Compliance Validator.

Validates that a project directory conforms to the 6-layer GOTCHA framework
and ATLAS workflow structure. Designed to run post-generation on child apps
or standalone against any ICDEV-compatible project.

The 6 GOTCHA layers:
  1. Goals       — goals/ with manifest + workflow files
  2. Orchestration — agent cards, agent config, or CLAUDE.md orchestration
  3. Tools       — tools/ with deterministic Python scripts
  4. Context     — context/ with reference material
  5. Hard Prompts — hardprompts/ with LLM instruction templates
  6. Args        — args/ with YAML/JSON behavior settings

Additional BMAD-adapted quality checks:
  7. CLAUDE.md   — project documentation referencing GOTCHA
  8. Memory      — memory/MEMORY.md for long-term context
  9. Database    — tools/db/ with init script
  10. ATLAS      — goals/build_app.md (ATLAS workflow present)

Decision D44: Flag-based backward compatibility (--gate for CI/CD blocking).
Pattern: Follows claude_dir_validator.py declarative check registry.

Usage:
    python tools/builder/gotcha_validator.py --project-dir /path/to/app --json
    python tools/builder/gotcha_validator.py --project-dir /path/to/app --human
    python tools/builder/gotcha_validator.py --project-dir /path/to/app --gate
    python tools/builder/gotcha_validator.py --project-dir /path/to/app --check goals --json

Exit codes: 0 = all checks pass, 1 = at least one check failed
"""

import argparse
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Result types (follows ClaudeConfigCheck pattern from claude_dir_validator.py)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class GotchaCheck:
    """Result of a single GOTCHA compliance check."""
    check_id: str
    check_name: str
    layer: str  # "goals", "orchestration", "tools", "context", "hardprompts", "args", "meta"
    status: str  # "pass", "fail", "warn"
    expected: str
    actual: str
    fix_suggestion: str
    message: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @property
    def passed(self) -> bool:
        return self.status == "pass"


@dataclasses.dataclass
class GotchaReport:
    """Aggregate GOTCHA compliance validation report."""
    overall_pass: bool
    timestamp: str
    project_dir: str
    total_checks: int
    passed_checks: int
    failed_checks: int
    warned_checks: int
    layer_summary: dict  # layer_name -> pass/fail/warn
    score: float  # 0.0 - 1.0
    checks: List[GotchaCheck]

    def to_dict(self) -> dict:
        return {
            "overall_pass": self.overall_pass,
            "timestamp": self.timestamp,
            "project_dir": self.project_dir,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "warned_checks": self.warned_checks,
            "layer_summary": self.layer_summary,
            "score": self.score,
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------

def _check_goals(project_dir: Path) -> List[GotchaCheck]:
    """Check Layer 1: Goals — workflow definitions exist."""
    checks = []
    goals_dir = project_dir / "goals"

    # Check goals directory exists
    if not goals_dir.is_dir():
        checks.append(GotchaCheck(
            check_id="GOTCHA-01",
            check_name="Goals directory exists",
            layer="goals",
            status="fail",
            expected="goals/ directory with workflow definitions",
            actual="Directory not found",
            fix_suggestion="Create goals/ and add workflow files (build_app.md, manifest.md)",
            message="GOTCHA Layer 1 (Goals) missing: no goals/ directory",
        ))
        return checks

    # Check for manifest
    manifest = goals_dir / "manifest.md"
    if manifest.exists():
        checks.append(GotchaCheck(
            check_id="GOTCHA-01a",
            check_name="Goals manifest exists",
            layer="goals",
            status="pass",
            expected="goals/manifest.md",
            actual="Present",
            fix_suggestion="",
            message="Goals manifest found",
        ))
    else:
        checks.append(GotchaCheck(
            check_id="GOTCHA-01a",
            check_name="Goals manifest exists",
            layer="goals",
            status="warn",
            expected="goals/manifest.md",
            actual="Missing",
            fix_suggestion="Create goals/manifest.md listing all goal workflows",
            message="Goals manifest missing — create manifest.md indexing all goals",
        ))

    # Check for at least 1 goal file (not counting manifest)
    goal_files = [f for f in goals_dir.glob("*.md") if f.name != "manifest.md"]
    if goal_files:
        checks.append(GotchaCheck(
            check_id="GOTCHA-01b",
            check_name="Goal workflow files present",
            layer="goals",
            status="pass",
            expected="At least 1 goal workflow file",
            actual=f"{len(goal_files)} goal file(s): {', '.join(f.name for f in goal_files[:5])}",
            fix_suggestion="",
            message=f"{len(goal_files)} goal workflow(s) found",
        ))
    else:
        checks.append(GotchaCheck(
            check_id="GOTCHA-01b",
            check_name="Goal workflow files present",
            layer="goals",
            status="fail",
            expected="At least 1 goal workflow file (e.g., build_app.md)",
            actual="0 goal files (empty directory)",
            fix_suggestion="Add goal files: build_app.md (ATLAS), tdd_workflow.md, compliance_workflow.md",
            message="GOTCHA Layer 1 (Goals) empty: no workflow definitions found",
        ))

    return checks


def _check_orchestration(project_dir: Path) -> List[GotchaCheck]:
    """Check Layer 2: Orchestration — agent infrastructure exists."""
    checks = []

    # Check for agent cards OR agent config
    agent_cards_dir = project_dir / "tools" / "agent" / "cards"
    agent_config = project_dir / "args" / "agent_config.yaml"
    claude_md = project_dir / "CLAUDE.md"

    has_cards = agent_cards_dir.is_dir() and any(agent_cards_dir.glob("*.json"))
    has_config = agent_config.exists()
    has_claude_md = claude_md.exists()

    if has_cards:
        card_count = len(list(agent_cards_dir.glob("*.json")))
        checks.append(GotchaCheck(
            check_id="GOTCHA-02a",
            check_name="Agent cards present",
            layer="orchestration",
            status="pass",
            expected="Agent card JSON files in tools/agent/cards/",
            actual=f"{card_count} agent card(s) found",
            fix_suggestion="",
            message=f"Orchestration: {card_count} agent card(s) found",
        ))
    elif has_config:
        checks.append(GotchaCheck(
            check_id="GOTCHA-02a",
            check_name="Agent config present",
            layer="orchestration",
            status="pass",
            expected="Agent cards or args/agent_config.yaml",
            actual="args/agent_config.yaml found",
            fix_suggestion="",
            message="Orchestration: agent_config.yaml found (no individual cards)",
        ))
    elif has_claude_md:
        # CLAUDE.md exists — orchestration is implicit (Claude is the orchestrator)
        checks.append(GotchaCheck(
            check_id="GOTCHA-02a",
            check_name="Orchestration layer present",
            layer="orchestration",
            status="warn",
            expected="Agent cards in tools/agent/cards/ or args/agent_config.yaml",
            actual="Only CLAUDE.md found (implicit orchestration)",
            fix_suggestion="Add agent cards or agent_config.yaml for explicit agent definitions",
            message="Orchestration: only CLAUDE.md found — consider adding agent definitions",
        ))
    else:
        checks.append(GotchaCheck(
            check_id="GOTCHA-02a",
            check_name="Orchestration layer present",
            layer="orchestration",
            status="fail",
            expected="Agent cards, agent_config.yaml, or CLAUDE.md",
            actual="None found",
            fix_suggestion="Run child_app_generator.py or create agent definitions manually",
            message="GOTCHA Layer 2 (Orchestration) missing: no agent definitions or CLAUDE.md",
        ))

    return checks


def _check_tools(project_dir: Path) -> List[GotchaCheck]:
    """Check Layer 3: Tools — deterministic Python scripts exist."""
    checks = []
    tools_dir = project_dir / "tools"

    if not tools_dir.is_dir():
        checks.append(GotchaCheck(
            check_id="GOTCHA-03",
            check_name="Tools directory exists",
            layer="tools",
            status="fail",
            expected="tools/ directory with deterministic Python scripts",
            actual="Directory not found",
            fix_suggestion="Create tools/ and add deterministic scripts (one job each)",
            message="GOTCHA Layer 3 (Tools) missing: no tools/ directory",
        ))
        return checks

    # Check for minimum tool subdirectories
    min_tool_dirs = {"db", "memory", "mcp"}
    tool_subdirs = {d.name for d in tools_dir.iterdir() if d.is_dir()}
    present_min = min_tool_dirs & tool_subdirs

    if len(tool_subdirs) >= 3:
        checks.append(GotchaCheck(
            check_id="GOTCHA-03a",
            check_name="Tool subdirectories present",
            layer="tools",
            status="pass",
            expected="At least 3 tool subdirectories",
            actual=f"{len(tool_subdirs)} subdirectories: {', '.join(sorted(tool_subdirs)[:8])}",
            fix_suggestion="",
            message=f"Tools: {len(tool_subdirs)} tool package(s) found",
        ))
    else:
        missing = min_tool_dirs - tool_subdirs
        checks.append(GotchaCheck(
            check_id="GOTCHA-03a",
            check_name="Tool subdirectories present",
            layer="tools",
            status="fail" if len(tool_subdirs) == 0 else "warn",
            expected=f"At least 3 tool subdirectories (recommended: {', '.join(sorted(min_tool_dirs))})",
            actual=f"{len(tool_subdirs)} subdirectory(ies)",
            fix_suggestion=f"Add missing tool directories: {', '.join(sorted(missing))}",
            message=f"Tools: only {len(tool_subdirs)} subdirectory(ies) — expected at least 3",
        ))

    # Check for Python files in tools
    py_files = list(tools_dir.rglob("*.py"))
    py_files = [f for f in py_files if "__pycache__" not in str(f)]
    if py_files:
        checks.append(GotchaCheck(
            check_id="GOTCHA-03b",
            check_name="Tool scripts present",
            layer="tools",
            status="pass",
            expected="Python scripts in tools/",
            actual=f"{len(py_files)} Python file(s)",
            fix_suggestion="",
            message=f"Tools: {len(py_files)} Python script(s) found",
        ))
    else:
        checks.append(GotchaCheck(
            check_id="GOTCHA-03b",
            check_name="Tool scripts present",
            layer="tools",
            status="fail",
            expected="At least 1 Python script in tools/",
            actual="0 Python files",
            fix_suggestion="Add deterministic Python tools following GOTCHA pattern",
            message="GOTCHA Layer 3 (Tools) empty: no Python scripts in tools/",
        ))

    return checks


def _check_args(project_dir: Path) -> List[GotchaCheck]:
    """Check Layer 4 (Args): YAML/JSON behavior settings exist."""
    checks = []
    args_dir = project_dir / "args"

    if not args_dir.is_dir():
        checks.append(GotchaCheck(
            check_id="GOTCHA-04",
            check_name="Args directory exists",
            layer="args",
            status="fail",
            expected="args/ directory with YAML/JSON config files",
            actual="Directory not found",
            fix_suggestion="Create args/ and add config files (project_defaults.yaml, etc.)",
            message="GOTCHA Layer 6 (Args) missing: no args/ directory",
        ))
        return checks

    yaml_files = list(args_dir.glob("*.yaml")) + list(args_dir.glob("*.yml"))
    json_files = list(args_dir.glob("*.json"))
    all_config = yaml_files + json_files

    if all_config:
        checks.append(GotchaCheck(
            check_id="GOTCHA-04a",
            check_name="Args config files present",
            layer="args",
            status="pass",
            expected="At least 1 YAML/JSON config file",
            actual=f"{len(all_config)} config file(s): {', '.join(f.name for f in all_config[:5])}",
            fix_suggestion="",
            message=f"Args: {len(all_config)} config file(s) found",
        ))
    else:
        checks.append(GotchaCheck(
            check_id="GOTCHA-04a",
            check_name="Args config files present",
            layer="args",
            status="fail",
            expected="At least 1 YAML/JSON config file",
            actual="0 config files",
            fix_suggestion="Add args/project_defaults.yaml, args/security_gates.yaml, etc.",
            message="GOTCHA Layer 6 (Args) empty: no config files found",
        ))

    return checks


def _check_context(project_dir: Path) -> List[GotchaCheck]:
    """Check Layer 5 (Context): static reference material exists."""
    checks = []
    context_dir = project_dir / "context"

    if not context_dir.is_dir():
        checks.append(GotchaCheck(
            check_id="GOTCHA-05",
            check_name="Context directory exists",
            layer="context",
            status="fail",
            expected="context/ directory with reference material",
            actual="Directory not found",
            fix_suggestion="Create context/ and add reference material (compliance catalogs, patterns)",
            message="GOTCHA Layer 5 (Context) missing: no context/ directory",
        ))
        return checks

    # Check for at least 1 subdirectory with content
    context_subdirs = [d for d in context_dir.iterdir() if d.is_dir()]
    non_empty_subdirs = [d for d in context_subdirs
                         if any(d.rglob("*")) and any(f.is_file() for f in d.rglob("*"))]

    if non_empty_subdirs:
        checks.append(GotchaCheck(
            check_id="GOTCHA-05a",
            check_name="Context subdirectories with content",
            layer="context",
            status="pass",
            expected="At least 1 context subdirectory with files",
            actual=f"{len(non_empty_subdirs)} context package(s): {', '.join(d.name for d in non_empty_subdirs[:5])}",
            fix_suggestion="",
            message=f"Context: {len(non_empty_subdirs)} reference package(s) found",
        ))
    else:
        checks.append(GotchaCheck(
            check_id="GOTCHA-05a",
            check_name="Context subdirectories with content",
            layer="context",
            status="fail",
            expected="At least 1 context subdirectory with files",
            actual="0 non-empty subdirectories",
            fix_suggestion="Add context/compliance/, context/languages/, or domain-specific reference material",
            message="GOTCHA Layer 5 (Context) empty: no reference material found",
        ))

    return checks


def _check_hardprompts(project_dir: Path) -> List[GotchaCheck]:
    """Check Layer 6 (Hard Prompts): reusable LLM instruction templates exist."""
    checks = []
    hp_dir = project_dir / "hardprompts"

    if not hp_dir.is_dir():
        checks.append(GotchaCheck(
            check_id="GOTCHA-06",
            check_name="Hard Prompts directory exists",
            layer="hardprompts",
            status="fail",
            expected="hardprompts/ directory with LLM instruction templates",
            actual="Directory not found",
            fix_suggestion="Create hardprompts/ and add LLM instruction templates (.md files)",
            message="GOTCHA Layer 4 (Hard Prompts) missing: no hardprompts/ directory",
        ))
        return checks

    md_files = list(hp_dir.rglob("*.md"))
    if md_files:
        checks.append(GotchaCheck(
            check_id="GOTCHA-06a",
            check_name="Hard prompt templates present",
            layer="hardprompts",
            status="pass",
            expected="At least 1 .md template in hardprompts/",
            actual=f"{len(md_files)} template(s): {', '.join(f.name for f in md_files[:5])}",
            fix_suggestion="",
            message=f"Hard Prompts: {len(md_files)} template(s) found",
        ))
    else:
        checks.append(GotchaCheck(
            check_id="GOTCHA-06a",
            check_name="Hard prompt templates present",
            layer="hardprompts",
            status="fail",
            expected="At least 1 .md template in hardprompts/",
            actual="0 templates (empty directory)",
            fix_suggestion="Add LLM instruction templates: hardprompts/agent/architect.md, etc.",
            message="GOTCHA Layer 4 (Hard Prompts) empty: no instruction templates found",
        ))

    return checks


def _check_claude_md(project_dir: Path) -> List[GotchaCheck]:
    """Check Meta: CLAUDE.md exists and references GOTCHA."""
    checks = []
    claude_md = project_dir / "CLAUDE.md"

    if not claude_md.exists():
        checks.append(GotchaCheck(
            check_id="GOTCHA-07",
            check_name="CLAUDE.md exists",
            layer="meta",
            status="fail",
            expected="CLAUDE.md with project documentation",
            actual="Not found",
            fix_suggestion="Generate CLAUDE.md using claude_md_generator.py or create manually",
            message="CLAUDE.md missing — project lacks AI orchestration documentation",
        ))
        return checks

    content = claude_md.read_text(encoding="utf-8", errors="replace")
    has_gotcha = "GOTCHA" in content or "gotcha" in content.lower()

    if has_gotcha:
        checks.append(GotchaCheck(
            check_id="GOTCHA-07",
            check_name="CLAUDE.md references GOTCHA",
            layer="meta",
            status="pass",
            expected="CLAUDE.md mentioning GOTCHA framework",
            actual="GOTCHA reference found",
            fix_suggestion="",
            message="CLAUDE.md found with GOTCHA framework reference",
        ))
    else:
        checks.append(GotchaCheck(
            check_id="GOTCHA-07",
            check_name="CLAUDE.md references GOTCHA",
            layer="meta",
            status="warn",
            expected="CLAUDE.md mentioning GOTCHA framework",
            actual="CLAUDE.md exists but no GOTCHA reference",
            fix_suggestion="Add GOTCHA framework section to CLAUDE.md documenting the 6-layer structure",
            message="CLAUDE.md exists but does not reference GOTCHA framework",
        ))

    return checks


def _check_memory(project_dir: Path) -> List[GotchaCheck]:
    """Check Meta: memory/MEMORY.md exists."""
    checks = []
    memory_md = project_dir / "memory" / "MEMORY.md"

    if memory_md.exists():
        checks.append(GotchaCheck(
            check_id="GOTCHA-08",
            check_name="Memory system present",
            layer="meta",
            status="pass",
            expected="memory/MEMORY.md",
            actual="Present",
            fix_suggestion="",
            message="Memory system found (memory/MEMORY.md)",
        ))
    else:
        checks.append(GotchaCheck(
            check_id="GOTCHA-08",
            check_name="Memory system present",
            layer="meta",
            status="warn",
            expected="memory/MEMORY.md",
            actual="Not found",
            fix_suggestion="Create memory/MEMORY.md with project identity and preferences",
            message="Memory system missing — create memory/MEMORY.md for long-term context",
        ))

    return checks


def _check_database(project_dir: Path) -> List[GotchaCheck]:
    """Check Meta: tools/db/ has an init script."""
    checks = []
    db_dir = project_dir / "tools" / "db"

    if not db_dir.is_dir():
        checks.append(GotchaCheck(
            check_id="GOTCHA-09",
            check_name="Database init script present",
            layer="meta",
            status="warn",
            expected="tools/db/ with database init script",
            actual="tools/db/ directory not found",
            fix_suggestion="Create tools/db/init_db.py with schema initialization",
            message="Database layer missing — no tools/db/ directory",
        ))
        return checks

    init_scripts = [f for f in db_dir.glob("init*.py")]
    if init_scripts:
        checks.append(GotchaCheck(
            check_id="GOTCHA-09",
            check_name="Database init script present",
            layer="meta",
            status="pass",
            expected="Database init script in tools/db/",
            actual=f"Found: {', '.join(f.name for f in init_scripts)}",
            fix_suggestion="",
            message=f"Database init script found: {init_scripts[0].name}",
        ))
    else:
        checks.append(GotchaCheck(
            check_id="GOTCHA-09",
            check_name="Database init script present",
            layer="meta",
            status="warn",
            expected="init_*.py in tools/db/",
            actual="No init scripts found",
            fix_suggestion="Add tools/db/init_db.py (or init_<appname>_db.py)",
            message="Database init script missing — add init script to tools/db/",
        ))

    return checks


def _check_atlas(project_dir: Path) -> List[GotchaCheck]:
    """Check Meta: ATLAS workflow (goals/build_app.md) exists."""
    checks = []
    build_app = project_dir / "goals" / "build_app.md"

    if build_app.exists():
        content = build_app.read_text(encoding="utf-8", errors="replace")
        has_atlas = "ATLAS" in content
        checks.append(GotchaCheck(
            check_id="GOTCHA-10",
            check_name="ATLAS workflow present",
            layer="meta",
            status="pass" if has_atlas else "warn",
            expected="goals/build_app.md with ATLAS workflow",
            actual="Present" + (" with ATLAS reference" if has_atlas else " but no ATLAS reference"),
            fix_suggestion="" if has_atlas else "Ensure build_app.md documents the ATLAS workflow",
            message="ATLAS workflow " + ("found" if has_atlas else "file exists but ATLAS not referenced"),
        ))
    else:
        checks.append(GotchaCheck(
            check_id="GOTCHA-10",
            check_name="ATLAS workflow present",
            layer="meta",
            status="warn",
            expected="goals/build_app.md with ATLAS workflow definition",
            actual="Not found",
            fix_suggestion="Copy build_app.md from ICDEV or create ATLAS workflow documentation",
            message="ATLAS workflow missing — no goals/build_app.md",
        ))

    return checks


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------

CHECK_REGISTRY = {
    "goals": _check_goals,
    "orchestration": _check_orchestration,
    "tools": _check_tools,
    "args": _check_args,
    "context": _check_context,
    "hardprompts": _check_hardprompts,
    "claude_md": _check_claude_md,
    "memory": _check_memory,
    "database": _check_database,
    "atlas": _check_atlas,
}


# ---------------------------------------------------------------------------
# Main validation function
# ---------------------------------------------------------------------------

def validate(
    project_dir: str | Path,
    checks: Optional[List[str]] = None,
) -> GotchaReport:
    """Run GOTCHA compliance validation on a project directory.

    Args:
        project_dir: Path to the project root directory.
        checks: Optional list of check IDs to run. If None, runs all checks.

    Returns:
        GotchaReport with validation results.
    """
    project_path = Path(project_dir).resolve()
    all_checks: List[GotchaCheck] = []

    checks_to_run = checks or list(CHECK_REGISTRY.keys())

    for check_name in checks_to_run:
        check_fn = CHECK_REGISTRY.get(check_name)
        if check_fn:
            try:
                results = check_fn(project_path)
                all_checks.extend(results)
            except Exception as e:
                all_checks.append(GotchaCheck(
                    check_id=f"GOTCHA-ERR-{check_name}",
                    check_name=f"Error running {check_name}",
                    layer=check_name,
                    status="fail",
                    expected="Check to run without errors",
                    actual=str(e),
                    fix_suggestion="Investigate the error and fix the underlying issue",
                    message=f"Check {check_name} raised an error: {e}",
                ))

    # Compute summary
    passed = sum(1 for c in all_checks if c.status == "pass")
    failed = sum(1 for c in all_checks if c.status == "fail")
    warned = sum(1 for c in all_checks if c.status == "warn")
    total = len(all_checks)
    overall_pass = failed == 0

    # Layer summary
    layers = {}
    for check in all_checks:
        layer = check.layer
        if layer not in layers:
            layers[layer] = "pass"
        if check.status == "fail":
            layers[layer] = "fail"
        elif check.status == "warn" and layers[layer] != "fail":
            layers[layer] = "warn"

    score = passed / total if total > 0 else 0.0

    return GotchaReport(
        overall_pass=overall_pass,
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        project_dir=str(project_path),
        total_checks=total,
        passed_checks=passed,
        failed_checks=failed,
        warned_checks=warned,
        layer_summary=layers,
        score=round(score, 3),
        checks=all_checks,
    )


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------

def _format_human(report: GotchaReport) -> str:
    """Format report as colored terminal output."""
    lines = []
    lines.append("")
    lines.append("=" * 65)
    lines.append("  GOTCHA Framework Compliance Validation")
    lines.append("=" * 65)
    lines.append(f"  Project:  {report.project_dir}")
    lines.append(f"  Score:    {report.score:.0%} ({report.passed_checks}/{report.total_checks} passed)")
    status_label = "PASS" if report.overall_pass else "FAIL"
    lines.append(f"  Status:   {status_label}")
    lines.append("-" * 65)

    # Layer summary
    lines.append("")
    lines.append("  Layer Summary:")
    layer_order = ["goals", "orchestration", "tools", "args", "context", "hardprompts", "meta"]
    layer_labels = {
        "goals": "1. Goals",
        "orchestration": "2. Orchestration",
        "tools": "3. Tools",
        "args": "4. Args",
        "context": "5. Context",
        "hardprompts": "6. Hard Prompts",
        "meta": "   Meta Checks",
    }
    for layer in layer_order:
        if layer in report.layer_summary:
            status = report.layer_summary[layer]
            icon = "[OK]" if status == "pass" else ("[!!]" if status == "fail" else "[??]")
            label = layer_labels.get(layer, layer)
            lines.append(f"    {icon} {label}")

    # Individual checks
    lines.append("")
    lines.append("  Check Details:")
    for check in report.checks:
        icon = "[OK]" if check.passed else ("[!!]" if check.status == "fail" else "[??]")
        lines.append(f"    {icon} {check.check_id}: {check.check_name}")
        if check.status != "pass":
            lines.append(f"        {check.message}")
            if check.fix_suggestion:
                lines.append(f"        Fix: {check.fix_suggestion}")

    lines.append("")
    lines.append("=" * 65)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="GOTCHA Framework Compliance Validator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/builder/gotcha_validator.py --project-dir . --human\n"
            "  python tools/builder/gotcha_validator.py --project-dir /path/to/child --json\n"
            "  python tools/builder/gotcha_validator.py --project-dir /path/to/child --gate\n"
            "  python tools/builder/gotcha_validator.py --project-dir . --check goals --json\n"
        ),
    )
    parser.add_argument("--project-dir", required=True,
                        help="Path to the project directory to validate")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    parser.add_argument("--human", action="store_true",
                        help="Output results as human-readable text")
    parser.add_argument("--gate", action="store_true",
                        help="Exit with code 1 if any checks fail (for CI/CD gates)")
    parser.add_argument("--check", choices=list(CHECK_REGISTRY.keys()) + ["all"],
                        help="Run a specific check (default: all)")

    args = parser.parse_args()

    project_dir = Path(args.project_dir)
    if not project_dir.is_dir():
        print(f"Error: Directory not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    checks = None
    if args.check and args.check != "all":
        checks = [args.check]

    report = validate(project_dir, checks=checks)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    elif args.human or not args.json:
        print(_format_human(report))

    if args.gate and not report.overall_pass:
        sys.exit(1)


if __name__ == "__main__":
    main()
