# CUI // SP-CTI

# xbm-promote-01 — A benchmark finding becomes work

`tools/innovation/kanban_promoter.py` existed and was referenced by nothing.
The innovation engine wrote 1,179 signals, 91 trends, 403 competitor scans and
8 solutions, and the trail ended there. This wires the last step: a benchmark
finding on a subsystem ICDEV is behind on becomes a kanban card an operator
can confirm.

## Why it is gated the way it is

The prior art for an unbounded seeder in this repo is 353 branches. Three
gates bound this one, and each is load-bearing rather than decorative.

**Scope before rate limit.** `source_types` restricts the query to benchmark
sources (`external_repo_scouting`, `external_framework_analysis`) *before* any
cap applies. Promoting on `triage_result='approved'` alone would have queued
266 approved CVEs in a single run — CVEs have their own pipeline. A rate limit
applied to the wrong population is a slow flood, not a gate.

**Gap-gated.** A finding is promoted only when it maps to a benchmark
subsystem whose verdict says ICDEV is behind. Verdicts are transcribed from
the summary table of `docs/research/external-benchmark-map.md` into
`args/innovation_promoter.yaml`. A finding on a subsystem we are ahead on or at
parity with is intelligence, not work — it is counted and reported in
`skipped_not_a_gap`, never silently dropped.

`ahead_weak_hygiene` (section 7, delivery pipeline) is deliberately excluded
from `gap_verdicts`: that section's hygiene gaps are already tracked by the kpr
and tch cards, and promoting them would duplicate live work.

**Human-confirmed.** Cards are written `status='suggested'`, reusing
`tools/awareness/suggested_card_writer.py` semantics rather than inventing a
second promotion path. `promote_signals()` raises if handed anything else, so
reaching `backlog` stays an operator action on the board. This matters because
`backlog` is dispatchable — a promoter that can reach it can start an agent
without a human ever looking.

## Idempotency

Writes go through `tools.kanban.task_factory.create_tasks` with an
`idempotency_key` derived from the signal id (`innovation-promoter:<signal_id>`)
and a task id derived the same way — never from the clock. A clock-seeded id
makes every re-run look like new work and defeats the factory's dedup.

There is a second, independent dedup: the candidate query anti-joins on
`kanban_tasks.source_prediction_id`. This is what catches the 11 legacy
`INNOV-*` cards created by an earlier `genesis_scheduler` path, which predate
this module and carry no `idempotency_key` at all.

## Live proof (2026-08-03, production data)

Run as a dry run before enabling, per the task's requirement.

**Nothing to do, and that is correct.** The shipped configuration against the
live database returns `candidates: 0`. All 11 approved benchmark signals
already carry a card (the legacy `genesis_scheduler` ones above), so the
anti-join correctly excludes them. `would_create: 0`.

**The caps, proven on the same 11 real signals** with the dedup anti-join
lifted so the gate and caps have a population to act on:

| stage | count |
|---|---|
| real approved benchmark signals, score ≥ 0.5 | 11 |
| gap-verdict eligible | 8 |
| unmapped to any subsystem (skipped) | 2 |
| mapped but not a gap (`rag_knowledge_graph` → `parity`) | 1 |
| kept after caps (`max_per_run=5`, `max_per_subsystem=2`) | **4** |
| `truncated` | **True** |

Per-subsystem counts after the cap: `{developer_portal: 2, agent_runtime: 2}`.
The per-subsystem cap bit — `agent_runtime` alone had 5 eligible findings and
would otherwise have consumed the whole run. Forcing `max_per_run=3` on the
same set yields `kept: 3, truncated: True`, so the run cap bites independently.

Truncation is not silent. The emitted record:

```
WARNING CAP TRUNCATED: per-subsystem cap (2) held back 4 finding(s):
        {'agent_runtime': 3, 'developer_portal': 1}. They remain eligible on
        the next run.
```

Held-back findings are reported by id in `dropped_by_subsystem_cap` and
`dropped_by_run_cap`. A cap that drops findings with a bare `[:n]` slice is
indistinguishable from a promoter that found nothing — that is the whole
reason the counts are in the payload.

## Wiring

`tools/genesis/reflexes/scout.py` calls `run_promotion()` after each scout
pass, **off by default** (`args/scout_config.yaml` → `genesis_reflex.promotion.enabled: false`).
The brief is the reflex's contract; writing to the board is an escalation an
operator opts into. The call never raises — a promotion failure must not wedge
the reflex loop behind it.

`run_promotion()` itself defaults to `dry_run=True`, and the CLI requires an
explicit `--promote` to write. A promoter whose default is to write is a
promoter that writes by accident.

### The innovation engine runner (xbm-promote-01-d4)

The scout reflex is one producer of benchmark signals; the innovation engine
pipeline is the other, and it ended at GENERATE. `run_full_pipeline()` now
closes with a PROMOTE stage:

```
DISCOVER → SCORE → TRIAGE → GENERATE → PROMOTE
```

Placement is load-bearing rather than incidental. The promoter's candidate
query filters on `triage_result` and left-joins `innovation_solutions` for the
card body, so promoting before TRIAGE and GENERATE have persisted would query
a half-built row — an empty run indistinguishable from "nothing to promote".

The stage calls one function, `kanban_promoter.promote_findings_to_kanban()`,
which is the promoter's entry point for automated callers and adds exactly two
things to `run_promotion()`:

* **Off by default.** `KANBAN_PROMOTE_ENABLED` (unset / `false` / `0` / `no` /
  `off`) short-circuits *before* `get_connection()` — a disabled promoter opens
  no connection, so "off" can never be confused with "ran and found nothing".
  The variable is read per call, so a long-lived daemon sees the switch flip
  without a restart.
* **Cannot fail its caller.** Any exception is logged and returned as
  `{"enabled": True, "created": 0, "error": ...}`. The pipeline's own stages
  already degrade this way; a promotion failure must not wedge the run.

Everything that bounds a write is unchanged and not re-implemented at the call
site: the gap-verdict gate, `max_per_run`, `max_per_subsystem`, the
truncation report, and the `status='suggested'` invariant all still live in
`run_promotion()` / `promote_signals()`. Two switches now guard the same
promoter for two different runners — `genesis_reflex.promotion.enabled` in
`args/scout_config.yaml` for the reflex, `KANBAN_PROMOTE_ENABLED` for the
pipeline — and both default to off.

## Commands

```bash
python tools/innovation/kanban_promoter.py --dry-run --json   # preview (default)
python tools/innovation/kanban_promoter.py --list --json      # candidates + verdicts
python tools/innovation/kanban_promoter.py --promote --json   # write suggested cards
python tools/innovation/kanban_promoter.py --promote-id <signal_id>
```

## Tests

69 tests in `tests/innovation/test_kanban_promoter.py`. 43 cover the four
acceptance criteria: one gap verdict produces exactly one suggested task; a
re-run produces none; the caps are enforced and logged when they truncate; and
nothing can reach `backlog` without confirmation.

The rest pin the runner entry point (d4): off with the env unset — including
one that fails the test if `get_connection` is so much as called while
disabled; off for each negative spelling; on for each affirmative one, with
`dry_run` passed through; an exploding `run_promotion` returned rather than
raised; and the switch honoured per call rather than at import. Two more assert
the wiring itself — that `stage_promote` delegates to the promoter, and that
`run_full_pipeline` runs it *after* TRIAGE and GENERATE, since ordering is the
only reason the stage sits where it does.

41 of those assert against a recorded `create_tasks`. That is fast and precise
about the specs, but it cannot catch a spec whose columns the real
`kanban_tasks` does not have — a stubbed write reports success against a schema
that would reject it. The last two run the **real** `task_factory` against a
real SQLite file whose schema is built by the real `init_kanban_tables()`:

```
RUN 1 (real create_tasks): {'created': 1, 'task_ids': ['task-innov-0bd9bc49e9'],
                            'skipped_existing': 0, 'status': 'suggested'}
RUN 2 (same signal again): {'created': 0, 'task_ids': [],
                            'skipped_existing': 1, 'status': 'suggested'}
VERDICT: total=1 suggested=1 backlog=0
```

This is not hypothetical hardening. Building that proof against
`tests/conftest.py`'s `kanban_tasks` fails outright: the conftest schema is
missing `idempotency_key`, `acceptance_criteria`, `max_retries`,
`max_runtime_seconds`, `loop_type`, `adversarial_enabled`, `source_doc_id` and
`source_collection_id` — every one of which `create_tasks` writes. The fixture
pins `ICDEV_DB_PATH` with `monkeypatch.setenv` so the pointer cannot leak into
later tests; a stray one silently redirects every subsequent `get_connection()`
at a dead tmpdir.
