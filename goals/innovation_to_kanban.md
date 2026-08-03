# Goal: Promote Benchmark Findings to Kanban

> Turn an external benchmark finding into confirmable work, without letting the board flood.

## Context
The Innovation Engine (`goals/innovation_engine.md`) scans external sources, scores signals, and triages them into `approved`, `suggested`, `logged`, or `blocked`. The Genesis Scout Reflex (`tools/genesis/reflexes/scout.py`) monitors the benchmark watchlist in `context/genesis/competitors.yaml`. Both produced intelligence with no downstream path to execution.

`tools/innovation/kanban_promoter.py` closes that gap. It is deliberately *narrow*: it promotes benchmark findings only, and only when the benchmarked subsystem is one `docs/research/external-benchmark-map.md` judged deficient.

**Why so narrow.** `innovation_signals` is dominated by non-benchmark rows. Measured 2026-08-03: 588 CVEs and 410 CLI-harmonization findings out of ~1,179 total, with **266 CVEs carrying `triage_result='approved'`**. Promoting on triage state alone would queue hundreds of tasks in one run. (These counts grow; re-measure rather than trusting them.) There is direct prior art for an unbounded seeder producing 353 branches. Scope first (`source_types`), gate on verdict second, rate-limit third.

## Workflow
1. `python tools/innovation/kanban_promoter.py --list --json` — show candidates with the subsystem each maps to and its benchmark verdict.
2. `python tools/innovation/kanban_promoter.py --dry-run --json` — preview. **This is the default**; the tool does not write unless asked.
3. `python tools/innovation/kanban_promoter.py --promote --json` — create the cards as `kanban_tasks(status='suggested')`.
4. Open `/kanban` — findings appear in the **Suggested** column with `INNOV-<prefix>` titles.
5. A human promotes Suggested → Backlog (or dismisses) via the UI. **Nothing reaches backlog automatically.**

## The gap-verdict gate
A finding is promoted only if it maps to a benchmark subsystem whose verdict is in `gap_verdicts`. Mapping precedence:

1. `metadata.subsystem` on the signal — the routing tag the scout watchlist attaches to a target. Authoritative.
2. the signal `category`, via the `categories` list on each subsystem in `args/innovation_promoter.yaml`.
3. unmapped → **not promoted**, counted in `skipped_unmapped`.

Findings on subsystems ICDEV is *ahead* on (observability, compliance/ATO) or at *parity* with (RAG, IaC) are recorded in `skipped_not_a_gap` and produce no work. That is the point: a benchmark that only generates tasks is a task generator, not a benchmark.

## Rate limits
- `max_per_run` (default 5) — hard ceiling on cards created per invocation.
- `max_per_subsystem` (default 2) — no single subsystem consumes the whole run.

Highest-scoring findings survive a cap; the rest stay eligible for the next run. When a cap truncates, it is logged at **WARNING** (`CAP TRUNCATED: …`) and reported in the result payload under `truncated`, `dropped_by_run_cap` and `dropped_by_subsystem_cap`, listed by signal id. A cap that drops findings silently is indistinguishable from a promoter that found nothing.

## Idempotency
Writes go through `tools.kanban.task_factory.create_tasks` — never a raw INSERT. Each spec carries:
- a **stable task id** — `task-innov-<sha256(signal_id)[:10]>`, derived from the signal, never from the clock
- a **stable idempotency key** — `innovation-promoter:<signal_id>`

`create_tasks` skips on either collision, so a re-run creates zero rows. The query also excludes signals already present in `kanban_tasks.source_prediction_id`.

## Provenance
Every card stamps `source_prediction_id = innovation_signals.id` and `dispatch_source = 'innovation_promoter'`. The description records the subsystem, its verdict, the projects it was benchmarked against, and the ICDEV surface a fix would land on. Each run appends one `audit_trail` row (`innovation.kanban_promote`) recording what was created and what the caps held back.

## Automation
The Scout Reflex calls the promoter after each pass, gated by `genesis_reflex.promotion.enabled` in `args/scout_config.yaml` (**default: false**). Enabling it is an operator decision; cards still land as `suggested`. A promotion failure is logged and never fails the scout pass.

## Configuration
`args/innovation_promoter.yaml`:
- `source_types` — which signals count as benchmark findings (default: `external_repo_scouting`, `external_framework_analysis`)
- `triage_results` — which triage states to promote (default: `[approved]`)
- `min_innovation_score` — score gate, 0–1 scale (default: 0.5)
- `max_per_run` / `max_per_subsystem` — rate limits (default: 5 / 2)
- `gap_verdicts` — which verdicts warrant work (default: `gap`, `parity_with_named_gaps`)
- `benchmark_subsystems` — the 10 map subsystems, each with `verdict`, `categories`, `icdev_surface`
- `priority_thresholds` — score → priority mapping (high=0.7, medium=0.5)

## Related
- `docs/research/external-benchmark-map.md` — the source of every verdict
- `tools/awareness/suggested_card_writer.py` — the promotion semantics this reuses
- `goals/innovation_engine.md` — upstream producer
- `tools/genesis/reflexes/scout.py` — the automated caller
- `tools/innovation/kanban_promoter.py` — this promoter

## Acceptance
- A finding on a gap-verdict subsystem produces exactly one `suggested` card
- Re-running produces zero
- A finding on an `ahead`/`parity` subsystem produces none
- The per-run and per-subsystem caps are enforced, and truncation is logged and reported
- Nothing reaches `backlog` without a human moving it
