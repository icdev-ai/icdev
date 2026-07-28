---
name: icdev-divergence
description: "Run Divergent Ideation — a single isolated generative fan-out that produces many candidate ideas, then a separate critic scores them on novelty/viability/fit and flags seductive-but-broken traps. Use when a single-shot answer risks tunnel vision at a consequential decision point. OPT-IN and higher-cost than a direct answer."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-divergence

## What This Does
Divergent Ideation is the generative counterpart to the LLM Council. Instead of
critiquing one decision, it WIDENS the option space:

1. **Generate (divergence)** — one isolated round fans the problem out across
   several generative frames (First-Principles Rebuild, Analogical Transfer,
   Inversion, …). Branches never see each other; each produces candidate ideas
   and is forbidden from evaluating or ranking.
2. **Focus (critic)** — a SEPARATE, opposing LLM call scores every candidate on
   novelty / viability / fit (categorical enums composed to a number in Python,
   never a free-form model rating) and flags **traps** — seductive-but-broken
   ideas — with a mandatory written why. Trap flags are ADVISORY, never a blocker.

Cost is the headline risk (many model calls, several times a direct answer), so
divergence is OPT-IN per function and never a default path.

## Steps

### 1. Generate the idea pool
Divergence must be enabled for the target function in `args/llm_config.yaml`
(`chain_orchestration.divergence.per_function.<fn>.enabled: true`). Then:
```bash
python tools/llm/chain_orchestrator.py --divergence --function capability_deliberation --prompt "How should we reduce cold-start latency in the enclave?" --json
```
This prints the raw, frame-labeled idea pool plus telemetry (models_used, cost,
trace_id, stop_reason). Save the `content` field to a file for scoring.

### 2. Score + trap-flag the pool (the Focus half)
```bash
python tools/quality/divergence_critic.py --function capability_deliberation --pool-file .tmp/idea_pool.md --json
```
Returns the ideas ordered by composite score (composed in Python), each with its
novelty/viability/fit labels, a rationale, and any advisory trap warning.

### 3. (Optional) One-shot via MCP
The `divergence_invoke` MCP tool runs both halves in one call (`score: true`)
and returns `content`, `scored`, and advisory `trap_warnings` — the same surface
cross-repo callers (e.g. idea_lab) use for `council_query`.

## Notes
- Deterministic-first: the LLM proposes ideas and emits categorical judgments;
  Python composes every score and the ordering. LLM-agnostic — no vendor SDKs or
  hardcoded model IDs.
- CUI: divergence inherits the function's existing LOCAL-ONLY routing and opens
  no new egress path.
