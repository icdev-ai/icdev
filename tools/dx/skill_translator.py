#!/usr/bin/env python3
# CUI // SP-CTI
"""Translate Claude Code skills to other AI coding tool formats.

ADR D198: Skill translation preserves semantic intent — each tool gets
equivalent capability in its native format, not a literal copy.

Reads .claude/skills/*/SKILL.md and generates equivalent skill/command
files for Codex (.agents/skills/), Copilot (.github/prompts/), and
Cursor (.cursor/rules/).

Usage:
    python tools/dx/skill_translator.py --all --write --json
    python tools/dx/skill_translator.py --platform codex --skills icdev-build
    python tools/dx/skill_translator.py --platform copilot --write
"""

import argparse
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SKILLS_DIR = BASE_DIR / ".claude" / "skills"


# ── Skill Parser ────────────────────────────────────────────────────────

def parse_claude_skill(skill_dir):
    """Parse a .claude/skills/*/SKILL.md into structured data.

    Returns:
        dict with: name, description, context, allowed_tools, usage,
        steps, hard_prompts, examples, error_handling, raw_body
    """
    skill_path = Path(skill_dir) / "SKILL.md"
    if not skill_path.exists():
        return None

    text = skill_path.read_text(encoding="utf-8")

    # Parse YAML frontmatter
    frontmatter = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1].strip()
            body = parts[2].strip()
            for line in fm_text.split("\n"):
                if ":" in line:
                    key, _, val = line.partition(":")
                    frontmatter[key.strip()] = val.strip()

    # Extract sections from body
    sections = {}
    current_section = "intro"
    current_lines = []
    for line in body.split("\n"):
        header_match = re.match(r"^##\s+(.+)$", line)
        if header_match:
            sections[current_section] = "\n".join(current_lines).strip()
            current_section = header_match.group(1).strip().lower()
            current_lines = []
        else:
            current_lines.append(line)
    sections[current_section] = "\n".join(current_lines).strip()

    # Extract steps (### sub-sections within Steps or body)
    steps = []
    step_pattern = re.compile(r"^###\s+(\d+)\.\s+(.+)$")
    in_steps = False
    current_step = None
    step_lines = []
    for line in body.split("\n"):
        if line.strip().lower().startswith("## steps"):
            in_steps = True
            continue
        if in_steps and line.startswith("## ") and not line.startswith("### "):
            if current_step:
                steps.append({"title": current_step, "body": "\n".join(step_lines).strip()})
            in_steps = False
            continue
        if in_steps:
            m = step_pattern.match(line)
            if m:
                if current_step:
                    steps.append({"title": current_step, "body": "\n".join(step_lines).strip()})
                current_step = m.group(2).strip()
                step_lines = []
            else:
                step_lines.append(line)
    if current_step:
        steps.append({"title": current_step, "body": "\n".join(step_lines).strip()})

    return {
        "name": frontmatter.get("name", Path(skill_dir).name),
        "description": frontmatter.get("description", ""),
        "context": frontmatter.get("context", "fork"),
        "allowed_tools": frontmatter.get("allowed-tools", ""),
        "usage": sections.get("usage", ""),
        "what_this_does": sections.get("what this does", ""),
        "steps": steps,
        "hard_prompts": sections.get("hard prompts referenced", ""),
        "example": sections.get("example", ""),
        "error_handling": sections.get("error handling", ""),
        "raw_body": body,
        "source_path": str(skill_path),
    }


def list_skills(skills_dir=None):
    """List all available Claude Code skills."""
    sd = Path(skills_dir) if skills_dir else SKILLS_DIR
    if not sd.exists():
        return []
    return sorted([
        d.name for d in sd.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    ])


# ── Translators ─────────────────────────────────────────────────────────

def _translate_to_codex_skill(skill_data):
    """Generate .agents/skills/{name}/SKILL.md for OpenAI Codex.

    Codex skills use same YAML frontmatter + markdown body format as Claude,
    but invoked with $skill-name syntax.
    """
    name = skill_data["name"]
    desc = skill_data["description"]
    tools = skill_data["allowed_tools"]

    lines = [
        "---",
        f"name: {name}",
        f"description: {desc}",
    ]
    if tools:
        lines.append(f"allowed-tools: {tools}")
    lines.append("---")
    lines.append("")
    lines.append(f"# ${name}")
    lines.append("")

    if skill_data["what_this_does"]:
        lines.append("## What This Does")
        lines.append(skill_data["what_this_does"])
        lines.append("")

    if skill_data["steps"]:
        lines.append("## Steps")
        lines.append("")
        for i, step in enumerate(skill_data["steps"], 1):
            lines.append(f"### {i}. {step['title']}")
            # Replace MCP tool references with CLI equivalents
            body = step["body"]
            body = body.replace("Use the `", "Run the CLI command or use MCP tool `")
            lines.append(body)
            lines.append("")

    if skill_data["example"]:
        lines.append("## Example")
        # Replace /command with $command syntax
        example = skill_data["example"].replace(f"/{name}", f"${name}")
        lines.append(example)
        lines.append("")

    if skill_data["error_handling"]:
        lines.append("## Error Handling")
        lines.append(skill_data["error_handling"])

    return "\n".join(lines)


def _translate_to_copilot_prompt(skill_data):
    """Generate .github/prompts/{name}.prompt.md for GitHub Copilot.

    Copilot prompt files use YAML frontmatter with mode, description, tools.
    """
    name = skill_data["name"]
    desc = skill_data["description"]

    lines = [
        "---",
        "mode: agent",
        f"description: \"{desc}\"",
        "tools:",
        "  - terminal",
        "  - file_search",
        "---",
        "",
        f"# {name}",
        "",
    ]

    if skill_data["what_this_does"]:
        lines.append(skill_data["what_this_does"])
        lines.append("")

    if skill_data["steps"]:
        lines.append("## Steps")
        lines.append("")
        for i, step in enumerate(skill_data["steps"], 1):
            lines.append(f"{i}. **{step['title']}**")
            # Convert MCP references to terminal commands
            body = step["body"]
            body = re.sub(
                r"Use the `(\w+)` MCP tool from (\w[\w-]*)",
                r"Run the equivalent CLI command for \1",
                body,
            )
            # Keep code blocks, trim excessive detail
            code_blocks = re.findall(r"```[^`]*```", body, re.DOTALL)
            if code_blocks:
                for block in code_blocks:
                    lines.append(block)
            else:
                # Include first 3 lines of body
                body_lines = [l for l in body.split("\n") if l.strip()][:3]
                lines.extend(body_lines)
            lines.append("")

    if skill_data["example"]:
        lines.append("## Example")
        example = skill_data["example"].replace(f"/{name}", f"#prompt:{name}")
        lines.append(example)

    return "\n".join(lines)


def _translate_to_cursor_rule(skill_data):
    """Generate .cursor/rules/{name}.mdc for Cursor.

    Cursor rules use MDC format with frontmatter: description, globs.
    Agent-requested rules are triggered when the AI decides they're relevant.
    """
    name = skill_data["name"]
    desc = skill_data["description"]

    lines = [
        "---",
        f"description: \"ICDEV workflow: {desc}\"",
        "globs:",
        '  - "**/*.py"',
        '  - "**/*.yaml"',
        "---",
        "",
        f"# {name}",
        "",
        f"When the user asks to {desc.lower()}, follow these steps:",
        "",
    ]

    if skill_data["steps"]:
        for i, step in enumerate(skill_data["steps"], 1):
            lines.append(f"{i}. **{step['title']}**")
            # Convert to concise CLI instructions
            body = step["body"]
            # Extract bash commands
            commands = re.findall(r"```(?:bash)?\s*\n(.*?)```", body, re.DOTALL)
            if commands:
                for cmd in commands:
                    for cmd_line in cmd.strip().split("\n"):
                        if cmd_line.strip() and not cmd_line.startswith("#"):
                            lines.append(f"   ```bash")
                            lines.append(f"   {cmd_line.strip()}")
                            lines.append(f"   ```")
            else:
                # Summarize the step
                summary = re.sub(
                    r"Use the `\w+` MCP tool from [\w-]+[:\s]*",
                    "Run: ",
                    body,
                )
                first_line = summary.strip().split("\n")[0][:120]
                if first_line:
                    lines.append(f"   {first_line}")
            lines.append("")

    return "\n".join(lines)


# ── Orchestrator ────────────────────────────────────────────────────────

TRANSLATORS = {
    "codex": ("codex_skill", _translate_to_codex_skill, ".agents/skills/{name}/SKILL.md"),
    "copilot": ("copilot_prompt", _translate_to_copilot_prompt, ".github/prompts/{name}.prompt.md"),
    "cursor": ("cursor_mdc", _translate_to_cursor_rule, ".cursor/rules/{name}.mdc"),
}


def translate_skills(directory=None, platforms=None, skills=None,
                     write=False, dry_run=False):
    """Translate Claude Code skills to other tool formats.

    Args:
        directory: Project root directory.
        platforms: List of platform IDs or ['all'].
        skills: List of skill names or None for all.
        write: Write files to disk.
        dry_run: Preview without writing.

    Returns:
        dict: {platform: {skill_name: {path, content, written}}}
    """
    directory = Path(directory) if directory else Path.cwd()
    skills_dir = directory / ".claude" / "skills"

    # Resolve skills to translate
    available = list_skills(str(skills_dir))
    if skills:
        target_skills = [s for s in skills if s in available]
    else:
        target_skills = available

    # Resolve platforms
    if platforms is None or platforms == ["all"]:
        platforms = list(TRANSLATORS.keys())
    elif isinstance(platforms, str):
        platforms = [p.strip() for p in platforms.split(",")]

    results = {}

    for platform in platforms:
        if platform not in TRANSLATORS:
            results[platform] = {"error": f"No translator for platform: {platform}"}
            continue

        fmt_name, translator_fn, path_template = TRANSLATORS[platform]
        platform_results = {}

        for skill_name in target_skills:
            skill_data = parse_claude_skill(skills_dir / skill_name)
            if not skill_data:
                platform_results[skill_name] = {"error": "Could not parse skill"}
                continue

            content = translator_fn(skill_data)
            output_path = path_template.format(name=skill_name)
            full_path = directory / output_path

            written = False
            if write and not dry_run:
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content, encoding="utf-8")
                written = True

            platform_results[skill_name] = {
                "path": output_path,
                "full_path": str(full_path),
                "content": content,
                "written": written,
                "size_bytes": len(content.encode("utf-8")),
            }

        results[platform] = platform_results

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Translate Claude Code skills to other AI tool formats"
    )
    parser.add_argument("--dir", help="Project directory")
    parser.add_argument("--platform", help="Comma-separated platform IDs")
    parser.add_argument("--all", action="store_true", help="All platforms")
    parser.add_argument("--skills", help="Comma-separated skill names")
    parser.add_argument("--write", action="store_true", help="Write files")
    parser.add_argument("--dry-run", action="store_true", help="Preview")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--list", action="store_true", help="List available skills")
    args = parser.parse_args()

    if args.list:
        skills = list_skills()
        if args.json:
            print(json.dumps({"skills": skills, "count": len(skills)}))
        else:
            print(f"Available skills ({len(skills)}):")
            for s in skills:
                print(f"  - {s}")
        return

    platforms = ["all"] if args.all else (
        [p.strip() for p in args.platform.split(",")] if args.platform else None
    )
    skills = [s.strip() for s in args.skills.split(",")] if args.skills else None

    results = translate_skills(
        directory=args.dir, platforms=platforms, skills=skills,
        write=args.write, dry_run=args.dry_run,
    )

    if args.json:
        out = {}
        for platform, platform_results in results.items():
            if "error" in platform_results:
                out[platform] = platform_results
                continue
            p_out = {}
            for sk, info in platform_results.items():
                p_out[sk] = {k: v for k, v in info.items() if k != "content"}
            out[platform] = p_out
        print(json.dumps(out, indent=2))
    else:
        for platform, platform_results in results.items():
            if "error" in platform_results:
                print(f"ERROR [{platform}]: {platform_results['error']}")
                continue
            print(f"\n{platform}:")
            for skill_name, info in platform_results.items():
                if "error" in info:
                    print(f"  ERROR [{skill_name}]: {info['error']}")
                    continue
                status = "WRITTEN" if info["written"] else "PREVIEW"
                print(f"  [{status}] {skill_name} -> {info['path']} ({info['size_bytes']} bytes)")


if __name__ == "__main__":
    main()
