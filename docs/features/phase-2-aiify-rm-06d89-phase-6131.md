# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6131

**Opportunity:** 6131 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `src/paperless_mail/serialisers.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`, reaped before the card ran)
**Disposition:** Closed as **duplicate** of `dfb671f09` (MONITOR `log_analyzer.py` anomaly-detection).

## Rationale

The `module_path` points at a temp `aiify_git_*` clone of an **external** open-source
repo (paperless-ngx) that the AI-ify engine shallow-clones, scans, and deletes. The
file is unmodifiable by the time the kanban card runs, with `function_name`
`<unknown>` and the generic boilerplate *"Hardcoded numeric threshold -- replace with
ML anomaly detection"*. Per the established disposition, the AI-ification lands in the
**analogous ICDEV internal subsystem** selected by **pattern + paradigm**, not by
filename.

`paperless_mail/serialisers.py` is the Django REST Framework **serializer** module for
the mail-account / mail-rule API — its hardcoded numbers are field-validation and
config constants (max-length limits, enum/choice bounds, default poll/age values). It
carries no match-confidence / date-parse / OCR / search-relevance / freshness document
semantics that would map it to a specific DIC detector. It therefore maps to the
default **MONITOR** config-driven anomaly layer (`tools/monitor/log_analyzer.py`),
where inline z-score / error-rate constants were replaced with a config-driven
`anomaly_detection` block in `args/monitoring_config.yaml` plus a robust MAD (modified
z-score) method.

This is the **same disposition** as the same-package siblings **6125 / 6126 / 6127 /
6129** (`mail.py`), **6130** (`oauth.py`, `b99d8b471`), **6132** (`tasks.py`,
`bfc7904fc`), and **6133** (`views.py`, `8eb75e46d`) — all closed as dups of
`dfb671f09`. The scanner re-emits this `paperless_mail` package threshold family on
every scan of the reaped clone. Document-semantic email work for this package was
handled separately by **6100** (DIC email envelope extraction, `093edb00a`); this
serializer-validation/config threshold is not that.

## Verification (worktree HEAD on `kanban/aiify-rm-06d89-phase-6131`)

- `dfb671f09` is an ancestor of HEAD ✓
- `_load_anomaly_cfg` present in `tools/monitor/log_analyzer.py` ✓
- `anomaly_detection:` block in `args/monitoring_config.yaml` (zscore + mad) ✓
- Same-package siblings 6125 / 6126 / 6127 / 6129 / 6130 / 6132 / 6133 already closed as dups of `dfb671f09` ✓

No competing implementation authored. Card moved to `done` with
`bypass_verification: true` + `bypass_reason` (no new code — documented duplicate of
`dfb671f09` and of same-package siblings 6125 / 6126 / 6127 / 6129 / 6130 / 6132 / 6133).
