#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""OpenClaw-to-ICDEV™ Compatibility Checker & Translator.

OpenClaw and ICDEV™ have fundamentally different architectures:
- OpenClaw: Node.js AI assistant, npm skills, flat markdown, shell/browser tools
- ICDEV™: FORGE 6-layer framework, Python tools, CUI compliance, MCP gateway

This module:
1. Validates an OpenClaw skill for ICDEV™ compatibility
2. Identifies blocking incompatibilities vs. adaptable differences
3. Translates the skill into ICDEV™ SKILL.md format
4. Generates a compatibility report

Usage:
    # Check compatibility
    python tools/marketplace/openclaw_compat.py --check /path/to/openclaw-skill --json

    # Translate to ICDEV™ format
    python tools/marketplace/openclaw_compat.py --translate /path/to/openclaw-skill \\
        --output /path/to/output --json

    # Full pipeline (check + translate)
    python tools/marketplace/openclaw_compat.py --full /path/to/openclaw-skill \\
        --output /path/to/output --json
"""

import argparse
import json
import re
import sys
import yaml
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Constants — OpenClaw ↔ ICDEV™ mapping
# ---------------------------------------------------------------------------

# OpenClaw tool names → ICDEV™ allowed-tools mapping
# OpenClaw exposes raw system capabilities; ICDEV™ exposes sandboxed MCP tools
TOOL_MAP = {
    # File system
    "read_file": "Read",
    "read": "Read",
    "write_file": "Write",
    "write": "Write",
    "edit_file": "Edit",
    "edit": "Edit",
    "list_files": "Glob",
    "list_directory": "Glob",
    "find_files": "Glob",
    "search_files": "Grep",
    "search": "Grep",
    "grep": "Grep",
    "glob": "Glob",
    # Shell
    "run_command": "Bash",
    "run_shell": "Bash",
    "execute": "Bash",
    "shell": "Bash",
    "bash": "Bash",
    "terminal": "Bash",
    # Web
    "browse": "WebFetch",
    "web_search": "WebSearch",
    "fetch_url": "WebFetch",
    "http_request": "WebFetch",
    "web_fetch": "WebFetch",
    # Agent
    "agent": "Agent",
    "delegate": "Agent",
    "spawn_agent": "Agent",
    # Task management
    "todo": "TodoWrite",
    "task": "TodoWrite",
    "tasks": "TodoWrite",
}

# ICDEV™ allowed tools (valid values for allowed-tools frontmatter)
ICDEV_TOOLS = {
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Agent",
    "TodoWrite",
}

# OpenClaw capabilities that have NO ICDEV™ equivalent (incompatible)
INCOMPATIBLE_CAPABILITIES = {
    "browser_control": "OpenClaw browser automation (click, type, navigate) has no ICDEV™ equivalent",
    "screen_capture": "OpenClaw screen capture requires desktop access",
    "mouse_control": "OpenClaw mouse/keyboard simulation not supported",
    "whatsapp": "OpenClaw messaging integration not available in ICDEV™",
    "telegram": "OpenClaw messaging integration not available in ICDEV™",
    "discord": "OpenClaw messaging integration not available in ICDEV™",
    "slack_send": "ICDEV™ doesn't send Slack messages (read-only via MCP)",
    "imessage": "OpenClaw iMessage integration not available in ICDEV™",
    "email_send": "ICDEV™ doesn't send emails autonomously",
    "cron": "OpenClaw cron/heartbeat not available (use Genesis reflexes instead)",
    "docker": "OpenClaw Docker control not directly exposed",
    "gui": "OpenClaw GUI interactions not supported in ICDEV™ CLI",
}

# OpenClaw Node.js patterns that need Python translation
NODE_PATTERNS = {
    re.compile(r"\bnpm\s+(install|run|exec)\b"): "pip install / python -m",
    re.compile(r"\bnpx\s+"): "python -m / direct script execution",
    re.compile(r"\bnode\s+"): "python",
    re.compile(r"\brequire\s*\("): "import (Python)",
    re.compile(r"\bconst\s+\w+\s*="): "Python variable assignment",
    re.compile(r"\basync\s+function\b"): "async def (Python)",
    re.compile(r"=>\s*\{"): "Python lambda/function",
    re.compile(r"\.then\s*\("): "await (Python async)",
    re.compile(r"\bconsole\.log\b"): "print() (Python)",
}

# License compatibility for Gov/DoD environments
# Permissive licenses are safe; copyleft licenses have distribution restrictions
PERMISSIVE_LICENSES = {
    "mit",
    "mit-0",
    "apache-2.0",
    "apache-2",
    "bsd-2-clause",
    "bsd-3-clause",
    "isc",
    "unlicense",
    "cc0-1.0",
    "0bsd",
    "wtfpl",
    "zlib",
    "usg-internal",
    "public-domain",
    "cc-by-4.0",
    "cc-by-sa-4.0",
}
COPYLEFT_LICENSES = {
    "gpl-2.0",
    "gpl-3.0",
    "agpl-3.0",
    "lgpl-2.1",
    "lgpl-3.0",
    "mpl-2.0",
    "eupl-1.2",
    "osl-3.0",
    "cecill-2.1",
}
# IL5/IL6 blocked (copyleft + gov distribution = legal risk)
IL5_IL6_BLOCKED_LICENSES = COPYLEFT_LICENSES

# Name validation (ICDEV™ requirement)
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")


# ---------------------------------------------------------------------------
# Compatibility Checker
# ---------------------------------------------------------------------------
class CompatibilityReport:
    """Structured compatibility analysis between OpenClaw skill and ICDEV™."""

    def __init__(self, skill_path):
        self.skill_path = str(skill_path)
        self.blockers = []  # Cannot import — must fix
        self.warnings = []  # Can import but degraded
        self.adaptations = []  # Auto-fixable differences
        self.info = []  # Informational notes
        self.tool_mapping = {}  # OpenClaw tool → ICDEV™ tool
        self.unmapped_tools = []  # Tools with no ICDEV™ equivalent
        self.node_patterns = []  # Node.js patterns found
        self.score = 100  # Compatibility score (0-100)

    def add_blocker(self, code, message, detail=""):
        self.blockers.append({"code": code, "message": message, "detail": detail})
        self.score = max(0, self.score - 25)

    def add_warning(self, code, message, detail=""):
        self.warnings.append({"code": code, "message": message, "detail": detail})
        self.score = max(0, self.score - 10)

    def add_adaptation(self, code, message, detail=""):
        self.adaptations.append({"code": code, "message": message, "detail": detail})
        self.score = max(0, self.score - 2)

    def add_info(self, message):
        self.info.append(message)

    @property
    def compatible(self):
        return len(self.blockers) == 0

    def to_dict(self):
        return {
            "skill_path": self.skill_path,
            "compatible": self.compatible,
            "score": self.score,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "adaptations": self.adaptations,
            "info": self.info,
            "tool_mapping": self.tool_mapping,
            "unmapped_tools": self.unmapped_tools,
            "node_patterns_found": len(self.node_patterns),
            "summary": (
                f"{'COMPATIBLE' if self.compatible else 'INCOMPATIBLE'} "
                f"(score: {self.score}/100, "
                f"{len(self.blockers)} blockers, "
                f"{len(self.warnings)} warnings, "
                f"{len(self.adaptations)} auto-adaptations)"
            ),
        }


def check_compatibility(skill_path):
    """Run full compatibility analysis on an OpenClaw skill.

    Args:
        skill_path: Path to OpenClaw skill directory.

    Returns:
        CompatibilityReport with detailed findings.
    """
    skill_path = Path(skill_path)
    report = CompatibilityReport(skill_path)

    # ── 1. Structure check ──────────────────────────────────────────────
    if not skill_path.is_dir():
        report.add_blocker("STRUCT_001", "Not a directory", str(skill_path))
        return report

    # Find skill.md (case-insensitive)
    skill_md = None
    for candidate in ("skill.md", "SKILL.md", "Skill.md", "README.md"):
        p = skill_path / candidate
        if p.exists():
            skill_md = p
            break

    if not skill_md:
        report.add_blocker("STRUCT_002", "No skill.md found in directory")
        return report

    # ── 2. Parse frontmatter ────────────────────────────────────────────
    try:
        content = skill_md.read_text(encoding="utf-8")
    except Exception as exc:
        report.add_blocker("PARSE_001", f"Cannot read skill.md: {exc}")
        return report

    frontmatter = {}
    body = content
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if fm_match:
        try:
            frontmatter = yaml.safe_load(fm_match.group(1)) or {}
            body = fm_match.group(2)
        except yaml.YAMLError as exc:
            report.add_blocker("PARSE_002", f"Invalid YAML frontmatter: {exc}")
            return report
    else:
        report.add_warning("PARSE_003", "No YAML frontmatter found — will use defaults")

    # ── 3. Name validation ──────────────────────────────────────────────
    name = frontmatter.get("name", skill_path.name)
    if not isinstance(name, str):
        name = str(name)

    icdev_name = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")[:63]
    if not icdev_name or len(icdev_name) < 3:
        icdev_name = f"oc-{skill_path.name.lower()}"[:63]

    if name != icdev_name:
        report.add_adaptation(
            "NAME_001",
            f"Name normalized: '{name}' → '{icdev_name}'",
            "ICDEV™ requires lowercase alphanumeric + hyphens, 3-64 chars",
        )

    if not NAME_PATTERN.match(icdev_name):
        report.add_warning("NAME_002", f"Name '{icdev_name}' still doesn't match ICDEV™ pattern after normalization")

    # ── 4. Description check ────────────────────────────────────────────
    description = frontmatter.get("description", "")
    if not description:
        # Try to extract from body first line
        first_line = body.strip().split("\n")[0] if body.strip() else ""
        if first_line and not first_line.startswith("#"):
            description = first_line[:200]
            report.add_adaptation("DESC_001", "Description extracted from body first line")
        else:
            report.add_warning("DESC_002", "No description found — will use generic")

    # ── 5. Tool/capability mapping ──────────────────────────────────────
    oc_tools = frontmatter.get("tools", [])
    if isinstance(oc_tools, str):
        oc_tools = [t.strip() for t in oc_tools.split(",")]

    mapped_tools = set()
    for tool in oc_tools:
        tool_lower = tool.lower().strip()
        if tool in ICDEV_TOOLS:
            # Direct match (already ICDEV™ format)
            mapped_tools.add(tool)
            report.tool_mapping[tool] = tool
        elif tool_lower in TOOL_MAP:
            icdev_tool = TOOL_MAP[tool_lower]
            mapped_tools.add(icdev_tool)
            report.tool_mapping[tool] = icdev_tool
            report.add_adaptation(
                "TOOL_001",
                f"Tool mapped: '{tool}' → '{icdev_tool}'",
            )
        else:
            # Check for incompatible capabilities
            if tool_lower in INCOMPATIBLE_CAPABILITIES:
                report.add_warning(
                    "TOOL_002",
                    f"Incompatible capability: '{tool}'",
                    INCOMPATIBLE_CAPABILITIES[tool_lower],
                )
            else:
                report.unmapped_tools.append(tool)
                report.add_warning(
                    "TOOL_003",
                    f"Unknown tool '{tool}' — will be stripped (no ICDEV™ equivalent)",
                )

    if not mapped_tools and not oc_tools:
        # No tools declared — give defaults
        mapped_tools = {"Read", "Grep", "Glob"}
        report.add_adaptation("TOOL_004", "No tools declared — using safe defaults: Read, Grep, Glob")

    # ── 6. Node.js / npm pattern detection ──────────────────────────────
    for pattern, replacement in NODE_PATTERNS.items():
        matches = pattern.findall(body)
        if matches:
            report.node_patterns.append(
                {
                    "pattern": pattern.pattern,
                    "replacement": replacement,
                    "count": len(matches),
                }
            )
            report.add_warning(
                "NODE_001",
                f"Node.js pattern found: {pattern.pattern} ({len(matches)}x)",
                f"ICDEV™ uses Python — equivalent: {replacement}",
            )

    # Also check scripts for Node.js
    scripts_dir = skill_path / "scripts"
    if scripts_dir.is_dir():
        for js_file in scripts_dir.rglob("*.js"):
            report.add_blocker(
                "NODE_002",
                f"JavaScript file found: {js_file.name}",
                "ICDEV™ only supports Python scripts — manual translation required",
            )
        for ts_file in scripts_dir.rglob("*.ts"):
            report.add_blocker(
                "NODE_003",
                f"TypeScript file found: {ts_file.name}",
                "ICDEV™ only supports Python scripts — manual translation required",
            )
        for sh_file in scripts_dir.rglob("*.sh"):
            report.add_warning(
                "SHELL_001",
                f"Shell script found: {sh_file.name}",
                "Shell scripts may work but should be reviewed for cross-platform compatibility",
            )

    # Check for package.json (Node.js dependency)
    if (skill_path / "package.json").exists():
        report.add_blocker(
            "NODE_004",
            "package.json found — skill depends on npm ecosystem",
            "ICDEV™ uses Python + pip; npm dependencies cannot be installed",
        )

    # ── 7. Dependency check ─────────────────────────────────────────────
    deps = frontmatter.get("dependencies", [])
    if isinstance(deps, str):
        deps = [d.strip() for d in deps.split(",")]

    for dep in deps:
        if isinstance(dep, str):
            # Check if it's a Python or npm package
            if dep.startswith("@") or "/" in dep:
                report.add_blocker(
                    "DEP_001",
                    f"npm-style dependency: '{dep}'",
                    "ICDEV™ marketplace uses pip packages, not npm",
                )
            else:
                report.add_info(f"Dependency declared: '{dep}' — will check marketplace availability")

    # ── 8. Instruction format check ─────────────────────────────────────
    # Check for OpenClaw-specific syntax
    oc_patterns = [
        (re.compile(r"\{\{skill\.\w+\}\}"), "OC_001", "OpenClaw template variable {{skill.*}}"),
        (re.compile(r"@claw\b"), "OC_002", "OpenClaw @claw mention"),
        (re.compile(r"/skill\s+"), "OC_003", "OpenClaw /skill command syntax"),
        (re.compile(r"claw\.memory\b"), "OC_004", "OpenClaw claw.memory API"),
        (re.compile(r"claw\.run\b"), "OC_005", "OpenClaw claw.run API"),
        (re.compile(r"claw\.browse\b"), "OC_006", "OpenClaw claw.browse API"),
    ]
    for pattern, code, desc in oc_patterns:
        matches = pattern.findall(body)
        if matches:
            report.add_warning(
                code,
                f"OpenClaw-specific syntax found: {desc} ({len(matches)}x)",
                "These references will not work in ICDEV™ and will be commented out",
            )

    # ── 9. Content quality ──────────────────────────────────────────────
    if len(body.strip()) < 50:
        report.add_warning("QUAL_001", "Skill body is very short (<50 chars) — may lack useful instructions")

    if not re.search(r"^#{1,3}\s+", body, re.MULTILINE):
        report.add_adaptation(
            "QUAL_002",
            "No markdown headers found — will add default ## sections",
        )

    # ── 10. Version check ───────────────────────────────────────────────
    version = frontmatter.get("version", "")
    if version and not re.match(r"^\d+\.\d+\.\d+", str(version)):
        report.add_adaptation(
            "VER_001",
            f"Non-semver version '{version}' → will default to '1.0.0'",
        )

    # ── 11. License compliance ──────────────────────────────────────────
    oc_license = str(frontmatter.get("license", "")).strip().lower()
    if oc_license:
        if oc_license in PERMISSIVE_LICENSES:
            report.add_info(f"License '{oc_license}' is permissive — compatible with all ILs")
        elif oc_license in COPYLEFT_LICENSES:
            report.add_warning(
                "LIC_001",
                f"Copyleft license '{oc_license}' — blocked for IL5/IL6 environments",
                "Copyleft + government distribution = legal risk. Consult legal counsel.",
            )
        else:
            report.add_warning(
                "LIC_002",
                f"Unknown license '{oc_license}' — review before deployment to IL4+",
            )
    else:
        report.add_warning(
            "LIC_003",
            "No license declared — assume all rights reserved, review before use",
        )

    # ── 12. OpenClaw hooks directory ────────────────────────────────────
    hooks_dir = skill_path / "hooks"
    if hooks_dir.is_dir():
        for js_hook in hooks_dir.rglob("*.js"):
            report.add_warning(
                "HOOK_001",
                f"OpenClaw JS hook found: {js_hook.relative_to(skill_path)}",
                "OpenClaw hooks use Node.js — will be stripped (ICDEV™ uses Python hooks)",
            )
        for ts_hook in hooks_dir.rglob("*.ts"):
            report.add_warning(
                "HOOK_002",
                f"OpenClaw TS hook found: {ts_hook.relative_to(skill_path)}",
                "OpenClaw hooks use TypeScript — will be stripped",
            )

    # ── 13. Learnings / state directories ───────────────────────────────
    for state_dir in (".learnings", ".state", ".cache", ".data"):
        sd = skill_path / state_dir
        if sd.is_dir():
            report.add_adaptation(
                "STATE_001",
                f"OpenClaw state directory '{state_dir}/' found — will be excluded from import",
                "ICDEV™ uses its own memory/state system (data/memory.db)",
            )

    return report


# ---------------------------------------------------------------------------
# Translator
# ---------------------------------------------------------------------------
def translate_to_icdev(skill_path, output_path=None):
    """Translate an OpenClaw skill into ICDEV™ SKILL.md format.

    Args:
        skill_path: Path to OpenClaw skill directory.
        output_path: Optional output directory (default: modifies in-place).

    Returns:
        dict with translated content, compatibility report, and output path.
    """
    skill_path = Path(skill_path)

    # Run compatibility check first
    report = check_compatibility(skill_path)

    if not report.compatible:
        return {
            "success": False,
            "error": "Skill has blocking incompatibilities — cannot translate",
            "report": report.to_dict(),
        }

    # Parse the original skill
    skill_md = None
    for candidate in ("skill.md", "SKILL.md", "Skill.md", "README.md"):
        p = skill_path / candidate
        if p.exists():
            skill_md = p
            break

    content = skill_md.read_text(encoding="utf-8")

    # Extract frontmatter and body
    frontmatter = {}
    body = content
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if fm_match:
        frontmatter = yaml.safe_load(fm_match.group(1)) or {}
        body = fm_match.group(2)

    # ── Build ICDEV™ frontmatter ─────────────────────────────────────────
    name = frontmatter.get("name", skill_path.name)
    icdev_name = re.sub(r"[^a-z0-9-]", "-", str(name).lower()).strip("-")[:63]
    if not icdev_name or len(icdev_name) < 3:
        icdev_name = f"oc-{skill_path.name.lower()}"[:63]

    # Map tools
    oc_tools = frontmatter.get("tools", [])
    if isinstance(oc_tools, str):
        oc_tools = [t.strip() for t in oc_tools.split(",")]

    icdev_tools = set()
    for tool in oc_tools:
        if tool in ICDEV_TOOLS:
            icdev_tools.add(tool)
        elif tool.lower().strip() in TOOL_MAP:
            icdev_tools.add(TOOL_MAP[tool.lower().strip()])
    if not icdev_tools:
        icdev_tools = {"Read", "Grep", "Glob"}

    description = frontmatter.get("description", "")
    if not description:
        first_line = body.strip().split("\n")[0] if body.strip() else ""
        if first_line and not first_line.startswith("#"):
            description = first_line[:200]
        else:
            description = f"Imported OpenClaw skill: {icdev_name}"

    version = str(frontmatter.get("version", "1.0.0"))
    if not re.match(r"^\d+\.\d+\.\d+", version):
        version = "1.0.0"

    icdev_frontmatter = {
        "name": icdev_name,
        "description": description,
        "context": "fork",
        "allowed-tools": sorted(icdev_tools),
    }
    tags = frontmatter.get("tags", [])
    if tags:
        icdev_frontmatter["tags"] = tags if isinstance(tags, list) else [tags]

    # ── Build ICDEV™ body ────────────────────────────────────────────────
    # Add title
    display_name = frontmatter.get("name", icdev_name)
    icdev_body_parts = [f"# {display_name}", "", "CUI // SP-CTI", ""]

    # Add overview section
    icdev_body_parts.append("## Overview")
    icdev_body_parts.append("")
    icdev_body_parts.append(description)
    icdev_body_parts.append("")

    # Add provenance section
    # Try to get author from frontmatter, then from _meta.json (SkillHub download)
    author = frontmatter.get("author", "")
    if not author:
        meta_path = skill_path / "_meta.json"
        if meta_path.exists():
            try:
                import json as _json

                meta = _json.loads(meta_path.read_text(encoding="utf-8"))
                author = (
                    meta.get("author_handle", "")
                    or meta.get("author_name", "")
                    or (meta.get("ownerId", "")[:20] if meta.get("ownerId") else "")
                )
            except Exception:
                pass
    if not author:
        author = "community contributor"

    icdev_body_parts.append("## Provenance")
    icdev_body_parts.append("")
    icdev_body_parts.append("- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)")
    icdev_body_parts.append(f"- **Original Author:** {author}")
    icdev_body_parts.append("- **Source:** OpenClaw Community (SkillHub)")
    icdev_body_parts.append(f"- **Author:** {author}")
    icdev_body_parts.append(f"- **Original Version:** {version}")
    icdev_body_parts.append(f"- **Compatibility Score:** {report.score}/100")
    if report.adaptations:
        icdev_body_parts.append(f"- **Auto-Adaptations:** {len(report.adaptations)}")
    if report.warnings:
        icdev_body_parts.append(f"- **Warnings:** {len(report.warnings)}")
    icdev_body_parts.append("")

    # Process the original body
    # Comment out OpenClaw-specific syntax
    translated_body = body
    oc_syntax_patterns = [
        (re.compile(r"\{\{skill\.\w+\}\}"), "<!-- ICDEV™: OpenClaw template variable removed -->"),
        (re.compile(r"@claw\b"), "Claude"),
        (re.compile(r"claw\.memory\b"), "memory system"),
        (re.compile(r"claw\.run\b"), "execute"),
        (re.compile(r"claw\.browse\b"), "WebFetch"),
    ]
    for pattern, replacement in oc_syntax_patterns:
        translated_body = pattern.sub(replacement, translated_body)

    # Replace Node.js command references with Python equivalents
    node_replacements = [
        (re.compile(r"\bnpm\s+install\s+"), "pip install "),
        (re.compile(r"\bnpm\s+run\s+"), "python -m "),
        (re.compile(r"\bnpx\s+"), "python -m "),
        (re.compile(r"\bnode\s+(\S+)"), r"python \1"),
        (re.compile(r"\bconsole\.log\b"), "print"),
    ]
    for pattern, replacement in node_replacements:
        translated_body = pattern.sub(replacement, translated_body)

    # Add the translated instructions
    if translated_body.strip():
        icdev_body_parts.append("## Instructions")
        icdev_body_parts.append("")
        icdev_body_parts.append(translated_body.strip())
        icdev_body_parts.append("")

    # Add compatibility notes if there are warnings
    if report.warnings:
        icdev_body_parts.append("## Compatibility Notes")
        icdev_body_parts.append("")
        icdev_body_parts.append("> The following items were flagged during import and may need attention:")
        icdev_body_parts.append("")
        for w in report.warnings:
            icdev_body_parts.append(f"- **{w['code']}**: {w['message']}")
            if w.get("detail"):
                icdev_body_parts.append(f"  - {w['detail']}")
        icdev_body_parts.append("")

    # Assemble final content
    fm_yaml = yaml.dump(icdev_frontmatter, default_flow_style=False, allow_unicode=True)
    icdev_content = f"---\n{fm_yaml}---\n" + "\n".join(icdev_body_parts)

    # Write output
    out_dir = Path(output_path) if output_path else skill_path
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "SKILL.md"
    out_file.write_text(icdev_content, encoding="utf-8", newline="")

    return {
        "success": True,
        "output_path": str(out_file),
        "icdev_name": icdev_name,
        "original_name": str(name),
        "tools_mapped": report.tool_mapping,
        "tools_unmapped": report.unmapped_tools,
        "adaptations_applied": len(report.adaptations),
        "warnings_remaining": len(report.warnings),
        "compatibility_score": report.score,
        "report": report.to_dict(),
    }


# ---------------------------------------------------------------------------
# Functional Validation (Dry-Run)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Content Intent Scanner — deterministic malice detection
# ---------------------------------------------------------------------------

# Patterns indicating malicious intent in skill instructions
MALICE_PATTERNS = {
    "data_exfiltration": [
        (re.compile(r"curl\s+.*-[dX]\s+.*https?://", re.IGNORECASE), "critical", "HTTP POST to external URL"),
        (re.compile(r"wget\s+.*--post", re.IGNORECASE), "critical", "wget POST to external"),
        (
            re.compile(r"send\s+(?:all\s+)?(?:files?|data|content|code|secrets?|keys?|tokens?)\s+to\b", re.IGNORECASE),
            "critical",
            "Exfiltrate data",
        ),
        (re.compile(r"upload\s+.*to\s+(?:https?://|ftp://|s3://)", re.IGNORECASE), "critical", "Upload to external"),
        (re.compile(r"base64\s+.*\|\s*curl", re.IGNORECASE), "critical", "Encoded exfiltration"),
        (
            re.compile(r"(?:cat|read)\s+.*(?:\.env|credentials|\.ssh|\.aws|\.gnupg)", re.IGNORECASE),
            "high",
            "Read sensitive files",
        ),
        (
            re.compile(r"pipe\s+.*(?:output|result).*(?:external|remote|webhook)", re.IGNORECASE),
            "high",
            "Pipe output externally",
        ),
    ],
    "privilege_escalation": [
        (re.compile(r"\bsudo\s+", re.IGNORECASE), "high", "Sudo usage"),
        (re.compile(r"\bchmod\s+777\b"), "high", "World-writable permissions"),
        (re.compile(r"\bchmod\s+\+s\b"), "critical", "Setuid bit"),
        (re.compile(r"--no-verify\b|--no-check\b|--skip-validation\b", re.IGNORECASE), "high", "Skip verification"),
        (
            re.compile(r"disable\s+(?:security|firewall|antivirus|protection|auth)", re.IGNORECASE),
            "critical",
            "Disable security",
        ),
        (re.compile(r"run\s+as\s+(?:root|admin|administrator)", re.IGNORECASE), "high", "Run as privileged user"),
    ],
    "destructive_actions": [
        (re.compile(r"\brm\s+-rf\s+[/~]", re.IGNORECASE), "critical", "Recursive delete from root/home"),
        (re.compile(r"(?:drop|truncate)\s+(?:table|database)\b", re.IGNORECASE), "critical", "Drop database objects"),
        (re.compile(r"format\s+[a-zA-Z]:", re.IGNORECASE), "critical", "Format drive"),
        (re.compile(r"delete\s+(?:all|everything|\*)", re.IGNORECASE), "high", "Mass deletion"),
        (re.compile(r"git\s+push\s+--force\s+(?:origin\s+)?main", re.IGNORECASE), "high", "Force push to main"),
    ],
    "social_engineering": [
        (
            re.compile(r"don'?t\s+(?:tell|show|inform|alert)\s+(?:the\s+)?user", re.IGNORECASE),
            "critical",
            "Hide actions from user",
        ),
        (
            re.compile(r"(?:secretly|silently|quietly|covertly)\s+(?:run|execute|send|install)", re.IGNORECASE),
            "critical",
            "Covert execution",
        ),
        (
            re.compile(r"without\s+(?:the\s+)?user(?:'s)?\s+(?:knowledge|consent|knowing|approval)", re.IGNORECASE),
            "critical",
            "Without user consent",
        ),
        (re.compile(r"pretend\s+(?:to\s+be|you\s+are)\b", re.IGNORECASE), "high", "Identity deception"),
    ],
    "bypass_instructions": [
        (
            re.compile(
                r"ignore\s+(?:all\s+)?(?:previous|prior|above|system)\s+(?:instructions|rules|prompts)", re.IGNORECASE
            ),
            "critical",
            "Override system prompt",
        ),
        (
            re.compile(r"bypass\s+(?:security|auth|validation|compliance|gate)", re.IGNORECASE),
            "critical",
            "Bypass security",
        ),
        (
            re.compile(r"override\s+(?:safety|security|restriction|permission|guard)", re.IGNORECASE),
            "critical",
            "Override safety",
        ),
        (
            re.compile(r"skip\s+(?:review|approval|audit|compliance|security\s+check)", re.IGNORECASE),
            "high",
            "Skip review/audit",
        ),
    ],
}


def scan_content_intent(text):
    """Scan skill instruction text for malicious intent patterns.

    Deterministic, no LLM needed, air-gap safe.

    Args:
        text: The instruction/body text of the skill.

    Returns:
        dict with 'safe' (bool), 'findings' (list), 'categories' (dict of counts).
    """
    findings = []
    categories = {}

    for category, patterns in MALICE_PATTERNS.items():
        cat_findings = []
        for pattern, severity, description in patterns:
            matches = pattern.findall(text)
            if matches:
                cat_findings.append(
                    {
                        "category": category,
                        "severity": severity,
                        "description": description,
                        "pattern": pattern.pattern[:80],
                        "match_count": len(matches),
                        "sample": matches[0][:100] if matches else "",
                    }
                )
        if cat_findings:
            findings.extend(cat_findings)
        categories[category] = len(cat_findings)

    critical_count = sum(1 for f in findings if f["severity"] == "critical")
    high_count = sum(1 for f in findings if f["severity"] == "high")
    safe = critical_count == 0 and high_count == 0

    return {
        "safe": safe,
        "findings": findings,
        "categories": categories,
        "critical_count": critical_count,
        "high_count": high_count,
        "total_findings": len(findings),
    }


def validate_translated_skill(skill_path):
    """Validate a translated ICDEV™ SKILL.md for functional correctness.

    Deterministic checks (no LLM needed):
    1. SKILL.md exists and parses correctly
    2. Frontmatter has all required fields
    3. Body has at least one ## section
    4. Allowed-tools are all valid ICDEV™ tools
    5. No broken markdown (unclosed code blocks, orphaned links)
    6. Instructions are non-trivial (>100 chars of actual content)
    7. No OpenClaw-specific syntax survived translation
    8. CUI banner present

    Args:
        skill_path: Path to translated skill directory.

    Returns:
        dict with pass/fail, findings, and readiness score.
    """
    skill_path = Path(skill_path)
    findings = []
    checks_passed = 0
    total_checks = 8

    # 1. SKILL.md exists
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        findings.append({"check": "file_exists", "pass": False, "detail": "SKILL.md not found"})
        return {"passed": False, "score": 0, "findings": findings, "checks_passed": 0, "total_checks": total_checks}

    findings.append({"check": "file_exists", "pass": True, "detail": "SKILL.md found"})
    checks_passed += 1

    content = skill_md.read_text(encoding="utf-8")

    # 2. Frontmatter parses with required fields
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if fm_match:
        try:
            fm = yaml.safe_load(fm_match.group(1)) or {}
            body = fm_match.group(2)
            has_name = bool(fm.get("name"))
            has_desc = bool(fm.get("description"))
            if has_name and has_desc:
                findings.append(
                    {"check": "frontmatter", "pass": True, "detail": f"name='{fm['name']}', description present"}
                )
                checks_passed += 1
            else:
                missing = []
                if not has_name:
                    missing.append("name")
                if not has_desc:
                    missing.append("description")
                findings.append(
                    {"check": "frontmatter", "pass": False, "detail": f"Missing fields: {', '.join(missing)}"}
                )
        except yaml.YAMLError as exc:
            findings.append({"check": "frontmatter", "pass": False, "detail": f"YAML parse error: {exc}"})
            body = content
    else:
        findings.append({"check": "frontmatter", "pass": False, "detail": "No YAML frontmatter found"})
        body = content
        fm = {}

    # 3. Body has sections
    sections = re.findall(r"^##\s+.+$", body, re.MULTILINE)
    if sections:
        findings.append(
            {
                "check": "sections",
                "pass": True,
                "detail": f"{len(sections)} sections found: {[s.strip('# ') for s in sections[:5]]}",
            }
        )
        checks_passed += 1
    else:
        findings.append({"check": "sections", "pass": False, "detail": "No ## sections in body"})

    # 4. Allowed-tools validation
    tools = fm.get("allowed-tools", [])
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",")]
    invalid_tools = [t for t in tools if t not in ICDEV_TOOLS]
    if not invalid_tools:
        findings.append(
            {"check": "tools_valid", "pass": True, "detail": f"All {len(tools)} tools are valid ICDEV™ tools"}
        )
        checks_passed += 1
    else:
        findings.append({"check": "tools_valid", "pass": False, "detail": f"Invalid tools: {invalid_tools}"})

    # 5. Markdown integrity
    unclosed_code = body.count("```") % 2 != 0
    orphaned_links = len(re.findall(r"\[([^\]]*)\]\(\s*\)", body))
    if not unclosed_code and orphaned_links == 0:
        findings.append({"check": "markdown_integrity", "pass": True, "detail": "No broken markdown"})
        checks_passed += 1
    else:
        issues = []
        if unclosed_code:
            issues.append("unclosed code block")
        if orphaned_links:
            issues.append(f"{orphaned_links} empty links")
        findings.append(
            {"check": "markdown_integrity", "pass": False, "detail": f"Broken markdown: {', '.join(issues)}"}
        )

    # 6. Non-trivial content
    # Strip headers and whitespace to measure actual instruction content
    stripped = re.sub(r"^#{1,6}\s+.*$", "", body, flags=re.MULTILINE).strip()
    stripped = re.sub(r"\s+", " ", stripped)
    if len(stripped) > 100:
        findings.append(
            {"check": "content_depth", "pass": True, "detail": f"{len(stripped)} chars of instruction content"}
        )
        checks_passed += 1
    else:
        findings.append(
            {"check": "content_depth", "pass": False, "detail": f"Only {len(stripped)} chars of content (need >100)"}
        )

    # 7. No OpenClaw syntax survived
    oc_survivors = []
    for pattern_str in ("@claw", "claw.memory", "claw.run", "claw.browse", "{{skill."):
        if pattern_str in body.split("## Compatibility Notes")[0] if "## Compatibility Notes" in body else body:
            oc_survivors.append(pattern_str)
    if not oc_survivors:
        findings.append({"check": "no_openclaw_syntax", "pass": True, "detail": "No OpenClaw syntax in instructions"})
        checks_passed += 1
    else:
        findings.append(
            {"check": "no_openclaw_syntax", "pass": False, "detail": f"OpenClaw syntax survived: {oc_survivors}"}
        )

    # 8. CUI banner present
    if "CUI // SP-CTI" in content or "CUI //" in content:
        findings.append({"check": "cui_banner", "pass": True, "detail": "CUI banner present"})
        checks_passed += 1
    else:
        findings.append({"check": "cui_banner", "pass": False, "detail": "Missing CUI // SP-CTI banner"})

    score = round((checks_passed / total_checks) * 100)
    return {
        "passed": checks_passed == total_checks,
        "score": score,
        "checks_passed": checks_passed,
        "total_checks": total_checks,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="OpenClaw-to-ICDEV™ compatibility checker and translator",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", metavar="PATH", help="Check compatibility of an OpenClaw skill")
    group.add_argument("--translate", metavar="PATH", help="Translate an OpenClaw skill to ICDEV™ format")
    group.add_argument("--full", metavar="PATH", help="Full pipeline: check + translate")

    parser.add_argument("--output", "-o", metavar="PATH", help="Output directory for translated skill")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    try:
        if args.check:
            report = check_compatibility(args.check)
            result = report.to_dict()
        elif args.translate:
            result = translate_to_icdev(args.translate, args.output)
        elif args.full:
            result = translate_to_icdev(args.full, args.output)
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, dict):
                if result.get("error"):
                    print(f"ERROR: {result['error']}")
                    sys.exit(1)
                for k, v in result.items():
                    if k != "report":
                        print(f"  {k}: {v}")

        if isinstance(result, dict) and result.get("error"):
            sys.exit(1)

    except Exception as exc:
        err = {"error": str(exc)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
