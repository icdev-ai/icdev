<!-- CUI // SP-CTI -->
# task-det-e6ee0b2393 — `needed_a_human` finding for mfx-sib-02, resolved

- **Task:** task-det-e6ee0b2393 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `e6ee0b239376f4bb`, fingerprint
  `needed_a_human`)
- **Subject:** mfx-sib-02 — PR #2091, 5 resumes, 30 failed rebases, escalated
- **Date measured:** 2026-09-05, against the live PG board and the forge

## Verdict

**Not moot.** The escalation was CORRECT, the branch was genuinely broken in two
independent ways, and both were still reproducing while this card was worked —
the newest `rebase_failed` fired at 18:51:21Z, minutes before the repair began.

| Criterion | Before | After |
|---|---|---|
| PR #2091 `mergeable` | `null` / `mergeable_state: unknown`, 30 `rebase_failed` rows | `true` |
| the 15 nav page tests | 30 failures, CI reported **3** | 246 passed |
| branch vs `origin/main` | 29 behind, conflicting | merged, fast-forward push |
| `detector_findings` `e6ee0b239376f4bb` | `active`, `seen_count=3` | clears by ageing out (below) |

Two independent blockers, and NEITHER was reachable by an LLM resume:

0. **30 test failures that CI could only ever report 3 of** — the shards run
   `pytest -x`, so each aborted at its first red file.
1. **A mirror-backfill merge conflict** in
   `icdev/data/claude_bootstrap/claude/commands/start.md`, retried 30 times
   across 16 base eras because the rebase budget is per base era.

## §0 — THE THIRTY FAILURES CI REPORTED AS THREE

mfx-sib-02 replaces the hand-written Compliance active-path list in `base.html`
with a generated `{% set compliance_active_paths = [...] %}` block. Fifteen
page tests asserted the path as a literal **on the trigger line**:

```python
trigger = <the line holding "Compliance ▾">
assert f"'{NEW_PATH}'" in trigger
```

That is true only while the list lives inside the `<a>` tag. All 15 files went
red on both `base.html` copies — **30 failures**, re-derived here:

```bash
python -m pytest tests/test_bdc_*_page.py tests/test_sdc_*_page.py -q
#  ->  30 failed, 198 passed        (at 586a9a580)
```

**CI reported THREE.** `icdev-ci.yml:528` (merged tree) runs `pytest "${CI_TESTS[@]}"
--tb=short -x`, so shards 2, 3 and 4 each aborted at their first failing file
and shard 1 held none of the fifteen. All 15 files were already gated at the
branch tip (17 `rmf-ui-*` fragments in `args/ci_test_files/core.d/`, identical
at `586a9a580` and on main), so this is not a gating gap — it is `-x`.

### Why five resumes could not converge, measured

| resume | at (UTC) | failing checks it was handed | branch tip after |
|---|---|---|---|
| 1 | 05:16:27 | `Lint` | `a6657ec2c`, then `586a9a580` |
| 2 | 05:43:42 | `Test Shard 2 of 4, 3 of 4, 4 of 4, Test` | unmoved |
| 3 | 05:54:05 | *identical* | unmoved |
| 4 | 06:05:13 | *identical* | unmoved |
| 5 | 06:16:19 | *identical* | unmoved |
| escalate | 06:17:55 | `resume cap reached (5/5) — manual intervention required` | — |

The branch tip has been `586a9a580` since 05:28:39Z. **Four of the five resumes
moved the head sha not at all** — the loop achieved nothing after the first.
Each was handed three file names out of fifteen, and the same three every time,
because `-x` re-truncates at the same place on every run. Even a resume that
had fixed all three would have been handed three more on the next poll, against
a budget of five. The escalation is the right verdict; what is wrong is that
the evidence it escalated on described 10% of the defect.

### The fix follows the derivation instead of pinning the old shape back

Each of the 15 now asserts (a) the trigger still READS the derived list, and
(b) the path is in the generated block, read through the generator's own
accessor `nav_paths.read_block(base_html, nav_paths.NAV_MARKER)` — never a
second copy of "where the block is". The failure message names
`nav_paths.REGEN_HINT`, so the next card that adds a compliance page is told to
run `--write` rather than hand-append, which is the whole point of the card.

**Behaviour is unchanged, and that was measured before the tests were touched**:
all 15 `NEW_PATH` values are present in the derived block, so not one page lost
its highlight. Both halves of the new assertion were proven to still
discriminate by breaking the template and watching them go red:

| probe | result |
|---|---|
| delete `'/boundary/ato-compliance'` from the generated block | RED, message names the regen command |
| repoint the trigger off `compliance_active_paths` | RED, on the other assertion |

`base.html` was restored byte-identically after each probe.
`test_bdc_compliance_hub_page.py` carried one extra assertion (its `OLD_PATH`
is still highlighted, because its 301 is live); it moves to the block too.

## §1 — THE CONFLICT: A MIRROR BACKFILL COLLISION, NOT A SIBLING APPEND

One conflicted file, `icdev/data/claude_bootstrap/claude/commands/start.md`.
The canonical `.claude/commands/start.md` merged CLEAN. That asymmetry is the
whole defect, and it is the same shape mfx-sib-03 recorded one file over.

Measured, not inferred — line counts and the presence of the `8b. ICDEV[FT] and
ICDEV[RT]` block at three refs:

| ref | `.claude/…/start.md` | `icdev/data/…/start.md` |
|---|---|---|
| merge base `d5591a1d0` | 313 lines, 8b present | **271 lines, 8b ABSENT** |
| `8ec7c2b85` flx-twin-01 (#2103) | 313, present | 313, present |
| `586a9a580` mfx-sib-02 | 317, present | 317, present |

The mirror was **42 lines stale** before either card existed. Two branches then
backfilled that same region independently — flx-twin-01 took it 271 → 313
without touching the canonical copy, mfx-sib-02 took it 271 → 317 alongside its
own generated block. Both rewrote the same region, so this card's block
anchored into freshly-rewritten text and git could not place it. The canonical
copy never collided because only one side ever appended to it.

**Resolution: take ours.** main's side of the hunk is the hand-written
`- Pages:` line this card exists to REMOVE — it is not a sibling addition to
union with. The proof that the resolution is right rather than merely chosen is
that after it, `.claude/commands/start.md` and its `icdev/data/` twin are
**byte-identical**.

Nothing was dropped and the derivation is current for the MERGED tree:

```bash
python tools/dashboard/nav_paths.py --check --nav-only     # exit 0
python tools/dashboard/nav_paths.py --check --pages-only   # exit 0, 16.2s, the real url_map probe
```

No regeneration was needed. main's hand-written line held 698 back-ticked
tokens against the generated 710, and 401 of the 698 are absent from the
derived list — every one an API endpoint
(`/opportunities/<opp_id>/compliance/batch`), a stale placeholder spelling
(`/projects/<id>` vs the url_map's `/projects/<project_id>`), or a path the
url_map spells with a converter (`/security/zig/pillar/user` vs
`/security/zig/pillar/<pillar_slug>`). That the hand-written line was a mixture
of pages, API routes and stale spellings is this card's own thesis, restated by
the merge.

### The retries were per BASE ERA, and the era advanced on unrelated commits

**30 `rebase_failed` rows across 16 distinct base shas**, 08:32:23Z → 18:51:21Z,
every one carrying the identical reason (`Could not apply 0497713eb...`):

```
8ec7c2b85 2x   7891832c7 2x   15fb4dfd8 2x   2d2b2cc9a 2x
29fd944a6 2x   39986a0bf 2x   3a8ce8cd7 2x   28c22c52e 2x
9192f7c45 2x   df1dae7fb 1x   0056318b3 2x   2c642317a 2x
e9f9674e6 1x   614617e82 2x   b4cdd2350 2x   f40935be5 2x
```

Only ONE of those sixteen landings (`8ec7c2b85`, flx-twin-01) touched the
conflicted file. The other fifteen are floci cards, a CI ratchet and audit
records. `max_rebase_attempts_per_task` is budgeted per base era, so every
unrelated commit on a busy board refunds the budget and the watcher retries a
conflict whose cause has not moved. This is **one unresolvable collision
retried sixteen times**, and in the audit log it is indistinguishable from a
real conflict train. Same finding as mfx-sib-03; recorded again because it
recurred, unchanged.

### A live forge outage sits underneath all of it

130 `pr_watcher.wait` rows, of which **104 are `fetch failed: gh pr view
failed: exit=1 stderr=GraphQL: API rate limit already exceeded`**, continuous
from 13:20:47Z to 19:24:47Z at ~34s intervals. `gh api rate_limit` reported
5000/5000 remaining on `core` and `graphql` throughout, and REST calls
(`gh api repos/…/pulls/2091`) answered normally, so the limit is on the
GraphQL path `gh pr view` takes and not on the token's budget. Every forge read
in this record was therefore taken through REST. The outage is a fleet event —
it explains why the watcher was making no progress on ANY PR this evening, and
it is not attributable to this branch.

## What was done

1. Claimed the task first (`cli.py --claim mfx-sib-02`, keeper
   `cli-claim-mfx-sib-02-t99f3688e`) so the runner could not race the repair.
2. Worktree on `kanban/mfx-sib-02` under the sanctioned `cli` root;
   `git merge origin/main` (never rebase — the push would be a force-push,
   which the hook refuses).
3. Resolved the single hunk as "ours", verified canonical and mirror are
   byte-identical, verified no conflict markers in any file the merge touched
   and `git diff --cached --check` clean. Three files on `origin/main` carry
   pre-existing `Updated upstream` markers —
   `docs/tasks/task-930837d9e2-d4-completion.md`, an `.iqe` query, and
   `icdev/tools/compliance/component_names.py`. Confirmed present on main,
   untouched here, not this card's business.
4. Fixed the 15 test files; proved both new assertions still discriminate.
5. Gates run locally on the merged tree, all clean: `nav_paths --check` (both
   halves), `gated_test_list --check` (630 targets), `census_growth --check`
   (+0/-0 on all three closed censuses), `skip_census --check`,
   `red_first_gate --gate` (16/16 discriminating), `isolation_run --run`
   (18/18 pass alone), `mirror_parity --gate` (2/2 in parity),
   `undeclared_import_census --changed --check` (0 unregistered), and
   `ruff check` on the changed set.
6. Pushed as a fast-forward only (`586a9a580..65a08e4c0`). No force-push, no
   rebase, no branch deleted. PR #2091 went `mergeable: null`/`unknown` ->
   **`mergeable: true`**.

`coherence_checker --tier fast --gate` reports one FAIL,
`capability_liveness` (`mcp_dispatch_tool` 468 over a 467 budget,
`verified_claim` 1 over budget). It is **pre-existing and not this card's**:
that check measures the LIVE board rather than the diff, it is documented as a
standing breach in CLAUDE.md, it reproduces identically on a main-based
checkout, and no CI job runs it. No budget, threshold or census ceiling was
raised.

No detector, threshold or window was touched.

## The gap this leaves, named and NOT closed here

`-x` on the shards is what turned a 30-failure branch into a 3-failure report,
and it is the reason five resumes could not converge. Removing it is a real
candidate, and the tree is already inconsistent about it — the `Test (Windows)`
tier runs `pytest "${WIN_TESTS[@]}" -v --tb=short --durations=15` with **no
`-x`** (`icdev-ci.yml:696`), while every shard of the gated tier carries it
(`:528`). The crx-test-05 spike also ran its 4-shard characterisation
deliberately "WITHOUT -x so one pass surfaces every order-dependent file rather
than one per shard" (`:439`) — that comment describes the SPIKE, not the
shipped job, and is cited only as evidence that a no-`-x` shard run is
feasible. (All three line numbers are read from the MERGED tree, i.e. what main
becomes when #2091 lands; on main today they are 40 lines lower.)

But `-x` on a sharded gate also buys the fast abort that keeps a red branch off
the four shard runners for the full ~12 minutes, and changing it is a CI-cost
decision that needs its own survey of how often a shard's first failure is its
only failure. Recorded here as the evidence for that card, not applied.

## Closing this card — the finding clears by AGEING OUT, not by the merge

`escalate` outranks any later `merge` (rem-hyg-16: "a merge recorded AFTER an
escalation is a human's merge"), so landing PR #2091 cannot clear this finding.
`_recovery_rows()` reads a 24-hour window over
`pr_watcher.{rebase,resume,escalate,merge}`, and this finding's newest counted
row is the escalation at **2026-09-05 06:17:55Z**. The derivation therefore
still reports `mfx-sib-02` today and stops reporting it after **2026-09-06
06:17:55Z**, at which point the next MEASURABLE `detector_findings_reflex`
cycle marks `e6ee0b239376f4bb` `cleared`.

`earliest_clear_at` for a recovery finding is last-attempt + 24h, so a terminal
card before then is HELD rather than re-filed as `-r2` (task-f05d2bc8d1). This
record is landed as an ORDINARY PR — no `hold` label, no `scheduled_at`
deferral — because the `hold` label alone is ignored by `pr-watcher.yml`.
