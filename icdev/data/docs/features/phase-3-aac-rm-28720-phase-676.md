# AAC Phase 3 Assessment — hardcoded_threshold in ai_augmentation/pattern_classifier.py

**Task ID:** aac-rm-28720-phase-676
**Roadmap:** rm-2872026e6f (AI Augmentation Roadmap — Scan 44)
**Opportunity:** 676
**Phase:** Phase 3 — Long-Horizon Investments
**Pattern:** hardcoded_threshold
**Source:** `tools/ai_augmentation/pattern_classifier.py` (first-party AAC module)
**AI paradigm:** anomaly_detection
**Model recommendation:** claude-haiku-4-5-20251001
**Scores:** composite=0.4387, value=0.5, feasibility=0.325, risk=0.5

## Finding

The AAC scanner flagged a `hardcoded_threshold` pattern inside
`pattern_classifier.py` and recommended applying the `anomaly_detection`
paradigm.

This is a **self-referential finding**: `pattern_classifier.py` *is* the AAC
pattern detector — the module the scanner uses to find these very patterns. The
scanner re-scanned itself and surfaced its own internal numeric constants.

## Assessment

**Already implemented.** The recommended `anomaly_detection` paradigm is not
just applicable here — it is already the core design of this module:

1. **The thresholds are config-driven, not hardcoded.** Every numeric constant
   the scanner relies on is externalized to `args/aac_config.yaml` under
   `threshold_anomaly_detection` and loaded at import time
   (`_AD_Z_SCORE_THRESHOLD`, `_AD_IQR_MULTIPLIER`, `_AD_MIN_SAMPLE_SIZE`,
   `_AD_MIN_CONSTANT_MAGNITUDE`, `_AD_Q1_PERCENTILE`, `_AD_Q3_PERCENTILE`,
   `_AD_PERCENTILE_SCALE`, plus `pattern_min_depth`, `rule_min_keys`,
   `keyword_list_min_strings`, and the Java tuning knobs). No business
   threshold is baked into Python.

2. **The module already performs statistical anomaly detection.** Rather than
   flagging every numeric literal, `_detect_hardcoded_threshold` builds a
   population of all numeric constants in the file and flags only statistical
   outliers via `_is_threshold_anomalous` (z-score, with IQR-fence fallback for
   zero-variance populations) and emits a continuous `_anomaly_score`. This is
   exactly the `anomaly_detection` paradigm the opportunity recommends, applied
   across the Python AST path, the C# tree-sitter/regex paths, and the Java
   regex path.

3. **Remaining literals are structural/protocol constants.** The handful of
   bare literals left in the module (e.g. `len(named_parts) < 2`,
   `int(c.value)` boundary guards, AST-shape checks) are structural
   requirements of the parsing logic, not behavioral thresholds — consistent
   with the false-positive finding documented in the sibling assessment
   `phase-3-aac-rm-8a699-phase-477.md`.

## Verification

- `pytest tests/test_pattern_classifier_threshold_ad.py` → **44 passed**.
- Config block present and complete at `args/aac_config.yaml:43`
  (`threshold_anomaly_detection`).

## Recommendation

- No code change warranted — the `anomaly_detection` capability the opportunity
  recommends is already shipped and tested in the flagged module.
- The AAC scanner should add a self-exclusion rule so it does not re-flag
  `pattern_classifier.py`'s own detector internals on future scans (calibration
  item, mirrors the `phase-477` recommendation).
- Low feasibility score (0.325) is consistent with this assessment.

## Status

Closed — no action required. The recommended paradigm is already implemented in
the flagged module. Documented for AAC scanner calibration.
