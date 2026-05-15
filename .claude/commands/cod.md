# Chain of Thought / Chain of Debate

> ANVIL workflow command for multi-LLM reasoning and debate.

## Run CoT

```bash
python tools/llm/chain_orchestrator.py --cot --function <name> --prompt '...' --json
```

## Run CoD

```bash
python tools/llm/chain_orchestrator.py --cod --function <name> --prompt '...' --json
```

## Self-consistency

```bash
python tools/llm/chain_orchestrator.py --cot --self-consistency 3 --function <name> --prompt '...' --json
```

## Stats

```bash
python tools/llm/chain_orchestrator.py --stats --json
```

## Show Config

```bash
python tools/llm/chain_orchestrator.py --show-config --json
```

## Router Integration

Set `chain_mode` on `LLMRequest` before calling `router.invoke()`:

```python
from tools.llm.router import LLMRouter
from tools.llm.provider import LLMRequest

router = LLMRouter()
req = LLMRequest(
    messages=[{"role": "user", "content": "Explain quantum computing"}],
    chain_mode="cot",
)
response = router.invoke("code_generation", req)
```

## Examples

### Architecture Review with CoT

```bash
python tools/llm/chain_orchestrator.py \
  --cot --function architecture_review \
  --prompt "Review this microservice design: ..." \
  --json
```

### Risk Assessment with CoD

```bash
python tools/llm/chain_orchestrator.py \
  --cod --function architecture_review \
  --prompt "Debate: Should we use a monolith or microservices?" \
  --json
```

## Configuration

Edit `args/llm_config.yaml` under `chain_orchestration`:

```yaml
chain_orchestration:
  enabled: true
  cost_cap_usd: 0.50
  token_cap: 32000
  timeout_seconds: 120
  cot:
    enabled: true
    max_rounds: 3
    self_consistency_runs: 1
    reasoner_model: qwen3-local
    critic_model: claude-sonnet
    synthesizer_model: claude-sonnet
  cod:
    enabled: true
    num_debaters: 3
    debate_rounds: 2
    judge_model: claude-sonnet
    debater_models: [qwen3-local, claude-sonnet, openai-gpt4o]
```

## Per-Function Overrides

```yaml
cot:
  per_function:
    code_generation:
      max_rounds: 5
      self_consistency_runs: 3
cod:
  per_function:
    architecture_review:
      num_debaters: 5
      debate_rounds: 3
```

## Cost Safety

- Hard cap: `$0.50` per chain (configurable)
- Token cap: `32K` tokens per chain
- Timeout: `120s` per chain
- Excluded functions: `pulse_generation`, `news_oracle`, `market_scan`

## Classification

All chain prompts include CUI // SP-CTI banner. All telemetry writes to
`llm_chain_telemetry` with full round-level detail.
