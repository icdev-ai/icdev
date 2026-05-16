# LLM Chain Orchestration (CoT / CoD)

> Multi-LLM reasoning engine. CoT = reason → critic → synthesize. CoD = parallel debate → judge.

## Tools

### `tools/llm/chain_orchestrator.py`

Multi-LLM orchestration engine.

| Method | Description |
|--------|-------------|
| `ChainOrchestrator.invoke_chain_of_thought(function, request)` | Run CoT: reasoner → critic → synthesizer (up to `max_rounds`). Returns `ChainResult`. |
| `ChainOrchestrator.invoke_chain_of_debate(function, request)` | Run CoD: N parallel debaters → neutral judge synthesis. Returns `ChainResult`. |

**Config:** `args/llm_config.yaml` → `chain_orchestration` section (cost cap, token cap, timeout, per-function overrides, model assignments).

**Telemetry:** writes to `llm_chain_telemetry` table in `icdev.db`. Publishes `cot_reasoning_completed` events via `tools/canvas/event_bus`.

**CLI:**

```bash
python -c "
from tools.llm.chain_orchestrator import ChainOrchestrator
from tools.llm.provider import LLMRequest
o = ChainOrchestrator()
r = o.invoke_chain_of_thought('code_generation', LLMRequest(function='code_generation', messages=[{'role':'user','content':'Explain NIST RMF step 3'}]))
print(r.content[:200])
"
```

---

### `tools/llm/chain_prompts.py`

Jinja2 prompt templates for each role.

| Method | Role |
|--------|------|
| `ChainPrompts.reasoner(...)` | Initial reasoning step |
| `ChainPrompts.critic(...)` | Critique of reasoning |
| `ChainPrompts.synthesizer(...)` | Final synthesis |
| `ChainPrompts.debater(...)` | Parallel debate position |
| `ChainPrompts.judge(...)` | Neutral synthesis of debate |
| `ChainPrompts.self_consistency_voter(...)` | Majority-vote synthesis |

---

## Integration Points

| System | Integration |
|--------|-------------|
| **LLM Router** | `router.invoke_chain_of_thought()` / `invoke_chain_of_debate()` via `chain_mode` field on `LLMRequest` |
| **SDC / BDC** | `/api/twin/<id>/simulate-cot` and `/api/twin/<id>/simulate-cod` routes |
| **AADC** | `/agentic-ai/api/designs/<id>/simulate-cot` and `/simulate-cod` routes |
| **Academy** | `apps/forge_academy/ai_coach.py` — CoT explanations + CoD debate mode |
| **GameDay** | `tools/ai_game_engine/chain_bridge.py` — `debate_strategy()` + `reason_strategy()` |
| **HITL Workflow** | `cot_trace_id` column in `wf_approvals` |
| **Kanban** | `cot_enabled` flag + `cot_trace_id` in `TransitionResult` |
| **Loop Engine** | `cot_config` in acceptance criteria |
| **Auto-Remediate** | CoT reasoning stored in remediation decisions |
| **MCP** | `cot_invoke` + `cod_invoke` tools in `tool_registry.py`; handlers in `gap_handlers.py` |
| **Knowledge Graph** | `reasoning_step` node type indexed by `kg_builder.py` with step_name, model_id, chain_mode, trace_id, round_num |
| **Event Bus** | `cot_reasoning_completed` published after every chain invocation |
| **Cost Intelligence** | `enable_cot` / `enable_cod` recommendation types for high-cost functions |
| **Readiness Score** | 5th `explainability` dimension in `tools/canvas/orchestrator.py:compute_readiness()` |

## FORGE Artifacts

| Artifact | Path |
|----------|------|
| Goal | `goals/enable_llm_cot_cod.md` |
| Policy | `context/llm_chain_policy.md` |
| CoT hard prompt | `hardprompts/chain_of_thought.md` |
| CoD hard prompt | `hardprompts/chain_of_debate.md` |
| ANVIL command | `.claude/commands/cod.md` |
| Tests | `tests/tools/llm/chain_orchestrator_test.py` |
