# Quality Gates

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Quality Gates
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Rigor Gates | tools/quality/rigor_gates.py | IL-tier quality rigor gates: coverage and static-analysis thresholds; auto-discovers coverage.xml; graceful degrade if no coverage data available | --tier (il2/il4/il5/il6), --project-dir, --coverage, --json, --gate | Gate pass/fail with violations list |
| Review Loop | tools/quality/review_loop.py | Local "review-until-green" loop (greploop-adapted): runs ICDEV's own gates (ruff lint, coherence_checker, SIPA integrity) over the local diff as a score function, applies deterministic autofixes, and iterates until every blocking gate passes or max_iterations. Pre-PR analog of tools/ci/pr_watcher.py; emits a fix_brief of remaining findings for the driving agent. Config: args/review_loop_config.yaml | --base \<ref\>, --max N, --no-autofix, --no-audit, --json, --gate | Per-iteration score table + fix_brief; --gate exits 0=green/1=not |

### IL-Tier Thresholds

| IL Tier | Coverage Min | SAST Required | DAST Required | SBOM Required |
|---------|-------------|---------------|---------------|---------------|
| IL2     | 70%         | Yes           | No            | No            |
| IL4     | 80%         | Yes           | Yes           | Yes           |
| IL5     | 85%         | Yes           | Yes           | Yes           |
| IL6     | 90%         | Yes           | Yes           | Yes           |
