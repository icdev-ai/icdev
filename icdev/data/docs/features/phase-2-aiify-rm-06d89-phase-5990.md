# Phase 2 — aiify-rm-06d89-phase-5990 (determination)

**Opportunity:** 5990 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern → paradigm:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `…/aiify_git_zwu66zfu/src/documents/barcodes.py` (paperless-ngx clone)

## Determination: duplicate of `dfb671f09` (already implemented)

This opportunity targets an **external open-source repo** (paperless-ngx) that the
AI-ify engine shallow-clones, scans, then deletes. The clone `aiify_git_zwu66zfu`
is **gone** (verified — path no longer exists). Per the established disposition,
the AI-ification lands in the **analogous ICDEV internal subsystem**, chosen by
pattern + paradigm, not by the (transient, external) path.

This is the **same triplet** as the sibling closure `aiify-rm-06d89-phase-5989`
(commit `02a37258b`) — identical file (`src/documents/barcodes.py`), identical
clone (`aiify_git_zwu66zfu`), same `hardcoded_threshold` → `anomaly_detection`
pattern, same `scan_id 43`, same roadmap `rm-06d89040cf`. All such siblings on
this branch closed as duplicates of the same commit.

For `hardcoded_threshold` → `anomaly_detection`, the analog is **MONITOR**
(`tools/monitor/log_analyzer.py`). The work was shipped in commit **`dfb671f09`**:
inline z-score (2.0) and error-rate-spike (0.10) constants were replaced with a
config-driven `anomaly_detection` block in `args/monitoring_config.yaml`, plus a
robust MAD (modified z-score, Iglewicz–Hoaglin) method.

## Verification at HEAD

- `dfb671f09` is an ancestor of HEAD. ✓
- External clone `aiify_git_zwu66zfu/src/documents/barcodes.py` no longer exists. ✓
- `_load_anomaly_cfg` present in `tools/monitor/log_analyzer.py` (L477), with the
  config-driven `anomaly_detection` paradigm wired through frequency/error-rate. ✓
- `anomaly_detection` block present in `args/monitoring_config.yaml` (L91). ✓

No code change required. Card moved to done with `bypass_verification: true`.
