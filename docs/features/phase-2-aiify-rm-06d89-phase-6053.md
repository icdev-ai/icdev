<!-- CUI // SP-CTI -->
# AI-ify Determination — aiify-rm-06d89-phase-6053

- **Kanban ID:** aiify-rm-06d89-phase-6053
- **Roadmap:** rm-06d89040cf (scan_id 43)
- **Opportunity ID:** 6053
- **Phase:** Phase 2 — Core Modernization
- **Pattern:** `hardcoded_threshold` → `anomaly_detection`
- **External module_path:** `…/aiify_git_zwu66zfu/src/documents/search/_backend.py` (paperless-ngx; shallow-cloned, scanned, and reaped by the AI-ify engine — external/unmodifiable)

## Determination: DUPLICATE of `dfb671f09` (MONITOR `log_analyzer.py`)

`src/documents/search/_backend.py` is an external paperless file in the `src/documents/*`
subtree carrying the generic `hardcoded_threshold`→`anomaly_detection` pattern. Per the
established AI-ify analog mapping, paperless `src/documents/*` files with this pattern land
in the internal **MONITOR** subsystem (`tools/monitor/log_analyzer.py`), not DIC/IQE — the
DIC divergences are reserved for filename-semantic-strong cases (`matching.py` →
`_is_confident_match`, `plugins/date_parsing/*` → `assess_document_dates`). A generic search
backend score threshold has no such distinguishing match-confidence/date semantics, so it
maps to the default MONITOR anomaly-detection implementation.

The AI-ification already exists internally:
- `tools/monitor/log_analyzer.py` — `_load_anomaly_cfg` (L477), z-score + robust MAD
  (modified z-score) frequency anomaly detection (L545–563).
- `icdev/tools/monitor/log_analyzer.py` — mirrored (`_load_anomaly_cfg` L300).
- `args/monitoring_config.yaml` — `anomaly_detection:` config block (L91), replacing the
  previously hardcoded z-score / error-rate-spike constants.
- Tests: `tests/test_log_analyzer_anomaly.py` — **23/23 pass**.

`dfb671f09` is an ancestor of HEAD (`86abe5e11`, irad/feature). No new code required — the
faithful anomaly_detection modernization of a hardcoded threshold is config-driven z-score/MAD
detection, already shipped.

## Disposition
Card moved to **done** with `bypass_verification: true` + `bypass_reason` (external file
deleted; impl already on branch).
