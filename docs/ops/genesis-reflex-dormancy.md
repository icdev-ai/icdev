# CUI // SP-CTI

# Why a Genesis reflex goes quiet — diagnosis and standing fix (xbm-wake-01)

**Investigated:** 2026-08-03 · **Trigger:** `scout` had not run in weeks
**Status:** cause identified, fixes landed

This is the write-up the task asked for: *a reflex that silently stops is a class of
failure worth understanding once rather than restarting repeatedly.*

**Answer, in one line:** scout is **rate-limited by GitHub** because it lost its
`GITHUB_TOKEN` and fell back to the anonymous 60-req/hr per-IP budget, which a
16-repo watchlist cannot fit in — cause **(b), an erroring reflex whose failure was
swallowed.**

But "scout is broken" is the wrong frame for the *fix*. Scout's code is correct and
its breaker is closed. Two **general** defects — one that erases the reason a reflex
failed, one that makes any three failures a permanent shutdown — turn a transient
external fault into silent, indefinite dormancy for *any* reflex. Scout is simply
where we noticed; 23 others are already in that state. Those two defects are what
this change fixes, and they are the reusable part of this document.

---

## 1. What was actually measured

Measured against the **live PostgreSQL backend** (`ICDEV_STORAGE_BACKEND=postgresql`,
`ICDEV_PG_NO_FALLBACK=true`) on 2026-08-03. The card's headline count reproduces
exactly: **69 of 93 registered reflexes have a run.**

| Metric | Value |
|---|---|
| `genesis_reflex_state` rows | 93 |
| …with a `last_run_at` | 69 |
| …ran since 2026-08-01 | 41 |
| **…circuit breaker OPEN** | **23** |
| `genesis_reflex_log` rows | 0 (by design — history lives in `_state`) |

So the daemon *is* executing reflexes: 41 ran in the last three days. Dormancy is not
platform-wide. It is concentrated in the 23 reflexes whose breaker is latched open.

Scout's own row is the one that answers the card:

```
scout | enabled 1 | daily 10:30 | total_runs 14 | successes 9 | failures 5
      | consecutive_failures 2 | breaker CLOSED
      | last_error 'github_api_unavailable: http_403_rate_limited (16/16 repos failed)'
```

Ruled out directly, against that row:

- *(a) long cadence* — no; scout is `daily 10:30`, and it is enabled.
- *(c) trust-kernel/breaker gate on scout* — no; scout's breaker is **closed**.
- *(d) disabled by config* — no; `enabled: true` in `args/genesis_config.yaml`.
- *active-hours gate* — no; `active_hours.enabled: false`, so the cycle never skips.
- *(b) erroring, swallowed* — **yes.** This is the cause. See §2.

Note `consecutive_failures: 2`. The trip threshold is 3. Scout was **one failed run
away** from joining the 23 latched reflexes permanently and silently — which is the
condition the card noticed and the reason this is worth fixing generally rather than
restarting scout by hand.

## 2. Root cause of scout's current failure: it is rate-limited, not unauthorised

Running scout today through its own daemon path reports, for the first time:

```json
{"status": "failed", "reflex": "scout",
 "error": "github_api_unavailable: http_403_rate_limited (16/16 repos failed)"}
```

Confirmed at the source — the anonymous budget is fully consumed:

```json
{"limit": 60, "remaining": 0, "used": 60}
```

The chain:

1. On **2026-07-28** a stale `GITHUB_TOKEN` was commented out of `.env`. That was the
   correct fix for a real, *different* problem: the dead token returned
   `HTTP 401: Bad credentials` and, because every tool calls `load_dotenv()` before
   shelling out, it **shadowed `gh`'s working keyring auth** and broke `pr_watcher`,
   the kanban GitHub executor, and the done-gate's PR check.
2. Side effect: `scout` does **not** use `gh`. It calls `api.github.com` with raw
   `urllib`, so it cannot reach the keyring. Removing the token dropped scout from
   an authenticated 5,000 req/hr to the **anonymous 60 req/hr, shared per-IP** with
   every other tool on this host.
3. A full watchlist scan costs **16–32 calls**. Scout cannot reliably fit in that
   budget, so it gets `403` on all 16 repos.

> Do not "fix" this by restoring the old token — it is invalid, and re-adding it
> re-breaks all the `gh` tooling. The durable fix is a **valid** `GITHUB_TOKEN`.
> Until one exists, scout will fail whenever the shared anonymous budget is spent.

## 3. Why it was invisible, and why it would have stayed dormant forever

Two defects, both now fixed. These are the reusable part of this document.

### Defect 1 — the failure reason was erased

`_github_api()` caught its exceptions, `print()`ed to a stdout nothing captures, and
returned a bare `None`. Scout then returned `success=False` with **no `error` key**,
so `run_reflex` fell through to a generic substitute:

```python
error_msg = details.get("error", "metric_threshold_not_met")
```

A total outage was recorded as *"the metric didn't clear its threshold."* Every
silently-dead reflex on this platform carries that exact string. It is why five
weeks of dormancy produced nothing anyone could act on.

**Fixed:** `_github_api` classifies failures (`http_401_bad_github_token` vs
`http_403_rate_limited` vs `network_*`) into an `error_sink`; scout reports the
dominant reason; and `run_reflex` only blames the metric when the reflex actually
*succeeded* and merely missed its threshold.

### Defect 2 — the circuit breaker was a latch, not a breaker

Three consecutive failures set `circuit_breaker_open = 1`. With
`auto_reenable: false`, `run_due_reflexes` then skipped the reflex with a bare
`continue` — **no audit row, no alert, no retry, ever**, however long ago the fault
cleared. Only a human running `daemon.py --reset` could undo it.

**23 reflexes** are sitting in exactly that state right now — latched between
2026-06-12 and 2026-08-02, every one of them stuck at exactly 3 consecutive failures
because the fourth attempt never happened. Grouped by the reason they recorded:

| Count | `last_error` | What it actually is |
|---|---|---|
| 15 | `metric_threshold_not_met` | **Reason unknown** — the substituted string, per Defect 1 |
| 4 | `watchdog_timeout_300s` | Real: reflex exceeded its wall clock |
| 3 | `'GenesisTrustKernel' object has no attribute 'execute'` | Real, actionable **code bug** — invisible for weeks |
| 1 | `column "tenant_id" does not exist` (`usage_rollup`) | Real, actionable **schema bug** |

That bottom half is the cost of the latch stated plainly: five reflexes were carrying
precise, fixable errors — a missing method and a missing column — and nobody saw them,
because a latched reflex emits nothing. The other fifteen could not even say that much.

A transient outage must not be a permanent shutdown. **Fixed:** `circuit_probe_due()`
admits one half-open probe once a cooldown elapses, backing off exponentially
(capped at `max_cooldown_minutes`, default 24h) so a genuinely broken reflex settles
at one cheap probe per day instead of hammering, while a recovered one re-enables
itself. `record_success()` now clears `circuit_breaker_open`, and probes emit a
`genesis.circuit_breaker.probe` audit event. Set `auto_reenable: false` to restore
the old latch.

## 4. Two further findings

**Ask the right database.** Genesis history exists in two places and they disagree.
The live backend is PostgreSQL; a stale SQLite copy at `data/icdev.db` also holds a
`genesis_reflex_state`, frozen months earlier. "When did this reflex last run?" has a
different answer depending on which you query, and only the PG one is current. Confirm
your backend before drawing a conclusion. `genesis_reflex_log` holds **0 rows in both**
— run history lives in `genesis_reflex_state`, so never conclude "never ran" from the
empty log table.

**`genesis_audit_log` holds 0 rows,** while `genesis_reflex_state` records 41 runs in
the last three days. So reflex execution is being recorded in state but the audit trail
is not being written — including the `reflex.skipped` rows that would have made the 23
latched reflexes visible without a database query. That is a second, independent reason
this failure class stayed silent, and it is **not fixed here**: it is an audit-wiring
problem rather than a reflex problem, and it deserves its own card. The probe events
this change emits will land in that same table, so they will only become visible once
it is fixed — the state-table evidence (`last_run_at` advancing, honest `last_error`)
is what to watch in the meantime.

**Registered-but-never-dispatched:** `idp_score_recorder` is enabled and scheduled
`every 3h` in `args/genesis_config.yaml` and exists in both trees, but was missing
from `REFLEX_NAMES` in `tools/genesis/daemon.py`, so it could never be dispatched —
the same class of silent absence. Added.

## 5. Verification — what the live backend shows now

Scout's row in the live PostgreSQL `genesis_reflex_state`, after running it through
the daemon path with these changes in place:

```
last_run_at  2026-08-03T20:57:53Z    <- advanced; it was months stale
total_runs   14  (successes 9, failures 5)
last_error   'github_api_unavailable: http_403_rate_limited (16/16 repos failed)'
```

Against the acceptance criteria:

- **Cause identified and recorded** — §2 (rate limit, no token), this document.
- **`last_run_at` advances** — yes, shown above. `record_failure()` advances it too,
  so the cadence is restored whether or not GitHub answers.
- **The swallowed error is now visible** — yes. `last_error` names the real reason
  instead of `metric_threshold_not_met`.

One honest caveat: scout will keep reporting `http_403_rate_limited` until a **valid**
`GITHUB_TOKEN` exists (§2). That is a credential the repo cannot supply for itself.
What changed is that the failure is now *legible and self-recovering* rather than
silent and permanent — the day a token appears, scout resumes without a human, and
until then it says exactly what it needs.

## 6. Triage checklist for "reflex X went quiet"

1. **Read `genesis_reflex_state`, not `genesis_reflex_log`** (the log is empty by
   design here) — and confirm *which backend* you are querying.
2. `circuit_breaker_open = 1`? It was latched. It now self-probes; `daemon.py --reset
   <name>` still forces it immediately.
3. `last_error = "metric_threshold_not_met"`? Historically that means *"reason
   unknown"*, not a threshold miss. Post-fix, a real cause should appear instead.
4. `total_runs` high with `total_failures = 0` and a stale `last_run_at`? The reflex
   is healthy and **was never dispatched** — look at the daemon, not the reflex.
5. Check the reflex is in `REFLEX_NAMES`, not just in the config.
6. `daemon.py --reflex <name> --json` runs it now and prints the real error.
