#!/usr/bin/env python3
# CUI // SP-CTI
"""Detect which AI coding tools are present in the current environment.

ADR D197: Detection is advisory — auto-detect for convenience,
explicit --platform override for certainty (D110/D185 pattern).

Checks environment variables, configuration directories, and config
files to determine which AI coding tools are active or installed.

Usage:
    python tools/dx/tool_detector.py --json
    python tools/dx/tool_detector.py --dir /path/to/project
"""

import argparse
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

try:
    import yaml as _yaml

    def _load_yaml(path):
        with open(path, encoding="utf-8") as f:
            return _yaml.safe_load(f)
except ImportError:
    _yaml = None  # type: ignore[assignment]

    def _load_yaml(path):
        """Minimal YAML subset loader for the companion registry."""
        import re
        with open(path, encoding="utf-8") as f:
            text = f.read()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}


REGISTRY_PATH = BASE_DIR / "args" / "companion_registry.yaml"


def _load_registry(registry_path=None):
    """Load the companion registry."""
    path = Path(registry_path) if registry_path else REGISTRY_PATH
    if not path.exists():
        return {}
    return _load_yaml(str(path))


def detect_tools(directory=None, registry_path=None):
    """Detect AI coding tools from env vars, config dirs, config files.

    Returns:
        dict with keys:
        - detected: list of {tool_id, display_name, confidence, evidence}
        - primary: tool_id of highest-confidence tool or None
        - all_tools: list of all registered tool_ids
    """
    directory = Path(directory) if directory else Path.cwd()
    registry = _load_registry(registry_path)
    companions = registry.get("companions", {})

    detected = []
    for tool_id, config in companions.items():
        evidence = []
        detection = config.get("env_detection", {})

        # Check env vars
        for var in detection.get("env_vars", []):
            if os.environ.get(var):
                evidence.append(f"env:{var}")

        # Check config directories
        for d in detection.get("config_dirs", []):
            if (directory / d).is_dir():
                evidence.append(f"dir:{d}")

        # Check config files
        for f in detection.get("config_files", []):
            if (directory / f).exists():
                evidence.append(f"file:{f}")

        # Check instruction file exists
        inst_file = config.get("instruction_file", "")
        if inst_file and (directory / inst_file).exists():
            evidence.append(f"instruction:{inst_file}")

        if evidence:
            confidence = min(1.0, len(evidence) * 0.4)
            detected.append({
                "tool_id": tool_id,
                "display_name": config.get("display_name", tool_id),
                "vendor": config.get("vendor", "unknown"),
                "confidence": round(confidence, 2),
                "evidence": evidence,
                "mcp_support": config.get("mcp_support", False),
                "skill_format": config.get("skill_format", "none"),
            })

    # Sort by confidence descending
    detected.sort(key=lambda x: x["confidence"], reverse=True)

    return {
        "detected": detected,
        "primary": detected[0]["tool_id"] if detected else None,
        "all_tools": list(companions.keys()),
        "directory": str(directory),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Detect AI coding tools in the current environment"
    )
    parser.add_argument("--dir", help="Directory to scan")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--registry", help="Path to companion registry YAML")
    args = parser.parse_args()

    result = detect_tools(directory=args.dir, registry_path=args.registry)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if not result["detected"]:
            print("No AI coding tools detected.")
            print(f"Registered tools: {', '.join(result['all_tools'])}")
        else:
            print(f"Detected {len(result['detected'])} AI coding tool(s):")
            for t in result["detected"]:
                mcp = " [MCP]" if t["mcp_support"] else ""
                print(f"  {t['display_name']}{mcp} (confidence: {t['confidence']})")
                for e in t["evidence"]:
                    print(f"    - {e}")
            print(f"\nPrimary: {result['primary']}")


if __name__ == "__main__":
    main()
