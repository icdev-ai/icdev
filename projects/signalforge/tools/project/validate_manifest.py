#!/usr/bin/env python3
# CUI // SP-CTI
"""Validate an signalforge.yaml manifest file.

Thin CLI wrapper around manifest_loader.validate_manifest().
Referenced in docs/dx/signalforge-yaml-spec.md.

Usage:
    python tools/project/validate_manifest.py --file signalforge.yaml
    python tools/project/validate_manifest.py --file signalforge.yaml --json
    python tools/project/validate_manifest.py --dir /path/to/project --json
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))
from tools.project.manifest_loader import load_manifest


def main():
    parser = argparse.ArgumentParser(
        description="Validate an signalforge.yaml manifest file"
    )
    parser.add_argument("--file", help="Path to signalforge.yaml")
    parser.add_argument("--dir", help="Directory containing signalforge.yaml")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result = load_manifest(directory=args.dir, file_path=args.file)

    if args.json:
        print(json.dumps({
            "valid": result["valid"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "file_path": result["file_path"],
        }, indent=2))
    else:
        for err in result["errors"]:
            print(f"ERROR: {err}")
        for warn in result["warnings"]:
            print(f"WARNING: {warn}")
        if result["valid"]:
            print("Manifest is valid.")

    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
