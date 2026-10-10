# No rate in the RMF surfaces returns 0.0 or 100.0 over an empty denominator (rmf-rail-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m pytest tests/test_rmf_honesty_rails.py -q           # the rail: behavioural + structural
python tools/ci/perfect_score_census.py --check               # 100.0 fallback over a ratio: 0 sites, ceiling 0
python tools/workflow/coherence_checker.py --check substrate_liveness --gate
python tools/awareness/capability_consumption.py --probe-diff origin/main   # what a branch reads that is EMPTY
```

MEASURED on the live board 2026-09-03: project_controls, cato_evidence,
rmf_workflow_stages and asset_visibility_snapshots ALL hold 0 rows -- and over
that estate the ATO dashboard reported posture_pct 0.0 ("assessed, nothing
implemented"), graded the project F at 16 points (0.0 controls, 0.0 RMF
progress over SIX SYNTHETIC not_started stages, and 55 artifact points awarded
for the ABSENCE of POAM items and STIG findings -- the rmf-zt-01 "absent probe
reads as a pass" shape one table over), and compute_cato_readiness returned
readiness_pct 0.0 / automated_pct 0.0 over no evidence at all. rmf-fab-02 had
already refused to CARRY those two numbers and re-derived its own; this fixes
them at source. Every one is now None, never 0.0, and a MEASURED zero (one
planned control, one stale manual evidence row) stays a real 0.0 -- the two
are asserted side by side because confusing them is the whole defect.
get_posture_score refuses a composite while ANY component is unmeasured
(`score_basis: unmeasured`, `unmeasured_components` named) -- a composite over
a subset under the unscoped name "score" is a scoped computation wearing an
unscoped name. The page renders a null as "not assessed" with an EMPTY gauge:
`x || 0` in the template would put the red 0% straight back.
TWO RAILS, and they are different tests. Behavioural: each rate against an
empty denominator. Structural: an AST scan of the 16 RMF modules for a RATIO
(perfect_score_census.computes_ratio -- the same predicate, not a copy)
falling back to 0 / 0.0 / 100 / 100.0 through a conditional expression --
the rem-hyg-13 census widened to the ZERO fallback, scoped to these modules
ONLY because tree-wide `if x else 0` is ~1,167 ordinary counters. Zero sites.
Registered as substrates (args/capability_consumption.yaml): rmf_workflow_stages,
asset_visibility_snapshots, cato_evidence, project_controls -- so a branch that
designs against them is TOLD they are empty (--probe-diff names all four).
The fix for the empties is a WRITER, and it is not this card: nothing on this
deployment has assigned a control to a project or collected cATO evidence.
