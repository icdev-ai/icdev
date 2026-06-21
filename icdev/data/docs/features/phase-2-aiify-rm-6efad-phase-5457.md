# Phase 2 — Core Modernization: Adaptive file-size anomaly detection in auto_indexer

**CUI // SP-CTI**

AI-ify opportunity **5457** (roadmap `rm-6efad73721`, scan 28) — `hardcoded_threshold → anomaly_detection`
in `tools/rag/auto_indexer.py:132` (the static `max_file_size_mb = 10` gate).

## Problem

`AutoIndexer.scan()` rejected any file larger than a fixed `max_file_size_mb`
(default 10 MB). A single byte ceiling is brittle: in a corpus of mostly 4 KB
markdown files an 8 MB blob is plainly anomalous yet sails under the 10 MB cap,
while a legitimately large PDF corpus is gated at the same fixed number.

## Change

The fixed cutoff is replaced with a **config-driven adaptive cutoff** that also
flags files whose size is a statistical outlier in the scanned corpus
(`mean + sigma_multiplier * std_dev`), following the established AI-ify
anomaly-detection pattern (`inspect_adapt`, `query_classifier`, `crag_evaluator`).

- `_compute_size_threshold_mb(sizes_mb, static_max_mb, anomaly_cfg)` — pure helper
  returning `(effective_max_mb, computed)`.
  - Effective cutoff = `min(static_max, anomaly_cutoff)` — anomaly detection can
    only make the gate **stricter**, never more permissive than the configured
    `max_file_size_mb` (which remains an absolute hard ceiling / safety control).
  - Falls back to the static ceiling when disabled, when there are fewer than
    `min_samples` files, or when the computed cutoff would drop below
    `size_floor_mb` (so a corpus of tiny files can never block normal documents).
- `scan()` is now two-pass: collect candidate `(path, size)` pairs, derive the
  effective cutoff from the size distribution, then apply the gate + CUI boundary
  check. The result dict reports `size_threshold_mb` and `size_threshold_adaptive`.

## Config (`args/rag_config.yaml` → `rag.auto_indexer.size_anomaly_detection`)

```yaml
size_anomaly_detection:
  enabled: true
  min_samples: 20          # need >= this many files to model the size spread
  sigma_multiplier: 3.0    # outlier cutoff = mean + this * std_dev (MB)
  size_floor_mb: 1.0       # never flag files below this as anomalous
```

## Tests

`tests/test_auto_indexer_anomaly.py` — 10 tests: constant sanity, the adaptive
helper (disabled / too-few-samples / tiny-corpus fallback, outlier tightening,
never-more-permissive cap, return types), and end-to-end `scan()` behavior
(anomalous large file gated out, disabled uses static ceiling, absolute ceiling
still enforced). All pass; `ruff check` clean.

## Note — duplicate opportunity

Sibling opportunity **5458** (line 345) was mislabeled by a concurrent session as
the same `max_file_size_mb` threshold; per DB it is actually the `project_level < 2`
classification-level constant. This task (5457) addresses the genuinely-flagged
line 132 size gate and is the canonical in-place modernization for it.
