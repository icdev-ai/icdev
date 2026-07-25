# Quality Gates

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Quality Gates
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Rigor Gates | tools/quality/rigor_gates.py | IL-tier quality rigor gates: coverage and static-analysis thresholds; auto-discovers coverage.xml; graceful degrade if no coverage data available | --tier (il2/il4/il5/il6), --project-dir, --coverage, --json, --gate | Gate pass/fail with violations list |
| Completion Auditor | tools/quality/completion_auditor.py | Per-canvas 8-component completeness scorecard; enumerates EVERY dashboard canvas (not just those with page.html) and scores each against the CLAUDE.md completeness gate — surfaces canvases the coherence gate never sees | --json, --md | JSON to stdout, or writes docs/quality/completion-scorecard.md (sorted least→most complete) |
| Categorical Scoring (deterministic-picker) | tools/quality/categorical_scoring.py | agx-pick-02 composition half of the deterministic-picker rule: the LLM emits only a 3-value enum per dimension, pure Python composes the score/verdict. Versioned vocabularies + `compose_fitness` / `compose_eval_overall` / `compose_grounding` / `compose_divergence`; unknown tokens degrade deterministically (neutral for fitness/eval/divergence, fail-closed for grounding). Consumed by fitness.py, ace/evaluator.py, content_grounding.py, divergence_critic.py | (library) VOCABULARY_VERSION, compose_* fns | Composed float in [0,1] + vocabulary_version provenance |
| Divergence Critic (Focus half) | tools/quality/divergence_critic.py | dvg-critic-01 scorer for the raw idea pool from `invoke_divergence`. SEPARATE opposing LLM call (harsh-critic system prompt, never a continuation of the generator); LLM emits categorical enums per idea on novelty/viability/fit + rationale; Python composes composite AND ordering via `compose_divergence` (never asks the model to rank). Unlike gap_scorer.py (scores against a DB of known items), scores ideas with no DB row yet. Degrades cleanly (empty_pool / critic_unavailable / unparseable_critic_output). Persists to `divergence_idea_scores` (migration 293) keyed by trace_id | `score_idea_pool(pool_text, *, function, trace_id=..., router=..., persist=True)` / CLI `--function --pool-file [--json]` | `ScoredPool(ordered=[IdeaScore...], stop_reason, ...)` composite-descending |
| Constitutional AI (per-rule critique/revise) | tools/quality/constitutional_ai.py | agx-verify-02 per-rule critique + targeted revision for LLM-drafted artifacts. Rules loaded as DATA from `args/security_gates.yaml` `constitution:` block (single-source encoding of existing gate/CUI/TRUST invariants). Each rule critiqued in its OWN call → 3-value enum verdict `{pass/fail/not_applicable}` + offending span; Python composes overall via **any-block-rule-fail** policy; failed BLOCK rules get a bounded targeted revision (no silent give-up). Per-rule trail → append-only `constitutional_audit_log` (migration 292). Fail-closed on malformed | `constitutional_review(artifact, artifact_type=..., router=...)` | {passed, revised_text, rule_trace[], failed/unresolved rules, audit_records} |

### IL-Tier Thresholds

| IL Tier | Coverage Min | SAST Required | DAST Required | SBOM Required |
|---------|-------------|---------------|---------------|---------------|
| IL2     | 70%         | Yes           | No            | No            |
| IL4     | 80%         | Yes           | Yes           | Yes           |
| IL5     | 85%         | Yes           | Yes           | Yes           |
| IL6     | 90%         | Yes           | Yes           | Yes           |
