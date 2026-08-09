# CUI // SP-CTI

# hgx-obs-02 — Reflex Dispatch Reconciliation

**Card:** HGX — Harness Agent Parity and Graph Runtime
**Status:** implemented
**Date:** 2026-08-09

## Problem

The Genesis self-improvement loop was largely not dispatched, in three distinct ways.
None of the three announced itself: every one of them looks, from the dashboard and
from `genesis_audit`, exactly like "ran and found nothing".

### 1. Registered but unschedulable

A reflex needs **both** halves to run, and neither half warns you when the other is
missing:

* `tools/genesis/daemon.py` `REFLEX_NAMES` — `DaemonBase.__init__` builds schedule and
  state entries only for names in this list.
* the `reflexes:` block of `args/genesis_config.yaml` — `__init__` reads
  `config["reflexes"][name]["schedule"]`, and with no block there is no schedule.

`run_due_reflexes` then does:

```python
schedule = self.schedules.get(name)
if not schedule:
    continue          # every cycle, forever, nothing logged
```

So a name in `REFLEX_NAMES` with no config block is **as dead as an unregistered
module**. `reflexion_loop` and `evolution` were in exactly that state.
`gepa_optimizer` was in neither half — while its own module docstring said
*"Runs every 24 hours via the genesis daemon."*

Measured 2026-08-09 before this task: 88 registered names, 85 config blocks, and the
two sets differed in **31** places.

### 2. ORANGE reflexes returned before `importlib`

```python
# tools/genesis/daemon.py, _run_reflex_impl_inner — before
if trust.requires_human_approval(risk_tier):
    return True, 0.0, {"status": "awaiting_human_approval", "risk_tier": risk_tier}
```

That fired **before the reflex module was imported**, so `evolve` and `experiment` —
the only two ORANGE reflexes — never executed a line of their mutation code. The cycle
was still recorded as a *success* with nothing attached, and the operator was told to
"approve and re-trigger" with no artifact naming what they were approving and no
trigger to press.

The guard was also self-defeating: both reflexes are propose-only by construction.
`evolve` ends at `_export_mutation_proposal` with `require_human_merge: true`;
`experiment` exports GKPs for promotion. Neither merges anything on its own. The early
return was suppressing exactly the artifact the ORANGE tier exists to produce.

### 3. Configured but never dispatched

Fourteen config blocks named reflexes absent from `REFLEX_NAMES`, so the daemon never
looked them up. Nine of those have modules and already carried a measured blocker in
`tests/test_reflex_registration.py` `EXEMPT` (`quality`, `failure_triage`,
`oracle_triage`, …). Five — `oracle`, `goal_learner`, `remediation_lens`,
`aadc_compliance`, `cost_optimizer` — have no module at all; their blocks are inert.

## What changed

### A parity guard — `tests/test_reflex_dispatch_parity.py`

The same class of guard `tests/test_migration_version_uniqueness.py` provides for
migrations, and the missing other half of `tests/test_reflex_registration.py` (which
guards module-vs-`REFLEX_NAMES`; this guards `REFLEX_NAMES`-vs-config).

It fails when a name is in `REFLEX_NAMES` but not config, or in config but not
`REFLEX_NAMES`, outside two **frozen, shrink-only** baselines:

| Baseline | Contents | Why grandfathered |
|---|---|---|
| `_MISSING_CONFIG_BASELINE` (17) | registered, no config block | each needs a cadence decision from its owner; hgx-obs-02 fixed the three named on the card rather than guessing at seventeen |
| `_CONFIG_ONLY_ORPHANS` (5) | config block, no module anywhere | inert blocks for names removed from `REFLEX_NAMES` (see the `rri:` comment in daemon.py) |

Config-only names that *do* have a module are deliberately not grandfathered here —
they must carry a measured blocker in `test_reflex_registration.EXEMPT`, and
`test_config_only_orphans_really_have_no_module` stops a writable reflex hiding behind
the orphan label.

`test_missing_config_baseline_has_not_rotted` and `test_config_only_orphans_are_still_orphans`
make both lists shrink-only: fixing a reflex forces its line out of the baseline.

### The three flywheel reflexes are dispatched

| Reflex | Tier | Cadence | Note |
|---|---|---|---|
| `reflexion_loop` | yellow | `weekly Sun 03:00` | no-ops unless `ICDEV_HARNESS_COLEARN` is set (checked inside the reflex) |
| `evolution` | yellow | `weekly Sun 04:00` | tuning stays in `args/nova_sela_config.yaml`; the config block is scheduling keys only |
| `gepa_optimizer` | yellow | `every 24h` | added to `REFLEX_NAMES`; removed from `EXEMPT` |

`gepa_optimizer.run()` returned `{"status": "ok", ...}` with **no `success` key**.
`DaemonBase` reads `result.get("success", False)`, so registering it as-is would have
scored every cycle a failure and tripped its circuit breaker in three — swapping one
silent failure for another. Its envelope is now
`{success, metric_value, details}`, and it honours `dry_run` from config. Promotion
still respects the `ICDEV_GEPA_FROZEN` freeze inside `tools/skills/gepa_optimizer.py`.

### The ORANGE decision: run in proposal mode, stage a reviewable artifact

`_run_reflex_impl_inner` now delegates to `GenesisDaemon._run_orange_proposal`, which:

1. imports the reflex module (`propose()` if it exposes one, else `run()`);
2. executes it under a **proposal-mode config overlay** — `proposal_only: True`,
   `require_human_merge: True`, `auto_apply: False`, `dry_run` defaulted on
   (`setdefault`, so a reflex configured to really write keeps its setting). The
   overlay copies the config; it never mutates the dict the daemon reuses across cycles.
   Today's two ORANGE reflexes never merge anyway — the overlay is there so a *future*
   one cannot apply a change merely because the daemon now runs it;
3. stages the outcome as an `orange_proposal` GKP at `pending_review`.

**No new surface.** `genesis_gkp` is the Genesis staging store; its `pending_review`
rows are already listed, inspected, promoted and rejected through
`/api/genesis/gkps*` and `tools/dashboard/templates/genesis.html`. No second review
queue, no second table, no second UI.

Degradation is explicit at every step:

| Situation | Behaviour |
|---|---|
| `ICDEV_GENESIS_ORANGE_PROPOSALS=0` | old early return, module not imported |
| module missing, or has neither `propose` nor `run` | old early return — do not invent an artifact describing nothing |
| GKP staging raises | reflex result still returned, `status: proposal_run`, `gkp_id: ""` |
| reflex fails | still staged (`reflex_success: false`) — the failed run is the only record it ran, and `success=False` is still reported to the breaker |

The `awaiting_human_approval: True` signal is preserved in the returned details; it is
now accompanied by a `gkp_id` an operator can actually open.

#### Why an ORANGE proposal cannot approve itself

`auto_promote_eligible()` matches pending GKPs on artifact_type / reflex / source.
`orange_proposal` is absent from `promoter.auto_promote` and listed under
`promoter.human_approve` — **that** is the mechanism, asserted directly by
`test_orange_proposal_can_never_auto_promote`.

`_import_orange_proposal` exists so the reviewer's Promote click succeeds and is
audited rather than erroring with "No import handler"; an artifact that can only ever
be rejected is half a review surface. It writes to no v1.x store (`table: None`) —
whatever the reflex wants merged travels as its own GKP (`evolve` exports a
`code_patch`, which carries its own `human_approve` rule) and is reviewed separately.

Enforcing "never auto-promote" by leaving the import handler broken would be silently
undone the day someone adds one; the config assertion cannot be.

## Files changed

| File | Change |
|---|---|
| `tools/genesis/daemon.py` | `gepa_optimizer` in `REFLEX_NAMES`; `_orange_proposal_config`, `_run_orange_proposal`, `_stage_orange_gkp`; both `awaiting_human_approval` early returns routed through the proposal path |
| `tools/genesis/promoter.py` | `orange_proposal` artifact type + `_import_orange_proposal` acknowledgement handler |
| `tools/genesis/reflexes/gepa_optimizer.py` | daemon envelope (`success`/`metric_value`/`details`); honours `dry_run` |
| `args/genesis_config.yaml` | config blocks for `reflexion_loop`, `evolution`, `gepa_optimizer`; `orange_proposal` under `promoter.human_approve` |
| `tools/genesis/CONTEXT.md` | ORANGE section rewritten — it documented the early return as intended behaviour |
| `tests/test_reflex_dispatch_parity.py` | **new** — the parity guard |
| `tests/test_genesis_orange_proposal.py` | **new** — the ORANGE proposal path |
| `tests/test_reflex_registration.py` | `gepa_optimizer` out of `EXEMPT`, with the measurement that justified it |
| `tests/test_genesis_a2a_fanout.py` | ORANGE test updated: still asserts ORANGE never fans out to A2A, now asserts it stages instead |
| `tools/manifest/genesis-v2-0-autonomous-research-lab.md` | both-halves rule and the ORANGE path documented |

`tools/genesis/{daemon,promoter,CONTEXT.md}` and `reflexes/gepa_optimizer.py` mirrored
to `icdev/tools/`.

## Verification

```bash
pytest tests/test_reflex_dispatch_parity.py tests/test_genesis_orange_proposal.py \
       tests/test_reflex_registration.py tests/test_genesis_a2a_fanout.py -q
# 58 passed

pytest tests/test_reflex_registry_integrity.py tests/test_reflex_health.py \
       tests/test_reflex_dependency_ordering.py tests/test_genesis_reflex_experiment.py \
       tests/test_dcpr_reflex_registration.py tests/test_dsyn_reflex_registration.py \
       tests/test_harness_reflex_registration.py tests/test_pdx_ops_reflex_registration.py -q
# 88 passed

pytest tests/test_mirror_symbol_parity.py tests/test_mirror_drift_baseline.py \
       tests/test_coherence_mirror_drift.py -q
# 25 passed

ruff check tools/genesis/    # clean
```

## Known gaps

* **17 reflexes remain registered with no config block** (`_MISSING_CONFIG_BASELINE`).
  Each is now visible and shrink-only rather than silent, but each still needs a
  cadence, risk tier and success metric from someone who knows what it should do.
  That is deliberately not a guess made here.
* The `test_db_isolation` coherence check reports one finding at
  `tests/test_genesis_a2a_fanout.py:222`. It is a **pre-existing false positive**,
  unchanged by this task: the check sees `patch(... get_connection)` near a raw
  `sqlite3.connect` in the same function, but the factory's return value is already the
  translating `_sql_compat.connect` wrapper. It surfaces only because the file entered
  this change's `--changed-files` set.
