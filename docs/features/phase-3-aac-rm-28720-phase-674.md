# AAC Phase 3 Assessment — hardcoded_threshold in tools/ai_augmentation/pattern_classifier.py

**Task ID:** aac-rm-28720-phase-674
**Roadmap:** rm-2872026e6f (AI Augmentation Roadmap — Scan 44)
**Phase:** Phase 3 — Long-Horizon Investments
**Pattern:** hardcoded_threshold
**Source:** `tools/ai_augmentation/pattern_classifier.py` (first-party, scan_id=44)
**Function:** `<unknown>` (module-level, per opportunity 674)
**AI paradigm recommended:** anomaly_detection (`claude-haiku-4-5-20251001`)
**Scores:** composite=0.4387, value=0.5, feasibility=0.325, risk=0.5

## Finding

The AAC scanner flagged a `hardcoded_threshold` in the pattern classifier itself.
Re-running the detector against the file (`_detect_via_ast_fallback`) reproduces three
hits, all of which are the literal `2`:

| Line | Enclosing function | Construct | Source |
|------|--------------------|-----------|--------|
| 322 | `_is_threshold_anomalous` | `BinOp` (`Pow`) | `variance = sum((x - mean) ** 2 for x in population) / n` |
| 354 | `_anomaly_score` | `BinOp` (`Pow`) | `variance = sum((x - mean) ** 2 for x in population) / n` |
| 1159 | `_cs_get_method_call` | `Compare` (`Lt`) | `if len(named_parts) < 2:` |

## Assessment

**False positive / mathematical-and-structural constant.** None of the three flagged
`2` literals is a tunable behavioral threshold:

- **Lines 322 & 354 — `(x - mean) ** 2`.** The `2` is the exponent in the textbook
  population-variance formula (σ² = Σ(xᵢ − μ)² / n). It is a mathematical constant; any
  other value computes something that is no longer variance. It cannot be made
  configurable without producing incorrect statistics.
- **Line 1159 — `len(named_parts) < 2`.** The `2` is a structural arity requirement: a
  C# `member_access_expression` must yield at least an object part and a method-name part
  before `_cs_get_method_call` can return `(receiver, method)`. It is a shape invariant of
  the AST node, not a threshold.

ML anomaly detection (`ai_paradigm: anomaly_detection`) has no applicable role here. This
is reinforced by an unusual fact specific to this target:

> **This module is already the canonical, reference implementation of the recommended
> paradigm.** `pattern_classifier.py` *is* the anomaly-detection engine — it externalizes
> every genuine threshold into the `threshold_anomaly_detection` block of
> `args/aac_config.yaml` (`z_score_threshold`, `iqr_multiplier`, `min_sample_size`,
> `q1_percentile`, `q3_percentile`, `min_constant_magnitude`, …) and applies a
> z-score-then-IQR outlier test (`_is_threshold_anomalous`, `_anomaly_score`) to every
> numeric constant it inspects. The paradigm the opportunity asks us to introduce is the
> one this file implements.

The detector flags its own `** 2` and `< 2` only because those literals appear in
comparisons/binops; the file's own `_AD_MIN_CONSTANT_MAGNITUDE` guard (default `1.0`) does
not exclude them, and against the file's own numeric population the value `2` scores just
over the anomaly boundary (≈1.15). That is the detector being consistent with itself, not
a latent defect.

## Recommendation

- **No code change warranted.** The flagged constants are a variance exponent and a node
  arity check; both are correctness-defining, not configurable. Genuine thresholds in this
  module are already config-driven via `args/aac_config.yaml`.
- **Scanner calibration (future work):** suppress `hardcoded_threshold` for (a) the
  exponent operand of a `Pow` BinOp and (b) `len(...) </<= N` arity guards. These two
  shapes are structurally never behavioral thresholds and recur as false positives (cf.
  the `len(h) == 2` RFC-constant finding in `aac-rm-8a699-phase-477`).
- The low feasibility score (0.325) is consistent with this assessment.

## Status

Closed — no action required. Documented for AAC scanner calibration.
