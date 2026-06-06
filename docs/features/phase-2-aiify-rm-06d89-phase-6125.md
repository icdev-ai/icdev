# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6125

**Opportunity:** 6125 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**External target:** `src/paperless_mail/mail.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`, reaped before task ran)
**Model recommendation:** claude-haiku-4-5-20251001 · composite 0.5326

## Disposition: DUPLICATE — closed, no new code

Exact-family sibling of the same-scan `paperless_mail/*` opportunities already
closed as duplicates of `dfb671f09`:

- 6130 — `src/paperless_mail/oauth.py` (`b99d8b471`)
- 6132 — `src/paperless_mail/tasks.py` (`bfc7904fc`)
- 6133 — `src/paperless_mail/views.py` (`8eb75e46d`)

All four are the identical generic `hardcoded_threshold → anomaly_detection`
pattern with `function_name` `<unknown>`, emitted against the ephemeral
paperless-ngx clone (`aiify_git_zwu66zfu`) that the scanner clones and reaps every
pass.

`src/paperless_mail/mail.py` is the paperless `MailDocumentParser` / mail-fetch
module. Its **non-anomaly** augmentation was already landed: the email-envelope
extractor (aiify-opp-6100, `093edb00a`) is the DIC NLP-extractor analog of this
file's email header parser, already present in
`tools/document_intelligence/ingest_orchestrator.py`. Its **anomaly** concern —
runaway / pathological ingest cost on incoming mail attachments — is already
covered by the DIC ingest-workload anomaly detector (aiify-opp-6097,
`bb95541b8`, `assess_ingest_workload`). The residual generic config-threshold
anomaly maps to the default MONITOR `log_analyzer` config-driven anomaly layer,
not a new DIC detector, per the established `paperless_mail/*` mapping. The
canonical work `dfb671f09` already replaced hardcoded thresholds with a
configurable `anomaly_detection` block (legacy z-score + robust MAD modified
z-score) — precisely the modernization this opportunity calls for. No distinct,
uncovered anomaly semantic remains for `mail.py`.

## Verification (HEAD on branch irad/feature)

- `dfb671f09` IS an ancestor of HEAD.
- Same-scan `paperless_mail/*` siblings 6130 / 6132 / 6133 already closed as dups
  of `dfb671f09` (`b99d8b471` / `bfc7904fc` / `8eb75e46d`, all ancestors of HEAD).
- mail.py's non-anomaly analog already shipped: envelope extractor 6100
  (`093edb00a`); workload-anomaly analog already shipped: 6097 (`bb95541b8`,
  `assess_ingest_workload` in `ingest_orchestrator.py`).
- `_load_anomaly_cfg` + z-score / modified-z-score (MAD) present in
  `tools/monitor/log_analyzer.py` and the `icdev/` mirror.

## Board action

Moved to `done` with `bypass_verification: true` + `bypass_reason` (no new code —
disposition is a documented duplicate of `dfb671f09` and of same-scan
`paperless_mail/*` siblings 6130 / 6132 / 6133).
