<!-- CUI // SP-CTI -->
# task-det-9fc1bc0c38 — `needed_a_human` finding for rmf-rail-02, resolved

- **Task:** task-det-9fc1bc0c38 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `9fc1bc0c3837af52`, `seen_count` 4)
- **Subject:** rmf-rail-02 — PR #2159, five `resume` cycles, escalated
- **Date measured:** 2026-09-09, against the live PG board (`icdev`), the forge
  (REST) and the watcher's own checkout

## Verdict

**The escalation was CORRECT and a human DID answer it.** This is a POSITIVE
control — the fourth after rmf-ui-08, fni-api-01 and rmf-ui-13 — not the
"escalation was premature, nobody did anything" case that is the majority of
this card class. Commit `4261a59c1` ("Merge origin/main into
kanban/rmf-rail-02", 2026-09-08T06:02:51Z) is a real repair inside the squash,
authored 8h43m after the escalation, and the watcher merged the PR 17m30s
later. So `summarize_recovery` giving `escalate` priority over the later `merge`
reports exactly what happened here, and counting that merge as an autonomous
recovery would have been the rem-hyg-16 inflation.

**The finding is moot, and the derivation already agrees.** Re-derived first, as
the card demands:

```
$ python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'rmf-rail-02'])
EOF
[]                                     # 2026-09-09T02:0xZ, 65 rows in the 24h window
```

The subject reads `done` on the board (`updated_at` 2026-09-08T06:21:08Z,
`completed_via_bypass` 0), PR #2159 merged 06:20:21Z, and **no lease exists for
`rmf-rail-02`** — nothing to release. The `escalate` row (2026-09-07T21:19:42Z)
left the detector's 24h window at 2026-09-08T21:19:42Z; the last reflex cycle
that could still see it ran at 20:45:26Z, 34 minutes earlier, which is why the
`detector_findings` row still read `active` when this card dispatched. It clears
on the next `detector_findings_reflex` cycle. Nothing was done to the detector,
its threshold or its window.

## The ledger

`pr_watcher` rows naming the subject, lifetime: **123 `wait`, 5 `resume`,
1 `escalate`, 14 `union_refused`, 14 `rebase_failed`, 1 `sibling_conflict_warn`,
2 `merge`** — 160 rows spanning 2026-09-07T20:23:06Z to 2026-09-08T06:21:09Z.

| at (UTC) | action | reason |
|---|---|---|
| 09-07 20:32:20 | `resume` | cycle 1 — `Failing checks: Test Shard 3 of 4, Test Shard 4 of 4, Test`; prior injections **unmeasured** (first) |
| 09-07 20:42:51 | `resume` | cycle 2 — prior injections **undelivered** (1 unread) |
| 09-07 20:53:06 | `resume` | cycle 3 — **undelivered** (2 unread) |
| 09-07 21:03:44 | `resume` | cycle 4 — **undelivered** (3 unread) |
| 09-07 21:18:51 | `resume` | cycle 5 — **undelivered** (4 unread) |
| 09-07 21:19:42 | `escalate` | **resume cap reached (5/5) — NONE of the 5 injection(s) were ever read (5 still unread in the queue)** |
| 09-07 22:39:51 | `union_refused` + `rebase_failed` | `undeclared: icdev/tools/canvas_compliance/posture.py matches no union_resolver.files entry` |
| … ×13 more, to 09-08 05:18:08 | `union_refused` + `rebase_failed` | identical, every base era |
| 09-08 06:20:16 | `sibling_conflict_warn` | shares `CLAUDE.md` + the packaged bootstrap with #2173, #2170 |
| 09-08 06:20:21 | `merge` | **auto-merge ok** |
| 09-08 06:21:09 | `merge` | PR already merged |

The card's quoted `reason` is the last ATTEMPT's, not the escalation's — read
the `escalate` row itself, which here is a true resume cap and says in the same
breath why the cap was meaningless.

## Three causes, in the order they fired

### 1. A REAL branch defect started it — and one command fixes it

`classification: ci_failed` on all five resumes. The failing checks on head
`951129009` were `Test Shard 3 of 4`, `Test Shard 4 of 4` and the `Test`
aggregator. Reading the shard log rather than the rollup
(`gh api repos/icdev-ai/icdev/actions/jobs/101857287886/logs`):

```
assert checker.check_bootstrap_parity().status == "pass"
E   AssertionError: assert 'fail' == 'pass'
FAILED tests/test_bootstrap_hook_payload.py::test_payload_rule_is_green_on_the_tree_as_committed
======================== 1 failed, 437 passed in 42.20s ========================
```

The PR edited `CLAUDE.md` without regenerating
`icdev/data/claude_bootstrap/CLAUDE.md`. That is **mfx-ci-04's defect exactly**,
and CLAUDE.md's own mfx-ci-04 block already names this PR as its third instance.
The repair is `python tools/installer/prebuild_bootstrap.py` plus a `git add` —
precisely the kind of thing an LLM resume is for.

**mfx-ci-04's pre-commit refusal landed 71 minutes too late for this PR.**
`bed8317e4` (#2162) merged 2026-09-07T22:30:38Z; the escalation was 21:19:42Z. A
hook cannot refuse a commit that was made before it was on the tree.

### 2. Five resumes could not fix a one-command defect because none was read

`resume_delivery`, run from the watcher's own checkout (the queue is
per-checkout, so a worktree reads its own empty `.tmp` and would report
`unmeasured`):

```
$ python -m tools.ci.resume_delivery --task rmf-rail-02
rmf-rail-02: undelivered -- 5 pr_watcher message(s) still unread in the queue
```

Five attempts THAT WERE NEVER MADE — kpr-watch-13's finding, re-derived here on
its own subject. The escalate row states it in words, so the escalation is not
merely correct but self-describing.

**Already fixed on main, and it cites this subject by name.** `72765eec1`
(#2184, merged 2026-09-08T10:10:28Z) makes the watcher escalate as soon as an
injection is PROVEN unread instead of spending the rest of the budget, guarded
so that one genuine attempt is always made (`cycle > 0`) and so that only
`UNDELIVERED` — never `unmeasured` — may withhold a try. Its comment reads:
"Measured 2026-09-08: `dwr-anchor-05`, `dwr-ev-01` and `rmf-rail-02` each
escalated exactly this way". The delivery half of this incident closed 3h50m
after the PR merged.

### 3. What actually held the merge after the escalation was a DIFFERENT thing

Fourteen `rebase_failed`, each preceded by a `union_refused` naming `posture.py`
and its `icdev/` twin. The branch was 30 commits behind and `dirty`, and the
collision is a genuine two-sided edit of the same dict literal in
`compute_canvas_posture`:

- **rmf-rail-02** hoisted the `_max_ts(zconn, "zig_maturity_scores", ...)` read
  into `zig_last`, so the timestamp is taken BEFORE probing a table that may not
  exist.
- **main** — `6a540ef71` (rmf-inert-03, #2161), merged **2026-09-07T22:30:42Z** —
  added `last_assessed_source` and `last_reviewed` to the same literal.

First `rebase_failed` at 22:39:51Z, **9m09s after that landing**: the
sibling-train signature, one landing and then a failure on every subsequent base
era, because nothing ever resolved it.

**The union rung was RIGHT to refuse, on the merits.** `union_resolver.files`
declares blueprint / `app.py` / template / e2e-spec / `start.md` / feature-doc
paths — line-oriented append surfaces. `posture.py` is Python source carrying
two overlapping semantic edits, and a union there would have silently dropped
one side's intent. Verified that the refusal is about the declaration and not
about the mirror path it happens to name first:

```
icdev/tools/canvas_compliance/posture.py -> canon: tools/canvas_compliance/posture.py -> decl: None
tools/canvas_compliance/posture.py       -> canon: tools/canvas_compliance/posture.py -> decl: None
icdev/tools/boundary_canvas/blueprint.py -> canon: tools/boundary_canvas/blueprint.py -> decl: tools/boundary_canvas/blueprint.py
```

`union_resolver.canonical_path` maps an `icdev/` mirror onto its `tools/` twin
before matching, so a declared file's mirror IS covered. The refusal is simply
that `posture.py` is not declared — and should not be.

### Corroborating: the watcher was half-blind for most of that window

90 of the 123 `wait` rows carry `classification: error`:

```
fetch failed: gh pr view failed: exit=1
  stderr=GraphQL: API rate limit already exceeded for user ID 263484343.
```

The other 33 read `pr_opened` / "CI still running". So between the escalation and
the repair the watcher could not read the PR on roughly three polls in four. It
did not cause the escalation — that fired at 21:19:42Z, on a `ci_failed`
classification derived from a rollup it had read — and it is recorded here as
context, not as a finding.

## The repair, and the proof it kept both sides

`4261a59c1` — merge `origin/main` into the branch, resolve the one conflict by
keeping BOTH edits, and regenerate the packaged bootstrap (which the mfx-ci-04
pre-commit gate, by then on main, correctly refused to let past stale). The
resolved hunk:

```python
"declared_maturity": declared,
"open_findings": 0,
"closed_findings": 0,
# `zig_last` is main's same _max_ts read, hoisted by rmf-rail-02 so
# the timestamp is taken BEFORE probing a table that may not exist.
# main's two new fields ride along unchanged.
"last_assessed": zig_last,
"last_assessed_source": None,
"last_reviewed": None,
```

Neither side's intent is dropped and no value changed;
`tests/test_compliance_posture_honesty.py` 23 passed, mirror parity clean. The
watcher merged at 06:20:21Z on "auto-merge ok".

## What this instance adds

- **Positive control #4.** Check the branch's own history for a merge commit
  before concluding nobody intervened — `gh pr view --json commits` shows it even
  for a PR that squash-merged to a single parent.
- **The escalation's reason and the actual blocker were about DIFFERENT things.**
  The five resumes were `ci_failed` (bootstrap parity); the 14 rebase attempts
  that followed were `merge_conflict` (posture.py). A record that read only the
  escalate row would have concluded "CI was red" and missed that a green re-run
  would still not have merged this PR.
- **Both halves already have landed fixes, from opposite directions**, and
  neither existed when this PR needed them: `bed8317e4` (mfx-ci-04, +71 min)
  stops the CI failure at commit time, `72765eec1` (#2184, +12h51m) stops the
  budget being spent on a queue nobody drains and names `rmf-rail-02` as one of
  its three measured cases. No new card is filed here: nothing outstanding was
  found that is not already carded and landed.

## Not done, and why

- **Nothing was changed in `recovery_summary`, `detector_findings` or the 24h
  window.** An actuator never edits what it verifies. The detector's verdict is
  correct on this subject in the strongest sense available — a human really was
  required and really did come.
- **The `union_refused` → `rebase_failed` pair repeating 14 times over 6h39m is
  not carded.** The escalation had already fired at 21:19:42Z, before the first
  one at 22:39:51Z, so a human had been asked before the retries began; they are
  harmless polling against a conflict only a human could resolve.
