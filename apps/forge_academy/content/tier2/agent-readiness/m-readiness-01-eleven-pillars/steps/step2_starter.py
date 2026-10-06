"""Agent readiness report — the 11-pillar check, rendered for humans.

The Academy sandbox cannot import `tools.*`, so `run_readiness_check()` below is a
sandbox stub that returns a canned result with the same shape as the platform's
`tools.ai_augmentation.agent_readiness.checker.run_readiness_check`. Use it as-is.
Like the real checker, each pillar's "percentage" is a fraction from 0.0 to 1.0.
"""

# ── Provided: sandbox stub of the readiness checker ───────────────────────────

SAMPLE_RESULT = {
    "pillar_scores": {
        "code-quality":    {"passed": 7, "total": 8, "percentage": 0.875},
        "testing":         {"passed": 2, "total": 6, "percentage": 0.3333},
        "stig-compliance": {"passed": 1, "total": 8, "percentage": 0.125},
    },
    "overall_readiness_score": 0.4443,
    "icdev_checks": {
        "code-quality": [
            {"criterion_id": "cq-lint", "passed": True, "message": "ruff passes", "details": {}, "skipped": False},
        ],
        "testing": [
            {"criterion_id": "t-ci-gate", "passed": False, "message": "No CI test gate configured", "details": {}, "skipped": False},
            {"criterion_id": "t-coverage", "passed": False, "message": "Coverage below 80%", "details": {}, "skipped": False},
        ],
        "stig-compliance": [
            {"criterion_id": "stig-vid", "passed": False, "message": "No STIG V-ID markers found", "details": {}, "skipped": False},
        ],
    },
}


def run_readiness_check(repo_path: str = ".") -> dict:
    """Sandbox stub: returns SAMPLE_RESULT for any path."""
    return SAMPLE_RESULT


# ── Your code ─────────────────────────────────────────────────────────────────

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
        pct = score.get("percentage", 0.0)   # a fraction: 0.875 means 87.5%
        status = "PASS" if pct >= 0.7 else "FAIL"
        # TODO: print formatted line like "[PASS] code-quality: 88%"  (hint: f"{pct:.0%}")
        # TODO: for failed pillars, print the "message" of each criterion in
        #       icdev_checks[pillar_id] whose "passed" is False

    print(f"\nOVERALL SCORE: {overall:.1%}")

    # TODO: return 1 if overall < 0.7 (the deployment gate), else 0
    return 0


if __name__ == "__main__":
    # Return the exit code rather than calling sys.exit() so the grader still runs.
    check_and_report(".")
