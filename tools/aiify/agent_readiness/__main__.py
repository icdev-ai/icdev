# CUI // SP-CTI
"""CLI entry point: python -m tools.ai_augmentation.agent_readiness <repo_path> [--json]"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from tools.ai_augmentation.agent_readiness.checker import run_readiness_check


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Readiness Checker (ICDEV extended)")
    parser.add_argument("repo_path", nargs="?", default=".", help="Path to repo root (default: cwd)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    repo = pathlib.Path(args.repo_path).resolve()
    if not repo.exists():
        print(f"ERROR: repo_path does not exist: {repo}", file=sys.stderr)
        sys.exit(1)

    result = run_readiness_check(repo)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    score_pct = result["overall_readiness_score"] * 100
    print(f"\nAgent Readiness Score: {score_pct:.1f}%\n")
    print(f"{'Pillar':<32} {'Passed':>7} {'Total':>6} {'Score':>7}")
    print("-" * 56)
    for pid, sc in result["pillar_scores"].items():
        pct = sc["percentage"] * 100
        label = "(ICDEV)" if pid in {"il-classification", "nist-controls", "stig-compliance", "append-only-audit"} else ""
        print(f"  {pid:<30} {sc['passed']:>7} {sc['total']:>6} {pct:>6.0f}%  {label}")
    print()

    # Show failures
    failures = []
    for pid, checks in result["icdev_checks"].items():
        for c in checks:
            if not c["passed"] and not c["skipped"]:
                failures.append((pid, c["criterion_id"], c["message"]))
    if failures:
        print("Failures:")
        for pid, cid, msg in failures:
            print(f"  [{pid}] {cid}: {msg}")


if __name__ == "__main__":
    main()
