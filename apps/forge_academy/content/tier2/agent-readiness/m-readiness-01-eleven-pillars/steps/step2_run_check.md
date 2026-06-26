---
ontology_id: icdev:mission:m-readiness-01-eleven-pillars:step:2
step_class: icdev:coding
---
# Run the Readiness Check

Write a script that runs the 11-pillar readiness check and produces a human-readable report highlighting failures.

## Your task

Write a Python script that:
1. Runs `run_readiness_check(repo_path)` on a target directory
2. Prints each pillar's score: `[PASS] Pillar Name: 87%` or `[FAIL] Pillar Name: 34%`
3. Lists the specific failing criteria for each failed pillar
4. Prints the overall readiness score
5. Exits with code 1 if overall score < 0.7 (the deployment gate)
