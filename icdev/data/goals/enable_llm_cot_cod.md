# FORGE Goal: Enable LLM Chain of Thought / Chain of Debate

## Problem

Single-LLM calls lack reasoning transparency and robustness. For complex tasks
(architecture review, threat modeling, compliance assessment), a single pass
often misses edge cases, makes unstated assumptions, or produces inconsistent
results across repeated invocations.

## Solution

Router-native CoT/CoD multi-LLM orchestration:

- **Chain of Thought (CoT)**: reason → critic → synthesize. Up to N rounds.
  Self-consistency mode runs the chain multiple times and takes a majority vote.
- **Chain of Debate (CoD)**: parallel debaters argue positions → neutral judge
  evaluates and selects the strongest argument.

Both modes flow through the existing router pipeline (redaction, RAG, cache,
gateway, telemetry) and respect cost/token/timeout caps.

## Workflow

1. **Detect** high-variance tasks via `cost_intelligence.py` quality scoring
2. **Enable** CoT/CoD per-function in `args/llm_config.yaml`
3. **Verify** quality improvement via telemetry dashboard
4. **Report** cost delta vs single-LLM baseline

## Tools

| Tool | Path | Role |
|------|------|------|
| Chain Orchestrator | `tools/llm/chain_orchestrator.py` | Core engine |
| Chain Prompts | `tools/llm/chain_prompts.py` | Jinja2 prompt templates |
| LLM Router | `tools/llm/router.py` | Routing + mode switch |
| Cost Intelligence | `tools/llm/cost_intelligence.py` | Auto-recommendation |

## Expected Outputs

- Reasoning traces (CoT) stored in `llm_chain_telemetry`
- Debate transcripts (CoD) stored in `llm_chain_telemetry`
- Quality scores from judge synthesis
- Cost reports comparing chain vs single-LLM

## Success Criteria

- Quality score improvement > 15% for `architecture_review` within 14 days
- Zero cost overruns (all chains respect `$0.50` cap)
- Telemetry shows `chain_mode='cot'` or `chain_mode='cod'` for 100% of chain invocations

## Classification

CUI // SP-CTI
