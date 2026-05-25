# Model + Harness Co-Learning — Research Report

**Date:** 2026-05-25  
**Task:** task-d605f3e709  
**Type:** Research  

---

## Overview

"Model + harness co-learning" describes a closed-loop architecture where the AI model (Claude / local Ollama) and the ICDEV™ execution harness mutually adapt based on each other's outputs. Currently ICDEV™ has extensive self-improvement infrastructure but the model↔harness feedback direction is incomplete. This document maps what exists, what is missing, and recommends the minimal bridge.

---

## What Already Exists

### 1. Harness → Model (signals the harness sends that could influence model behavior)

| Mechanism | File | What it does |
|-----------|------|--------------|
| Memory system | `tools/memory/memory_read.py` | Persists facts, events, insights across sessions; loaded at session start |
| FORGE context loading | `tools/project/session_context_builder.py` | Injects project state, prior decisions into every session |
| Hardprompts | `hardprompts/` | Reusable LLM instruction templates; currently static |
| Oracle predictions → kanban | `tools/oracle/`, `tools/awareness/suggested_card_writer.py` | High-confidence predictions auto-create tasks for the model to act on |
| Genesis feedback collector | `tools/genesis/feedback_collector.py` | Aggregates runtime failures, quality trends, heal outcomes daily |
| Innovation engine calibration | `goals/innovation_engine.md` | Adjusts 5-dimension scoring weights (max 0.02/cycle, min 10 data points) |

### 2. Model → Harness (what model outputs feed back into harness state)

| Mechanism | File | What it updates |
|-----------|------|-----------------|
| Learn reflex | `tools/genesis/reflexes/learn.py` | Generates training pairs from approved outputs → fine-tunes local Ollama (qwen3.5) |
| Evolve reflex | `tools/genesis/reflexes/evolve.py` | Proposes code improvements; stages as GKP patch for human review |
| Heal reflex | `tools/genesis/reflexes/heal.py` | Pattern confidence ±0.05/−0.10 per outcome |
| Goal learner | `goals/genesis_goal_learner.md` | Tokenizes past task outcomes → suggests new FORGE goals |
| Code intelligence | `tools/analysis/runtime_feedback.py` | Maps test pass rates to function health scores; feeds innovation engine |
| Memory auto-consolidate | `tools/memory/auto_consolidate.py` | Deduplicates/merges memory entries every 6h |

### 3. Self-Calibrating Sub-Systems

- **Pattern confidence decay** (`tools/knowledge/pattern_detector.py`): Half-life 90 days, per-project α=0.6 weighting
- **Confidence gating** (Oracle, self-heal, evolve): Threshold 0.70 across all autonomous actions
- **Circuit breaker** (`args/genesis_config.yaml`): 3 consecutive reflex failures → OPEN → human reset required
- **Time-decay memory** (`args/memory_config.yaml`): fact 90d, event 7d, insight 30d, thinking 3d half-lives

---

## Identified Gaps

These gaps were surfaced in `docs/research/continuous-harness-oracle-genesis-improvement.md` and confirmed by this analysis:

### Gap 1 (P0) — No Harness Pause Mechanism
The model can identify that the harness is in a bad state but cannot pause Genesis/Awareness reflexes without manual intervention.  
**Proposed:** `ICDEV_HARNESS_PAUSE` env var + circuit-breaker API endpoint the model can call via MCP.

### Gap 2 (P2) — Oracle Confidence Has No Feedback Loop
Oracle assigns confidence at prediction time but never updates calibration_factor based on whether predictions materialized.  
**Proposed:** `oracle_lens_calibration` table + weekly `oracle_calibrate` reflex that computes prediction accuracy per lens and adjusts multiplier.

### Gap 3 (P3) — Awareness Gap Rules Are Static
7 structural gap rules are hardcoded in `gap_detector.py`; successful/failed predictions do not influence rule weights.  
**Proposed:** `awareness_gap_rules_proposed` table + `goal_learner` integration to evolve rules from validated gap outcomes.

### Gap 4 (NEW) — Hardprompts Are Never Updated by Outcomes
`hardprompts/` templates are written once; there is no mechanism to detect that a template is producing low-quality outputs and flag it for revision.  
**Proposed:** Lightweight `prompt_outcome` table — record task_id, hardprompt_slug, quality_score (from code_analyzer or test pass rate). Weekly scan: slugs with avg quality < 0.6 → kanban suggestion to revise.

### Gap 5 (NEW) — No Session-Level Adaptation Signal
Each Claude session starts from static FORGE context. Success/failure of the current session's actions is not fed back to inform how the *next* session is seeded (beyond what memory auto-capture writes).  
**Proposed:** End-of-session `session_outcome` record (task count, pass rate, gate failures, heals triggered) written by a `post_stop` hook; used by `session_context_builder.py` to surface "last session hot spots" at startup.

---

## Co-Learning Architecture (Recommended Minimal Bridge)

```
┌─────────────────────────────────────────────────────────────┐
│                        Claude (Model)                        │
│  reads: memory, FORGE context, oracle predictions            │
│  writes: memory entries, task outcomes, code changes         │
└──────────┬────────────────────────────────────┬─────────────┘
           │ session_outcome (post_stop hook)    │ oracle predictions
           ▼                                     ▼
┌─────────────────────┐               ┌──────────────────────┐
│ session_context_    │               │  oracle_lens_        │
│ builder.py          │               │  calibration table   │
│ (seeds next session)│               │  (weekly calibrate   │
└─────────────────────┘               │   reflex)            │
                                      └──────────────────────┘
           ▲                                     ▲
           │ feedback_collector daily JSON        │ heal outcomes
┌─────────────────────────────────────────────────────────────┐
│                     Genesis Daemon                           │
│  learn / evolve / heal / awareness reflexes                  │
│  circuit breaker | rate limiter | confidence gates           │
└─────────────────────────────────────────────────────────────┘
```

**Key principle:** The model never directly edits its own prompts or confidence thresholds. All mutations go through the harness (Genesis reflexes, human-gated staging), maintaining the FORGE invariant that LLM reasoning orchestrates but deterministic tools execute.

---

## Implementation Priority

| Priority | Work item | Effort |
|----------|-----------|--------|
| P0 | Harness pause API (circuit breaker endpoint) | S |
| P1 | `session_outcome` record in `post_stop` hook | S |
| P1 | `session_context_builder.py` — surface last-session hot spots | S |
| P2 | `oracle_lens_calibration` table + `oracle_calibrate` weekly reflex | M |
| P2 | `prompt_outcome` table + weekly kanban scan | M |
| P3 | Evolve awareness gap rules via goal_learner | L |

**Legend:** S = <1 day, M = 1-3 days, L = 3-5 days

---

## Key Files for Follow-Up Implementation

- `tools/genesis/feedback_collector.py` — extend to write `session_outcome`
- `tools/project/session_context_builder.py` — consume `session_outcome`
- `tools/oracle/base_lens.py` — add `calibration_factor` multiplier
- `tools/awareness/gap_detector.py` — add rule weight column
- `.claude/hooks/pre_tool_use.py` — add new append-only tables
- `args/genesis_config.yaml` — register `oracle_calibrate` reflex
- `hardprompts/` — instrument with `prompt_outcome` logging

---

## References

- `docs/research/continuous-harness-oracle-genesis-improvement.md` — master gap inventory (P0–P3)
- `goals/genesis_goal_learner.md` — goal auto-generation from experience
- `goals/innovation_engine.md` — 7-stage self-discovery loop with weight calibration
- `tools/genesis/reflexes/learn.py` — training pair generation for local fine-tuning
- `context/capabilities/harness.yaml` — maturity assessment dimensions
- `args/genesis_config.yaml` — reflex scheduling and circuit breaker config
