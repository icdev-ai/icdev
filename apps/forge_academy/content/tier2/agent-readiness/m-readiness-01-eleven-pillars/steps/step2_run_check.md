---
ontology_id: icdev:mission:m-readiness-01-eleven-pillars:step:2
step_class: icdev:coding
---
# Run the Readiness Check

Write a script that runs the 11-pillar readiness check and produces a human-readable report highlighting failures.

On the platform the checker is `tools.ai_augmentation.agent_readiness.checker.run_readiness_check(repo_path)`. It returns `pillar_scores` (`{pillar_id: {"passed", "total", "percentage"}}`), `overall_readiness_score` (0.0–1.0) and `icdev_checks` (`{pillar_id: [{"criterion_id", "passed", "message", ...}]}`).

**In the exercise** the sandbox cannot import `tools.*`, so the starter provides a sandbox stub `run_readiness_check()` that returns a canned `SAMPLE_RESULT` with exactly that shape. Use it as-is.

## Your task

Complete `check_and_report(repo_path)` so that it:
1. Runs `run_readiness_check(repo_path)` on a target directory
2. Prints each pillar's score: `[PASS] code-quality: 87%` or `[FAIL] testing: 33%` (pass = 70% or more)
3. Lists the specific failing criteria (their `message`) for each failed pillar
4. Prints the overall readiness score
5. Returns exit code 1 if the overall score is < 0.7 (the deployment gate), else 0 — the `__main__` block just calls `check_and_report(".")`, it does not `sys.exit()`
