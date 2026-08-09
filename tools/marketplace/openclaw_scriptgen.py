#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""OpenClaw Skill Script Generator — convert actionable steps to Python companion scripts.

Parses SKILL.md for actionable steps (file operations, shell commands, API calls,
data transforms) and generates a deterministic Python companion script. The LLM
orchestrates via SKILL.md instructions; the script handles execution.

This makes imported skills LLM-agnostic — any LLM can read the SKILL.md and
call the companion script for deterministic operations.

Usage:
    python tools/marketplace/openclaw_scriptgen.py --generate /path/to/skill --json
    python tools/marketplace/openclaw_scriptgen.py --analyze /path/to/skill --json
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Step detection patterns — what's actionable vs reasoning
# ---------------------------------------------------------------------------

# Patterns indicating actionable steps (can be automated in Python)
ACTIONABLE_PATTERNS = {
    "file_read": {
        "patterns": [
            re.compile(r"\bread\s+(?:the\s+)?(?:file|content|source)", re.IGNORECASE),
            re.compile(r"\bload\s+(?:the\s+)?(?:file|config|data|json|yaml)", re.IGNORECASE),
            re.compile(r"\bopen\s+(?:the\s+)?(?:file|document)", re.IGNORECASE),
            re.compile(r"\bcat\s+\S+", re.IGNORECASE),
        ],
        "python_func": "read_file",
        "description": "Read file contents",
    },
    "file_write": {
        "patterns": [
            re.compile(r"\bwrite\s+(?:the\s+)?(?:file|output|result|content)", re.IGNORECASE),
            re.compile(r"\bsave\s+(?:the\s+)?(?:file|output|result)", re.IGNORECASE),
            re.compile(r"\bcreate\s+(?:a\s+)?(?:file|directory|folder)", re.IGNORECASE),
            re.compile(r"\bappend\s+(?:to\s+)?(?:file|log)", re.IGNORECASE),
        ],
        "python_func": "write_file",
        "description": "Write/create files",
    },
    "file_search": {
        "patterns": [
            re.compile(r"\bsearch\s+(?:for|through)\s+(?:files?|patterns?|content)", re.IGNORECASE),
            re.compile(r"\bfind\s+(?:all\s+)?(?:files?|matches|occurrences)", re.IGNORECASE),
            re.compile(r"\bgrep\s+", re.IGNORECASE),
            re.compile(r"\bglob\s+", re.IGNORECASE),
        ],
        "python_func": "search_files",
        "description": "Search/find files or content",
    },
    "shell_command": {
        "patterns": [
            re.compile(r"\brun\s+(?:the\s+)?(?:command|script|test|build)", re.IGNORECASE),
            re.compile(r"\bexecute\s+(?:the\s+)?(?:command|script)", re.IGNORECASE),
            re.compile(r"```(?:bash|shell|sh)\n(.*?)```", re.DOTALL),
            re.compile(r"\$\s+\w+"),
        ],
        "python_func": "run_command",
        "description": "Execute shell commands",
    },
    "data_transform": {
        "patterns": [
            re.compile(r"\bparse\s+(?:the\s+)?(?:json|yaml|xml|csv|data)", re.IGNORECASE),
            re.compile(r"\bconvert\s+(?:the\s+)?(?:data|format|output)", re.IGNORECASE),
            re.compile(r"\btransform\s+(?:the\s+)?(?:data|input|output)", re.IGNORECASE),
            re.compile(r"\bextract\s+(?:the\s+)?(?:data|fields?|values?)", re.IGNORECASE),
        ],
        "python_func": "transform_data",
        "description": "Parse/transform data",
    },
    "api_call": {
        "patterns": [
            re.compile(r"\bcurl\s+", re.IGNORECASE),
            re.compile(r"\bfetch\s+(?:the\s+)?(?:url|api|endpoint|data\s+from)", re.IGNORECASE),
            re.compile(r"\bhttp\s+(?:get|post|put|delete)\b", re.IGNORECASE),
            re.compile(r"\bcall\s+(?:the\s+)?(?:api|endpoint|service)", re.IGNORECASE),
        ],
        "python_func": "api_request",
        "description": "HTTP API calls",
    },
    "directory_ops": {
        "patterns": [
            re.compile(r"\blist\s+(?:the\s+)?(?:files?|directory|contents?|structure)", re.IGNORECASE),
            re.compile(r"\btree\s+(?:the\s+)?(?:directory|project)", re.IGNORECASE),
            re.compile(r"\bmkdir\s+", re.IGNORECASE),
            re.compile(r"\bcopy\s+(?:the\s+)?(?:file|directory|folder)", re.IGNORECASE),
        ],
        "python_func": "directory_ops",
        "description": "Directory operations",
    },
}

# Patterns indicating reasoning steps (keep as LLM instructions, not scriptable)
REASONING_PATTERNS = [
    re.compile(r"\banalyze\s+(?:the\s+)?(?:code|logic|architecture|design)", re.IGNORECASE),
    re.compile(r"\breview\s+(?:the\s+)?(?:code|implementation|approach)", re.IGNORECASE),
    re.compile(r"\bevaluate\s+(?:the\s+)?(?:quality|performance|security)", re.IGNORECASE),
    re.compile(r"\bsuggest\s+(?:improvements?|changes?|alternatives?)", re.IGNORECASE),
    re.compile(r"\bexplain\s+(?:the\s+)?(?:code|logic|reason|approach)", re.IGNORECASE),
    re.compile(r"\bdecide\s+(?:which|whether|how|what)", re.IGNORECASE),
    re.compile(r"\bthink\s+about\b", re.IGNORECASE),
    re.compile(r"\bconsider\s+(?:the\s+)?(?:options?|alternatives?|trade-?offs?)", re.IGNORECASE),
    re.compile(r"\bprioritize\b", re.IGNORECASE),
    re.compile(r"\bdesign\s+(?:the\s+)?(?:solution|architecture|approach)", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Step analyzer
# ---------------------------------------------------------------------------
def analyze_skill_steps(skill_path):
    """Analyze a SKILL.md and classify each step as actionable or reasoning.

    Args:
        skill_path: Path to skill directory with SKILL.md.

    Returns:
        dict with classified steps, actionable count, reasoning count.
    """
    skill_path = Path(skill_path)
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        skill_md = skill_path / "skill.md"
    if not skill_md.exists():
        return {"error": "No SKILL.md found", "steps": []}

    content = skill_md.read_text(encoding="utf-8")

    # Extract steps from markdown (### numbered headers or numbered lists)
    steps = []
    step_pattern = re.compile(r"^###\s+(\d+)\.\s+(.+)$", re.MULTILINE)
    list_pattern = re.compile(r"^\d+\.\s+(.+)$", re.MULTILINE)

    # Try ### headers first
    matches = step_pattern.findall(content)
    if matches:
        for num, title in matches:
            # Get body between this header and next
            start = content.find(f"### {num}. {title}")
            next_match = re.search(r"^###\s+\d+\.", content[start + len(f"### {num}. {title}") :], re.MULTILINE)
            end = start + len(f"### {num}. {title}") + next_match.start() if next_match else len(content)
            body = content[start:end]
            steps.append({"number": int(num), "title": title, "body": body})
    else:
        # Fall back to numbered list items
        for match in list_pattern.finditer(content):
            steps.append({"number": len(steps) + 1, "title": match.group(1)[:100], "body": match.group(1)})

    # Also scan the full body for code blocks (actionable by nature)
    code_blocks = re.findall(r"```(?:bash|shell|sh|python)\n(.*?)```", content, re.DOTALL)

    # Classify each step
    classified = []
    for step in steps:
        text = step.get("body", "") + " " + step.get("title", "")
        step_type = "reasoning"
        action_types = []

        # Check if it's reasoning
        is_reasoning = any(p.search(text) for p in REASONING_PATTERNS)

        # Check if it's actionable
        for action_id, action_def in ACTIONABLE_PATTERNS.items():
            if any(p.search(text) for p in action_def["patterns"]):
                action_types.append(action_id)

        if action_types and not is_reasoning:
            step_type = "actionable"
        elif action_types and is_reasoning:
            step_type = "mixed"  # Has both — script the action part, LLM does reasoning

        classified.append(
            {
                "number": step.get("number"),
                "title": step.get("title", "")[:100],
                "type": step_type,
                "action_types": action_types,
                "preview": text[:150],
            }
        )

    actionable_count = sum(1 for s in classified if s["type"] in ("actionable", "mixed"))
    reasoning_count = sum(1 for s in classified if s["type"] == "reasoning")

    return {
        "success": True,
        "total_steps": len(classified),
        "actionable_steps": actionable_count,
        "reasoning_steps": reasoning_count,
        "mixed_steps": sum(1 for s in classified if s["type"] == "mixed"),
        "code_blocks": len(code_blocks),
        "steps": classified,
        "has_actionable": actionable_count > 0,
    }


# ---------------------------------------------------------------------------
# Script generator
# ---------------------------------------------------------------------------
def generate_companion_script(skill_path):
    """Generate a Python companion script for actionable steps in a skill.

    The generated script:
    - Contains functions for each actionable step type (read_file, write_file, etc.)
    - Includes a CLI interface for the LLM to call
    - Is deterministic and LLM-agnostic
    - Follows ICDEV™ patterns (CUI banner, pathlib, encoding='utf-8')

    Args:
        skill_path: Path to skill directory.

    Returns:
        dict with generated script path, step analysis, and script content.
    """
    skill_path = Path(skill_path)
    analysis = analyze_skill_steps(skill_path)

    if not analysis.get("has_actionable"):
        return {
            "success": True,
            "generated": False,
            "reason": "No actionable steps found — skill is reasoning-only",
            "analysis": analysis,
        }

    # Collect unique action types needed
    action_types = set()
    for step in analysis.get("steps", []):
        if step["type"] in ("actionable", "mixed"):
            action_types.update(step.get("action_types", []))

    # Parse skill metadata for name
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        skill_md = skill_path / "skill.md"

    content = skill_md.read_text(encoding="utf-8")
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    fm = yaml.safe_load(fm_match.group(1)) if fm_match else {}
    skill_name = fm.get("name", skill_path.name)

    # Generate the companion script
    script_lines = [
        "#!/usr/bin/env python3",
        "# CUI // SP-CTI",
        f'"""Companion script for skill: {skill_name}',
        "",
        "Auto-generated by ICDEV™ OpenClaw Script Generator.",
        "The LLM orchestrates via SKILL.md; this script handles deterministic execution.",
        "",
        "Usage:",
        "    python scripts/companion.py --help",
        '"""',
        "",
        "import argparse",
        "import json",
        "import sys",
        "from pathlib import Path",
        "",
    ]

    # Add function implementations for each action type
    func_map = {}

    if "file_read" in action_types:
        func_map["read_file"] = True
        script_lines.extend(
            [
                "",
                "def read_file(path):",
                '    """Read and return file contents."""',
                "    p = Path(path)",
                "    if not p.exists():",
                '        return {"error": f"File not found: {path}"}',
                "    content = p.read_text(encoding='utf-8', errors='replace')",
                '    return {"success": True, "path": str(p), "lines": len(content.splitlines()), "content": content}',
                "",
            ]
        )

    if "file_write" in action_types:
        func_map["write_file"] = True
        script_lines.extend(
            [
                "",
                "def write_file(path, content, append=False):",
                '    """Write content to a file."""',
                "    p = Path(path)",
                "    p.parent.mkdir(parents=True, exist_ok=True)",
                "    mode = 'a' if append else 'w'",
                "    p.open(mode, encoding='utf-8').write(content)",
                '    return {"success": True, "path": str(p), "bytes": len(content.encode("utf-8")), "mode": mode}',
                "",
            ]
        )

    if "file_search" in action_types:
        func_map["search_files"] = True
        script_lines.extend(
            [
                "",
                "def search_files(directory, pattern, content_pattern=None):",
                '    """Search for files by glob pattern, optionally grep content."""',
                "    d = Path(directory)",
                "    matches = []",
                "    for f in sorted(d.rglob(pattern)):",
                "        if f.is_file():",
                "            entry = {'path': str(f), 'name': f.name}",
                "            if content_pattern:",
                "                import re",
                "                text = f.read_text(encoding='utf-8', errors='replace')",
                "                hits = re.findall(content_pattern, text)",
                "                if hits:",
                "                    entry['matches'] = len(hits)",
                "                    matches.append(entry)",
                "            else:",
                "                matches.append(entry)",
                '    return {"success": True, "count": len(matches), "matches": matches}',
                "",
            ]
        )

    if "shell_command" in action_types:
        func_map["run_command"] = True
        script_lines.extend(
            [
                "",
                "def run_command(cmd, cwd=None, timeout=60):",
                '    """Run a shell command and return output."""',
                "    import subprocess",
                "    try:",
                "        result = subprocess.run(",
                "            cmd, shell=True, capture_output=True, text=True,",
                "            timeout=timeout, cwd=cwd,",
                "        )",
                "        return {",
                '            "success": result.returncode == 0,',
                '            "returncode": result.returncode,',
                '            "stdout": result.stdout[:5000],',
                '            "stderr": result.stderr[:2000],',
                "        }",
                "    except subprocess.TimeoutExpired:",
                '        return {"success": False, "error": f"Timeout after {timeout}s"}',
                "    except Exception as exc:",
                '        return {"success": False, "error": str(exc)}',
                "",
            ]
        )

    if "data_transform" in action_types:
        func_map["transform_data"] = True
        script_lines.extend(
            [
                "",
                "def transform_data(input_path, output_format='json'):",
                '    """Read data file and convert to specified format."""',
                "    import csv",
                "    p = Path(input_path)",
                "    text = p.read_text(encoding='utf-8')",
                "    if p.suffix == '.json':",
                "        data = json.loads(text)",
                "    elif p.suffix in ('.yaml', '.yml'):",
                "        import yaml",
                "        data = yaml.safe_load(text)",
                "    elif p.suffix == '.csv':",
                "        reader = csv.DictReader(text.splitlines())",
                "        data = list(reader)",
                "    else:",
                "        data = text",
                '    return {"success": True, "format": output_format, "data": data}',
                "",
            ]
        )

    if "api_call" in action_types:
        func_map["api_request"] = True
        script_lines.extend(
            [
                "",
                "def api_request(url, method='GET', headers=None, data=None, timeout=30):",
                '    """Make an HTTP request."""',
                "    from urllib.request import Request, urlopen",
                "    from urllib.error import HTTPError, URLError",
                "    req = Request(url, method=method)",
                "    if headers:",
                "        for k, v in headers.items():",
                "            req.add_header(k, v)",
                "    if data:",
                "        req.data = json.dumps(data).encode('utf-8')",
                "        req.add_header('Content-Type', 'application/json')",
                "    try:",
                "        with urlopen(req, timeout=timeout) as resp:",
                "            body = resp.read().decode('utf-8')",
                "        return {'success': True, 'status': 200, 'body': body[:5000]}",
                "    except HTTPError as exc:",
                "        return {'success': False, 'status': exc.code, 'error': exc.reason}",
                "    except URLError as exc:",
                "        return {'success': False, 'error': str(exc.reason)}",
                "",
            ]
        )

    if "directory_ops" in action_types:
        func_map["directory_ops"] = True
        script_lines.extend(
            [
                "",
                "def list_directory(path, pattern='*'):",
                '    """List directory contents."""',
                "    d = Path(path)",
                "    if not d.is_dir():",
                '        return {"error": f"Not a directory: {path}"}',
                "    items = []",
                "    for f in sorted(d.glob(pattern)):",
                "        items.append({'name': f.name, 'type': 'dir' if f.is_dir() else 'file', 'size': f.stat().st_size if f.is_file() else 0})",
                '    return {"success": True, "path": str(d), "count": len(items), "items": items}',
                "",
            ]
        )

    # CLI dispatcher
    script_lines.extend(
        [
            "",
            "def main():",
            f'    parser = argparse.ArgumentParser(description="Companion script for {skill_name}")',
            "    sub = parser.add_subparsers(dest='action', required=True)",
            "",
        ]
    )

    if "read_file" in func_map:
        script_lines.extend(
            [
                "    p = sub.add_parser('read', help='Read a file')",
                "    p.add_argument('path', help='File path')",
                "",
            ]
        )

    if "write_file" in func_map:
        script_lines.extend(
            [
                "    p = sub.add_parser('write', help='Write to a file')",
                "    p.add_argument('path', help='File path')",
                "    p.add_argument('--content', required=True, help='Content to write')",
                "    p.add_argument('--append', action='store_true', help='Append mode')",
                "",
            ]
        )

    if "search_files" in func_map:
        script_lines.extend(
            [
                "    p = sub.add_parser('search', help='Search files')",
                "    p.add_argument('directory', help='Directory to search')",
                "    p.add_argument('--pattern', default='*', help='Glob pattern')",
                "    p.add_argument('--grep', help='Content pattern to match')",
                "",
            ]
        )

    if "run_command" in func_map:
        script_lines.extend(
            [
                "    p = sub.add_parser('run', help='Run a shell command')",
                "    p.add_argument('cmd', help='Command to run')",
                "    p.add_argument('--cwd', help='Working directory')",
                "    p.add_argument('--timeout', type=int, default=60, help='Timeout in seconds')",
                "",
            ]
        )

    if "transform_data" in func_map:
        script_lines.extend(
            [
                "    p = sub.add_parser('transform', help='Transform data file')",
                "    p.add_argument('input', help='Input file path')",
                "    p.add_argument('--format', default='json', help='Output format')",
                "",
            ]
        )

    if "api_request" in func_map:
        script_lines.extend(
            [
                "    p = sub.add_parser('api', help='Make API request')",
                "    p.add_argument('url', help='URL to request')",
                "    p.add_argument('--method', default='GET', help='HTTP method')",
                "",
            ]
        )

    if "directory_ops" in func_map:
        script_lines.extend(
            [
                "    p = sub.add_parser('ls', help='List directory')",
                "    p.add_argument('path', help='Directory path')",
                "    p.add_argument('--pattern', default='*', help='Glob pattern')",
                "",
            ]
        )

    # Dispatch
    script_lines.extend(
        [
            "    args = parser.parse_args()",
            "    result = {}",
            "",
        ]
    )

    dispatch_lines = []
    if "read_file" in func_map:
        dispatch_lines.append("    if args.action == 'read': result = read_file(args.path)")
    if "write_file" in func_map:
        dispatch_lines.append(
            "    elif args.action == 'write': result = write_file(args.path, args.content, args.append)"
        )
    if "search_files" in func_map:
        dispatch_lines.append(
            "    elif args.action == 'search': result = search_files(args.directory, args.pattern, args.grep)"
        )
    if "run_command" in func_map:
        dispatch_lines.append("    elif args.action == 'run': result = run_command(args.cmd, args.cwd, args.timeout)")
    if "transform_data" in func_map:
        dispatch_lines.append("    elif args.action == 'transform': result = transform_data(args.input, args.format)")
    if "api_request" in func_map:
        dispatch_lines.append("    elif args.action == 'api': result = api_request(args.url, args.method)")
    if "directory_ops" in func_map:
        dispatch_lines.append("    elif args.action == 'ls': result = list_directory(args.path, args.pattern)")

    # Fix first elif to if
    if dispatch_lines:
        dispatch_lines[0] = dispatch_lines[0].replace("    elif ", "    if ", 1)

    script_lines.extend(dispatch_lines)
    script_lines.extend(
        [
            "",
            "    print(json.dumps(result, indent=2, default=str))",
            "",
            "",
            'if __name__ == "__main__":',
            "    main()",
            "",
        ]
    )

    # Write the companion script
    scripts_dir = skill_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    script_path = scripts_dir / "companion.py"
    script_content = "\n".join(script_lines)
    script_path.write_text(script_content, encoding="utf-8", newline="")

    # Update SKILL.md with companion script reference
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        skill_md = skill_path / "skill.md"

    if skill_md.exists():
        md_content = skill_md.read_text(encoding="utf-8")
        if "## Companion Script" not in md_content:
            companion_section = "\n\n## Companion Script (LLM-Agnostic)\n\n"
            companion_section += (
                "A Python companion script is available for deterministic execution of actionable steps.\n"
            )
            companion_section += "The LLM orchestrates; the script executes.\n\n"
            companion_section += "```bash\n"
            companion_section += "python scripts/companion.py --help\n"
            for func_name in sorted(func_map.keys()):
                companion_section += f"python scripts/companion.py {func_name.replace('_file', '').replace('_files', '').replace('_command', '').replace('_data', '').replace('_request', '').replace('_ops', '')} --help\n"
            companion_section += "```\n"
            companion_section += f"\n**Functions:** {', '.join(sorted(func_map.keys()))}\n"
            companion_section += f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"

            # Insert before Compatibility Notes or Enrichment
            for marker in ("## Compatibility Notes", "## Enrichment"):
                if marker in md_content:
                    md_content = md_content.replace(marker, companion_section + marker)
                    break
            else:
                md_content += companion_section

            skill_md.write_text(md_content, encoding="utf-8", newline="")

    return {
        "success": True,
        "generated": True,
        "script_path": str(script_path),
        "functions": sorted(func_map.keys()),
        "function_count": len(func_map),
        "analysis": analysis,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="OpenClaw Skill Script Generator")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--generate", metavar="PATH", help="Generate companion script for a skill")
    group.add_argument("--analyze", metavar="PATH", help="Analyze skill steps without generating")

    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    try:
        if args.generate:
            result = generate_companion_script(args.generate)
        elif args.analyze:
            result = analyze_skill_steps(args.analyze)
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if result.get("error"):
                print(f"ERROR: {result['error']}")
                sys.exit(1)
            for k, v in result.items():
                if k not in ("analysis", "steps"):
                    print(f"  {k}: {v}")
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
