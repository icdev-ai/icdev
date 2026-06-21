# Phase ACF — Autonomous Capability Foundry

CUI // SP-CTI

> A 0→1 product factory: continuously and autonomously **invents, designs, decomposes,
> and ships brand-new capabilities** into the ICDEV™ ecosystem. Distinct from Oracle /
> Genesis reflexes (which improve *existing* tasks incrementally) — ACF creates *net-new*
> products end-to-end.

## Problem

The Innovation, Creative, and Research engines produce **signals but not products**:
noisy GitHub dumps, uniform scores, irrelevant pain points (verified 2026-06-05 — three
full engine runs yielded no coherent "build this next" output). The Kanban autonomous
dispatcher can *build* anything but needs a clean, atomic, dependency-ordered task graph.
**Nothing bridges signal-discovery → coherent net-new product → autonomous build.** ACF is
that bridge.

## Design decisions (locked 2026-06-05)

1. **Layer on existing engines** — ACF consumes the Innovation/Creative/Research signal
   stores + Genesis predictions + internal telemetry; it does not build new scanners.
2. **Fully autonomous — no human concept gate.** Approved-by-score concepts auto-decompose
   and queue for build. This makes the *automated* gates load-bearing (below).
3. **Continuous** — runs as a scheduled Genesis daemon reflex (`foundry_cycle`).
4. **Full-Canvas output** — every generated capability targets the 8-component completeness
   gate (template + route + module + IQE + nav + DB + icdev/ mirror + tests).

## Guardrails (because there is no human gate)

- **Novelty gate** — dedup every concept against the existing capability catalog
  (`canvas_registry`, `tools/manifest/*`, `goals/manifest.md`). Reject concepts too similar
  to existing ones. *This is the differentiator from Oracle: it forbids incremental rehashes.*
- **Composite score threshold** — novelty × market-signal × ICDEV-fit ÷ effort, with a
  compliance/risk penalty; below threshold → rejected with reason.
- **Rate limits** — `max_concepts_per_cycle` (default 1) and `max_active_foundry_projects`
  (default 2) so the board is never flooded.
- **Circuit breaker** — if recent ACF-spawned projects fail V&V above a threshold, new
  concept generation pauses until a human clears it.
- **Self-vetting** — ACF-spawned build tasks carry an `integrity_gate` flag so the dispatcher
  runs **SIPA** (`tools/integrity/engine.py --gate`) + the standard security/coherence gates
  on the generated code before merge. ACF cannot ship code SIPA quarantines.
- **Air-gap safe** — all LLM use is optional with a deterministic fallback; CUI classification
  on every row.

## Architecture — `tools/foundry/`

| Module | Role |
|--------|------|
| `constants.py` | enums (source_engine, concept_status, outcome), score weights, thresholds, rate limits, `ICDEV_FOUNDRY_ENABLED` |
| `db/init_db.py` | PG-first dual schema; 6 RLS tables (tenant_id/classification; append-only where noted) |
| `harvester.py` | pull signals from innovation/creative/research stores + genesis predictions + introspective telemetry → `foundry_signals` |
| `synthesizer.py` | cluster signals → coherent concept candidates (deterministic co-occurrence + optional CoT assist w/ fallback) |
| `novelty_gate.py` | dedup vs capability catalog → novelty score; reject duplicates/incremental |
| `scorer.py` | composite scoring + thresholds + reject reasons |
| `deliberator.py` | **CoT/CoD decision layer** (wraps `tools/llm/chain_orchestrator`): CoD go/no-go approval gate (market/feasibility/compliance/novelty debaters → judge) + CoT synthesis assist; deterministic-first, defers to score gate on fallback |
| `spec_generator.py` | concept → spec markdown + canvas contract (reuse creative/innovation generators) |
| `task_graph.py` | **canonical canvas-epic templater**: concept → atomic db→core→engine→dash(8-comp)→mcp→reflex→doc→vv task list |
| `seeder.py` | emit tasks to `kanban_tasks` (scheduled) + `kanban_project_sync` + `foundry_tasks_emitted` + register in `args/projects.yaml` |
| `learner.py` | record outcomes (kanban status → `foundry_outcomes`) + tune scorer weights + feed circuit breaker |
| `engine.py` | orchestrate harvest→synth→novelty→score→design→decompose→queue; CLI; rate-limited; persists `foundry_runs` |
| `blueprint.py` | `/foundry` canvas — concept pipeline board, spec viewer, emitted-task links, outcomes; JSON API + IQE |

DB tables: `foundry_signals` (append-only), `foundry_concepts`, `foundry_specs`
(append-only), `foundry_tasks_emitted` (append-only), `foundry_outcomes` (append-only),
`foundry_runs` (append-only).

## The loop

```
Genesis reflex (cadence)
  → harvester  → foundry_signals
  → synthesizer → concept candidates
  → novelty_gate (reject incremental/duplicate)
  → scorer (threshold)            ── rejected concepts logged with reason
  → deliberator CoD go/no-go      ── multi-LLM debate on score-passing survivors only
                                     (build / reject / defer; deterministic fallback)
  → spec_generator → canvas contract
  → task_graph → atomic task list
  → seeder → kanban_tasks (scheduled)  ── rate-limited, circuit-breaker-gated
        → existing dispatcher builds each task (SIPA + security + coherence gates)
        → V&V → ship
  → learner ← outcomes → tune weights
```

## Build

Seeded as Kanban project `acf` (prefix `acf-`, 28 atomic tasks, root `acf-db-01`,
final gate `acf-vv-04`) via `tools/kanban/seed_foundry.py` → built autonomously by the
dispatcher. Once `ICDEV_FOUNDRY_ENABLED=true` and the reflex is registered, ACF begins
proposing and building net-new capabilities on its own.
