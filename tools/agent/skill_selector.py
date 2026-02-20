#!/usr/bin/env python3
# CUI // SP-CTI
"""Selective Skill Injection — analyze task context to return relevant skills (D146).

Deterministic keyword-based category matching. No LLM required.
Declarative YAML config (D26 pattern).

CLI:
    python tools/agent/skill_selector.py --query "fix the login tests" --json
    python tools/agent/skill_selector.py --detect --project-dir /path --json
    python tools/agent/skill_selector.py --query "deploy to staging" --format-context
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Default configuration (fallback if YAML unavailable)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "categories": {
        "build": {
            "description": "Code generation, scaffolding, TDD",
            "keywords": [
                "build", "code", "implement", "scaffold", "generate", "tdd",
                "test", "lint", "format", "refactor", "python", "java",
                "javascript", "typescript", "go", "rust", "csharp", "flask",
            ],
            "commands": ["feature", "bug", "chore", "patch", "test",
                         "resolve_failed_test", "commit", "pull_request", "review"],
            "goals": ["build_app.md", "tdd_workflow.md", "integration_testing.md"],
            "context_dirs": ["languages", "templates"],
        },
        "compliance": {
            "description": "ATO artifacts, compliance frameworks",
            "keywords": [
                "compliance", "ato", "nist", "stig", "sbom", "fedramp", "cmmc",
                "oscal", "emass", "cato", "ssp", "poam", "cui", "fips", "hipaa",
                "pci", "cjis", "soc2", "iso27001", "crosswalk", "control",
            ],
            "commands": [],
            "goals": ["compliance_workflow.md", "ato_acceleration.md",
                       "universal_compliance.md"],
            "context_dirs": ["compliance"],
        },
        "infrastructure": {
            "description": "Deployment, IaC, pipelines, DevSecOps",
            "keywords": [
                "deploy", "terraform", "ansible", "k8s", "kubernetes", "pipeline",
                "infrastructure", "iac", "cicd", "docker", "container", "helm",
                "devsecops", "zero trust", "zta", "service mesh",
            ],
            "commands": [],
            "goals": ["deploy_workflow.md", "cicd_integration.md",
                       "devsecops_workflow.md", "zero_trust_architecture.md"],
            "context_dirs": ["ci"],
        },
        "requirements": {
            "description": "Requirements intake, simulation, COA",
            "keywords": [
                "requirements", "intake", "spec", "coa", "simulation",
                "monte carlo", "readiness", "decomposition", "safe", "epic",
                "story", "bdd", "gherkin", "stakeholder", "elicitation",
            ],
            "commands": [],
            "goals": ["requirements_intake.md", "simulation_engine.md"],
            "context_dirs": ["requirements"],
        },
        "maintenance": {
            "description": "Dependency management, modernization",
            "keywords": [
                "maintenance", "dependency", "vulnerability", "cve", "modernize",
                "migrate", "legacy", "remediate", "upgrade", "supply chain",
                "scrm", "isa", "vendor",
            ],
            "commands": [],
            "goals": ["maintenance_audit.md", "modernization_workflow.md"],
            "context_dirs": [],
        },
        "dashboard": {
            "description": "Web dashboard, UX, monitoring",
            "keywords": [
                "dashboard", "web", "ui", "ux", "flask", "template", "jinja",
                "monitoring", "agent", "health", "heartbeat", "self-healing",
                "chat", "gateway", "kanban",
            ],
            "commands": [],
            "goals": ["dashboard.md", "monitoring.md", "agent_management.md"],
            "context_dirs": ["dashboard"],
        },
    },
    "file_extension_map": {
        ".py": ["build"], ".java": ["build"], ".js": ["build"],
        ".ts": ["build"], ".go": ["build"], ".rs": ["build"],
        ".tf": ["infrastructure"], ".html": ["dashboard"],
        ".feature": ["build", "requirements"],
    },
    "path_pattern_map": {
        "tools/compliance/": ["compliance"],
        "tools/infra/": ["infrastructure"],
        "tools/builder/": ["build"],
        "tools/requirements/": ["requirements"],
        "tools/dashboard/": ["dashboard"],
        "tools/monitor/": ["dashboard"],
    },
    "always_include": {
        "commands": ["commit", "pull_request", "classify_issue",
                     "classify_workflow", "generate_branch_name"],
        "goals": [],
        "context_dirs": [],
    },
    "confidence_threshold": 0.3,
    "min_keyword_matches": 1,
}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load skill injection config from YAML.

    Falls back to DEFAULT_CONFIG if file is missing or yaml unavailable.
    """
    path = config_path or (BASE_DIR / "args" / "skill_injection_config.yaml")
    try:
        import yaml  # type: ignore
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            # Merge with defaults
            config = dict(DEFAULT_CONFIG)
            if "categories" in data:
                config["categories"] = data["categories"]
            for key in ("file_extension_map", "path_pattern_map",
                        "always_include", "confidence_threshold",
                        "min_keyword_matches"):
                if key in data:
                    config[key] = data[key]
            return config
    except (ImportError, Exception):
        pass
    return dict(DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

def match_keywords(query: str, categories: Dict[str, Any]) -> Dict[str, float]:
    """Score each category based on keyword matches in the query.

    Supports multi-word keywords (e.g., "zero trust", "supply chain").
    Returns dict mapping category_name -> match_score (0.0-1.0).
    """
    query_lower = query.lower()
    query_tokens = set(re.findall(r'\w+', query_lower))
    scores: Dict[str, float] = {}

    for cat_name, cat_data in categories.items():
        keywords = cat_data.get("keywords", [])
        if not keywords:
            scores[cat_name] = 0.0
            continue

        match_count = 0
        for kw in keywords:
            kw_lower = kw.lower()
            if " " in kw_lower:
                # Multi-word keyword: check as phrase
                if kw_lower in query_lower:
                    match_count += 2  # Phrase matches weighted higher
            else:
                if kw_lower in query_tokens:
                    match_count += 1

        # Saturating scale: 1 match = 0.3, 2 = 0.5, 3+ = 0.7+
        # Ensures even a single keyword match is meaningful
        if match_count == 0:
            scores[cat_name] = 0.0
        else:
            scores[cat_name] = min(1.0, 0.3 + (match_count - 1) * 0.2)

    return scores


# ---------------------------------------------------------------------------
# File-based detection
# ---------------------------------------------------------------------------

def detect_from_files(
    project_dir: str,
    config: Dict[str, Any],
) -> Dict[str, float]:
    """Detect relevant categories from project file extensions and paths."""
    project_path = Path(project_dir)
    if not project_path.is_dir():
        return {}

    ext_map = config.get("file_extension_map", {})
    path_map = config.get("path_pattern_map", {})
    category_hits: Dict[str, int] = {}
    total_files = 0

    # Scan files (max 500 to avoid slow scans)
    try:
        for i, f in enumerate(project_path.rglob("*")):
            if i > 500:
                break
            if not f.is_file():
                continue
            # Skip hidden dirs, node_modules, __pycache__, .git
            parts_str = str(f.relative_to(project_path))
            if any(skip in parts_str for skip in
                   ["node_modules", "__pycache__", ".git", ".tmp"]):
                continue

            total_files += 1
            ext = f.suffix.lower()
            if ext in ext_map:
                for cat in ext_map[ext]:
                    category_hits[cat] = category_hits.get(cat, 0) + 1

            # Check path patterns
            rel = parts_str.replace("\\", "/")
            for pattern, cats in path_map.items():
                if rel.startswith(pattern) or f"/{pattern}" in f"/{rel}":
                    for cat in cats:
                        category_hits[cat] = category_hits.get(cat, 0) + 1
    except (PermissionError, OSError):
        pass

    if total_files == 0:
        return {}

    # Normalize scores
    max_hits = max(category_hits.values()) if category_hits else 1
    return {cat: min(1.0, hits / max(max_hits, 1))
            for cat, hits in category_hits.items()}


# ---------------------------------------------------------------------------
# Main selection
# ---------------------------------------------------------------------------

def select_skills(
    query: Optional[str] = None,
    project_dir: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Main entry point: select relevant skills/goals/context for a task.

    Merges keyword matching (from query) and file detection (from project_dir).
    Falls back to ALL items if no category passes confidence threshold.
    """
    config = load_config(config_path)
    categories = config.get("categories", {})
    threshold = config.get("confidence_threshold", 0.3)
    min_matches = config.get("min_keyword_matches", 1)

    # Score categories
    keyword_scores: Dict[str, float] = {}
    file_scores: Dict[str, float] = {}

    if query:
        keyword_scores = match_keywords(query, categories)
    if project_dir:
        file_scores = detect_from_files(project_dir, config)

    # Merge scores (max of keyword and file scores)
    all_cats = set(list(keyword_scores.keys()) + list(file_scores.keys()))
    merged: Dict[str, float] = {}
    for cat in all_cats:
        merged[cat] = max(keyword_scores.get(cat, 0.0), file_scores.get(cat, 0.0))

    # Filter above threshold
    matched = {cat: score for cat, score in merged.items() if score >= threshold}

    # Safety fallback: if nothing matched, include everything
    fallback = len(matched) == 0
    if fallback:
        matched = {cat: 0.0 for cat in categories}

    # Collect items from matched categories
    commands: Set[str] = set()
    goals: Set[str] = set()
    context_dirs: Set[str] = set()

    for cat_name in matched:
        cat_data = categories.get(cat_name, {})
        commands.update(cat_data.get("commands", []))
        goals.update(cat_data.get("goals", []))
        context_dirs.update(cat_data.get("context_dirs", []))

    # Always-include items
    always = config.get("always_include", {})
    commands.update(always.get("commands", []))
    goals.update(always.get("goals", []))
    context_dirs.update(always.get("context_dirs", []))

    max_score = max(matched.values()) if matched else 0.0

    return {
        "classification": "CUI // SP-CTI",
        "status": "fallback_all" if fallback else "ok",
        "matched_categories": [
            {
                "name": cat,
                "score": round(score, 4),
                "description": categories.get(cat, {}).get("description", ""),
            }
            for cat, score in sorted(matched.items(), key=lambda x: -x[1])
        ],
        "commands": sorted(commands),
        "goals": sorted(goals),
        "context_dirs": sorted(context_dirs),
        "confidence": round(max_score, 4),
        "query": query,
        "project_dir": project_dir,
    }


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _resolve_paths(selection: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve selections to absolute file paths, validating existence."""
    cmd_dir = BASE_DIR / ".claude" / "commands"
    goal_dir = BASE_DIR / "goals"
    ctx_dir = BASE_DIR / "context"

    command_paths: List[str] = []
    goal_paths: List[str] = []
    context_dir_paths: List[str] = []
    missing: List[str] = []

    for cmd in selection.get("commands", []):
        p = cmd_dir / f"{cmd}.md"
        if p.exists():
            command_paths.append(str(p))
        else:
            missing.append(f"command:{cmd}")

    for goal in selection.get("goals", []):
        p = goal_dir / goal
        if p.exists():
            goal_paths.append(str(p))
        else:
            missing.append(f"goal:{goal}")

    for cd in selection.get("context_dirs", []):
        p = ctx_dir / cd
        if p.is_dir():
            context_dir_paths.append(str(p))
        else:
            missing.append(f"context:{cd}")

    selection["command_paths"] = command_paths
    selection["goal_paths"] = goal_paths
    selection["context_dir_paths"] = context_dir_paths
    selection["missing_items"] = missing
    return selection


# ---------------------------------------------------------------------------
# Context formatting
# ---------------------------------------------------------------------------

def format_injection_context(selection: Dict[str, Any]) -> str:
    """Format selected items into compact markdown for context injection."""
    lines = [
        "# CUI // SP-CTI",
        "# Relevant ICDEV Context",
        "",
    ]

    cats = selection.get("matched_categories", [])
    if cats:
        lines.append("## Active Categories")
        for cat in cats:
            lines.append(f"- **{cat['name']}** ({cat['score']:.2f}): "
                         f"{cat['description']}")
        lines.append("")

    cmds = selection.get("commands", [])
    if cmds:
        lines.append("## Available Commands")
        lines.append(", ".join(f"`/{c}`" for c in cmds))
        lines.append("")

    goals = selection.get("goals", [])
    if goals:
        lines.append("## Relevant Goals")
        for g in goals:
            lines.append(f"- `goals/{g}`")
        lines.append("")

    ctx = selection.get("context_dirs", [])
    if ctx:
        lines.append("## Context Directories")
        for c in ctx:
            lines.append(f"- `context/{c}/`")
        lines.append("")

    if selection.get("status") == "fallback_all":
        lines.append("> Note: No specific match found — showing all items.")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Selective Skill Injection (D146)"
    )
    parser.add_argument("--query", type=str,
                        help="Task description to analyze")
    parser.add_argument("--detect", action="store_true",
                        help="Detect from project files")
    parser.add_argument("--project-dir", type=str,
                        help="Project directory to scan (for --detect)")
    parser.add_argument("--resolve", action="store_true",
                        help="Resolve to absolute file paths")
    parser.add_argument("--format-context", action="store_true",
                        help="Output as injection-ready markdown")
    parser.add_argument("--json", action="store_true",
                        help="JSON output")
    parser.add_argument("--config", type=Path,
                        help="Override config path")
    args = parser.parse_args()

    if not args.query and not args.detect:
        parser.print_help()
        return

    if args.detect and not args.project_dir:
        print("Error: --project-dir required with --detect", file=sys.stderr)
        sys.exit(1)

    selection = select_skills(
        query=args.query,
        project_dir=args.project_dir if args.detect else None,
        config_path=args.config,
    )

    if args.resolve:
        selection = _resolve_paths(selection)

    if args.format_context:
        print(format_injection_context(selection))
    elif args.json:
        print(json.dumps(selection, indent=2))
    else:
        # Human-readable output
        print(f"Status: {selection['status']} "
              f"(confidence: {selection['confidence']:.2f})")
        print()
        for cat in selection.get("matched_categories", []):
            if cat["score"] > 0:
                print(f"  [{cat['score']:.2f}] {cat['name']}: "
                      f"{cat['description']}")
        print()
        if selection["commands"]:
            print(f"Commands: {', '.join(selection['commands'])}")
        if selection["goals"]:
            print(f"Goals: {', '.join(selection['goals'])}")
        if selection["context_dirs"]:
            print(f"Context: {', '.join(selection['context_dirs'])}")


if __name__ == "__main__":
    main()
