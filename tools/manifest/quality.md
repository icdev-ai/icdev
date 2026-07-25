# Quality Gates

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Quality Gates
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Rigor Gates | tools/quality/rigor_gates.py | IL-tier quality rigor gates: coverage and static-analysis thresholds; auto-discovers coverage.xml; graceful degrade if no coverage data available | --tier (il2/il4/il5/il6), --project-dir, --coverage, --json, --gate | Gate pass/fail with violations list |
| Completion Auditor | tools/quality/completion_auditor.py | Per-canvas 8-component completeness scorecard; enumerates EVERY dashboard canvas (not just those with page.html) and scores each against the CLAUDE.md completeness gate — surfaces canvases the coherence gate never sees | --json, --md | JSON to stdout, or writes docs/quality/completion-scorecard.md (sorted least→most complete) |
| Categorical Scoring (deterministic-picker) | tools/quality/categorical_scoring.py | agx-pick-02 composition half of the deterministic-picker rule: the LLM emits only a 3-value enum per dimension, pure Python composes the score/verdict. Versioned vocabularies + `compose_fitness` / `compose_eval_overall` / `compose_grounding`; unknown tokens degrade deterministically (neutral for fitness/eval, fail-closed for grounding). Consumed by fitness.py, ace/evaluator.py, content_grounding.py | (library) VOCABULARY_VERSION, compose_* fns | Composed float in [0,1] + vocabulary_version provenance |

### IL-Tier Thresholds

| IL Tier | Coverage Min | SAST Required | DAST Required | SBOM Required |
|---------|-------------|---------------|---------------|---------------|
| IL2     | 70%         | Yes           | No            | No            |
| IL4     | 80%         | Yes           | Yes           | Yes           |
| IL5     | 85%         | Yes           | Yes           | Yes           |
| IL6     | 90%         | Yes           | Yes           | Yes           |
