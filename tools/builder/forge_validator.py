#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""FORGE Framework Compliance Validator.

Validates that a project directory conforms to the 6-layer FORGE framework
and ANVIL workflow structure. Designed to run post-generation on child apps
or standalone against any ICDEV™-compatible project.

The 6 FORGE layers:
  1. Goals       — goals/ with manifest + workflow files
  2. Orchestration — agent cards, agent config, or CLAUDE.md orchestration
  3. Tools       — tools/ with deterministic Python scripts
  4. Context     — context/ with reference material
  5. Hard Prompts — hardprompts/ with LLM instruction templates
  6. Args        — args/ with YAML/JSON behavior settings

Additional BMAD-adapted quality checks:
  7. CLAUDE.md   — project documentation referencing FORGE
  8. Memory      — memory/MEMORY.md for long-term context
  9. Database    — tools/db/ with init script
  10. ANVIL      — goals/build_app.md (ANVIL workflow present)

Hardening checks (cvx-gen-01 — prevent broken child apps passing --gate):
  FORGE-03c  — anti-hallucination grounding modules present AND carry the
               current API (content_grounding.ground_content,
               citation_grounding.classify_confidence). A stale pre-
               ``ground_content`` snapshot FAILS, not just an absent file.
  FORGE-11   — coherence checker MISSING (no tools/workflow/coherence_checker.py)
               is an explicit FAIL, not a silent pass. tools/workflow is always
               shipped by DIRECTORY_TREE, so absence means a stale/incomplete child.
  FORGE-12   — banned DB patterns: sqlite3.connect() outside db/init_db.py/tests
               FAILS; bare '?' SQLite-dialect placeholders in runtime files WARN.

Completeness gate (cvx-gen-02 — generated canvases must ship all 8 components):
  FORGE-13   — 8-component completeness gate for every canvas the child declares.
               Reuses tools.config.component_registry.validate_canvas_completeness
               (the same validator the parent coherence checker runs — no 8-point
               logic duplicated), loading the CHILD's own
               args/component_registry.yaml and pointing repo_root at the child
               tree. A generated canvas missing a component — template in either
               tree, blueprint route, backing module, constants, DB migration, nav
               link, or IQE integration — FAILS. No registry / no canvases = pass
               (nothing to validate); a missing parent validator SKIPS (pass) so a
               stale toolchain never false-fails an otherwise-good child.

Decision D44: Flag-based backward compatibility (--gate for CI/CD blocking).
Pattern: Follows claude_dir_validator.py declarative check registry.

Usage:
    python tools/builder/forge_validator.py --project-dir /path/to/app --json
    python tools/builder/forge_validator.py --project-dir /path/to/app --human
    python tools/builder/forge_validator.py --project-dir /path/to/app --gate
    python tools/builder/forge_validator.py --project-dir /path/to/app --check goals --json

Exit codes: 0 = all checks pass, 1 = at least one check failed
"""

import argparse
import dataclasses
import json
import re
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
    """Result of a single FORGE compliance check."""

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
    """Aggregate FORGE compliance validation report."""

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
        checks.append(
            GotchaCheck(
                check_id="FORGE-01",
                check_name="Goals directory exists",
                layer="goals",
                status="fail",
                expected="goals/ directory with workflow definitions",
                actual="Directory not found",
                fix_suggestion="Create goals/ and add workflow files (build_app.md, manifest.md)",
                message="FORGE Layer 1 (Goals) missing: no goals/ directory",
            )
        )
        return checks

    # Check for manifest
    manifest = goals_dir / "manifest.md"
    if manifest.exists():
        checks.append(
            GotchaCheck(
                check_id="FORGE-01a",
                check_name="Goals manifest exists",
                layer="goals",
                status="pass",
                expected="goals/manifest.md",
                actual="Present",
                fix_suggestion="",
                message="Goals manifest found",
            )
        )
    else:
        checks.append(
            GotchaCheck(
                check_id="FORGE-01a",
                check_name="Goals manifest exists",
                layer="goals",
                status="warn",
                expected="goals/manifest.md",
                actual="Missing",
                fix_suggestion="Create goals/manifest.md listing all goal workflows",
                message="Goals manifest missing — create manifest.md indexing all goals",
            )
        )

    # Check for at least 1 goal file (not counting manifest)
    goal_files = [f for f in goals_dir.glob("*.md") if f.name != "manifest.md"]
    if goal_files:
        checks.append(
            GotchaCheck(
                check_id="FORGE-01b",
                check_name="Goal workflow files present",
                layer="goals",
                status="pass",
                expected="At least 1 goal workflow file",
                actual=f"{len(goal_files)} goal file(s): {', '.join(f.name for f in goal_files[:5])}",
                fix_suggestion="",
                message=f"{len(goal_files)} goal workflow(s) found",
            )
        )
    else:
        checks.append(
            GotchaCheck(
                check_id="FORGE-01b",
                check_name="Goal workflow files present",
                layer="goals",
                status="fail",
                expected="At least 1 goal workflow file (e.g., build_app.md)",
                actual="0 goal files (empty directory)",
                fix_suggestion="Add goal files: build_app.md (ANVIL), tdd_workflow.md, compliance_workflow.md",
                message="FORGE Layer 1 (Goals) empty: no workflow definitions found",
            )
        )

    # FORGE-01c: Goal content quality — files must have substance
    for gf in goal_files:
        try:
            content = gf.read_text(encoding="utf-8")
            if len(content.strip()) < 100:
                checks.append(
                    GotchaCheck(
                        check_id="FORGE-01c",
                        check_name="Goal content quality",
                        layer="goals",
                        status="warn",
                        expected="Goal file with >=100 chars of content",
                        actual=f"{gf.name}: {len(content.strip())} chars (stub)",
                        fix_suggestion=f"Add workflow steps and acceptance criteria to {gf.name}",
                        message=f"Goal file {gf.name} appears to be a stub ({len(content.strip())} chars)",
                    )
                )
        except Exception:
            pass

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
        checks.append(
            GotchaCheck(
                check_id="FORGE-02a",
                check_name="Agent cards present",
                layer="orchestration",
                status="pass",
                expected="Agent card JSON files in tools/agent/cards/",
                actual=f"{card_count} agent card(s) found",
                fix_suggestion="",
                message=f"Orchestration: {card_count} agent card(s) found",
            )
        )
    elif has_config:
        checks.append(
            GotchaCheck(
                check_id="FORGE-02a",
                check_name="Agent config present",
                layer="orchestration",
                status="pass",
                expected="Agent cards or args/agent_config.yaml",
                actual="args/agent_config.yaml found",
                fix_suggestion="",
                message="Orchestration: agent_config.yaml found (no individual cards)",
            )
        )
    elif has_claude_md:
        # CLAUDE.md exists — orchestration is implicit (Claude is the orchestrator)
        checks.append(
            GotchaCheck(
                check_id="FORGE-02a",
                check_name="Orchestration layer present",
                layer="orchestration",
                status="warn",
                expected="Agent cards in tools/agent/cards/ or args/agent_config.yaml",
                actual="Only CLAUDE.md found (implicit orchestration)",
                fix_suggestion="Add agent cards or agent_config.yaml for explicit agent definitions",
                message="Orchestration: only CLAUDE.md found — consider adding agent definitions",
            )
        )
    else:
        checks.append(
            GotchaCheck(
                check_id="FORGE-02a",
                check_name="Orchestration layer present",
                layer="orchestration",
                status="fail",
                expected="Agent cards, agent_config.yaml, or CLAUDE.md",
                actual="None found",
                fix_suggestion="Run child_app_generator.py or create agent definitions manually",
                message="FORGE Layer 2 (Orchestration) missing: no agent definitions or CLAUDE.md",
            )
        )

    return checks


def _check_tools(project_dir: Path) -> List[GotchaCheck]:
    """Check Layer 3: Tools — deterministic Python scripts exist."""
    checks = []
    tools_dir = project_dir / "tools"

    if not tools_dir.is_dir():
        checks.append(
            GotchaCheck(
                check_id="FORGE-03",
                check_name="Tools directory exists",
                layer="tools",
                status="fail",
                expected="tools/ directory with deterministic Python scripts",
                actual="Directory not found",
                fix_suggestion="Create tools/ and add deterministic scripts (one job each)",
                message="FORGE Layer 3 (Tools) missing: no tools/ directory",
            )
        )
        return checks

    # Check for minimum tool subdirectories
    min_tool_dirs = {"db", "memory", "mcp"}
    tool_subdirs = {d.name for d in tools_dir.iterdir() if d.is_dir()}
    if len(tool_subdirs) >= 3:
        checks.append(
            GotchaCheck(
                check_id="FORGE-03a",
                check_name="Tool subdirectories present",
                layer="tools",
                status="pass",
                expected="At least 3 tool subdirectories",
                actual=f"{len(tool_subdirs)} subdirectories: {', '.join(sorted(tool_subdirs)[:8])}",
                fix_suggestion="",
                message=f"Tools: {len(tool_subdirs)} tool package(s) found",
            )
        )
    else:
        missing = min_tool_dirs - tool_subdirs
        checks.append(
            GotchaCheck(
                check_id="FORGE-03a",
                check_name="Tool subdirectories present",
                layer="tools",
                status="fail" if len(tool_subdirs) == 0 else "warn",
                expected=f"At least 3 tool subdirectories (recommended: {', '.join(sorted(min_tool_dirs))})",
                actual=f"{len(tool_subdirs)} subdirectory(ies)",
                fix_suggestion=f"Add missing tool directories: {', '.join(sorted(missing))}",
                message=f"Tools: only {len(tool_subdirs)} subdirectory(ies) — expected at least 3",
            )
        )

    # Check for Python files in tools
    py_files = list(tools_dir.rglob("*.py"))
    py_files = [f for f in py_files if "__pycache__" not in str(f)]
    if py_files:
        checks.append(
            GotchaCheck(
                check_id="FORGE-03b",
                check_name="Tool scripts present",
                layer="tools",
                status="pass",
                expected="Python scripts in tools/",
                actual=f"{len(py_files)} Python file(s)",
                fix_suggestion="",
                message=f"Tools: {len(py_files)} Python script(s) found",
            )
        )
    else:
        checks.append(
            GotchaCheck(
                check_id="FORGE-03b",
                check_name="Tool scripts present",
                layer="tools",
                status="fail",
                expected="At least 1 Python script in tools/",
                actual="0 Python files",
                fix_suggestion="Add deterministic Python tools following FORGE pattern",
                message="FORGE Layer 3 (Tools) empty: no Python scripts in tools/",
            )
        )

    # trust-cite-05: anti-hallucination grounding must ship with every child app
    # so generated apps cite sources / gate placeholders like the parent does.
    # FORGE-03c also enforces API-FRESHNESS: presence alone let a stale pre-
    # `ground_content` snapshot pass. The modules must carry their canonical
    # public API — ground_content() and classify_confidence() — or the child is
    # running an outdated grounding copy that silently no-ops.
    grounding = ["tools/quality/content_grounding.py", "tools/quality/citation_grounding.py"]
    missing_grounding = [g for g in grounding if not (project_dir / g).is_file()]
    # Canonical public symbols that must exist in each grounding module.
    api_markers = {
        "tools/quality/content_grounding.py": "def ground_content(",
        "tools/quality/citation_grounding.py": "def classify_confidence(",
    }
    stale_grounding: List[str] = []
    if not missing_grounding:
        for rel, marker in api_markers.items():
            try:
                text = (project_dir / rel).read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""
            if marker not in text:
                stale_grounding.append(f"{rel} (missing '{marker.rstrip('(')}')")
    if missing_grounding:
        _g_status = "fail"
        _g_actual = f"missing: {', '.join(missing_grounding)}"
        _g_fix = ("Ensure 'tools/quality' is in DIRECTORY_TREE so the shared "
                  "grounding modules are copied into the child app")
        _g_msg = f"Grounding modules missing: {', '.join(missing_grounding)}"
    elif stale_grounding:
        _g_status = "fail"
        _g_actual = f"present but stale API: {', '.join(stale_grounding)}"
        _g_fix = ("Re-copy the current tools/quality grounding modules — the child "
                  "carries a pre-`ground_content` snapshot missing the canonical API")
        _g_msg = ("Grounding modules present but stale (missing current API): "
                  f"{', '.join(stale_grounding)}")
    else:
        _g_status = "pass"
        _g_actual = "present with current API (ground_content, classify_confidence)"
        _g_fix = ""
        _g_msg = "Grounding: content + citation grounding modules present with current API"
    checks.append(
        GotchaCheck(
            check_id="FORGE-03c",
            check_name="Anti-hallucination grounding modules present and current",
            layer="tools",
            status=_g_status,
            expected="tools/quality/{content_grounding.py::ground_content, "
                     "citation_grounding.py::classify_confidence}",
            actual=_g_actual,
            fix_suggestion=_g_fix,
            message=_g_msg,
        )
    )

    return checks


def _check_args(project_dir: Path) -> List[GotchaCheck]:
    """Check Layer 4 (Args): YAML/JSON behavior settings exist."""
    checks = []
    args_dir = project_dir / "args"

    if not args_dir.is_dir():
        checks.append(
            GotchaCheck(
                check_id="FORGE-04",
                check_name="Args directory exists",
                layer="args",
                status="fail",
                expected="args/ directory with YAML/JSON config files",
                actual="Directory not found",
                fix_suggestion="Create args/ and add config files (project_defaults.yaml, etc.)",
                message="FORGE Layer 6 (Args) missing: no args/ directory",
            )
        )
        return checks

    yaml_files = list(args_dir.glob("*.yaml")) + list(args_dir.glob("*.yml"))
    json_files = list(args_dir.glob("*.json"))
    all_config = yaml_files + json_files

    if all_config:
        checks.append(
            GotchaCheck(
                check_id="FORGE-04a",
                check_name="Args config files present",
                layer="args",
                status="pass",
                expected="At least 1 YAML/JSON config file",
                actual=f"{len(all_config)} config file(s): {', '.join(f.name for f in all_config[:5])}",
                fix_suggestion="",
                message=f"Args: {len(all_config)} config file(s) found",
            )
        )
    else:
        checks.append(
            GotchaCheck(
                check_id="FORGE-04a",
                check_name="Args config files present",
                layer="args",
                status="fail",
                expected="At least 1 YAML/JSON config file",
                actual="0 config files",
                fix_suggestion="Add args/project_defaults.yaml, args/security_gates.yaml, etc.",
                message="FORGE Layer 6 (Args) empty: no config files found",
            )
        )

    # ONTO: ontology inheritance check (onto-eco-05)
    ontology_dir = args_dir / "ontology"
    if ontology_dir.is_dir():
        ttl_files = list(ontology_dir.glob("*.ttl"))
        yaml_files_onto = list(ontology_dir.glob("*.yaml"))
        if ttl_files:
            checks.append(
                GotchaCheck(
                    check_id="FORGE-04b",
                    check_name="Ontology files present",
                    layer="args",
                    status="pass",
                    expected="args/ontology/*.ttl and *.yaml",
                    actual=f"{len(ttl_files)} TTL, {len(yaml_files_onto)} YAML",
                    fix_suggestion="",
                    message=f"Ontology: {len(ttl_files)} TTL + {len(yaml_files_onto)} YAML found",
                )
            )
        else:
            checks.append(
                GotchaCheck(
                    check_id="FORGE-04b",
                    check_name="Ontology files present",
                    layer="args",
                    status="warn",
                    expected="args/ontology/*.ttl",
                    actual="0 TTL files",
                    fix_suggestion="Add args/ontology/app.ttl extending parent ontology",
                    message="Ontology directory exists but no .ttl files found",
                )
            )

        # Validate owl:imports in app.ttl
        app_ttl = ontology_dir / "app.ttl"
        if app_ttl.exists():
            content = app_ttl.read_text(encoding="utf-8")
            if "owl:imports" in content:
                checks.append(
                    GotchaCheck(
                        check_id="FORGE-04c",
                        check_name="Ontology imports parent",
                        layer="args",
                        status="pass",
                        expected="owl:imports in app.ttl",
                        actual="owl:imports found",
                        fix_suggestion="",
                        message="Ontology: app.ttl imports parent ontology",
                    )
                )
            else:
                checks.append(
                    GotchaCheck(
                        check_id="FORGE-04c",
                        check_name="Ontology imports parent",
                        layer="args",
                        status="fail",
                        expected="owl:imports in app.ttl",
                        actual="owl:imports missing",
                        fix_suggestion="Add owl:imports pointing to parent ontology",
                        message="Ontology: app.ttl missing owl:imports (parent link)",
                    )
                )
    else:
        checks.append(
            GotchaCheck(
                check_id="FORGE-04b",
                check_name="Ontology directory present",
                layer="args",
                status="warn",
                expected="args/ontology/ directory",
                actual="Directory not found",
                fix_suggestion="Create args/ontology/ with app.ttl and app_config.yaml",
                message="Ontology directory missing — child apps should extend parent ontology",
            )
        )

    return checks


def _check_context(project_dir: Path) -> List[GotchaCheck]:
    """Check Layer 5 (Context): static reference material exists."""
    checks = []
    context_dir = project_dir / "context"

    if not context_dir.is_dir():
        checks.append(
            GotchaCheck(
                check_id="FORGE-05",
                check_name="Context directory exists",
                layer="context",
                status="fail",
                expected="context/ directory with reference material",
                actual="Directory not found",
                fix_suggestion="Create context/ and add reference material (compliance catalogs, patterns)",
                message="FORGE Layer 5 (Context) missing: no context/ directory",
            )
        )
        return checks

    # Check for at least 1 subdirectory with content
    context_subdirs = [d for d in context_dir.iterdir() if d.is_dir()]
    non_empty_subdirs = [d for d in context_subdirs if any(d.rglob("*")) and any(f.is_file() for f in d.rglob("*"))]

    if non_empty_subdirs:
        checks.append(
            GotchaCheck(
                check_id="FORGE-05a",
                check_name="Context subdirectories with content",
                layer="context",
                status="pass",
                expected="At least 1 context subdirectory with files",
                actual=f"{len(non_empty_subdirs)} context package(s): {', '.join(d.name for d in non_empty_subdirs[:5])}",
                fix_suggestion="",
                message=f"Context: {len(non_empty_subdirs)} reference package(s) found",
            )
        )
    else:
        checks.append(
            GotchaCheck(
                check_id="FORGE-05a",
                check_name="Context subdirectories with content",
                layer="context",
                status="fail",
                expected="At least 1 context subdirectory with files",
                actual="0 non-empty subdirectories",
                fix_suggestion="Add context/compliance/, context/languages/, or domain-specific reference material",
                message="FORGE Layer 5 (Context) empty: no reference material found",
            )
        )

    return checks


def _check_hardprompts(project_dir: Path) -> List[GotchaCheck]:
    """Check Layer 6 (Hard Prompts): reusable LLM instruction templates exist."""
    checks = []
    hp_dir = project_dir / "hardprompts"

    if not hp_dir.is_dir():
        checks.append(
            GotchaCheck(
                check_id="FORGE-06",
                check_name="Hard Prompts directory exists",
                layer="hardprompts",
                status="fail",
                expected="hardprompts/ directory with LLM instruction templates",
                actual="Directory not found",
                fix_suggestion="Create hardprompts/ and add LLM instruction templates (.md files)",
                message="FORGE Layer 4 (Hard Prompts) missing: no hardprompts/ directory",
            )
        )
        return checks

    md_files = list(hp_dir.rglob("*.md"))
    if md_files:
        checks.append(
            GotchaCheck(
                check_id="FORGE-06a",
                check_name="Hard prompt templates present",
                layer="hardprompts",
                status="pass",
                expected="At least 1 .md template in hardprompts/",
                actual=f"{len(md_files)} template(s): {', '.join(f.name for f in md_files[:5])}",
                fix_suggestion="",
                message=f"Hard Prompts: {len(md_files)} template(s) found",
            )
        )
    else:
        checks.append(
            GotchaCheck(
                check_id="FORGE-06a",
                check_name="Hard prompt templates present",
                layer="hardprompts",
                status="fail",
                expected="At least 1 .md template in hardprompts/",
                actual="0 templates (empty directory)",
                fix_suggestion="Add LLM instruction templates: hardprompts/agent/architect.md, etc.",
                message="FORGE Layer 4 (Hard Prompts) empty: no instruction templates found",
            )
        )

    return checks


def _check_claude_md(project_dir: Path) -> List[GotchaCheck]:
    """Check Meta: CLAUDE.md exists and references FORGE."""
    checks = []
    claude_md = project_dir / "CLAUDE.md"

    if not claude_md.exists():
        checks.append(
            GotchaCheck(
                check_id="FORGE-07",
                check_name="CLAUDE.md exists",
                layer="meta",
                status="fail",
                expected="CLAUDE.md with project documentation",
                actual="Not found",
                fix_suggestion="Generate CLAUDE.md using claude_md_generator.py or create manually",
                message="CLAUDE.md missing — project lacks AI orchestration documentation",
            )
        )
        return checks

    content = claude_md.read_text(encoding="utf-8", errors="replace")
    has_gotcha = "FORGE" in content or "gotcha" in content.lower()

    if has_gotcha:
        checks.append(
            GotchaCheck(
                check_id="FORGE-07",
                check_name="CLAUDE.md references FORGE",
                layer="meta",
                status="pass",
                expected="CLAUDE.md mentioning FORGE framework",
                actual="FORGE reference found",
                fix_suggestion="",
                message="CLAUDE.md found with FORGE framework reference",
            )
        )
    else:
        checks.append(
            GotchaCheck(
                check_id="FORGE-07",
                check_name="CLAUDE.md references FORGE",
                layer="meta",
                status="warn",
                expected="CLAUDE.md mentioning FORGE framework",
                actual="CLAUDE.md exists but no FORGE reference",
                fix_suggestion="Add FORGE framework section to CLAUDE.md documenting the 6-layer structure",
                message="CLAUDE.md exists but does not reference FORGE framework",
            )
        )

    return checks


def _check_memory(project_dir: Path) -> List[GotchaCheck]:
    """Check Meta: memory/MEMORY.md exists."""
    checks = []
    memory_md = project_dir / "memory" / "MEMORY.md"

    if memory_md.exists():
        checks.append(
            GotchaCheck(
                check_id="FORGE-08",
                check_name="Memory system present",
                layer="meta",
                status="pass",
                expected="memory/MEMORY.md",
                actual="Present",
                fix_suggestion="",
                message="Memory system found (memory/MEMORY.md)",
            )
        )
    else:
        checks.append(
            GotchaCheck(
                check_id="FORGE-08",
                check_name="Memory system present",
                layer="meta",
                status="warn",
                expected="memory/MEMORY.md",
                actual="Not found",
                fix_suggestion="Create memory/MEMORY.md with project identity and preferences",
                message="Memory system missing — create memory/MEMORY.md for long-term context",
            )
        )

    return checks


def _check_database(project_dir: Path) -> List[GotchaCheck]:
    """Check Meta: tools/db/ has an init script."""
    checks = []
    db_dir = project_dir / "tools" / "db"

    if not db_dir.is_dir():
        checks.append(
            GotchaCheck(
                check_id="FORGE-09",
                check_name="Database init script present",
                layer="meta",
                status="warn",
                expected="tools/db/ with database init script",
                actual="tools/db/ directory not found",
                fix_suggestion="Create tools/db/init_db.py with schema initialization",
                message="Database layer missing — no tools/db/ directory",
            )
        )
        return checks

    init_scripts = [f for f in db_dir.glob("init*.py")]
    if init_scripts:
        checks.append(
            GotchaCheck(
                check_id="FORGE-09",
                check_name="Database init script present",
                layer="meta",
                status="pass",
                expected="Database init script in tools/db/",
                actual=f"Found: {', '.join(f.name for f in init_scripts)}",
                fix_suggestion="",
                message=f"Database init script found: {init_scripts[0].name}",
            )
        )
    else:
        checks.append(
            GotchaCheck(
                check_id="FORGE-09",
                check_name="Database init script present",
                layer="meta",
                status="warn",
                expected="init_*.py in tools/db/",
                actual="No init scripts found",
                fix_suggestion="Add tools/db/init_db.py (or init_<appname>_db.py)",
                message="Database init script missing — add init script to tools/db/",
            )
        )

    return checks


def _check_atlas(project_dir: Path) -> List[GotchaCheck]:
    """Check Meta: ANVIL workflow (goals/build_app.md) exists."""
    checks = []
    build_app = project_dir / "goals" / "build_app.md"

    if build_app.exists():
        content = build_app.read_text(encoding="utf-8", errors="replace")
        has_atlas = "ANVIL" in content
        checks.append(
            GotchaCheck(
                check_id="FORGE-10",
                check_name="ANVIL workflow present",
                layer="meta",
                status="pass" if has_atlas else "warn",
                expected="goals/build_app.md with ANVIL workflow",
                actual="Present" + (" with ANVIL reference" if has_atlas else " but no ANVIL reference"),
                fix_suggestion="" if has_atlas else "Ensure build_app.md documents the ANVIL workflow",
                message="ANVIL workflow " + ("found" if has_atlas else "file exists but ANVIL not referenced"),
            )
        )
    else:
        checks.append(
            GotchaCheck(
                check_id="FORGE-10",
                check_name="ANVIL workflow present",
                layer="meta",
                status="warn",
                expected="goals/build_app.md with ANVIL workflow definition",
                actual="Not found",
                fix_suggestion="Copy build_app.md from ICDEV™ or create ANVIL workflow documentation",
                message="ANVIL workflow missing — no goals/build_app.md",
            )
        )

    return checks


def _check_child_app_templates(project_dir: Path) -> List[GotchaCheck]:
    """Check Template Layer: child-app flavor templates are well-formed.

    Only applies when the project being validated contains the parent
    template directory (`data/templates/child_apps/`). Child apps generated
    from these flavors are expected to satisfy the files/validators declared
    in each flavor's manifest.yaml.
    """
    checks = []
    templates_dir = project_dir / "data" / "templates" / "child_apps"
    if not templates_dir.is_dir():
        checks.append(
            GotchaCheck(
                check_id="FORGE-TPL-00",
                check_name="Child-app templates directory present",
                layer="templates",
                status="pass",
                expected="data/templates/child_apps/ (optional)",
                actual="Not present (skipped)",
                fix_suggestion="",
                message="Child-app template validation skipped: no data/templates/child_apps/ directory",
            )
        )
        return checks

    flavors = sorted([d for d in templates_dir.iterdir() if d.is_dir()])
    if not flavors:
        checks.append(
            GotchaCheck(
                check_id="FORGE-TPL-00",
                check_name="Child-app flavor directories present",
                layer="templates",
                status="warn",
                expected="At least one flavor directory under data/templates/child_apps/",
                actual="0 flavor directories",
                fix_suggestion="Add flavor templates: minimal, compliance, ai-lab, govcon",
                message="No child-app flavor templates found",
            )
        )
        return checks

    import yaml

    for flavor_dir in flavors:
        manifest_path = flavor_dir / "manifest.yaml"
        if not manifest_path.exists():
            checks.append(
                GotchaCheck(
                    check_id=f"FORGE-TPL-{flavor_dir.name}-01",
                    check_name=f"Flavor manifest exists ({flavor_dir.name})",
                    layer="templates",
                    status="fail",
                    expected=f"{flavor_dir.name}/manifest.yaml",
                    actual="Missing",
                    fix_suggestion=f"Create data/templates/child_apps/{flavor_dir.name}/manifest.yaml describing the flavor",
                    message=f"Flavor '{flavor_dir.name}' has no manifest.yaml",
                )
            )
            continue

        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            checks.append(
                GotchaCheck(
                    check_id=f"FORGE-TPL-{flavor_dir.name}-02",
                    check_name=f"Flavor manifest parses ({flavor_dir.name})",
                    layer="templates",
                    status="fail",
                    expected="Valid YAML manifest",
                    actual=f"Parse error: {exc}",
                    fix_suggestion=f"Fix YAML syntax in {manifest_path}",
                    message=f"Flavor '{flavor_dir.name}' manifest.yaml is not valid YAML: {exc}",
                )
            )
            continue

        if not isinstance(manifest, dict):
            checks.append(
                GotchaCheck(
                    check_id=f"FORGE-TPL-{flavor_dir.name}-02",
                    check_name=f"Flavor manifest structure ({flavor_dir.name})",
                    layer="templates",
                    status="fail",
                    expected="Mapping with name, kind, files",
                    actual=f"Top-level type: {type(manifest).__name__}",
                    fix_suggestion="Ensure manifest.yaml is a YAML mapping",
                    message=f"Flavor '{flavor_dir.name}' manifest is not a mapping",
                )
            )
            continue

        required_keys = {"name", "kind", "files"}
        missing_keys = required_keys - set(manifest.keys())
        if missing_keys:
            checks.append(
                GotchaCheck(
                    check_id=f"FORGE-TPL-{flavor_dir.name}-03",
                    check_name=f"Flavor manifest required keys ({flavor_dir.name})",
                    layer="templates",
                    status="fail",
                    expected=f"Keys: {sorted(required_keys)}",
                    actual=f"Missing: {sorted(missing_keys)}",
                    fix_suggestion=f"Add {sorted(missing_keys)} to {manifest_path}",
                    message=f"Flavor '{flavor_dir.name}' manifest missing keys: {sorted(missing_keys)}",
                )
            )
            continue

        checks.append(
            GotchaCheck(
                check_id=f"FORGE-TPL-{flavor_dir.name}-03",
                check_name=f"Flavor manifest required keys ({flavor_dir.name})",
                layer="templates",
                status="pass",
                expected="name, kind, files present",
                actual="All required keys present",
                fix_suggestion="",
                message=f"Flavor '{flavor_dir.name}' manifest has required keys",
            )
        )

        files = manifest.get("files", [])
        missing_src: List[str] = []
        for fdef in files:
            if not isinstance(fdef, dict):
                continue
            src = fdef.get("src")
            if not src:
                continue
            src_path = flavor_dir / src
            if not src_path.exists() and not (flavor_dir / (src + ".j2")).exists():
                missing_src.append(src)

        if missing_src:
            checks.append(
                GotchaCheck(
                    check_id=f"FORGE-TPL-{flavor_dir.name}-04",
                    check_name=f"Flavor template source files exist ({flavor_dir.name})",
                    layer="templates",
                    status="fail",
                    expected="All src paths in manifest files[] exist in flavor dir",
                    actual=f"Missing: {', '.join(missing_src[:5])}",
                    fix_suggestion=f"Add missing template files to data/templates/child_apps/{flavor_dir.name}/",
                    message=f"Flavor '{flavor_dir.name}' missing template source files: {missing_src[:5]}",
                )
            )
        else:
            checks.append(
                GotchaCheck(
                    check_id=f"FORGE-TPL-{flavor_dir.name}-04",
                    check_name=f"Flavor template source files exist ({flavor_dir.name})",
                    layer="templates",
                    status="pass",
                    expected="All src paths in manifest files[] exist",
                    actual=f"{len(files)} file mapping(s) valid",
                    fix_suggestion="",
                    message=f"Flavor '{flavor_dir.name}' template source files all present",
                )
            )

        validators = manifest.get("validators", [])
        if validators:
            checks.append(
                GotchaCheck(
                    check_id=f"FORGE-TPL-{flavor_dir.name}-05",
                    check_name=f"Flavor validators declared ({flavor_dir.name})",
                    layer="templates",
                    status="pass",
                    expected="At least one validator",
                    actual=f"{len(validators)} validator(s)",
                    fix_suggestion="",
                    message=f"Flavor '{flavor_dir.name}' declares {len(validators)} validator(s)",
                )
            )
        else:
            checks.append(
                GotchaCheck(
                    check_id=f"FORGE-TPL-{flavor_dir.name}-05",
                    check_name=f"Flavor validators declared ({flavor_dir.name})",
                    layer="templates",
                    status="warn",
                    expected="At least one validator",
                    actual="0 validators",
                    fix_suggestion="Add file_exists / python_syntax validators to manifest.yaml",
                    message=f"Flavor '{flavor_dir.name}' has no validators declared",
                )
            )

    return checks


def _check_coherence(project_dir: Path) -> List[GotchaCheck]:
    """Check Meta: FORGE-11 — Implementation coherence validation.

    tools/workflow is always shipped by DIRECTORY_TREE, so a child MISSING the
    coherence_checker module is stale/incomplete — that is an explicit FAIL, not
    a silent pass. Previously an ImportError here recorded a pass, letting broken
    child apps that lack tools/workflow entirely skip coherence validation
    completely.
    """
    checks = []
    checker_path = project_dir / "tools" / "workflow" / "coherence_checker.py"
    if not checker_path.is_file():
        checks.append(
            GotchaCheck(
                check_id="FORGE-11",
                check_name="Coherence Validation",
                layer="meta",
                status="fail",
                expected="tools/workflow/coherence_checker.py present (shipped by DIRECTORY_TREE)",
                actual="tools/workflow/coherence_checker.py not found",
                fix_suggestion="Ensure 'tools/workflow' is in DIRECTORY_TREE so the coherence "
                               "checker is copied into the child; regenerate with child_app_generator.py",
                message="Coherence checker missing — child app is stale/incomplete (no tools/workflow)",
            )
        )
        return checks
    try:
        # Import checker relative to the project being validated
        saved_path = list(sys.path)
        sys.path.insert(0, str(project_dir))
        try:
            from tools.workflow.coherence_checker import run_checks

            report = run_checks()
        finally:
            sys.path[:] = saved_path

        if report.overall_pass:
            checks.append(
                GotchaCheck(
                    check_id="FORGE-11",
                    check_name="Coherence Validation",
                    layer="meta",
                    status="pass",
                    expected="All coherence checks pass",
                    actual=f"{report.passed_checks}/{report.total_checks} passed",
                    fix_suggestion="",
                    message=f"Coherence: {report.passed_checks}/{report.total_checks} checks passed",
                )
            )
        else:
            checks.append(
                GotchaCheck(
                    check_id="FORGE-11",
                    check_name="Coherence Validation",
                    layer="meta",
                    status="fail",
                    expected="All coherence checks pass",
                    actual=f"{report.failed_checks} failures, {report.warned_checks} warnings",
                    fix_suggestion="Run: python tools/workflow/coherence_checker.py --all --fix --json",
                    message=f"Coherence failures: {report.failed_checks} failed, {report.warned_checks} warned",
                )
            )
    except Exception as e:
        checks.append(
            GotchaCheck(
                check_id="FORGE-11",
                check_name="Coherence Validation",
                layer="meta",
                status="pass",
                expected="Coherence checker available",
                actual=f"Skipped (not available): {e}",
                fix_suggestion="",
                message=f"Coherence check skipped: {e}",
            )
        )

    return checks


# Banned-pattern regexes for FORGE-12 (grep-level, no AST — kept fast).
_SQLITE_CONNECT_RE = re.compile(r"sqlite3\.connect\s*\(")
_BARE_Q_RE = re.compile(r"(=\s*\?|VALUES\s*\(\s*\?)")


def _check_db_patterns(project_dir: Path) -> List[GotchaCheck]:
    """Check Meta: FORGE-12 — banned DB access patterns in child tools/.

    PostgreSQL is the primary backend. A raw ``sqlite3.connect()`` outside
    ``db/init_db.py`` and tests bypasses ``get_connection()``/RLS (writes become
    invisible to the dashboard), and bare ``?`` placeholders are SQLite-dialect
    that break on PostgreSQL. Scan is grep-level (no AST) to stay fast.

    Severity (first iteration): ``sqlite3.connect()`` -> FAIL; bare ``?`` -> WARN.
    """
    checks: List[GotchaCheck] = []
    tools_dir = project_dir / "tools"
    if not tools_dir.is_dir():
        # tools/ absence is already reported by FORGE-03; nothing to scan.
        return checks

    sqlite_hits: List[str] = []
    placeholder_hits: List[str] = []
    for py in tools_dir.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(project_dir).as_posix()
        name = py.name
        is_test = ("tests" in py.parts) or name.startswith("test_") or name.endswith("_test.py")
        is_db_init = py.parent.name == "db" and name.startswith("init")
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if _SQLITE_CONNECT_RE.search(text) and not is_test and not is_db_init:
            sqlite_hits.append(rel)
        if not is_test and _BARE_Q_RE.search(text):
            placeholder_hits.append(rel)

    # FORGE-12: raw sqlite3.connect() outside db/init_db.py + tests -> FAIL
    checks.append(
        GotchaCheck(
            check_id="FORGE-12",
            check_name="No banned sqlite3.connect() in runtime tools",
            layer="meta",
            status="fail" if sqlite_hits else "pass",
            expected="sqlite3.connect( only in tools/db/init_db.py or tests",
            actual=(f"{len(sqlite_hits)} offending file(s): {', '.join(sqlite_hits[:5])}"
                    if sqlite_hits else "none"),
            fix_suggestion="Use get_connection()/get_canvas_connection() from tools.db.storage; "
                           "confine sqlite3.connect() to db/init_db.py",
            message=(f"Banned sqlite3.connect() in runtime files: {', '.join(sqlite_hits[:5])}"
                     if sqlite_hits else "No banned sqlite3.connect() in runtime tools/"),
        )
    )

    # FORGE-12a: bare '?' SQLite-dialect placeholders in runtime files -> WARN
    checks.append(
        GotchaCheck(
            check_id="FORGE-12a",
            check_name="No SQLite-dialect '?' placeholders in runtime tools",
            layer="meta",
            status="warn" if placeholder_hits else "pass",
            expected="PG-style %s placeholders (bare '?' is SQLite-only)",
            actual=(f"{len(placeholder_hits)} file(s): {', '.join(placeholder_hits[:5])}"
                    if placeholder_hits else "none"),
            fix_suggestion="Author PostgreSQL-native SQL with %s placeholders; the ?->%s "
                           "translator is an init-only fallback, not load-bearing at runtime",
            message=(f"SQLite-dialect '?' placeholders in runtime files (warning): "
                     f"{', '.join(placeholder_hits[:5])}"
                     if placeholder_hits else "No bare '?' placeholders in runtime tools/"),
        )
    )

    return checks


def _check_canvas_completeness(project_dir: Path) -> List[GotchaCheck]:
    """Check Completeness: FORGE-13 — 8-component gate on the child's canvases.

    Scaffolded/generated child apps and canvases never used to run through the
    8-component dashboard-page completeness gate; it lived only in the parent's
    ``tools/workflow/coherence_checker.py`` and generated child trees could not
    invoke it locally. This check closes that gap by REUSING the canonical
    validator ``tools.config.component_registry.validate_canvas_completeness``
    (the exact function the parent coherence checker's ``check_canvas_completeness``
    calls) rather than duplicating the 8-point logic.

    It loads the CHILD's own ``args/component_registry.yaml``, iterates every
    ``kind: canvas`` component, and validates each against the child tree
    (``repo_root=project_dir``). A canvas missing any *required* component —
    template in both trees, blueprint route, backing module, constants, DB
    migration (when declared), nav link, or IQE integration (when an adapter is
    declared) — is an explicit FAIL.

    Non-failure exits:
      * no ``args/component_registry.yaml``  -> pass (skipped, nothing to validate)
      * registry present but no canvases     -> pass (nothing to validate)
      * parent validator unimportable        -> pass (skipped; never false-fail a
                                                 good child on a stale toolchain)
    """
    checks: List[GotchaCheck] = []
    registry_path = project_dir / "args" / "component_registry.yaml"
    if not registry_path.is_file():
        checks.append(
            GotchaCheck(
                check_id="FORGE-13",
                check_name="Canvas completeness gate",
                layer="meta",
                status="pass",
                expected="args/component_registry.yaml (optional)",
                actual="Not present (skipped)",
                fix_suggestion="",
                message="Canvas completeness skipped: no args/component_registry.yaml to validate",
            )
        )
        return checks

    try:
        from tools.config.component_registry import (
            ComponentRegistry,
            validate_canvas_completeness,
        )
    except Exception as exc:  # pragma: no cover - defensive; parent toolchain absent
        checks.append(
            GotchaCheck(
                check_id="FORGE-13",
                check_name="Canvas completeness gate",
                layer="meta",
                status="pass",
                expected="tools.config.component_registry validator importable",
                actual=f"Skipped (validator unavailable): {exc}",
                fix_suggestion="",
                message=f"Canvas completeness skipped: {exc}",
            )
        )
        return checks

    try:
        registry = ComponentRegistry(registry_path=registry_path)
        canvases = registry.list_all(kind="canvas")
    except Exception as exc:
        checks.append(
            GotchaCheck(
                check_id="FORGE-13",
                check_name="Canvas completeness gate",
                layer="meta",
                status="fail",
                expected="Child component_registry.yaml loads and lists canvases",
                actual=f"Registry load error: {exc}",
                fix_suggestion="Fix args/component_registry.yaml so it parses and declares canvases",
                message=f"Canvas completeness validator failed to load child registry: {exc}",
            )
        )
        return checks

    if not canvases:
        checks.append(
            GotchaCheck(
                check_id="FORGE-13",
                check_name="Canvas completeness gate",
                layer="meta",
                status="pass",
                expected="At least one kind:canvas component (optional)",
                actual="0 canvases declared",
                fix_suggestion="",
                message="Canvas completeness skipped: registry declares no canvases",
            )
        )
        return checks

    for comp in canvases:
        try:
            report = validate_canvas_completeness(
                comp.key, registry=registry, repo_root=project_dir
            )
        except Exception as exc:
            checks.append(
                GotchaCheck(
                    check_id=f"FORGE-13-{comp.key}",
                    check_name=f"Canvas completeness ({comp.key})",
                    layer="meta",
                    status="fail",
                    expected="8-component completeness gate runs without error",
                    actual=f"Validator error: {exc}",
                    fix_suggestion="Investigate the completeness validator failure for this canvas",
                    message=f"Canvas '{comp.key}' completeness validation raised: {exc}",
                )
            )
            continue

        # `required` is False for components the registry never declared (e.g. a
        # canvas with no DB migration); only surface required-but-absent gaps.
        missing = [
            f"{item.point} ({item.path or item.message})"
            for item in report.items
            if item.required and not item.present
        ]
        if missing:
            checks.append(
                GotchaCheck(
                    check_id=f"FORGE-13-{comp.key}",
                    check_name=f"Canvas completeness ({comp.key})",
                    layer="meta",
                    status="fail",
                    expected="All 8 required components present for the canvas",
                    actual=f"{len(missing)} missing: {', '.join(missing[:6])}",
                    fix_suggestion="Ship all 8 components (template in both trees, route, module, "
                                   "constants, migration, nav link, IQE) before generating the canvas",
                    message=f"Canvas '{comp.key}' incomplete: {', '.join(missing[:6])}",
                )
            )
        else:
            checks.append(
                GotchaCheck(
                    check_id=f"FORGE-13-{comp.key}",
                    check_name=f"Canvas completeness ({comp.key})",
                    layer="meta",
                    status="pass",
                    expected="All 8 required components present for the canvas",
                    actual="Complete",
                    fix_suggestion="",
                    message=f"Canvas '{comp.key}' passes the 8-component completeness gate",
                )
            )

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
    "anvil": _check_atlas,
    "coherence": _check_coherence,
    "db_patterns": _check_db_patterns,
    "canvas_completeness": _check_canvas_completeness,
    "child_app_templates": _check_child_app_templates,
}


# ---------------------------------------------------------------------------
# Main validation function
# ---------------------------------------------------------------------------


def validate(
    project_dir: str | Path,
    checks: Optional[List[str]] = None,
) -> GotchaReport:
    """Run FORGE compliance validation on a project directory.

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
                all_checks.append(
                    GotchaCheck(
                        check_id=f"FORGE-ERR-{check_name}",
                        check_name=f"Error running {check_name}",
                        layer=check_name,
                        status="fail",
                        expected="Check to run without errors",
                        actual=str(e),
                        fix_suggestion="Investigate the error and fix the underlying issue",
                        message=f"Check {check_name} raised an error: {e}",
                    )
                )

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
    lines.append("  FORGE Framework Compliance Validation")
    lines.append("=" * 65)
    lines.append(f"  Project:  {report.project_dir}")
    lines.append(f"  Score:    {report.score:.0%} ({report.passed_checks}/{report.total_checks} passed)")
    status_label = "PASS" if report.overall_pass else "FAIL"
    lines.append(f"  Status:   {status_label}")
    lines.append("-" * 65)

    # Layer summary
    lines.append("")
    lines.append("  Layer Summary:")
    layer_order = ["goals", "orchestration", "tools", "args", "context", "hardprompts", "templates", "meta"]
    layer_labels = {
        "goals": "1. Goals",
        "orchestration": "2. Orchestration",
        "tools": "3. Tools",
        "args": "4. Args",
        "context": "5. Context",
        "hardprompts": "6. Hard Prompts",
        "templates": "   Templates",
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
        description="FORGE Framework Compliance Validator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/builder/forge_validator.py --project-dir . --human\n"
            "  python tools/builder/forge_validator.py --project-dir /path/to/child --json\n"
            "  python tools/builder/forge_validator.py --project-dir /path/to/child --gate\n"
            "  python tools/builder/forge_validator.py --project-dir . --check goals --json\n"
        ),
    )
    parser.add_argument("--project-dir", required=True, help="Path to the project directory to validate")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--human", action="store_true", help="Output results as human-readable text")
    parser.add_argument("--gate", action="store_true", help="Exit with code 1 if any checks fail (for CI/CD gates)")
    parser.add_argument(
        "--check", choices=list(CHECK_REGISTRY.keys()) + ["all"], help="Run a specific check (default: all)"
    )

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
