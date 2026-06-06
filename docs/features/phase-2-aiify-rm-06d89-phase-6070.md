# Phase 2 — aiify-rm-06d89-phase-6070 (determination)

**Opportunity:** 6070 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern → paradigm:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `…/aiify_git_zwu66zfu/src/documents/signals/handlers.py` (paperless-ngx clone)

## Determination: duplicate of `dfb671f09` (already implemented)

This opportunity targets an **external open-source repo** (paperless-ngx) that the
AI-ify engine shallow-clones, scans, then deletes. The clone `aiify_git_zwu66zfu`
is **gone** (verified — file unmodifiable). Per the established disposition, the
AI-ification lands in the **analogous ICDEV internal subsystem**, chosen by
pattern + paradigm, not by path.

This is the **exact triplet** of `aiify-rm-06d89-phase-6067` and
`aiify-rm-06d89-phase-6068` — same file (`src/documents/signals/handlers.py`),
same pattern/paradigm (`hardcoded_threshold` → `anomaly_detection`), same
`scan_id 43`, same roadmap. Both prior siblings closed as duplicates of the same
commit.

For `hardcoded_threshold` → `anomaly_detection`, the analog is **MONITOR**
(`tools/monitor/log_analyzer.py`). The work was shipped in commit **`dfb671f09`**:
inline z-score (2.0) and error-rate-spike (0.10) constants were replaced with a
config-driven `anomaly_detection` block in `args/monitoring_config.yaml`, plus a
robust MAD (modified z-score, Iglewicz–Hoaglin) method.

## Verification at HEAD (`ee46740c6`)

- `dfb671f09` is an ancestor of HEAD. ✓
- `_load_anomaly_cfg` present in `tools/monitor/log_analyzer.py` (L477), with the
  config-driven `anomaly_detection` paradigm wired through frequency/error-rate. ✓
- `anomaly_detection` block present in `args/monitoring_config.yaml` (L91). ✓
- Sibling closures: `phase-2-aiify-rm-06d89-phase-6067.md`,
  `phase-2-aiify-rm-06d89-phase-6068.md`. ✓

No code change required. Card moved to done with `bypass_verification: true`.
