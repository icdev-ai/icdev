<!-- CUI // SP-CTI -->
# task-det-c20060cd95 — `needed_a_human` finding for mfx-boot-01, resolved

- **Task:** task-det-c20060cd95 (filed by `detector_findings_reflex`, detector
  `recovery_summary` / rem-hyg-16, finding `c20060cd956c3fdf`)
- **Subject:** mfx-boot-01 — PR #2066, resumed 5x, escalated `needed_a_human`
- **Date measured:** 2026-09-05, against the live PG board

## Verdict

**A real, unfixed defect — not a stale finding.** Unlike the four preceding
`needed_a_human` audits, nothing had quietly landed here. PR #2066 was still
open, still red, and the cause had never been diagnosed.

## What the five resumes actually did

Nothing. **The PR head has not moved since `2026-09-04T13:56:05Z`** while the
resumes ran `18:00`–`18:31`:

| Recorded | Value |
|---|---|
| head sha | `d9c9aec651c2fc1526e0141135e737d31f03f6d1` (unchanged across all 5) |
| resume cycles | 2, 3, 4, 5 — all `injected resume context` |
| escalate | `18:32:39` — `resume cap reached (5/5) — manual intervention required` |
| every resume's context | `Failing checks: Test Shard 4 of 4, Test` — **no test name** |

The resume context named the *check*, never the *test*. An LLM handed
"Test Shard 4 of 4 failed" and a clean worktree has nothing to act on, which is
why five attempts produced zero commits.

## The actual cause — one defect, two sites, and a third hidden behind `-x`

`Test Shard 4 of 4` was the **only** failing check (18 others green). Its log:

```
tests/test_lease_litter_reflex.py:171: in test_registration_failure_is_a_warning_not_a_pass
    i = src.index('_coord_reg.register(intent="kanban scheduler')
E   ValueError: substring not found
1 failed, 3917 passed, 5 skipped
```

Both failures are **a structural test reading another function's source by
text**, broken by a refactor that *strengthened* the invariant they guard:

1. `test_registration_failure_is_a_warning_not_a_pass` — mfx-boot-01 moved the
   registration attempt out of `kanban_scheduler` into `RegistrationRetry`, so
   the call site it matched by text no longer exists.
2. `test_heartbeat_failure_is_not_swallowed_silently` — the guarded region
   *grew* by an `elif` arm, pushing `"heartbeat failed"` past a fixed 700-char
   window.

**CI only ever showed the first.** The shard runs with `-x`, so it stopped at
`........F` — 9 of the file's 10 tests. Re-derived at the CI head sha,
`heartbeat failed` was already outside the window there, so the branch carried
**two** failures and the watcher was told about one.

The same `-x` hid a third, in the same shard:

| Shard-4 entry | File |
|---|---|
| 141 | `tests/coordination/test_code_identity.py` |
| 172 | `tests/test_lease_litter_reflex.py` ← shard aborted here |
| 174 | `tests/coordination/test_registration_retry.py` |

`_as_session()` at entry 141 sets `CLAUDE_SESSION_ID` / `ICDEV_SESSION_ID` /
`ICDEV_AGENT` directly on `os.environ`, unmonkeypatched, and never restores
them. `hook_compat.get_session_id()` reads **`CLAUDE_SESSION_ID` first**, and
this card's own new fixture cleared only `ICDEV_SESSION_ID` / `ICDEV_AGENT` —
so the leaked value won and the service registered as `pre-migration-probe`.
That ordering is **CI's**, 33 entries apart, not a local accident. Fixing the
first two failures would have exposed these two on the next run.

## The invariant was never weakened

A registration failure is now loud on **every** attempt, not only the last, and
exhaustion states the process will not heartbeat in `agent_sessions`. The
guarantee was re-pinned, not relaxed:

- the registration test is now **behavioural** — drive a `register()` that
  always raises to exhaustion through a captured logger, assert it never reads
  registered, gives up out loud, and warns once per failed attempt. Grepping a
  function's source proved only that the text had not moved.
- the heartbeat test stays structural but anchors on **its own handler**
  (`except Exception as _hb_exc:`) rather than counting bytes from the call.
- two properties the refactor made checkable were added: the scheduler's
  wrapper warns rather than passing, and `registered` is set from the
  `register()` **result**, never from "it did not raise".

Red-first holds: `tools/coordination/registration_retry.py` does not exist at
the merge base, so the new tests are RED there by import.

## Not fixed here, and named

- `_as_session()` mutating `os.environ` without `monkeypatch` is a live
  landmine for **any** gated test that resolves a session id after it. This
  change defends the one file mfx-boot-01 owns; it does not disarm the mine.
- `tests/test_launcher_supervises_all_services.py` fails 6 params in-suite. It
  is **ungated** (in `args/ci_test_backlog.txt`), so it gates no merge and was
  not introduced here.
- The resume context naming only the failing **check** and never the failing
  **test** is why five resumes achieved nothing. That is a `pr_watcher`
  observation defect, and it is the reason this class escalates.

## Why the finding clears

The detector's window is a rolling 24h over four actions
(`resume`/`rebase`/`escalate`/`merge`). The last row for this subject is the
`18:32:39` escalate on 2026-09-04, so it ages out at `18:32:39` on 2026-09-05
regardless of any repair — which is exactly why the audit, not the clearance,
is the evidence that the cause was found.

## Landing status

The repair is pushed and **CI is fully green on the new head**
`eadc21bc3a1aa1d6be89f27c38dfeb14db972e75` — all 18 checks `success`, PR #2066
`mergeable_state: clean`, including `Test Shard 4 of 4`, the one check that had
been red since 2026-09-04.

It is **not merged**, and the reason is an outage rather than a defect. Every
`gh pr view` on this host has returned

```
GraphQL: API rate limit already exceeded for user ID 263484343.
```

continuously from `13:20` to at least `19:13` on 2026-09-05, while
`gh api rate_limit` reports REST **and** GraphQL at 5000/5000 — the documented
shared-token secondary limit. The done-gate's `pr_readable` rung refuses:

```
REFUSED: mfx-boot-01: PR state unreadable - refusing to merge blind
  [ok] pr_recorded  https://github.com/icdev-ai/icdev/pull/2066
  [XX] pr_readable  gh pr view failed: GraphQL: API rate limit already exceeded
```

That refusal is the gate **working**: it is fail-closed on every unknown, and
merging blind is precisely what it exists to prevent. `pr_watcher` is polling
this PR every ~34s and recording the identical failure, so it will merge the
moment the limit clears.

A raw `gh api .../merge` would have gone through — and was deliberately not
used. #2066 is a kanban-linked PR on a `kanban/<id>` branch, exactly the
population `kpr-rvfy-05` refuses a raw merge for, and it would have written
`done` without any of the thirteen `land.py` checks. The correct end state for
this card is a repaired, green, ready PR plus the sanctioned door, not a
bypassed one.
