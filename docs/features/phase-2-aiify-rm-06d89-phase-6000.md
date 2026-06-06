# Phase 2 — aiify-rm-06d89-phase-6000 (determination)

**Opportunity:** 6000 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern → paradigm:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `…/aiify_git_zwu66zfu/src/documents/classifier.py` (paperless-ngx clone)

## Determination: duplicate of `dfb671f09` (already implemented)

This opportunity targets an **external open-source repo** (paperless-ngx) that the
AI-ify engine shallow-clones, scans, then deletes. The clone `aiify_git_zwu66zfu`
is **gone**, so the path is unmodifiable. Per the established disposition, the
AI-ification lands in the **analogous ICDEV internal subsystem**, chosen by
pattern + paradigm, not by path.

This is the **same triplet** as the many sibling closures on this branch (e.g.
`aiify-rm-06d89-phase-5989/5990/5996/5997/5998/6003/6004`) — same
`hardcoded_threshold` → `anomaly_detection` pattern, same `scan_id 43`, same
roadmap `rm-06d89040cf`, same external paperless-ngx `documents/classifier.py`
path. All prior siblings closed as duplicates of the same commit.

For `hardcoded_threshold` → `anomaly_detection`, the analog is **MONITOR**
(`tools/monitor/log_analyzer.py`). The work was shipped in commit **`dfb671f09`**:
inline z-score (2.0) and error-rate-spike (0.10) constants were replaced with a
config-driven `anomaly_detection` block in `args/monitoring_config.yaml`, plus a
robust MAD (modified z-score, Iglewicz–Hoaglin) method.

## Verification at HEAD

- `dfb671f09` is an ancestor of HEAD. ✓
- `_load_anomaly_cfg` present in `tools/monitor/log_analyzer.py` (L477), with the
  config-driven `anomaly_detection` paradigm wired through frequency/error-rate. ✓
- `anomaly_detection` block present in `args/monitoring_config.yaml` (L87/L91). ✓

No code change required. Card moved to done with `bypass_verification: true`.
