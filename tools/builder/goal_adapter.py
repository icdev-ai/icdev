#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Goal Adapter - copies and adapts ICDEV goals for child applications.

Adapts the ICDEV goal library for use by child apps generated via the blueprint
engine. This involves:
  1. Copying essential goal markdown files (filtered by blueprint capabilities)
  2. Stripping the "Step 0: Agentic Fitness Assessment" from build_app.md
     (child apps don't assess fitness -- that was done by ICDEV at generation time)
  3. Generating a goals manifest.md for the child app
  4. Copying relevant hardprompt templates (excluding generation-only prompts)

Architecture Decision D23: Blueprint-driven generation -- single config drives
all generators; no hardcoded decisions.

CLI: python tools/builder/goal_adapter.py \\
       --blueprint /path/to/blueprint.json \\
       --child-root /path/to/child-app \\
       --json
"""

import argparse
import json
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
logger = logging.getLogger("icdev.goal_adapter")

# ============================================================
# GOAL FILE MAP — maps goal keys to filenames
# ============================================================

GOAL_FILE_MAP: Dict[str, str] = {
    "build_app": "build_app.md",
    "tdd_workflow": "tdd_workflow.md",
    "compliance_workflow": "compliance_workflow.md",
    "security_scan": "security_scan.md",
    "deploy_workflow": "deploy_workflow.md",
    "monitoring": "monitoring.md",
    "self_healing": "self_healing.md",
    "agent_management": "agent_management.md",
    "integration_testing": "integration_testing.md",
    "dashboard": "dashboard.md",
    "code_review": "code_review.md",
    "mbse_integration": "mbse_integration.md",
}

# ============================================================
# GOAL PURPOSE MAP — human-readable descriptions for manifest
# ============================================================

GOAL_PURPOSE_MAP: Dict[str, str] = {
    "build_app": "5/6-step ATLAS build process",
    "tdd_workflow": "RED\u2192GREEN\u2192REFACTOR cycle with BDD",
    "compliance_workflow": "Generate ATO artifacts (SSP, POAM, STIG, SBOM)",
    "security_scan": "SAST, dependency audit, secret detection",
    "deploy_workflow": "IaC generation and deployment",
    "monitoring": "Log analysis, metrics, alerts",
    "self_healing": "Pattern detection and auto-remediation",
    "agent_management": "A2A agent lifecycle management",
    "integration_testing": "Multi-layer testing pipeline",
    "dashboard": "Web UI for status monitoring",
    "code_review": "Enforced review gates",
    "mbse_integration": "Model-Based Systems Engineering",
}

# ============================================================
# GOAL DISPLAY NAME MAP — table-friendly names for manifest
# ============================================================

GOAL_DISPLAY_NAME_MAP: Dict[str, str] = {
    "build_app": "ATLAS/M-ATLAS Workflow",
    "tdd_workflow": "TDD Workflow",
    "compliance_workflow": "Compliance Workflow",
    "security_scan": "Security Scanning",
    "deploy_workflow": "Deployment Workflow",
    "monitoring": "Monitoring",
    "self_healing": "Self-Healing",
    "agent_management": "Agent Management",
    "integration_testing": "Integration Testing",
    "dashboard": "Dashboard",
    "code_review": "Code Review",
    "mbse_integration": "MBSE Integration",
}

# ============================================================
# EXCLUDED FILES — never copy these to child apps
# ============================================================

EXCLUDED_FILES: List[str] = [
    "fitness_evaluation.md",
    "agentic_architect.md",
    "skill_design.md",
    "governance_review.md",
]


# ============================================================
# CORE FUNCTIONS
# ============================================================


def copy_essential_goals(
    source_dir: Path,
    dest_dir: Path,
    goals_config: List[str],
) -> List[str]:
    """Copy goal markdown files from ICDEV goals/ to child app goals/.

    Only copies goals listed in the blueprint's goals_config. Each file is
    copied verbatim except build_app.md which gets the fitness step stripped.

    Args:
        source_dir: Path to ICDEV goals/ directory.
        dest_dir: Path to child app goals/ directory.
        goals_config: List of goal keys to copy (e.g. ["build_app", "tdd_workflow"]).

    Returns:
        List of copied goal filenames.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []

    for goal_key in goals_config:
        filename = GOAL_FILE_MAP.get(goal_key)
        if filename is None:
            logger.warning("Unknown goal key '%s' — skipping", goal_key)
            continue

        source_file = source_dir / filename
        if not source_file.exists():
            logger.warning(
                "Goal file not found: %s — skipping", source_file
            )
            continue

        dest_file = dest_dir / filename

        # Special handling for build_app.md — strip fitness step
        if goal_key == "build_app":
            content = source_file.read_text(encoding="utf-8")
            adapted_content = strip_fitness_step(content)
            dest_file.write_text(adapted_content, encoding="utf-8")
            logger.info("Copied and adapted: %s (fitness step stripped)", filename)
        else:
            shutil.copy2(source_file, dest_file)
            logger.info("Copied: %s", filename)

        copied.append(filename)

    logger.info("Copied %d/%d goal files", len(copied), len(goals_config))
    return copied


def strip_fitness_step(build_app_content: str) -> str:
    """Remove the 'Step 0: Agentic Fitness Assessment' section from build_app.md.

    Strips the entire subsection starting at a heading matching
    '### Step 0: Agentic Fitness' (or similar) and ending just before the
    next heading of equal or higher level (### or ##). Also removes any
    standalone references to agentic_fitness.py throughout the document.

    Args:
        build_app_content: Full text content of build_app.md.

    Returns:
        Modified content with the fitness step removed.
    """
    # Pattern: match "### Step 0: Agentic Fitness..." heading and everything
    # until the next heading at ### or ## level (or end of file).
    # Using re.DOTALL so . matches newlines within the lazy quantifier.
    pattern = re.compile(
        r"###\s*Step\s*0\s*:\s*Agentic\s+Fitness.*?"  # heading line
        r"(?=^##[#\s]|\Z)",                            # stop before next ## or ###
        re.MULTILINE | re.DOTALL,
    )
    content = pattern.sub("", build_app_content)

    # Remove any remaining references to agentic_fitness.py
    # Match lines that reference the tool (entire line including newline)
    content = re.sub(
        r"^.*agentic_fitness\.py.*\n?",
        "",
        content,
        flags=re.MULTILINE,
    )

    # Clean up any resulting triple-or-more blank lines
    content = re.sub(r"\n{3,}", "\n\n", content)

    return content


def build_goals_manifest(
    goals_config: List[str],
    app_name: str,
) -> str:
    """Generate a manifest.md listing all goals present in the child app.

    Args:
        goals_config: List of goal keys included in the child app.
        app_name: Name of the child application.

    Returns:
        Markdown string for goals/manifest.md.
    """
    lines: List[str] = [
        f"# Goals Manifest - {app_name}",
        "",
        "> Index of all goal workflows. Check here before starting any task.",
        "",
        "| Goal | File | Purpose |",
        "|------|------|---------|",
    ]

    for goal_key in goals_config:
        filename = GOAL_FILE_MAP.get(goal_key)
        if filename is None:
            continue
        display_name = GOAL_DISPLAY_NAME_MAP.get(goal_key, goal_key)
        purpose = GOAL_PURPOSE_MAP.get(goal_key, "")
        lines.append(f"| {display_name} | goals/{filename} | {purpose} |")

    lines.append("")  # trailing newline
    return "\n".join(lines)


def copy_hardprompts(
    source_dir: Path,
    dest_dir: Path,
    capabilities: Dict[str, bool],
) -> List[str]:
    """Copy hardprompt templates from ICDEV to child app.

    Always copies:
      - hardprompts/agent/*.md (minus excluded files like fitness_evaluation.md)
      - hardprompts/architect/*.md
      - hardprompts/builder/*.md
      - hardprompts/security/*.md
      - hardprompts/knowledge/*.md
      - hardprompts/infra/*.md

    Conditionally copies:
      - hardprompts/compliance/*.md  (if compliance capability enabled)
      - hardprompts/mbse/*.md        (if mbse capability enabled)
      - hardprompts/maintenance/*.md (if compliance or security capability)

    Never copies:
      - Files in EXCLUDED_FILES list
      - hardprompts/modernization/  (ICDEV-only, not for child apps)
      - hardprompts/requirements/   (ICDEV-only, RICOAS)
      - hardprompts/simulation/     (ICDEV-only, RICOAS)
      - hardprompts/integration/    (ICDEV-only, RICOAS)

    Args:
        source_dir: Path to ICDEV hardprompts/ directory.
        dest_dir: Path to child app hardprompts/ directory.
        capabilities: Resolved capability map from blueprint.

    Returns:
        List of copied hardprompt file paths (relative to dest_dir).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []

    # Subdirectories that are always copied
    always_copy_dirs = ["agent", "architect", "builder", "security", "knowledge", "infra"]

    # Conditional directories: (subdir, required_capability)
    conditional_dirs: List[tuple] = [
        ("compliance", "compliance"),
        ("mbse", "mbse"),
        ("maintenance", "compliance"),  # maintenance prompts need compliance context
    ]

    # Never copy these subdirectories to child apps
    never_copy_dirs = {"modernization", "requirements", "simulation", "integration",
                       "dashboard", "ci"}

    # Build the list of subdirectories to process
    dirs_to_copy: List[str] = list(always_copy_dirs)
    for subdir, required_cap in conditional_dirs:
        if capabilities.get(required_cap, False):
            dirs_to_copy.append(subdir)

    for subdir in dirs_to_copy:
        src_subdir = source_dir / subdir
        if not src_subdir.is_dir():
            logger.debug("Hardprompt subdir not found: %s — skipping", src_subdir)
            continue

        dst_subdir = dest_dir / subdir
        dst_subdir.mkdir(parents=True, exist_ok=True)

        for md_file in sorted(src_subdir.glob("*.md")):
            if md_file.name in EXCLUDED_FILES:
                logger.debug("Excluded hardprompt: %s", md_file.name)
                continue

            dst_file = dst_subdir / md_file.name
            shutil.copy2(md_file, dst_file)
            rel_path = f"{subdir}/{md_file.name}"
            copied.append(rel_path)
            logger.debug("Copied hardprompt: %s", rel_path)

    logger.info("Copied %d hardprompt files across %d directories",
                len(copied), len(dirs_to_copy))
    return copied


# ============================================================
# MAIN ORCHESTRATOR
# ============================================================


def adapt_goals(
    blueprint: Dict[str, Any],
    icdev_root: Path,
    child_root: Path,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Adapt and copy goals from ICDEV to child app.

    Reads the blueprint's goals_config and capabilities to determine which
    goals and hardprompts to copy. Strips the fitness assessment step from
    build_app.md. Generates a goals manifest for the child app.

    Args:
        blueprint: Complete blueprint dict from app_blueprint.py.
        icdev_root: Path to ICDEV project root.
        child_root: Path to child app root.
        dry_run: If True, don't actually copy files — just report what would
                 be done.

    Returns:
        Dict with results: {goals_copied, hardprompts_copied,
                            manifest_generated, manifest_path}
    """
    app_name = blueprint.get("app_name", "child-app")
    goals_config = blueprint.get("goals_config", [])
    capabilities = blueprint.get("capabilities", {})

    icdev_goals_dir = icdev_root / "goals"
    icdev_hardprompts_dir = icdev_root / "hardprompts"
    child_goals_dir = child_root / "goals"
    child_hardprompts_dir = child_root / "hardprompts"

    result: Dict[str, Any] = {
        "app_name": app_name,
        "goals_copied": [],
        "hardprompts_copied": [],
        "manifest_generated": False,
        "manifest_path": None,
        "dry_run": dry_run,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }

    if dry_run:
        logger.info("[DRY RUN] Would adapt goals for '%s'", app_name)

        # Report goals that would be copied
        for goal_key in goals_config:
            filename = GOAL_FILE_MAP.get(goal_key)
            if filename is None:
                continue
            source_file = icdev_goals_dir / filename
            if source_file.exists():
                result["goals_copied"].append(filename)
                note = " (fitness step would be stripped)" if goal_key == "build_app" else ""
                logger.info("[DRY RUN] Would copy goal: %s%s", filename, note)
            else:
                logger.warning("[DRY RUN] Goal file missing: %s", filename)

        # Report hardprompts that would be copied
        always_dirs = ["agent", "architect", "builder", "security", "knowledge", "infra"]
        cond_dirs = [("compliance", "compliance"), ("mbse", "mbse"),
                     ("maintenance", "compliance")]
        dirs_to_check = list(always_dirs)
        for subdir, cap in cond_dirs:
            if capabilities.get(cap, False):
                dirs_to_check.append(subdir)

        for subdir in dirs_to_check:
            src_subdir = icdev_hardprompts_dir / subdir
            if not src_subdir.is_dir():
                continue
            for md_file in sorted(src_subdir.glob("*.md")):
                if md_file.name not in EXCLUDED_FILES:
                    rel_path = f"{subdir}/{md_file.name}"
                    result["hardprompts_copied"].append(rel_path)
                    logger.info("[DRY RUN] Would copy hardprompt: %s", rel_path)

        # Report manifest
        result["manifest_generated"] = True
        result["manifest_path"] = str(child_goals_dir / "manifest.md")
        logger.info("[DRY RUN] Would generate manifest at %s",
                    result["manifest_path"])

        return result

    # --- Actual execution ---

    # Step 1: Copy essential goals
    logger.info("Adapting goals for '%s' (%d goals, %d capabilities)",
                app_name, len(goals_config), len(capabilities))
    copied_goals = copy_essential_goals(
        icdev_goals_dir, child_goals_dir, goals_config
    )
    result["goals_copied"] = copied_goals

    # Step 2: Generate and write manifest
    manifest_content = build_goals_manifest(goals_config, app_name)
    manifest_path = child_goals_dir / "manifest.md"
    child_goals_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest_content, encoding="utf-8")
    result["manifest_generated"] = True
    result["manifest_path"] = str(manifest_path)
    logger.info("Generated goals manifest: %s", manifest_path)

    # Step 3: Copy hardprompts
    copied_prompts = copy_hardprompts(
        icdev_hardprompts_dir, child_hardprompts_dir, capabilities
    )
    result["hardprompts_copied"] = copied_prompts

    logger.info(
        "Goal adaptation complete: %d goals, %d hardprompts, manifest=%s",
        len(copied_goals),
        len(copied_prompts),
        result["manifest_generated"],
    )

    return result


# ============================================================
# CLI
# ============================================================


def main() -> None:
    """CLI entry point for goal adaptation."""
    parser = argparse.ArgumentParser(
        description="Adapt ICDEV goals for child applications",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/builder/goal_adapter.py \\\n"
            "    --blueprint /path/to/blueprint.json \\\n"
            "    --child-root /path/to/child-app\n\n"
            "  python tools/builder/goal_adapter.py \\\n"
            "    --blueprint /path/to/blueprint.json \\\n"
            "    --child-root /path/to/child-app \\\n"
            "    --dry-run --json\n"
        ),
    )
    parser.add_argument(
        "--blueprint",
        required=True,
        help="Path to blueprint JSON file (output of app_blueprint.py)",
    )
    parser.add_argument(
        "--icdev-root",
        default=None,
        help="Path to ICDEV project root (defaults to auto-detect from script location)",
    )
    parser.add_argument(
        "--child-root",
        required=True,
        help="Path to child application root directory",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be done without actually copying files",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Load blueprint
    blueprint_path = Path(args.blueprint)
    if not blueprint_path.exists():
        logger.error("Blueprint file not found: %s", blueprint_path)
        sys.exit(1)

    try:
        blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read blueprint: %s", exc)
        sys.exit(1)

    # Resolve ICDEV root
    icdev_root = Path(args.icdev_root) if args.icdev_root else BASE_DIR
    if not (icdev_root / "goals").is_dir():
        logger.error("ICDEV root does not contain goals/ directory: %s", icdev_root)
        sys.exit(1)

    child_root = Path(args.child_root)

    # Run adaptation
    result = adapt_goals(
        blueprint=blueprint,
        icdev_root=icdev_root,
        child_root=child_root,
        dry_run=args.dry_run,
    )

    # Output
    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        prefix = "[DRY RUN] " if args.dry_run else ""
        print(f"\n{prefix}Goal Adaptation Results for '{result['app_name']}'")
        print("=" * 60)
        print(f"  Goals copied:      {len(result['goals_copied'])}")
        for g in result["goals_copied"]:
            print(f"    - {g}")
        print(f"  Hardprompts copied: {len(result['hardprompts_copied'])}")
        for hp in result["hardprompts_copied"]:
            print(f"    - {hp}")
        print(f"  Manifest generated: {result['manifest_generated']}")
        if result["manifest_path"]:
            print(f"  Manifest path:      {result['manifest_path']}")
        print()


if __name__ == "__main__":
    main()
