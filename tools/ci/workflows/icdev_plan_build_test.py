# CUI // SP-CTI
# ICDEV Plan+Build+Test — Combined workflow

"""
Usage: python tools/ci/workflows/icdev_plan_build_test.py <issue-number> [run-id]
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.workflow_ops import ensure_run_id


def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/ci/workflows/icdev_plan_build_test.py <issue-number> [run-id]")
        sys.exit(1)

    issue_number = sys.argv[1]
    run_id = ensure_run_id(issue_number, sys.argv[2] if len(sys.argv) > 2 else None)
    script_dir = Path(__file__).parent

    phases = [
        ("Plan", "icdev_plan.py", []),
        ("Build", "icdev_build.py", []),
        ("Test", "icdev_test.py", ["--skip-e2e"]),
    ]

    for phase, script, extra in phases:
        print(f"\n=== {phase.upper()} PHASE ===")
        cmd = [sys.executable, str(script_dir / script), issue_number, run_id] + extra
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
        if result.returncode != 0:
            print(f"{phase} phase failed")
            sys.exit(1)

    print(f"\nPlan+Build+Test completed for issue #{issue_number} (run_id: {run_id})")


if __name__ == "__main__":
    main()
