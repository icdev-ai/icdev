import sys
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parents[6]))

from tools.ai_augmentation.agent_readiness.checker import run_readiness_check


def check_and_report(repo_path: str = ".") -> int:
    """Run readiness check and print report. Returns exit code."""
    result = run_readiness_check(repo_path)

    pillar_scores = result.get("pillar_scores", {})
    icdev_checks = result.get("icdev_checks", {})
    overall = result.get("overall_readiness_score", 0.0)

    print(f"\n{'=' * 60}")
    print(f"AGENT READINESS REPORT — {repo_path}")
    print(f"{'=' * 60}\n")

    for pillar_id, score in pillar_scores.items():
        pct = score.get("percentage", 0)
        status = "PASS" if pct >= 70 else "FAIL"
        # TODO: print formatted line like "[PASS] code-quality: 87%"
        # TODO: for failed pillars, print their failing criteria from icdev_checks

    print(f"\nOVERALL SCORE: {overall:.1%}")

    # TODO: return 1 if overall < 0.7
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    sys.exit(check_and_report(target))
