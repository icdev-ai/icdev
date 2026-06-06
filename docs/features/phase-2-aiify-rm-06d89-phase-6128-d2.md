# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6128-d2

**Parent opportunity:** 6128 (scan_id 43, roadmap `rm-06d89040cf`)
**Subtask:** "Create `anomalies.yaml` config file for threshold settings"
**Requested target:** `src/paperless_mail/anomalies.yaml` in temp clone `aiify_git_zwu66zfu`
**Pattern:** `hardcoded_threshold` → `anomaly_detection`
**Disposition:** Closed as **duplicate** — no code authored (subtask of the already-closed
duplicate opp 6128).

## Rationale

This `-d2` card is a decomposed subtask of opportunity **6128**, which is already closed
as a **duplicate of `dfb671f09`** (MONITOR `log_analyzer.py` config-driven
anomaly-detection) — see `docs/features/phase-2-aiify-rm-06d89-phase-6128.md` on `main`.

The subtask asks to write `anomalies.yaml` into `src/paperless_mail/` of an **external**
paperless-ngx shallow clone (`aiify_git_zwu66zfu`) that the AI-ify engine clones, scans,
and reaps. That directory **no longer exists** (only a bare `.git` remnant remains), and
writing a config file into a throwaway external open-source clone produces no ICDEV
artifact — nothing imports it, nothing commits it, it is deleted on the next scan.

The real "migration path for the hardcoded threshold" already exists in the **analogous
ICDEV internal subsystem** (MONITOR), where inline z-score / error-rate constants were
replaced with a config-driven `anomaly_detection` block. The ICDEV equivalent of the
requested `anomalies.yaml` **is** `args/monitoring_config.yaml` → `anomaly_detection:`
(zscore + robust MAD), shipped under `dfb671f09`. No separate per-file YAML in an
external clone is warranted or possible.

## Verification (worktree HEAD on `kanban/aiify-rm-06d89-phase-6128-d2`)

- `dfb671f09` is an ancestor of HEAD ✓
- `anomaly_detection:` block present in `args/monitoring_config.yaml` (L91) ✓
- Requested path `…/aiify_git_zwu66zfu/src/paperless_mail/` does not exist (clone reaped) ✓
- Parent opp 6128 already closed as dup of `dfb671f09` (commit `cae927830`) ✓
- Exact-file siblings 6125 / 6126 / 6127 / 6129 already closed as dups of `dfb671f09` ✓

No competing implementation authored. Card moved to `done` with `bypass_verification: true`
(no new code — documented duplicate; config-migration path already lives in
`args/monitoring_config.yaml` via `dfb671f09`).
