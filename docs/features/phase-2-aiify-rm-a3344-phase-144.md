# Phase 2 AI-ify — hardcoded_threshold in paperless_mail/mail.py → anomaly_detection (opp 144)

**Opportunity ID:** 144  
**Roadmap:** rm-a334408112  
**Phase:** Phase 2 — Core Modernization  
**Pattern:** hardcoded_threshold → anomaly_detection  
**Module:** paperless_mail/mail.py (external temp clone)  
**Closed:** duplicate

## Resolution

Opportunity 144 targets `src/paperless_mail/mail.py` inside a temporary aiify clone
(`aiify_git_5cc2wcba`). This module contains mail-polling config constants (poll windows,
attachment-size limits, retry/backoff counts) — operational config, not document semantics.

The analogous ICDEV implementation already exists:

- **Commit:** dfb671f09  
- **File:** `tools/monitor/log_analyzer.py` — `_load_anomaly_cfg()`  
- **Config:** `args/monitoring_config.yaml` L91 — `anomaly_detection:` block (zscore + MAD method)

**Closed as duplicate of dfb671f09.** No code changes required.

### Prior siblings (same file, same pattern)
6125, 6126, 6127, 6128, 6129 — all closed docs-only as dup of dfb671f09.

The temp clone (`aiify_git_5cc2wcba`) is deleted before this card runs; the external
paperless repo is not part of ICDEV. Do not modify the clone or author competing MONITOR code.
