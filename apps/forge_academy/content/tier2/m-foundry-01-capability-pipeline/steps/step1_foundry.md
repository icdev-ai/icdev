---
ontology_id: icdev:mission:m-foundry-01-capability-pipeline:step:1
step_class: icdev:Lab
---

# Autonomous Capability Foundry — 0 to 1, No Human in the Loop

Most AI "automation" improves work that already exists: Oracle predicts the next kanban
card, Genesis reflexes tune what is already running. The **Autonomous Capability Foundry**
(ACF, `tools/foundry/`) does something different — it invents a **net-new ICDEV canvas**
end to end, with no human approving the concept. The engine (`tools/foundry/engine.py`,
`run_cycle()`; MCP tools `foundry_run` / `foundry_status`) runs one loop:

```
harvest -> synthesize -> novelty-gate -> score -> CoD go/no-go -> spec -> task-graph -> seed
```

Because there is no human concept gate, the **guardrails are the gates themselves**. This
lab builds the two that decide whether an idea lives, plus the seeder that turns a survivor
into buildable work.

## The novelty gate — invent, don't rehash

An autonomous factory that re-proposes canvases ICDEV already has is worse than useless.
`tools/foundry/novelty_gate.py` builds a **catalog** of what the platform already ships —
active canvases (`canvas_registry`), tool manifests (`tools/manifest/*.md`), and goal
workflows (`goals/manifest.md`) — then scores a concept against it:

```
novelty_score = 1 - max_similarity(concept_capability, catalog)
```

The verdict rules (in order):

- `max_similarity >= duplicate_similarity` (default 0.8) -> **duplicate** (reject)
- `novelty_score < min_novelty` (default 0.35) -> **low_novelty** (reject)
- otherwise -> **pass**

The scoring is deterministic (token-frequency cosine blended with Jaccard overlap), so it
is air-gap safe; an optional LLM embedding pass only re-ranks. Your `novelty_score()` models
this with plain Jaccard.

## Chain-of-Debate go/no-go

Survivors of the novelty gate are scored (composite = novelty x market-signal x ICDEV-fit
÷ effort, minus compliance/risk penalty) and then face a **Chain-of-Debate (CoD)**: separate
market / feasibility / compliance / novelty debaters argue, and a judge rules go or no-go.
When the multi-LLM debate is unavailable, the engine falls back to the **deterministic score
gate** — `composite_score >= min_composite` (default `0.6`, `scoring.min_composite`) — because
`deliberation.defer_to_score_on_fallback` is set. Your `cod_go_no_go()` is that fallback.

## Seeding the task graph

An approved concept becomes a spec (`spec_generator.generate_spec` -> a canvas contract),
then `task_graph.build_task_graph` emits the **canonical full-canvas skeleton** — the same
epic shape the SIPA integrity seeder uses — in a fixed order:

```
db -> core -> engine -> dash -> mcp -> reflex -> doc -> vv
```

Task ids follow `f"{slug}-{epic}-{n:02d}"` with a linear `depends_on` chain, and every
**build** task (code-generating epics) carries `integrity_gate=True` so the dispatcher runs
**SIPA** (`tools/integrity/engine.py --gate`) on the generated code before merge — *ACF cannot
ship code SIPA quarantines*. Documentation (`doc`) and verification (`vv`) tasks produce no
shippable code, so they carry no integrity gate. Your `seed_task_graph()` emits that list.

Open `step1_starter.py` and implement the four `TODO`s. The handlers are offline and
deterministic — the real engine harvests from live stores and calls LLMs, but the gate logic
you build here is exactly what decides whether a brand-new canvas gets born.
