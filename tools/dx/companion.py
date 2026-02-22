#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV Universal AI Coding Companion — single CLI entry point.

Orchestrates tool detection, instruction file generation, MCP config
generation, and skill translation across all supported AI coding tools.

Usage:
    python tools/dx/companion.py --setup --write              # Auto-detect + generate
    python tools/dx/companion.py --setup --all --write        # All platforms
    python tools/dx/companion.py --setup --platforms codex,cursor --write
    python tools/dx/companion.py --detect --json              # Detect only
    python tools/dx/companion.py --sync --write               # Regenerate after changes
    python tools/dx/companion.py --list --json                # List all platforms
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.dx.tool_detector import detect_tools
from tools.dx.instruction_generator import generate_instructions, collect_project_data
from tools.dx.mcp_config_generator import generate_mcp_config
from tools.dx.skill_translator import translate_skills, list_skills

try:
    import yaml as _yaml
    def _load_yaml(path):
        with open(path, encoding="utf-8") as f:
            return _yaml.safe_load(f)
except ImportError:
    def _load_yaml(path):
        with open(path, encoding="utf-8") as f:
            return json.loads(f.read())


REGISTRY_PATH = BASE_DIR / "args" / "companion_registry.yaml"


def setup_companion(directory=None, platforms=None, write=False,
                    dry_run=False, detect=False):
    """Full companion setup: detect tools, generate all configs.

    Returns:
        dict: {detected_tools, instruction_files, mcp_configs,
               skill_translations, project_data, summary}
    """
    directory = Path(directory) if directory else Path.cwd()

    # Step 1: Detect tools
    detection = detect_tools(directory=str(directory))

    # If detect-only mode, return early
    if detect:
        return {"detected_tools": detection}

    # Resolve platforms
    if platforms is None:
        # Auto: use detected tools (excluding claude_code)
        if detection["detected"]:
            platforms = [
                t["tool_id"] for t in detection["detected"]
                if t["tool_id"] != "claude_code"
            ]
        if not platforms:
            # Default to top 4 most popular
            platforms = ["codex", "cursor", "copilot", "gemini"]
    elif platforms == ["all"]:
        pass  # Let generators handle "all"

    # Step 2: Collect project data
    project_data = collect_project_data(str(directory))

    # Step 3: Generate instruction files
    instruction_results = generate_instructions(
        directory=str(directory),
        platforms=platforms if platforms != ["all"] else ["all"],
        write=write,
        dry_run=dry_run,
    )

    # Step 4: Generate MCP configs (for platforms that support MCP)
    mcp_platforms = []
    registry = _load_yaml(str(REGISTRY_PATH)) if REGISTRY_PATH.exists() else {}
    companions = registry.get("companions", {})
    target = platforms if platforms != ["all"] else list(companions.keys())
    for p in target:
        cfg = companions.get(p, {})
        if cfg.get("mcp_support", False) and p != "claude_code":
            mcp_platforms.append(p)

    mcp_results = {}
    if mcp_platforms:
        mcp_results = generate_mcp_config(
            directory=str(directory),
            platforms=mcp_platforms,
            write=write,
            dry_run=dry_run,
        )

    # Step 5: Translate skills (for platforms that support skills)
    skill_platforms = []
    for p in target:
        cfg = companions.get(p, {})
        if cfg.get("skill_format", "none") not in ("none", "claude_skill"):
            skill_platforms.append(p)

    skill_results = {}
    if skill_platforms:
        skill_results = translate_skills(
            directory=str(directory),
            platforms=skill_platforms,
            write=write,
            dry_run=dry_run,
        )

    # Build summary
    instruction_count = sum(
        1 for v in instruction_results.values()
        if isinstance(v, dict) and v.get("written")
    )
    mcp_count = sum(
        1 for v in mcp_results.values()
        if isinstance(v, dict) and v.get("written")
    )
    skill_count = sum(
        sum(1 for sv in pv.values() if isinstance(sv, dict) and sv.get("written"))
        for pv in skill_results.values()
        if isinstance(pv, dict) and "error" not in pv
    )

    summary = {
        "platforms_targeted": len(target),
        "instruction_files_written": instruction_count,
        "mcp_configs_written": mcp_count,
        "skills_translated": skill_count,
        "project_name": project_data.get("project_name", "unknown"),
        "has_icdev_yaml": project_data.get("has_icdev_yaml", False),
    }

    return {
        "detected_tools": detection,
        "instruction_files": {
            k: {kk: vv for kk, vv in v.items() if kk != "content"}
            if isinstance(v, dict) else v
            for k, v in instruction_results.items()
        },
        "mcp_configs": {
            k: {kk: vv for kk, vv in v.items() if kk != "content"}
            if isinstance(v, dict) else v
            for k, v in mcp_results.items()
        },
        "skill_translations": {
            platform: {
                skill: {kk: vv for kk, vv in info.items() if kk != "content"}
                if isinstance(info, dict) else info
                for skill, info in pdata.items()
            } if isinstance(pdata, dict) and "error" not in pdata else pdata
            for platform, pdata in skill_results.items()
        },
        "project_data": {
            "project_name": project_data.get("project_name"),
            "impact_level": project_data.get("impact_level"),
            "classification_level": project_data.get("classification_level"),
            "has_icdev_yaml": project_data.get("has_icdev_yaml"),
        },
        "summary": summary,
    }


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Universal AI Coding Companion"
    )
    parser.add_argument("--dir", help="Project directory")
    parser.add_argument("--setup", action="store_true", help="Full setup")
    parser.add_argument("--sync", action="store_true", help="Regenerate (alias for --setup)")
    parser.add_argument("--detect", action="store_true", help="Detect tools only")
    parser.add_argument("--list", action="store_true", help="List all platforms")
    parser.add_argument("--platforms", help="Comma-separated platform IDs")
    parser.add_argument("--all", action="store_true", help="All platforms")
    parser.add_argument("--write", action="store_true", help="Write files")
    parser.add_argument("--dry-run", action="store_true", help="Preview")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if args.list:
        registry = _load_yaml(str(REGISTRY_PATH)) if REGISTRY_PATH.exists() else {}
        companions = registry.get("companions", {})
        if args.json:
            out = {}
            for k, v in companions.items():
                out[k] = {
                    "display_name": v.get("display_name"),
                    "vendor": v.get("vendor"),
                    "instruction_file": v.get("instruction_file"),
                    "mcp_support": v.get("mcp_support"),
                    "skill_format": v.get("skill_format"),
                }
            print(json.dumps({"platforms": out, "count": len(out)}, indent=2))
        else:
            print(f"Supported AI coding tools ({len(companions)}):\n")
            for k, v in companions.items():
                mcp = " [MCP]" if v.get("mcp_support") else ""
                skill = f" [{v.get('skill_format')}]" if v.get("skill_format", "none") != "none" else ""
                print(f"  {k:15s} {v.get('display_name', k):25s} -> {v.get('instruction_file', 'N/A')}{mcp}{skill}")
        return

    if args.detect:
        result = setup_companion(directory=args.dir, detect=True)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            detection = result["detected_tools"]
            if not detection["detected"]:
                print("No AI coding tools detected.")
            else:
                print(f"Detected {len(detection['detected'])} tool(s):")
                for t in detection["detected"]:
                    print(f"  {t['display_name']} (confidence: {t['confidence']})")
        return

    if args.setup or args.sync:
        platforms = ["all"] if args.all else (
            [p.strip() for p in args.platforms.split(",")] if args.platforms else None
        )

        result = setup_companion(
            directory=args.dir,
            platforms=platforms,
            write=args.write,
            dry_run=args.dry_run,
        )

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            s = result["summary"]
            print(f"ICDEV Companion Setup — {s['project_name']}")
            print(f"{'=' * 50}")
            print(f"Platforms targeted: {s['platforms_targeted']}")
            print(f"Instruction files:  {s['instruction_files_written']} written")
            print(f"MCP configs:        {s['mcp_configs_written']} written")
            print(f"Skills translated:  {s['skills_translated']} written")
            print()

            # List instruction files
            for platform, info in result["instruction_files"].items():
                if isinstance(info, dict) and "path" in info:
                    status = "WRITTEN" if info.get("written") else "PREVIEW"
                    print(f"  [{status}] {info.get('display_name', platform)}: {info['path']}")

            if not args.write and not args.dry_run:
                print("\nUse --write to save files to disk.")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
