<!-- CUI // SP-CTI -->
# task-det-21a987e887 — `needed_a_human` for dwr-collab-01: the escalation was RIGHT, it fired for a reason unrelated to what was actually wrong, and the real blocker was a gate correctly refusing a correct repair

- **Task:** task-det-21a987e887 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `21a987e887c69c8f`)
- **Subject:** dwr-collab-01 — icdev#2195, 1 `pr_watcher.resume` + 3
  `pr_watcher.rebase` (the third landed 23 minutes after this card dispatched),
  escalated after the FIRST resume
- **Date measured:** 2026-09-09 06:47–07:2x UTC, against the live PG board,
  `origin/main` and the `icdev-ai/icdev` forge

## Verdict

**This one had real work left, and it is the first of the 29 recorded instances
where the platform's own disposition rule says so up front.**

```
python -m tools.kanban.detector_findings --records
card    dwr-collab-01   active   the escalation is the newer of the two rows
```

autonomy-act-04's conjunction — a `pr_watcher.merge` newer than the newest
`pr_watcher.escalate` **and** the subject closed on the board — is not satisfied
in either half: there is no `merge` row for dwr-collab-01 at all, and
`kanban_tasks.dwr-collab-01` reads `pr_opened`. Compare the two siblings filed
in the same batch, which both read `record` and had nothing left to land:

| subject | disposition |
|---|---|
| dwr-ws-02 | `record` — merge 2026-09-08T21:06:39Z after escalate 11:09:19Z, subject `done` |
| dwr-anchor-04 | `record` — merge 2026-09-08T23:11:58Z after escalate 03:02:44Z, subject `done` |
| **dwr-collab-01** | **`card` — the escalation is the newer of the two rows** |

So this is not instance 4/5/6/8/11's "nobody fixed it and the watcher merged it
anyway". #2195 was open, red and going nowhere for **seven hours** when the card
dispatched. It merged at 07:50:08Z once the two real blockers were removed by
hand — see **Outcome** at the end, where the disposition flips to `record`.

## The timeline

| when (UTC) | what |
|---|---|
| 09-08 22:48:57 | `agent_execution_started` — first worker run |
| 09-08 23:32:03 | `agent_execution_completed` |
| 09-08 23:37:32 | lease `kanban:task:dwr-collab-01` taken by `genesis-daemon-39996`, TTL 3600s |
| 09-08 23:37:36 | `agent_execution_started` — second run |
| 09-08 23:46:33 | `pr_watcher.resume` #1 — *"first injection for this task — nothing prior to judge"* |
| 09-08 23:56:57 | **`pr_watcher.escalate`** — *"resume undelivered after 1 attempt(s) — 1 pr_watcher message(s) still unread in the queue"* |
| 09-09 00:37:32 | lease TTL expires (holder pid 39996 gone; the live daemon is 17924) |
| 09-09 01:44:24 | `pr_watcher.rebase_refund` — *"forge reported CONFLICTING but the merge is clean"* (a `phantom`, refunded correctly) |
| 09-09 01:44:43 | `pr_watcher.rebase` → head `bb0e469fe` |
| 09-09 02:44:16 | `pr_watcher.rebase` → head `a87ce1c3c` — **newest counted attempt** |
| 09-09 02:45:34 | finding `21a987e887c69c8f` first seen; card filed |
| 09-09 00:39–06:43 | 335 × `pr_watcher.wait` — *GraphQL API rate limit already exceeded* (see below) |
| 09-09 06:27:12/16 | `husk_sweep` removes `.tmp/worktrees/dwr-collab-01` (323.6 MB, 24,449 entries), `remove.unconfirmed` |
| 09-09 06:47:10 | **card dispatched** |
| 09-09 07:10:38 | `pr_watcher.rebase` → head `5fa976aba` — *"rebased a stale branch onto main"*, 3 commits, **while this card was being worked** |

**Clear-by** = newest counted attempt + `window_hours=24`. At dispatch that was
**2026-09-10 02:44:16Z**, already 19h57m out — the widest margin of any instance
recorded so far, and on the wrong side of it.

**But the clear-by is a MOVING TARGET, and this card watched it move.** At
07:10:38Z — 23 minutes after dispatch — the watcher rebased #2195 again (it was
26 commits behind `main`, over `max_behind_commits: 10`), which is a fourth
counted attempt and pushed the clear-by out to **2026-09-10 07:10:38Z**:

```
attempts 3, at 2026-09-09 02:44:16   ->   attempts 4, at 2026-09-09 07:10:38
```

That is not a defect in the detector, but it is a property of this finding class
worth stating plainly: **`behind_main` rebases are counted attempts, so a stale
open PR renews its own recovery finding every time the watcher touches it.** The
finding cannot age out until 24h after the branch stops being rebased, which in
practice means 24h after it MERGES. A reader who computes a clear-by from
today's rows and then closes the card against that date is computing against a
number the watcher can move at any time. Take the clear-by as a floor, never a
date.

Landing this record as an ordinary PR is nonetheless safe on #2057's ground:
`fb989f6ad` (`earliest_clear_at`) is confirmed an ancestor of `origin/main`, so
a terminal card inside the window is HELD (`held_closed_early`), not re-filed as
`-r2`. Neither `rebase_refund` nor `wait` extends the clear-by — both
`claims._recovery_rows` and `detector_findings.recovery_rows` fetch only
`pr_watcher.{rebase,resume,escalate,merge}`.

## The escalation was correct — and it fired for a reason that had nothing to do with the blocker

The watcher did **not** escalate because it had exhausted five attempts on a
stubborn branch. It escalated after **one**, on kpr-watch-13's rule:

```
python -m tools.ci.resume_delivery --task dwr-collab-01
dwr-collab-01: undelivered -- 1 pr_watcher message(s) still unread in the queue
```

`_send_resume` appends a line to `.tmp/kanban/messages/dwr-collab-01.jsonl` and
the only drain, `check_message_queue`, runs solely while a dispatch is in
flight — which by definition is not the state of a task whose worker exited at
23:32 and whose PR is open. So the injection was never read, the watcher
measured that, and it declined to spend four more attempts nobody could read.
**That is the correct call and it is the system working as kpr-watch-13
intended.**

But it says nothing about *why* #2195 was not merging, and the card's stock
sentence — *"the branch it is asked to repair has no defect IN it"* — is wrong
here. The branch had a defect in it. A delivered resume could not have fixed it
either, for a different reason: the defect was in the branch's relationship to a
**gate**, not in its code, and the repair is a config entry an LLM asked to "fix
the failing test" would have been actively steered away from.

## What was actually blocking it

`gh pr view 2195`: `OPEN`, `MERGEABLE`, `mergeStateStatus: BLOCKED`, not a
draft, no requested changes — 16 SUCCESS, 3 SKIPPED, **2 FAILURE**: `Test Gates`
and `Test`.

`Test` is an aggregator (crx-test-05) over `test-gates` + the four shards, and
**all four `Test Shard k of 4` jobs passed**. So the two red checks are one
failure, in `Test Gates`:

```
Red-first proof vs origin/main (fae2b05c4f1b): 5 changed test file(s)
  — 2 discriminating, 1 not, 0 indecisive, 0 exempt, 2 not applicable.
[not_discriminating] tests/docmod/test_standards_catalog.py: the test PASSES
  unchanged against the merge-base tree — it asserts CURRENT behaviour rather
  than REQUIRED behaviour
##[error]Process completed with exit code 1.
```

**The gate is right and the branch is right, and that is the whole finding.**
The file carries a drive-by repair of a test that is **red on `main` today**:

```
$ git log --oneline -1            # 0f16cfb7d, origin/main
$ python -m pytest tests/docmod/test_standards_catalog.py::test_pages_line_updated -q
>       assert "`/standards-catalog`" in start
E       AssertionError
1 failed in 1.49s
```

The cause is mfx-sib-02. That `Pages:` line in `.claude/commands/start.md` is no
longer hand-written — `tools/dashboard/nav_paths.py` derives it from the live
Flask `url_map`, and the canonical rule for this blueprint carries a **trailing
slash**: `tools/doc_modernization/blueprint.py:30` constructs `docmod_bp` with
`url_prefix="/standards-catalog"` and its index is `@docmod_bp.route("/")`, so
the rule is `/standards-catalog/` and a bare `/standards-catalog` answers 308.
The generator therefore writes `` `/standards-catalog/` ``, which does not
contain the token `` `/standards-catalog` `` the old assertion looked for. Both
sides verified directly:

| tree | `start.md` token | `test_pages_line_updated` |
|---|---|---|
| `origin/main` @ 0f16cfb7d | `` `/standards-catalog/` `` | **1 failed of 8** |
| `kanban/dwr-collab-01` | `` `/standards-catalog/` `` | 8 passed |

So the branch's assertion is **stricter** than the substring it replaces, not
weaker — it pins the canonical rule, and a route that changed shape or lost its
trailing slash would still fail it. And precisely because the corrected
assertion is true of the merge-base tree as well, red-first is **correct** to
report it non-discriminating: the defect was in the TEST, so there is no commit
at which the new assertion would have been red.

This is the `passed_at_birth` / assertion-repair shape, and it has a sanctioned
door — CLAUDE.md: *"The escape hatch is an exemption **with a written reason** in
`args/red_first_gate.yaml`; never `mode: advisory` and never `|| true`."*

## The repair

Commit `4508ba0ff` on `kanban/dwr-collab-01` (non-force fast-forward from
`a87ce1c3c`; the remote head was confirmed an ancestor before pushing) adds one
exemption entry, scoped to that ONE file, in the idiom of the
`tests/test_aca_cert_evidence.py` entry and carrying the same caveat: *a
test-only repair is not mechanically distinguishable from a test weakened to
match broken code, so this is not the pattern for the next one.* Nothing else
changed — no threshold, no `mode:`, no production code.

The watcher's 07:10:38Z `behind_main` rebase replayed it as **`5fa976aba`**; the
exemption survived intact (`git show origin/kanban/dwr-collab-01:args/red_first_gate.yaml`
still carries the entry) and the branch is now 0 behind `main`.

Gate before → after, same tree, `--base origin/main`:

```
before  5 changed test file(s) — 2 discriminating, 1 not, 0 indecisive, 0 exempt, 2 not applicable   exit 1
after   5 changed test file(s) — 2 discriminating, 0 not, 0 indecisive, 1 exempt, 2 not applicable   exit 0
```

The RED for what #2195 actually changes is untouched and still recorded:
`tests/docmod/test_collab_poll.py` (merge-base `collection_error`, 35 pass here)
and `tests/docmod/test_suggestion_accept_anchor.py` (merge-base 2 failed/22
passed, 24 pass here) are both still `discriminating`.

**Deliberately NOT done:** the file was not promoted out of
`args/ci_test_backlog.txt`. Gating it needs `gate_promoter`'s green-alone AND
green-in-suite proof (rem-tst-06), and widening the gated set is not #2195's
subject. The `tests/test_aca_cert_evidence.py` precedent gated its file; this
one does not, and says so in the entry rather than leaving a reader to wonder.

**Also deliberately NOT done:** the drive-by was not reverted out of #2195.
Reverting would have cleared the gate with a smaller diff, and it would have put
a test that is red on `main` back to red — the census discipline
(`born_red_survey`, rem-hyg-14) says repair it, and the repair is correct and
verified in both directions above.

## The exemption is CONFIRMED, and it uncovered a second, unrelated blocker

Run `34323252209` on head `cda00a86f` is the proof the exemption worked:
**`Test Gates` PASSED**, where it had failed on every run since 02:44Z. The
red-first block is closed.

That run then failed on something else entirely — `Test Shard 2 of 4`:

```
tests/docmod/test_collab_poll.py::test_alone_is_measured_and_excludes_me
sqlite3.OperationalError: no such column: expires_at
```

and the test **passes ALONE**. It is an in-suite order dependency over a table
with **two incompatible definitions, both pre-existing on `main`**:

| source | PK | `joined_at` | `expires_at` |
|---|---|---|---|
| `document_intelligence/db/init_db.py` — **twice in one file, in BOTH trees** | `session_id` | ✗ | **✗** |
| `presence_registry._ensure_table` | `session_key` | ✓ | ✓ |
| `db/schema/pg_consolidated.sql` (PG schema of record) | `session_key` | ✓ | ✓ |
| this branch's own test fixture | `session_key` | ✓ | ✓ |

`CREATE TABLE IF NOT EXISTS` **never alters an existing table** — the rule
CLAUDE.md already states — so whichever runs first wins and the loser's columns
simply never exist. Reproduced directly rather than inferred: run `init_db`'s
DDL then the registry's against one SQLite file, and the live columns are
`init_db`'s, after which the registry's OWN
`DELETE ... WHERE expires_at < ?` raises the exact CI error.

**This is not a test-only defect.** On any SQLite deployment where `init_db`
runs before the registry, presence is broken at runtime with that error.
`_ensure_active_section_col` — a targeted `ALTER` for exactly one column — is
evidence the collision had already been met once and patched only where it was
tripped over; a PK rename cannot be patched that way at all.

`init_db.py` is the side that is wrong: the PG schema of record, the module that
owns the table (`test_presence_is_read_through_the_registry_not_a_private_select`
asserts that ownership) and the fixture all agree on the other shape. Both
occurrences in both trees now match it (`29e83c55a`, mirror parity green, 887
`tests/docmod` tests pass).

**NOT fixed, and named:** an EXISTING SQLite database already carrying the old
shape stays broken — the difference includes the PRIMARY KEY, which SQLite
cannot `ALTER`, so repairing one needs a table rebuild, not a column backfill.
This change makes every NEW database correct. The definition is also still
duplicated twice inside `init_db.py`; the two copies are now identical and
deduplicating them is a wider edit than this branch should carry.

That the branch's own new test is what exposed a latent `main` defect is the
test working, not the branch failing.

## And an infrastructure blocker on top: `cancel-in-progress` inverts under a job cap

The exemption did not get a clean run on the first try, and the reason is a
defect in mfx-ci-02's concurrency declaration that only appears when the
account's job cap makes runs queue.

| run | head | created | started | ended |
|---|---|---|---|---|
| `34321300424` | `4508ba0ff` — **superseded** | 06:55:24Z | **07:11:25Z** | cancelled by hand |
| `34322543640` | `5fa976aba` — **current** | 07:10:41Z | 07:10:4xZ | **cancelled 07:16:28Z** |

The older run sat queued for **16 minutes** behind the job cap and *started*
after the newer one had already begun — `Lint` and `Helm Lint` had passed. Both
share the group `icdev-ci-${{ github.ref }}` with `cancel-in-progress: true`,
and **GitHub orders that group by START, not by creation**, so the
superseded-sha run joined late and cancelled the current-sha run. The stale
`updated_at` (07:16:28Z) is the newer run's cancellation instant to the second.

The consequence is worth stating in general terms, because it is not specific to
this PR: **a force-push while a run is queued inverts the ordering — the older
sha wins, the head's run is destroyed, and the branch is left with a rollup of
`cancelled` that no merge door accepts.** `pr_watcher`'s own `behind_main`
rebase is a force-push, so the watcher can trigger this against itself, which is
exactly what happened here.

**The watcher cannot self-heal it.** `_retrigger_ci` (close/reopen) fires only
behind `_ci_never_fired`, i.e. an EMPTY rollup. A rollup full of `cancelled` is
not empty, so the cheap repair is never reached and the PR waits in `pr_opened`
indefinitely — the same "waits forever with no red anywhere" shape that
`_ci_never_fired` was written to close, one state over.

Repaired by hand: the stale run cancelled first (so it could not repeat the
inversion), then an empty commit `cda00a86f` to re-fire on the current head.
`gh run rerun` was deliberately NOT used — in a `cancel-in-progress` group it
cancels the run it is rerunning. Not carded here; it needs its own survey of how
often a queued run outlives a force-push, which is a property of the job cap and
not of this branch.

## Two further things found on the way, neither this card's to fix, both named

**1. The watcher has been blind to every PR since 00:39Z — a GitHub API rate
limit, not a per-PR condition.** 335 `pr_watcher.wait` rows between
00:39:49Z and 06:43:56Z, all reading *"fetch failed: gh pr view failed: exit=1
stderr=GraphQL: API rate limit already exceeded for user ID 263484343"*, across
**10 distinct tasks** — `autonomy-act-05`, `dwr-collab-01`, `dwr-word-01`,
`dwr-word-02`, `kpr-watch-14`, `kpr-watch-15`, `task-det-73b8573563`,
`task-det-82438c25bc`, `task-det-95bd035ed2`, `task-det-a02733a6dc`. So for the
last four of the seven hours #2195 sat red, the watcher could not have read it
even if the PR had been perfect. The quota had reset by the time this card ran
(`gh api rate_limit` → core 5000/5000, graphql 5000/5000, used 0), so the
condition is cleared and no action is proposed; it is recorded because a
fleet-wide forge outage attributed 335 times to 10 innocent PRs is exactly the
shape kpr-watch-02 keeps out of `alarm` by attributing it ONCE, and the `wait`
rows here carry no such attribution.

**2. `husk_sweep.remove.unconfirmed` left 1.7 MB behind.** The 06:27 sweep
removed 323.6 MB / 24,449 entries of `.tmp/worktrees/dwr-collab-01` and reported
`remove.unconfirmed` rather than removed: `.logs` survives, which is the same
directory its own proof named as `newest_path`. That is mfx-own-04's known
`node_modules`-only residue shape wearing a different name, and the act reported
it honestly rather than claiming a removal it had not confirmed. Left alone.

## Note on the stale lease

`.tmp/coordination/leases/kanban_task_dwr_collab_01-*.json` names
`genesis-daemon-39996`, acquired 23:37:32Z with a 3600s TTL, so it expired
00:37:32Z — six hours before this card dispatched — and pid 39996 is not
running (the live daemon is 17924). Nothing was holding the task, so no claim
had to be released; `--release` was not run because there is no live keeper to
end, and writing one would have been inventing a holder to then remove.

## Outcome — the subject LANDED, and the disposition flipped under it

**#2195 merged at 2026-09-09T07:50:08Z** (`052311da9`), and
`kanban_tasks.dwr-collab-01` reads `done` at 07:50:46Z. Final rollup on
`29e83c55a`: **19 pass, 2 skipping, 0 failures** — including
`Test Gates` (the block this card was filed for) and `Test Shard 2 of 4` (the
`main` defect the first green `Test Gates` uncovered).

**The watcher merged it, not a human.** There is a `pr_watcher.merge` row at
07:50:07.313855Z. So the shape of this instance is not "the escalation asked for
a human and a human merged it" — it is *the escalation asked for a human, a
human removed two blockers the automation structurally could not
(a gate exemption and a schema collision), and the automation then completed the
merge on its own*. Both halves were necessary; neither is the whole story.

And the platform's own disposition rule moved with it, which is the cleanest
possible confirmation that the work was real:

```
at dispatch   card    dwr-collab-01  the escalation is the newer of the two rows
after         record  dwr-collab-01  a pr_watcher.merge at 2026-09-09T07:50:07Z landed
                                     AFTER the escalation at 2026-09-08T23:56:57Z,
                                     and the subject is `done`: nothing is left to land
```

This card was filed as a `card` — the first of the 29 to be — did the work, and
ended as a `record`. Instances 4/5/6/8/11 were `record` at dispatch and had
nothing to do.

**The finding is still `active`, and that is correct.** `escalate` outranks the
later merge by design (rem-hyg-16), so `21a987e887c69c8f` clears at the first
`detector_findings_reflex` cycle after **2026-09-10 07:10:38Z** — 24h past the
newest counted attempt, which is now the 07:10:38Z rebase and no longer moves,
because the branch has merged and nothing will rebase it again. Nothing in this
card touched the detector, its window or its threshold to bring that forward.

## Instance count

This is the **29th** `needed_a_human` resolution record in `docs/audits/`. It is
the first recorded instance where `--records` classified the finding as a
dispatchable **card** rather than a **record**, and the first where the blocker
was a *correct* gate refusing a *correct* change.
