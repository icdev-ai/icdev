# CUI // SP-CTI

# Phase 2 — Core Modernization: Adaptive Hiring-Surge Anomaly Detection (Talent Reflex)

**Task:** `aiify-rm-15851-phase-5447`
**Opportunity:** 5447 · scan 27 · roadmap `rm-1585150d1c`
**Pattern:** `hardcoded_threshold` → AI paradigm `anomaly_detection`
**Module:** `tools/proposal_genesis/reflexes/talent.py` (R20 Talent Intelligence, §3.16)

## Problem

The R20 Talent reflex flagged competitor "hiring surges" with a hardcoded absolute
cut-off — `_detect_surges(signals, threshold: int = 5)`. Any competitor with ≥ 5
postings in the window was a surge, regardless of the overall hiring climate. The
config already declared `velocity_threshold_zscore: 2.0` ("Alert on hiring spikes
> 2 std devs"), but the code never used it. The static threshold is brittle:

- In a hot market where everyone posts heavily, 5 postings is noise — false positives.
- In a quiet market, a competitor doubling its (small) baseline never trips 5 — false negatives.

## Change

Replaced the static count with a **data-driven upper control limit** over the
competitor posting distribution:

```
surge_threshold = clamp_floor( mean + zscore * std , min_absolute_surge )
```

- **`_count_by_competitor(signals)`** — extracted shared tally helper.
- **`_compute_surge_threshold(competitor_counts, anomaly_cfg)`** — computes the
  adaptive threshold. Honors the long-standing top-level `velocity_threshold_zscore`
  as the default z-score. Clamped to `min_absolute_surge` so a flat, low-volume field
  never manufactures spurious surges. Falls back to the static count when fewer than
  `min_samples` competitors exist or detection is disabled — **behavior is unchanged
  with no/low history.**
- **`_detect_surges(...)`** — now accepts a `float` threshold (static or adaptive).
- **`run()`** — resolves the threshold from config, records it in the audit row and
  returned `details.surge_threshold`. Backward-compat: an explicit legacy
  `talent_surge_threshold` is still respected as the static fallback.

All thresholds are config-only — module-level constants are fallbacks:
`_SURGE_COUNT_THRESHOLD=5`, `_DEFAULT_ZSCORE=2.0`, `_MIN_SURGE_SAMPLES=3`,
`_LOOKBACK_DAYS=30`.

## Config (`args/proposal_genesis_config.yaml` → `reflexes.talent`)

```yaml
velocity_threshold_zscore: 2.0      # default z-score (existing, now wired in)
anomaly_detection:
  enabled: true
  min_samples: 3                    # min competitors before adaptive limit computed
  min_absolute_surge: 5             # floor — never flag below this count
  fallback_surge_count: 5           # static fallback (legacy behavior)
```

## Tests

`tests/genesis/test_talent_anomaly.py` — 18 tests, all passing:
- `_count_by_competitor` tallying, unknown bucket, empty.
- `_compute_surge_threshold`: disabled→fallback, insufficient-samples→fallback,
  module defaults, adaptive UCL math, `min_absolute_surge` floor, zero-variance.
- `_detect_surges`: float threshold, at-or-above boundary, descending sort.
- `run()` wiring: adaptive threshold reported in details, top-level z-score honored,
  backward-compat static fallback, empty signals.

```bash
python -m pytest tests/genesis/test_talent_anomaly.py --noconftest -q   # 18 passed
ruff check tools/proposal_genesis/reflexes/talent.py                    # clean
```

## Risk

GREEN tier, read-only. No DB schema change. With no signal history the adaptive
limit yields the prior static `5`, so the modernization is a no-op until enough
competitors exist to form a distribution.

# CUI // SP-CTI
