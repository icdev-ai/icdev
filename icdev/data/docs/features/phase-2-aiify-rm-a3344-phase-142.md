# AI-ify Opportunity 142 — Closed as Duplicate

**Opportunity ID:** 142  
**Roadmap:** rm-a334408112 (Phase 2 — Core Modernization)  
**Pattern:** hardcoded_threshold → anomaly_detection  
**Source file:** `src/paperless_mail/mail.py` (external temp clone)  
**Status:** CLOSED — duplicate of dfb671f09

## Decision

`paperless_mail/mail.py` contains mail polling/fetch config constants (poll windows,
attachment-size limits, retry/backoff counts). These are operational config with no
document semantics, mapping to the default MONITOR config-driven anomaly layer.

This opportunity is a duplicate of the already-shipped implementation:

- **Commit:** `dfb671f09`
- **Module:** `tools/monitor/log_analyzer.py` → `_load_anomaly_cfg()`
- **Config:** `args/monitoring_config.yaml` L91 (`anomaly_detection:` block, zscore + MAD method)

## Prior siblings closed as dup of dfb671f09

Opps 6125, 6126, 6127, 6128, 6129 — all `paperless_mail/mail.py`
hardcoded_threshold→anomaly_detection, all closed docs-only.

The temp clone (`aiify_git_5cc2wcba`) is reaped — the source file no longer exists
on disk by the time this card runs. No code changes required; the existing MONITOR
anomaly detection implementation already covers this pattern.
