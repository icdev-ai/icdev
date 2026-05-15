# LLM Chain Policy

> Operational policy for Chain of Thought / Chain of Debate multi-LLM orchestration.

Classification: CUI // SP-CTI

## Chain Design

### Chain of Thought (CoT)

```
Round 1: Reasoner → Critic → (loop)
Round N: Reasoner → Synthesizer → Final Answer
```

- Reasoner thinks step by step, shows full reasoning.
- Critic reviews reasoning, identifies errors/gaps/assumptions.
- Synthesizer combines reasoning and critique into polished final answer.
- Up to `max_rounds` (default 3). Per-function override supported.

### Chain of Debate (CoD)

```
Debate Round 1: Debater 1 | Debater 2 | Debater 3 (parallel)
Debate Round 2: Debater 1 | Debater 2 | Debater 3 (parallel, with prior arguments)
...
Judge: Evaluate all positions → strongest argument + confidence
```

- Each debater argues a distinct position.
- Debaters address prior arguments from other debaters.
- Judge is neutral; does not average — picks the best reasoning.
- `num_debaters` (default 3), `debate_rounds` (default 2).

### Self-Consistency

Run CoT `self_consistency_runs` times in parallel. Use synthesizer as a
"majority vote" judge to pick the consensus answer.

## Cost Caps and Safety Controls

| Control | Default | Abort Behavior |
|---------|---------|----------------|
| cost_cap_usd | $0.50 | `stop_reason='budget_exceeded'` |
| token_cap | 32,000 | `stop_reason='budget_exceeded'` |
| timeout_seconds | 120 | `stop_reason='timeout'` |

All caps are per-chain, not per-step. A chain aborts immediately when any
cap is exceeded.

## PII Handling

Each step flows through `_pre_invoke_redaction()` before the LLM call and
`_post_invoke_deanonymize()` after. No raw PII is sent to cloud models.

When routing is local-only (Ollama), redaction is skipped per config.

## Security Mitigations

- **Timing jitter**: Not yet implemented. Future: add random delay between steps
to prevent timing side-channels.
- **Excluded functions**: `pulse_generation`, `news_oracle`, `market_scan` are
excluded from CoT/CoD by default to avoid unnecessary cost on high-volume,
low-variance tasks.
- **Prompt injection scan**: Applied at chain entry, not per-step (to avoid
redundant scans).

## Workflow Integration

### HITL Engine

CoT traces are recorded in `wf_approvals.cot_trace_id` when auto-advance fires.
If `cot_required: true` in stage config, auto-advance gates until a CoT trace
is present.

### Kanban State Machine

`kanban_status_transitions.reason` stores CoT rationale JSON.
`kanban_tasks.cot_enabled` flag (default false) controls whether CoT is required
for task completion.

### Loop Engine

`loop_engine.verify_criterion()` accepts optional `cot_config`. All criteria
must have CoT evidence if `cot_config` is set.

### Auto-Remediation

`attempt_remediation()` carries CoT traces in the `info` dict explaining the
chosen remediation path.

## Digital Twin Integration

- `security_canvas/twin.py`: `simulate_delta(use_cot=True)` reasons over
  STRIDE coverage and picks the safest delta.
- `boundary_canvas/twin.py`: `simulate_delta(use_cod=True)` debates policy
  deltas before applying.

## Operational Runbook

### Enable CoT for a function

Edit `args/llm_config.yaml`:

```yaml
chain_orchestration:
  cot:
    per_function:
      my_function:
        max_rounds: 5
        self_consistency_runs: 3
```

### Disable CoT/CoD globally

```yaml
chain_orchestration:
  enabled: false
```

### View chain stats

```bash
python tools/llm/chain_orchestrator.py --stats --json
```

### View chain config

```bash
python tools/llm/chain_orchestrator.py --show-config --json
```
