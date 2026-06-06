# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6128

**Opportunity:** 6128 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External module:** `src/paperless_mail/mail.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`, reaped before the card ran)
**Disposition:** Closed as **duplicate** of `dfb671f09` (MONITOR `log_analyzer.py` anomaly-detection).

## Rationale

The `module_path` points at a temp `aiify_git_*` clone of an **external** open-source
repo (paperless-ngx) that the AI-ify engine shallow-clones, scans, and deletes. The
file is unmodifiable by the time the kanban card runs, with `function_name`
`<unknown>` and the generic boilerplate *"Hardcoded numeric threshold -- replace with
ML anomaly detection"*. Per the established disposition, the AI-ification lands in the
**analogous ICDEV internal subsystem** selected by **pattern + paradigm**, not by
filename.

`paperless_mail/mail.py` is the mail-account **polling / fetch** module — its hardcoded
numbers are operational config constants (poll age windows, attachment-size limits,
retry/backoff counts). It carries no match-confidence / date-parse / OCR /
search-relevance / freshness document semantics that would map it to a specific DIC
detector. It therefore maps to the default **MONITOR** config-driven anomaly layer
(`tools/monitor/log_analyzer.py`), where inline z-score / error-rate constants were
replaced with a config-driven `anomaly_detection` block in `args/monitoring_config.yaml`
plus a robust MAD (modified z-score) method.

This is the **same disposition and same `paperless_mail/mail.py` file** as the
exact-file siblings **6125**, **6126**, **6127**, and **6129** — all closed as dups of
`dfb671f09` — and the same-package siblings **6130** (`oauth.py`, `b99d8b471`),
**6131** (`serialisers.py`), **6132** (`tasks.py`, `bfc7904fc`), and **6133**
(`views.py`, `8eb75e46d`). The scanner re-emits this same `mail.py` threshold
opportunity on every scan of the reaped clone. Document-semantic email work for this
package was handled separately by **6100** (DIC email envelope extraction,
`093edb00a`); this config/polling threshold is not that.

## Verification (worktree HEAD on `kanban/aiify-rm-06d89-phase-6128`)

- `dfb671f09` is an ancestor of HEAD ✓
- `_load_anomaly_cfg` present in `tools/monitor/log_analyzer.py` ✓
- `_load_anomaly_cfg` present in `icdev/tools/monitor/log_analyzer.py` mirror ✓
- `anomaly_detection:` block in `args/monitoring_config.yaml` (L91, zscore + mad) ✓
- Exact-file siblings 6125 / 6126 / 6127 / 6129 already closed as dups of `dfb671f09` ✓
- Same-package siblings 6130 / 6131 / 6132 / 6133 already closed as dups of `dfb671f09` ✓

No competing implementation authored. Card moved to `done` with
`bypass_verification: true` + `bypass_reason` (no new code — documented duplicate of
`dfb671f09` and of exact-file siblings 6125 / 6126 / 6127 / 6129).

## Test subtask `-d4` (lint + mail.py anomaly test) — 2026-06-06

The `-d4` subtask asked to lint the temp clone and run `mail.py`'s anomaly-detection
test suite. Findings:

- The clone `aiify_git_zwu66zfu` is present on disk but **gutted** — only a `.git`
  directory, empty working tree. `src/paperless_mail/mail.py` is not checked out, so
  there is nothing in the clone to lint, and no `mail.py` anomaly implementation was
  ever authored in ICDEV (the opp was a no-code dup).
- Lint/test were therefore run against the **real ICDEV target** the opp maps to,
  MONITOR `log_analyzer.py`:
  - `ruff check tools/monitor/log_analyzer.py icdev/tools/monitor/log_analyzer.py` →
    **All checks passed** (no syntax errors).
  - `pytest tests/test_log_analyzer_anomaly.py` → **23 passed**.

Verdict: nothing to fix. No syntax errors anywhere relevant; the anomaly-detection
code this opportunity maps to is green.
