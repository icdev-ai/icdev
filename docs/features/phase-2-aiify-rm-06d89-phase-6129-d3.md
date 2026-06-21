# AI-ify Determination — aiify-rm-06d89-phase-6129-d3 (config-file slice of opp 6129)

**Date:** 2026-06-06
**Disposition:** Closed as **duplicate** of `dfb671f09` (MONITOR `anomaly_detection`) — no new code.
**Move:** `bypass_verification:true` (external-repo opp; config already landed in the analogous ICDEV subsystem).

## Subtask

Decomposed `-d3` child of opp 6129 (`aiify-rm-06d89-phase-6129`). Scope is the **configuration-file slice**:

> "Edit the corresponding configuration (e.g., config.yaml or settings.py in the cloned paperless repo) to remove hardcoded numbers and insert references to environment variables or runtime-loaded model parameters for anomaly detection."

## Why this is a duplicate

The target config file lives in the temp shallow-clone `aiify_git_zwu66zfu/` that the AI-ify engine clones, scans, then deletes. At run time the clone is an empty `.git` shell — `src/paperless_mail/mail.py` and any companion config are gone and unmodifiable (see [[aiify-external-repo-opps-land-in-dic]]). Per the established disposition, the AI-ification lands in the **analogous internal ICDEV subsystem**, then re-emitted slices close as dups of that work.

The parent `aiify-rm-06d89-phase-6129` already closed as a dup of `dfb671f09` (generic `hardcoded_threshold → anomaly_detection` maps to MONITOR, not DIC). This `-d3` is the **configuration** half of that same opportunity, and the config-driven-threshold capability shipped in `dfb671f09`:

- `anomaly_detection:` block in `args/monitoring_config.yaml` — z-score and robust MAD thresholds expressed as config values, **no magic constants**
- `_load_anomaly_cfg()` in `tools/monitor/log_analyzer.py` loads those values at runtime; config/PyYAML failure degrades to the legacy constants

That is precisely the "remove hardcoded numbers → runtime-loaded parameters" change this subtask asks for, applied to the internal analog rather than a deleted external clone.

## Verification

- Temp clone `aiify_git_zwu66zfu/` confirmed empty (`.git` shell only); paperless config GONE/unmodifiable ✓
- Parent `aiify-rm-06d89-phase-6129` closure doc present on branch (`b34d19089`) ✓
- `dfb671f09` authored the config-driven `anomaly_detection:` block + `_load_anomaly_cfg()` loader (MONITOR) ✓

## Conclusion

No competing implementation authored. Closed as dup of `dfb671f09`. Future `src/paperless_mail/*` config-slice siblings: verify the MONITOR `anomaly_detection:` config block exists, close as dup.
