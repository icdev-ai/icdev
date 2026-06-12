#!/usr/bin/env python3
# CUI // SP-CTI
"""OPT-41 — Skill registry parser.

Parses every `.agents/skills/icdev-*/SKILL.md` file and extracts its
frontmatter (name, description, allowed-tools), the bash command lines
it documents, and any MCP tool references. Produces a cached JSON
registry at `tools/skills/registry.json` for fast lookup by
`tools/skills/invoke.py`.

Usage:
    python tools/skills/registry.py --rebuild --json
    python tools/skills/registry.py --list --json
    python tools/skills/registry.py --get icdev-init --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

BASE_DIR = Path(__file__).resolve().parents[2]
SKILLS_DIR = BASE_DIR / ".agents" / "skills"
REGISTRY_PATH = BASE_DIR / "tools" / "skills" / "registry.json"


_FENCE_RE = re.compile(r"```(?:bash|shell|sh)?\n(.*?)```", re.DOTALL)
_PYTHON_CMD_RE = re.compile(r"^\s*(?:!)?\s*(python(?:\s+-m)?\s+\S+[^\n]*)", re.MULTILINE)
_MCP_REF_RE = re.compile(r"(mcp__[a-z0-9_]+__[a-z0-9_]+|MCP tool[^\n]*)", re.IGNORECASE)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body) from a SKILL.md file."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    fm: dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        fm[key.strip()] = val.strip().strip('"').strip("'")
    return fm, body


def _extract_commands(body: str) -> list[str]:
    """Return deduplicated python command invocations in fenced bash blocks."""
    cmds: list[str] = []
    seen: set[str] = set()
    for fence in _FENCE_RE.findall(body):
        for m in _PYTHON_CMD_RE.finditer(fence):
            cmd = m.group(1).strip()
            # Drop inline comments
            if " #" in cmd:
                cmd = cmd.split(" #", 1)[0].rstrip()
            if cmd not in seen:
                seen.add(cmd)
                cmds.append(cmd)
    return cmds


def _extract_mcp_refs(body: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for m in _MCP_REF_RE.finditer(body):
        ref = m.group(1).strip()
        if ref not in seen:
            seen.add(ref)
            refs.append(ref[:200])
    return refs


def parse_skill(skill_dir: Path) -> dict[str, Any]:
    """Parse a single .agents/skills/<name>/SKILL.md file."""
    skill_name = skill_dir.name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return {"name": skill_name, "error": "SKILL.md missing"}
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    fm, body = _parse_frontmatter(text)
    return {
        "name": fm.get("name") or skill_name,
        "path": str(skill_md.relative_to(BASE_DIR)),
        "description": fm.get("description", ""),
        "allowed_tools": [t.strip() for t in fm.get("allowed-tools", "").split(",") if t.strip()],
        "commands": _extract_commands(body),
        "mcp_references": _extract_mcp_refs(body),
        "body_line_count": len([ln for ln in body.splitlines() if ln.strip()]),
    }


def build_registry() -> dict[str, Any]:
    """Parse every icdev-* skill and return a registry dict."""
    if not SKILLS_DIR.exists():
        return {"skills": {}, "count": 0, "error": f"{SKILLS_DIR} missing"}
    skills: dict[str, dict] = {}
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir() or not d.name.startswith("icdev-"):
            continue
        entry = parse_skill(d)
        skills[entry["name"]] = entry
    return {"skills": skills, "count": len(skills), "generated_from": str(SKILLS_DIR.relative_to(BASE_DIR))}


def load_registry(rebuild: bool = False) -> dict[str, Any]:
    if rebuild or not REGISTRY_PATH.exists():
        reg = build_registry()
        save_registry(reg)
        return reg
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        reg = build_registry()
        save_registry(reg)
        return reg


def save_registry(reg: dict[str, Any]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rebuild", action="store_true", help="Force rebuild of registry.json")
    p.add_argument("--list", action="store_true", help="List all skills")
    p.add_argument("--get", metavar="SKILL", help="Return a single skill entry")
    p.add_argument("--json", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    reg = load_registry(rebuild=args.rebuild)

    if args.get:
        entry = reg.get("skills", {}).get(args.get)
        if entry is None:
            out = {"error": "skill not found", "name": args.get,
                   "available": sorted(reg.get("skills", {}).keys())}
            print(json.dumps(out, indent=2) if args.json else f"ERROR: {out}")
            return 1
        print(json.dumps(entry, indent=2) if args.json else
              f"{entry['name']}: {entry['description'][:100]}\n  commands: {len(entry['commands'])}")
        return 0

    if args.list:
        names = sorted(reg.get("skills", {}).keys())
        if args.json:
            print(json.dumps({"skills": names, "count": len(names)}, indent=2))
        else:
            for n in names:
                e = reg["skills"][n]
                print(f"  {n:22s} cmds={len(e['commands']):2d}  {e['description'][:80]}")
        return 0

    # Default: print registry summary
    if args.json:
        print(json.dumps({"count": reg.get("count", 0),
                         "skill_names": sorted(reg.get("skills", {}).keys())},
                        indent=2))
    else:
        print(f"Skills indexed: {reg.get('count', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
