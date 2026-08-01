#!/usr/bin/env python3
"""Run delta detection then regenerate the capability sheet Excel."""
import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="ICDEV Capability Sheet — full update pipeline")
    parser.add_argument("--delta-only",    action="store_true", help="Only run delta, skip Excel generation")
    parser.add_argument("--generate-only", action="store_true", help="Only regenerate Excel from existing YAML")
    parser.add_argument("--dry-run",       action="store_true")
    parser.add_argument("--yaml",          default=None)
    parser.add_argument("--output",        default=None)
    args = parser.parse_args()

    root   = Path(__file__).resolve().parents[3]
    python = sys.executable

    if not args.generate_only:
        cmd = [python, str(root / "icdev" / "tools" / "showcase" / "capability_sheet_delta.py")]
        if args.dry_run:
            cmd.append("--dry-run")
        if args.yaml:
            cmd += ["--yaml", args.yaml]
        result = subprocess.run(cmd, cwd=root)
        if result.returncode != 0:
            print("[runner] delta step failed")
            sys.exit(1)

    if not args.delta_only:
        cmd = [python, str(root / "icdev" / "tools" / "showcase" / "capability_sheet_generator.py")]
        if args.yaml:
            cmd += ["--yaml", args.yaml]
        if args.output:
            cmd += ["--output", args.output]
        result = subprocess.run(cmd, cwd=root)
        if result.returncode != 0:
            print("[runner] generate step failed")
            sys.exit(1)


if __name__ == "__main__":
    main()
