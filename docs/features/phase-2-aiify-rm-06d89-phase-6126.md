# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6126

**Opportunity:** 6126 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External target:** `src/paperless_mail/mail.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`, reaped before task ran)
**Composite score:** 0.5326 (value 0.491 / feasibility 0.7475 / risk 0.75)

## Disposition: DUPLICATE — closed, no new code

The `module_path` points into the temp clone `aiify_git_zwu66zfu/src/paperless_mail/mail.py`,
which the AI-ify engine cloned and **already deleted** (`GONE` on disk). Per the
established `src/paperless/*` external-repo disposition, opportunities targeting
generic paperless infrastructure files are AI-ify'd against the analogous ICDEV
internal subsystem rather than the reaped external tree.

`paperless_mail/mail.py` is the email-ingestion plumbing of the mail plugin —
account fetching, mail-rule evaluation, connection/retry constants — flagged with
`function_name` `<unknown>` and the boilerplate detail *"Hardcoded numeric
threshold -- replace with ML anomaly detection."* It carries no
match-confidence / date-parse / search-relevance / OCR / freshness document
semantics, so (exactly like its same-class siblings `src/paperless/settings/__init__.py`
and `adapter.py`) it maps to the default **MONITOR `log_analyzer`** config-driven
anomaly layer, **not** DIC.

The canonical work `dfb671f09` already performed precisely this modernization:
it replaced the hardcoded z-score (2.0) and error-rate spike (0.10) thresholds in
`tools/monitor/log_analyzer.py` with a configurable `anomaly_detection` block in
`args/monitoring_config.yaml`, adding a robust MAD / Iglewicz–Hoaglin modified
z-score method alongside the legacy mean/std-dev z-score, with safe degradation to
the legacy constants. That is the modernization this opportunity calls for.

Same-class siblings closed identically: 6095/6096 (adapter.py → `111ba4c54`/`76dc75e01`),
6115/6116/6117 (settings/__init__.py → `762b06628`/`96f31560e`/`554feadae`).

## Verification (HEAD `4f4d973e7`, branch `irad/feature`)

- `dfb671f09` IS an ancestor of HEAD.
- `_load_anomaly_cfg` + z-score / modified-z-score (MAD) present in
  `tools/monitor/log_analyzer.py` (and the `icdev/` mirror).
- `anomaly_detection:` config block present in `args/monitoring_config.yaml`.
- Temp clone `aiify_git_zwu66zfu/src/paperless_mail/mail.py` confirmed deleted.

## Board action

Moved to `done` with `bypass_verification: true` + `bypass_reason` (no new code —
disposition is a documented duplicate of `dfb671f09` and of same-class paperless
infra siblings 6095/6096/6115/6116/6117).
