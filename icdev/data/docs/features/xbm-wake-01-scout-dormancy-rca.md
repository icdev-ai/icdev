# CUI // SP-CTI

# xbm-wake-01 — Why the `scout` reflex went dormant for five weeks

**Date:** 2026-08-03
**Status:** Cause identified, fixed, cadence restored
**Affected:** `scout` (dormant 2026-06-28 → 2026-08-03), `research` (dormant 2026-06-13 → 2026-08-03)

---

## Summary

`scout` did not break. It was **switched off by its own circuit breaker after three
transient GitHub API outages, and nothing was ever going to switch it back on.**

The breaker had no cooldown, no half-open probe and no auto-reset — despite
`args/genesis_config.yaml` advertising `cooldown_minutes: 60`, a key that **no code
read**. The only exit was a human running `--reset scout`, and nothing told a human
it was needed: the daemon skipped the reflex with a bare `continue`, logging nothing.

The state row an operator would consult made it worse. It reported
`last_error = 'metric_threshold_not_met'` and `last_metric_value = 16.0` — implying
a metric of 16 had failed a threshold of `>= 0`, which is nonsense. Both fields were
wrong: the failing runs scored `0.0`, and the failure was not a threshold miss.

## Timeline (measured, from `genesis_audit`)

| Date | Event | repos_scouted | repos_failed |
|---|---|---|---|
| 2026-06-12 … 06-23 | `genesis.reflex.completed` ×8 | 16 | 0 |
| 2026-06-24T17:55Z | `genesis.reflex.failed` | 0 | 16 |
| 2026-06-27T10:43Z | `genesis.reflex.failed` | 0 | 16 |
| 2026-06-28T10:58Z | `genesis.circuit_breaker.tripped` | 0 | 16 |
| 2026-06-28 → 2026-08-03 | *(nothing)* | — | — |

Eight clean runs, then three consecutive **total-loss** failures — 0 of 16 repos, not a
partial degradation — then silence. The all-or-nothing pattern is the signature of a
network/egress fault, not a logic bug: a code defect would not have scored a clean
16/16 the day before, and a rate limit would have produced partial results.

Confirmed by re-running the reflex on 2026-08-03: **16 of 16 repos, `success=True`,
`metric_value=16.0`**. The trigger was transient and had long since cleared.

A later verification run the same hour returned **4 of 16** — unauthenticated GitHub
allows 60 requests/hour and the earlier runs had spent the budget. That run was still
scored a **success** (`4 > 0`, and `4 >= 0` passes the threshold), which is the
important detail: ordinary rate limiting degrades scout partially and never trips the
breaker. Only a *total* loss — 0 of 16, the June signature — scores as a failure.
Setting `GITHUB_TOKEN` raises the ceiling to 5,000 requests/hour; `scout.py` already
uses it when present.

## Candidate causes, as ruled out

| # | Candidate | Verdict |
|---|---|---|
| a | Cadence is simply very long | **No.** `CORE`, `interval_h=2.0` in `reflex_registry.py`; `schedule: "daily 10:30"` in config. Neither is five weeks. |
| b | Erroring, with the failure swallowed | **Partly.** It did error, and `genesis_audit` recorded the truth. But `genesis_reflex_state` — the operator-facing row, and the one this task was scoped from — mislabelled it. |
| c | Circuit breaker / trust-kernel gate opened | **Yes — this is the mechanism.** `circuit_breaker_open=1`, tripped `2026-06-28T10:58:59Z`, `consecutive_failures=3`. |
| d | Disabled by config | **No.** `enabled: true` in config, `enabled=1` in state. |
| e | GitHub rate limit / egress policy | **Yes — this is the trigger.** 0/16 on all three failing runs; 16/16 today. |

So (e) knocked it down and (c) held it down. Neither alone explains five weeks.

## The four defects

**1. The breaker was a permanent latch.** `ReflexStateBase.is_circuit_open()` read a
boolean with no time component. `run_due_reflexes()` skipped any reflex with
`circuit_breaker_open=1`. There was no half-open state and no expiry. A reflex that
tripped stayed tripped until a human intervened.

**2. `cooldown_minutes: 60` was dead config.** Declared in `args/genesis_config.yaml`
since the beginning, and read by nothing — the only references were a defaults dict in
`tools/daemon/base.py` and an unrelated `reflex_health` alert-flap window. The config
promised a recovery path that did not exist. (`tools/resilience/circuit_breaker.py`
implements a correct `HALF_OPEN` state machine; the daemon never used it.)

**3. Dormancy was silent.** The skip in `run_due_reflexes()` was a bare `continue` —
no log line, no audit event, no alert. A dormant reflex was indistinguishable from a
healthy idle one. Note that `run_reflex()` *does* emit `reflex.skipped`, but
`run_due_reflexes()` filtered the reflex out before ever calling it, so that event
never fired. `genesis_audit` holds 0 `genesis.reflex.skipped` rows across 27,974 events.

**4. The state row lied, in two independent ways.**
   - `record_failure()` never wrote `last_metric_value`, so scout's row kept showing
     `16.0` from its last success on 06-23 while every failing run scored `0.0`.
   - `last_error` came from `details.get("error", "metric_threshold_not_met")` — a
     catch-all default applied to *any* failure whose `details` lacked an `error` key.
     Scout's all-repos-failed path returns exactly that shape, so a total network
     outage was filed as a metric-threshold miss.

Together these point a debugger at the metric configuration — the one subsystem that
was working correctly.

## Fixes

| Defect | Fix |
|---|---|
| 1, 2 | `is_circuit_open(cb_config)` honours `cooldown_minutes` as a **half-open probe** when `auto_reenable` is on: after the window, the next scheduled run is allowed through. `record_success()` now clears `circuit_breaker_open`, so a successful probe closes the breaker; a failed probe re-trips it and restarts the window. A genuinely broken reflex retries once per hour instead of never. |
| 2 | `auto_reenable: true` in `args/genesis_config.yaml`, with the incident recorded inline. `proposal_genesis_config.yaml` keeps `false` — its hard latch is unchanged and still supported. |
| 3 | `_warn_reflex_dormant()` prints a warning naming the reflex, how long it has been dormant, its last error and the `--reset` command, and emits a `genesis.reflex.dormant` audit event. Throttled to once per hour per reflex. |
| 4 | `record_failure(..., metric_value=)` writes the metric of the run that actually failed. New `classify_failure()` distinguishes `reflex_reported_failure` from a real `metric_threshold_not_met`, and records the comparison that failed. |

Behaviour is unchanged for any daemon that does not set `auto_reenable` — the latch is
preserved by default, and `tests/test_reflex_circuit_breaker_recovery.py` pins that.

## Restoring cadence

```bash
python tools/genesis/daemon.py --reset scout
python tools/genesis/daemon.py --reset research
```

`scout` returns to its 2-hour cadence; `research` to 1-hour. With the fix in place,
neither requires this step again — a transient outage now self-heals after 60 minutes.

## Verification

```bash
pytest tests/test_reflex_circuit_breaker_recovery.py -v    # 15 passed
```

`scout` cadence restored — `genesis_reflex_state.last_run_at` advanced
`2026-06-28T10:58:59Z` → `2026-08-03T20:54:01Z`, breaker closed, `last_error` cleared,
`total_successes` 8 → 9, `genesis.reflex.completed` logged.

### The bug reproduced live during this task

One minute after that run, the **live** genesis daemon — still on the pre-fix code —
picked scout up on its own schedule, hit the GitHub rate limit that the verification
runs above had just exhausted, scored 0 of 16, and wrote:

```
last_error = 'metric_threshold_not_met'   last_metric_value = 4.0
```

Neither is true. It was rate-limit exhaustion, and the failing run scored `0.0` — the
`4.0` is left over from the success a minute earlier. This is the June failure
reproducing in real time, and it is what the fix in this PR corrects.

Budget note: unauthenticated GitHub allows 60 requests/hour and one scout pass costs
~16–32. The normal 2-hour cadence fits comfortably; several manual runs stacked in one
hour do not. Set `GITHUB_TOKEN` if scout is to be run ad hoc.

## `research` — same dormancy, different cause

`research` was tripped by `watchdog_timeout_90s` on 2026-06-13. Its breaker was reset
alongside scout, and `last_run_at` now advances — but it **still fails**: the run takes
longer than its configured `timeout_seconds: 90` (measured `duration_ms: 90023`), and
several of its feeds are dead (`fedramp.gov/rss.xml` → 404, `owasp.org/feed.xml` →
response-consumed error).

That is a genuine, persistent fault, not a transient one, and the 90s timeout is
deliberate ("cut hangs fast so the loop proceeds"). Raising it, pruning the dead feeds,
or making the fetch loop resumable is a real design decision with an owner — so it is
**not** guessed at here. What this PR changes for `research` is that it can no longer
go permanently dormant: it now retries once per cooldown window and reports itself as
dormant in the audit log instead of vanishing. Its state row is also honest now —
`last_metric_value` moved from a stale `30.0` to the actual `0.0`.

**Follow-up card needed:** fix `research`'s timeout and dead feeds.

## Note for whoever picks up the next reflex bug

`genesis_reflex_log` holds 0 rows and `genesis_audit_log` does not exist. Run history
lives in **`genesis_reflex_state`** (current state) and **`genesis_audit`** (per-run
events, 27,974 rows). Do not conclude "never ran" from the empty log tables — and when
`genesis_reflex_state` and `genesis_audit` disagree, `genesis_audit` is the one that
was written at the time of the run.

## Related, not fixed here

`tests/test_reflex_registration.py::test_every_reflex_registered_or_exempt` fails on
`origin/main` (pre-existing, unrelated to this change): `idp_score_recorder` is in
neither `REFLEX_NAMES` nor `EXEMPT`, so it is **never dispatched** — the same class of
failure as this task, at the registration layer rather than the breaker layer. It needs
an owner decision (register vs. mark on-demand), so it is flagged rather than guessed at.
