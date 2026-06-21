# AAC Phase 3 Assessment — hardcoded_threshold in ai_augmentation/pattern_classifier.py

**Task ID:** aac-rm-28720-phase-677
**Roadmap:** rm-2872026e6f (AI Augmentation Roadmap — Scan 44)
**Opportunity:** 677
**Phase:** Phase 3 — Long-Horizon Investments
**Pattern:** hardcoded_threshold
**File:** `tools/ai_augmentation/pattern_classifier.py` (function `<unknown>` / module scope)
**Recommended paradigm:** anomaly_detection (`claude-haiku-4-5-20251001`)
**Scores:** composite=0.4387, value=0.5, feasibility=0.325, risk=0.5

## Finding

The AAC scanner (Semgrep `hardcoded_threshold` rule) flagged 39 numeric comparisons in
`pattern_classifier.py` and recommended replacing them with ML anomaly detection. The
cluster that produced opportunity 677 sits in the anomaly-detection core itself, e.g.:

```python
# _is_threshold_anomalous (lines 311-336)
if n < _AD_MIN_SAMPLE_SIZE:        # line 318
    return _AD_FALLBACK_TO_ALL
...
if variance > 0:                   # line 323
    z = abs(value - mean) / (variance ** 0.5)
    if z > _AD_Z_SCORE_THRESHOLD:  # line 325
        return True
...
if iqr > 0:                        # line 330
    ...
    if value < lower or value > upper:  # line 333
        return True
```

## Assessment

**False positive / circular recommendation — already implemented.** Two reasons:

1. **The module *is* the anomaly-detection engine.** `pattern_classifier.py` already
   implements exactly the paradigm the scanner recommends — `_is_threshold_anomalous`,
   `_anomaly_score`, and `_compute_percentile_bounds` apply z-score + IQR outlier
   detection over the population of numeric constants. Replacing these comparisons with
   "ML anomaly detection" would mean replacing the anomaly detector with itself.

2. **The flagged thresholds are already externalized, not hardcoded.** Every tuning
   constant is loaded from `args/aac_config.yaml → threshold_anomaly_detection`
   (`pattern_classifier.py` lines 55–66): `z_score_threshold=2.0`, `iqr_multiplier=1.5`,
   `min_sample_size=5`, `min_constant_magnitude=1.0`, `q1/q3_percentile=25/75`,
   `percentile_scale=100`. The literals the scanner sees in source (`> 0`, `< lower`,
   `n < _AD_MIN_SAMPLE_SIZE`) are structural comparisons against zero/variable bounds or
   against config-driven variables — not magic business thresholds.

ML anomaly detection has no applicable role here: the constants define the statistical
machinery itself (variance-zero guards, percentile bounds), not a behavioral threshold a
model could learn.

This matches the prior Phase-3 assessment of the same pattern/score profile in
`docs/features/phase-3-aac-rm-8a699-phase-477.md` (composite=0.4387, feasibility=0.325).

## Recommendation

- **No code change warranted.** Thresholds are already config-driven and the module
  already implements the recommended `anomaly_detection` paradigm.
- AAC scanner calibration: suppress `hardcoded_threshold` hits where (a) the operand is a
  module-level config-loaded variable (`_AD_*`, `_*_cfg.get(...)`) rather than a literal,
  or (b) the comparison is a `> 0` / variance/zero structural guard. Both reduce the
  false-positive rate against the canvas's own tooling.
- The low feasibility score (0.325) is consistent with this assessment.

## Status

Closed — no action required. Documented for AAC scanner calibration.
