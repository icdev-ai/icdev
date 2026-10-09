# CUI // SP-CTI

# xbm-wake-02 — `reflex_registry.py` called itself authoritative and dispatched nothing

**Date:** 2026-08-07
**Status:** Resolved — one reflex registered, eight exempted with measured blockers, the false claim removed
**Follows:** [xbm-wake-01](xbm-wake-01-scout-dormancy-rca.md), section "Still open — reflex_registry.py schedules nothing"

---

## Summary

`tools/genesis/reflex_registry.py` documented itself as the *"authoritative list of all
reflexes and their tiers."* No dispatcher imported it. Outside its own module and the test
suite, the only non-test references were string literals inside kanban seed scripts.

Dispatch actually requires **two** things, and neither reads that file:

1. the reflex name in `REFLEX_NAMES` in `tools/genesis/daemon.py` — `DaemonBase.run_due_reflexes`
   iterates only that list; and
2. a block in `args/genesis_config.yaml` with `enabled: true` **and a parseable `schedule:`** —
   `DaemonBase.__init__` builds `self.schedules` from it, and `run_due_reflexes` skips any
   reflex with no schedule entry.

Same defect class as the permanently-latched circuit breaker xbm-wake-01 fixed: a registry
that lists what it cannot run, so a capability quietly never fires and nothing goes red.

## The measurement that settled it

`genesis_reflex_state` holds 93 rows and `genesis_audit` holds 28,662 events. **Not one of
the nine reflexes audited here had a row in `genesis_reflex_state` at all** — the row is
created when the daemon first schedules a reflex, so its absence is proof of zero dispatches,
not merely zero successes. Every one was `enabled: true` with a working module.

```sql
SELECT reflex_name, total_runs FROM genesis_reflex_state
 WHERE reflex_name = ANY(ARRAY['govcon_scan','socmint','failure_triage',
       'fathomdesk_trap_sweep','nocc_sla_watcher','peering_agreement_renewal',
       'quality','fathomdesk_pc_ratio','idp_score_recorder']);
-- idp_score_recorder | 0     ← row exists, never ran
-- (no other rows)
```

## The decision table

Nine reflexes: the eight xbm-wake-01 flagged, plus `idp_score_recorder`, which was failing
`test_every_reflex_registered_or_exempt` on `main` (flagged as out of scope by xbm-wake-01).

| Reflex | Real invoker? | Daemon contract | Decision |
|---|---|---|---|
| `idp_score_recorder` | none | `run(config, trust)` → `success` ✔, `every 3h`, own `timeout_seconds`, GREEN, internal-only | **REGISTERED** |
| `govcon_scan` | none | contract ✔ *(after a fix, below)* — but runtime unbounded | exempt: runtime |
| `socmint` | none | backing module does not exist; no `schedule:` key | exempt: no backing module |
| `failure_triage` | none | no `success` key; YELLOW + `ICDEV_AUTOFIX_ENABLED=true` | exempt: contract + owner decision |
| `fathomdesk_trap_sweep` | tests only | cooldown guard dead on PostgreSQL | exempt: PG blocker |
| `fathomdesk_pc_ratio` | none | SQLite-only DDL; target table absent | exempt: PG blocker |
| `nocc_sla_watcher` | **CLI `__main__`** | no `success` key | exempt: on-demand CLI |
| `peering_agreement_renewal` | none | no `success` key; source table empty | exempt: contract |
| `quality` | **`POST /api/genesis/quality`** | has `run_reflex()`, **no `run()`** | exempt: on-demand route |

### What "no invoker" means here, precisely

Three things looked like invokers and are not:

* **ACE role YAML** (`genesis_reflex: govcon_scan`, `genesis_reflex: fathomdesk_trap_sweep`) —
  `tools/ace/role_loader.py` reads the key into a dataclass field and `coworker_thread.py`
  surfaces it as a display string. It dispatches nothing.
* **`apps/forge_academy/configurator.py::_handle_govcon_scan`** — an explicitly *simulated*
  lab handler whose own docstring says it must never invent an opportunity. It does not call
  the reflex.
* **Kanban seed scripts** — string literals in task descriptions telling a future session to
  register the reflex.

### The two verified on-demand invokers

* `quality` → `POST /api/genesis/quality` in `tools/qdc_canvas/blueprint.py::api_genesis_quality`,
  which calls `quality.run_reflex()`. Also surfaced as a CLI recommendation by
  `tools/oracle/lenses/lens_quality.py`. It is **not daemon-dispatchable**: the module defines
  `run_reflex()`, not `run()`, so adding it to `REFLEX_NAMES` would fail
  `coherence_checker.py::check_reflex_registry` and the daemon would fall through to stub mode.
* `nocc_sla_watcher` → `python tools/genesis/reflexes/nocc_sla_watcher.py` (the module has a
  `__main__`), documented in `docs/reference/commands.md` and `.agents/skills/icdev-noc/REFERENCE.md`.

## The blockers, measured

**Return contract.** `DaemonBase` reads `result.get("success", False)`. Three modules —
`failure_triage`, `nocc_sla_watcher`, `peering_agreement_renewal` — return a summary dict with
no `success` key at all. Registering any of them would record **every** cycle as a failure and
trip its own circuit breaker after three, producing exactly the dormancy xbm-wake-01 traced.

**PostgreSQL dialect.** `ad_reflex_cooldowns` does not exist on the primary backend, and
`_mark_cooldown` creates it with SQLite-only `INSERT OR REPLACE`. `_check_cooldown` returns
`True` from its bare `except`, so on PostgreSQL the duplicate-suppression guard for
`fathomdesk_trap_sweep` is permanently open. `fathomdesk_pc_ratio` is worse: its `_DDL` uses
`INTEGER PRIMARY KEY AUTOINCREMENT` and `datetime('now')`, so `ad_pc_ratio_history` can never
be created on PostgreSQL and `_persist_snapshot` fails whenever the CBOE fetch *succeeds*.

**Missing backing module.** `socmint` wraps `tools.strategos.socmint_harvester`, which does not
exist anywhere in the tree — `run()` returns `success=False` on every call. Its config block
also has no `schedule:` key, so it would never be scheduled even if named.

**Unbounded runtime — `govcon_scan`.** `scan_sam_gov()` loops 8 NAICS codes × 4 notice types
and fetches a full description per opportunity (up to `max_per_poll: 100` each). A measured
live scan **ran past 30 minutes without returning**, against a
`defaults.reflex_timeout_seconds` of 300. Registering it as-is guarantees
`watchdog_timeout_300s` → three consecutive failures → breaker. Raising the cap to fit would
stall a *sequential* daemon loop — which runs `heal` and `dic_inbox_sweep` every five minutes —
for over half an hour daily. That is a scope-or-cap decision with an owner, so it was flagged
rather than guessed at.

## Two bugs fixed on the way

**1. `govcon_scan` could never report success.** `aggregate_demand_signals()` and
`get_high_demand_signals()` are both annotated `-> dict`. The reflex iterated the returned
dict, which yields its **str keys**, so `s.get("article_generated")` raised
`AttributeError` on every run, incremented `errors`, and forced `success = (errors == 0)`
to `False`. Measured before the fix:

```
GovCon: Demand detection failed — 'str' object has no attribute 'get'
{"success": false, ...}
```

and after:

```
GovCon: Demand signals — 0 high demand, 0 pending articles
{"success": true, ...}
```

The runtime question above is now the only thing standing between `govcon_scan` and
registration.

**2. `idp_score_recorder` was unaccounted for.** It was in neither `REFLEX_NAMES` nor `EXEMPT`,
so `test_every_reflex_registered_or_exempt` failed on `main` — pre-existing, and flagged by
xbm-wake-01 as needing an owner decision. The evidence says register: GREEN tier, no external
service, `every 3h` with its own `timeout_seconds: 300`, `run(config, trust)` returning
`success`, and `tools/manifest/idp-scorecards.md` already described it as a live reflex on the
awareness cadence. Verified before registering — **68 rows written, `success=True`**.

## Changes

| File | Change |
|---|---|
| `tools/genesis/reflex_registry.py` | Docstring no longer claims authority. States plainly that it schedules nothing and names the two things that do. `fathomdesk_pc_ratio` catalogued (it was module + config block only). |
| `tools/genesis/daemon.py` | `idp_score_recorder` added to `REFLEX_NAMES`. |
| `tools/genesis/reflexes/govcon_scan.py` | Demand-signal return-shape fix; `new_high` bound before the `try`. |
| `tests/test_reflex_registration.py` | Nine reasons rewritten from category labels to measured evidence; six new tests. |
| `README.md`, `.claude/commands/start.md` | Both stated `failure_triage` runs on a live 30-min Genesis cadence. It has never run. Corrected. |

Mirrored to `icdev/tools/...` for the three modules that live in both trees.

## Guards added

`tests/test_reflex_registration.py` now pins:

* `test_xbm_wake_02_registered_reflexes_honour_the_daemon_contract` — a registered reflex must
  expose `run(config, trust)` **and** set a `success` key. Registering is not enough.
* `test_xbm_wake_02_registered_reflexes_have_a_parseable_schedule` — `enabled: true` plus a
  schedule `parse_schedule()` accepts, which is the half of the trap `socmint` is still in.
* `test_audited_exemptions_name_a_real_invoker_or_blocker` — no reflex audited here may carry
  "unverified — inherited exemption", and a reason must be a sentence, not a category label.
* `test_no_new_unverified_exemptions` — `_UNVERIFIED_BASELINE` freezes the nine canvas/seed
  reflexes still carrying the placeholder. The set may shrink; a new name fails the test.
* `test_reflex_registry_no_longer_claims_to_be_authoritative` — the summary line may not claim
  authority, and the docstring must point at `REFLEX_NAMES`.
* `test_govcon_scan_demand_signal_shape_is_fixed` — pins the dict-vs-list bug closed.

## Still open — for whoever picks this up next

1. **`govcon_scan` runtime.** Decide a scan scope (fewer NAICS per cycle, skip the per-opportunity
   description fetch, or paginate across cycles) or an explicit `max_execution_seconds`, then
   register. The reflex itself is otherwise ready.
2. **Three reflexes need four lines each.** `failure_triage`, `nocc_sla_watcher` and
   `peering_agreement_renewal` need a `success` key in their return dict. That alone does not
   settle `failure_triage`, whose 30-min cadence with `ICDEV_AUTOFIX_ENABLED=true` is a
   blast-radius decision.
3. **FathomDesk on PostgreSQL.** `ad_reflex_cooldowns` and `ad_pc_ratio_history` need a
   migration with PG-native DDL. Three reflexes are blocked behind it.
4. **`socmint`.** Either ship `tools/strategos/socmint_harvester` or delete the reflex, the
   config block and the catalogue entry. It currently promises a capability that does not exist.
5. **Nine more grandfathered exemptions** (`_UNVERIFIED_BASELINE`) have never had this evidence
   pass: `aadc_reflex`, `aimc_orphan_refs`, `cyber_feed_refresh`, `sim_training_export`,
   `gameday_orchestrator`, `govchain_anchor`, `idc_cloud_drift`, `mdc_cutover_countdown`,
   `qdc_gate_breach`. They were outside this task's scope; the frozen baseline stops the set
   from growing while they wait.
