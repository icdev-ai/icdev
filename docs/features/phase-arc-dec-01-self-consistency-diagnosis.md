# Phase ARC: self-consistency diagnosis (WS4.1 / arc-dec-01)

> A SINGLE LLM call's `confidence` is mostly a self-reported number — easy to
> over-rate. The fix: sample N diagnoses with a temperature spread, then derive
> the REAL confidence from the cross-sample AGREEMENT on the two decisions the
> auto-apply gate actually relies on (root_cause class + primary suspect file).
> High agreement → many independent reads converged on the same structural
> answer → high confidence. Low agreement → stochastic / ambiguous → confidence
> is bounded low regardless of what any individual sample claimed.

## What it does

Before `should_auto_apply` decides whether to auto-apply a patch, the failure
triage reflex now takes **N diagnoses** of the same failure (default N=3) with
a temperature spread (default `[0.1, 0.4, 0.7]`) instead of trusting a single
self-reported `confidence` value. The cross-sample AGREEMENT on
`(root_cause_class, primary_suspect_file)` is the new confidence the gate
consumes.

- Disagreement (e.g. 1/3 vote) is a HARD short-circuit to the suggested-card
  path — even a 0.95 raw confidence cannot save it, because that is exactly
  the failure mode the SC path exists to catch.
- Agreement (e.g. 3/3 vote) translates directly to a confidence ≥ threshold,
  so the gate proceeds with the SC-derived number.
- Both the raw per-sample confidences and the agreement-derived (SC)
  confidence are recorded so the `arc-cal-*` calibration can later compare
  them.

## Where it lives

| File | What changed |
|------|--------------|
| `tools/workflow/failure_triage.py` | New helpers: `_sc_load_config`, `_sc_classify_root_cause`, `_sc_primary_suspect`, `_sc_sample_one`, `_sc_temperatures`, `_sc_aggregate`, `_sc_diagnose_task`. `should_auto_apply` accepts an optional `sc_aggregate` argument and uses it as the gate. `triage_once` calls `_sc_diagnose_task` per failure and threads the aggregate through the gate + diagnosis record. New event types: `EVENT_SC_SAMPLE`, `EVENT_SC_AGGREGATED`. |
| `icdev/tools/workflow/failure_triage.py` | Mirror of the above (the canonical namespace). |
| `args/genesis_config.yaml` | New `reflexes.failure_triage.self_consistency` block — `enabled`, `n_samples`, `temperature_spread`, `agreement_threshold`, `token_budget_per_sample`. Read on every failure so the operator can dial knobs without a daemon restart. |
| `tests/test_failure_triage.py` | 29 new tests across `TestSCConfig`, `TestSCRootCauseClassify`, `TestSCPrimarySuspect`, `TestSCTemperatures`, `TestSCAggregate`, `TestSCDiagnoseTask`, `TestShouldAutoApplySCOverride`. |

## Output schema

`_sc_diagnose_task` always returns:

```json
{
  "samples": [ <raw LLM diag 0>, ... ],
  "aggregate": {
    "samples_valid": 3,
    "agreement_score": 1.0,
    "consensus_root_cause": "import",
    "consensus_suspect_file": "tools/foo.py",
    "self_consistency_confidence": 1.0,
    "raw_confidence_mean": 0.87,
    "meets_threshold": true,
    "per_sample": [
      {"root_cause_class": "import", "suspect_file": "tools/foo.py"}, ...
    ]
  },
  "enabled": true,
  "config": {
    "n_samples": 3, "temperature_spread": 0.6,
    "agreement_threshold": 0.5, "token_budget_per_sample": 2000,
    "enabled": true
  }
}
```

The diagnosis record on the triage entry has a nested `self_consistency` block
that mirrors the aggregate, plus the legacy `confidence` / `recommendation`
fields for back-compat with the panel (arc-obs-03).

## Configuration

```yaml
# args/genesis_config.yaml
reflexes:
  failure_triage:
    self_consistency:
      enabled: true                  # master switch; set false to bypass SC
      n_samples: 3                   # 3 = smallest size distinguishing 3/3 from 2/3 from 1/3
      temperature_spread: 0.6        # spread [0.1, 0.7] for N=3
      agreement_threshold: 0.5       # majority (>=0.5) of N must agree on (rc, suspect)
      token_budget_per_sample: 2000  # max_tokens cap on each LLM call in the SC batch
```

The defaults are deliberately small: N=3 is the smallest sample size that
meaningfully distinguishes 3/3 from 2/3 or 1/3 agreement, and 2000 chars/sample
caps the LLM spend on a single failure.

## Why it works

The auto-apply gate has one job: keep a high-confidence patch from being
applied to a wrong diagnosis. The single LLM call is good at
"here's what I think", but the calibration between its self-reported confidence
and the truth is poor — a model that returns `confidence: 0.95` on a wrong
diagnosis is the exact failure mode that has been observed in practice. Cross-
sample agreement is a calibration-free proxy: if three independent reads
(with different temperatures) all converge on the same `(root_cause_class,
suspect_file)`, the structural answer is unlikely to be a fluke. If they
disagree, the right move is the suggested-card path, not a high-confidence
auto-apply.

## Calibration path (arc-cal-*)

The aggregate records both `self_consistency_confidence` (the new gate value)
and `raw_confidence_mean` (the legacy self-reported mean). When the human
follows up on a suggested card and marks the real outcome, the calibration
service can later compare:

- Agreement vs. actual-correct → does high agreement actually predict success?
- Raw confidence vs. actual-correct → does the legacy self-reported number
  predict success on the same population?

If agreement is a stronger predictor, the SC path is doing its job. If raw
confidence is just as good, the SC path can be turned off (`enabled: false`)
and the gate reverts to the legacy single-shot path.
