<!-- CUI // SP-CTI -->
# task-det-27a4838a70 — `needed_a_human` finding for task-det-9a62ee81a7, resolved

- **Task:** task-det-27a4838a70 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `27a4838a70e8cfb9`)
- **Subject:** task-det-9a62ee81a7 — PR #2123, five `resume` cycles, escalated
- **Date measured:** 2026-09-06, against the live PG board

## Verdict

**The escalation was CORRECT, the PR was genuinely stuck, and the repair was one
button.** PR #2123 adds ONE markdown file (+160/-0,
`docs/audits/task-det-9a62ee81a7-needed-a-human-resolution.md`) and nothing else.
Its `Test Shard 2 of 4` check reports FAILURE. **Every test in that shard passed**
— `4351 passed, 1 skipped in 661.32s (0:11:01)`. The only red step in the job is
`actions/upload-artifact@v4`, which exhausted five retries against GitHub's
artifact service:

```
18:55:14 Attempt 1 of 5 failed with error: Request timeout: /twirp/github.actions.results.api.v1.ArtifactService/CreateArtifact. Retrying request in 3000 ms...
18:55:22 Attempt 2 of 5 failed ...
18:55:33 Attempt 3 of 5 failed ...
18:55:48 Attempt 4 of 5 failed ...
18:56:04 ##[error]Failed to CreateArtifact: Failed to make request after 5 attempts: Request timeout: /twirp/github.actions.results.api.v1.ArtifactService/CreateArtifact
```

This is the **ninth** instance of this card class and the **third** that was not
moot. It is the first that is **recursive** — the subject `task-det-9a62ee81a7`
is *itself* a `needed_a_human` card (the seventh instance, for `mfx-mrg-01`), so
this is a recovery card filed against a recovery card's own record PR. And it is
the first whose cause is neither a sibling-conflict train, nor a stale `args/`
mirror, nor "CI never fired": **a required check went red on a transient network
timeout while uploading EVIDENCE, on a branch containing no code.**

## The ledger

`pr_watcher` rows naming the subject, lifetime: **224 `wait`, 5 `resume`, 1
`escalate`** — and **no `rebase`, no `rebase_failed`, no `merge`.** Every row
carries `"classification": "ci_failed"`. There was never a conflict to resolve.

| # | at (UTC) | action | reason |
|---|---|---|---|
| 1 | 19:26:22 | `resume` | injected resume context (cycle 1) |
| 2 | 19:37:54 | `resume` | cycle 2 |
| 3 | 19:48:02 | `resume` | cycle 3 |
| 4 | 19:58:32 | `resume` | cycle 4 |
| 5 | 20:26:27 | `resume` | cycle 5 |
| 6 | 20:28:00 | `escalate` | **resume cap reached (5/5) — manual intervention required** |

Read the *escalate* row's own reason, not the card's quoted `reason` (which is
the last **attempt's**, `injected resume context`). Unlike the eighth instance
(#2088, "CI never fired; re-trigger exhausted") this is a true resume-cap
escalation.

## The five resumes produced nothing, and could not have

PR #2123: `createdAt` **18:33:05Z**, `updatedAt` **18:33:05Z**, head
`cb0a089c72155a101eea6b8039312853a0636521`, ONE commit. The five resume sessions
ran 19:26–20:26, an hour after creation, ~10 minutes apart. **`updatedAt` never
moved off `createdAt`, so not one of the five produced a commit.** The loop
achieved literally nothing, five times.

It could not have. Three independent reasons:

1. **There is no code on the branch.** The diff is a single new `.md` file. There
   is no implementation to repair and no test to fix.
2. **The shard's tests all passed.** A worker asked to fix "Test Shard 2 of 4" is
   being pointed at 4351 green tests.
3. **The resume context names the CHECK, never the failing STEP.** The injected
   text reads `Failing checks: Test Shard 2 of 4, Test` and stops there. Nothing
   in it says the failure was an artifact upload, so the worker cannot even
   discover that the branch is not at fault without fetching the job log itself.

## The failure is provably not this branch's, and provably not a gate

Three measurements, all from the same job (`101355485802`, run `33984419940`):

1. **The suite passed:** `4351 passed, 1 skipped in 661.32s`.
2. **The gate that consumes the same XML passed, AFTER the upload failed.** The
   `Skip census (runtime)` step runs `skip_census.py --from-report
   .tmp/ci-junit-shard-2.xml --check` at 18:58:13 — 2m09s *after* the upload gave
   up — and reports `81 registered (ceiling 81), 0 unregistered`. It reads the
   **local** path, never the artifact, so it is structurally unaffected by
   whether the upload lands. That is the measured proof that the upload is not a
   gate.
3. **It was one request, not an outage.** `ci-junit-shard-2` is the **only**
   missing artifact of the run's 20. Shards 1, 3 and 4 uploaded their JUnit, and
   every Playwright bundle uploaded — including four over 14 MB each.

## It is a recurring class, not a one-off — 2 in 40

Replaying the last **40 failed `ICDEV CI` runs** and grepping every failed job's
log for `Failed to CreateArtifact`:

| run | job | tests | CreateArtifact failures |
|---|---|---|---|
| `33984419940` | `Test Shard 2 of 4` (**required**) | `4351 passed, 1 skipped` | 1 |
| `33820506839` | `E2E Shard 2 of 4` (not required) | `210 passed`, `2 passed` | 2 |

Both are the same shape: **every test passed and the job went red on an evidence
upload.** Only the first blocked a merge and drove an escalation, because only
the first is a required check. Re-derive with the script in `## Re-derive`.

## The root cause, and the repo's own rule for it

`.github/workflows/icdev-ci.yml`, `Upload shard JUnit XML`, carries
`if: always()` and `if-no-files-found: warn` but **no `continue-on-error`** — so
a network timeout on an evidence upload fails the job, and with it the required
`Test` check.

CLAUDE.md already states the governing principle for this exact artifact
(crx-test-07):

> this directory governs how FAST the gate runs, never what it COVERS, and a
> `Test` that goes red over a malformed JSON file is a check people learn to
> bypass.

That rule was applied to a malformed snapshot and not to the upload that produces
it. This change applies it to the upload. Note what is *not* being changed: the
step already tolerates a **missing file** (`if-no-files-found: warn`), so the only
behaviour that moves is the **network** failure.

The consumer degrades as designed. `.github/workflows/shard-timings.yml` asks for
each `ci-junit-shard-<k>` by name and "skips the ones that do not exist", erroring
only when **zero** survive; snapshots merge newest-`generated_at`-wins per path,
so the files in the absent shard keep their previous weights. A missing shard-2
artifact costs one week of freshness for those files' timings — it cannot drop a
test, and it cannot weaken a gate.

## What was done

1. **`gh run rerun --failed` on run `33984419940`.** This is the human act the
   escalation asked for, and no automated path attempts it for this failure mode:
   `pr_watcher` has `max_ci_retriggers_per_pr` for the *empty-rollup* case, but a
   `ci_failed` classification goes straight to LLM resume. Five LLM sessions were
   spent where one re-run was the answer.
2. **`continue-on-error: true` on the `Upload shard JUnit XML` step**, so an
   evidence upload can no longer turn a fully-green shard into a red required
   check.

## Not fixed here, and named

- **The other evidence uploads carry the identical exposure, and one has
  MEASURABLY flaked.** `Upload red-first proof` (`test-gates`, a **required**
  job) is the same shape — `if: always()`, `if-no-files-found: ignore`, no
  `continue-on-error` — and has not been observed to flake. The four E2E uploads
  (`route-smoke`, `playwright-report`, `playwright-artifacts`,
  `dwo-vv-screenshots`) *have*: run `33820506839` above. They sit in a job that
  is deliberately **not** required (crx-test-06 — `E2E (Playwright)` promotion is
  `blocked`), so their red blocks no merge and drives no escalation. Only the step
  that demonstrably caused *this* escalation is changed here; sweeping the rest is
  a separate card. Deliberately not folded in: `red-first-proof` is *the record
  that the RED was observed*, so making its upload non-fatal is a evidence-
  retention decision that deserves its own argument, not a drive-by.
- **`pr_watcher` cannot tell an infrastructure failure from a code failure.** A
  `ci_failed` classification spends the LLM resume budget without ever asking
  whether the failing step was a test. A cheap discriminator exists — the job log
  carries `##[error]Failed to CreateArtifact` next to a passing pytest summary
  line — but `tools/ci/pr_watcher.py` is a `protected_path` (kpr-watch-05), so
  folding a change to it into this card would stall the card behind a hand merge.
  Same reasoning as kpr-watch-12 for the eighth instance.
- **Shard balance looks wrong and is out of scope.** Shard 2 ran 4351 tests in
  661s while shard 1's JUnit artifact is 652 bytes. Both were green; whether the
  crx-test-07 bin packing has degraded is its own measurement.

## Why the derivation still reports the subject

It must, and this is unchanged from every prior instance. `summarize_recovery`
gives `escalate` priority over any later `merge`, and an entry leaves only when
its last `pr_watcher.resume`/`rebase` row falls outside the 24h window. Newest
`resume` row **2026-09-05 20:26:27.871819Z**, so the finding cannot clear before
**2026-09-06 20:26:27Z**, and the `detector_findings` row flips to `cleared` on
the first `detector_findings_reflex` cycle (6h) after that. Nothing here touches
the detector, its threshold or its window.

Closing this card inside that window is safe: `fb989f6ad` (#2057,
`earliest_clear_at`) is on `origin/main`, so a terminal card before the clear time
is HELD (`held_closed_early`) rather than re-filed as `-r2`. Like every instance
since #2114 this lands as an ordinary PR — no `hold` label, no `scheduled_at`
deferral.

## Re-derive

```bash
python - <<'PYEOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000)
       if e['task_id'] == 'task-det-9a62ee81a7'])
PYEOF

# the incident job: a passing suite and a failed upload, side by side
gh api repos/icdev-ai/icdev/actions/jobs/101355485802/logs |
  grep -E "passed|CreateArtifact"

# the only missing artifact of the run's 20
gh api repos/icdev-ai/icdev/actions/runs/33984419940/artifacts \
  -q '.artifacts[].name' | sort

# the 2-in-40 survey
gh run list --workflow "ICDEV CI" --status failure --limit 40 \
    --json databaseId -q '.[].databaseId' | while read -r rid; do
  gh api "repos/icdev-ai/icdev/actions/runs/$rid/jobs?per_page=100" \
     -q '.jobs[] | select(.conclusion=="failure") | "\(.id)\t\(.name)"' |
  while IFS=$'\t' read -r jid jname; do
    log=$(gh api "repos/icdev-ai/icdev/actions/jobs/$jid/logs" 2>/dev/null)
    n=$(printf '%s' "$log" | grep -c "Failed to CreateArtifact")
    [ "$n" -gt 0 ] && echo "$rid $jid $jname CreateArtifact_failures=$n"
  done
done
```
