# LLM Chain Orchestration (CoT / CoD)

> Multi-LLM reasoning engine. CoT = reason → critic → synthesize. CoD = parallel debate → judge.

## Tools

### `tools/llm/chain_orchestrator.py`

Multi-LLM orchestration engine.

| Method | Description |
|--------|-------------|
| `ChainOrchestrator.invoke_chain_of_thought(function, request)` | Run CoT: reasoner → critic → synthesizer (up to `max_rounds`). Returns `ChainResult`. |
| `ChainOrchestrator.invoke_chain_of_debate(function, request)` | Run CoD: N parallel debaters → neutral judge synthesis. Returns `ChainResult`. |

**Config:** `args/llm_config.yaml` → `chain_orchestration` section (cost cap, token cap, timeout, per-function overrides, model assignments, role keys).

**Role-based routing (multi-LLM):** CoT roles (`reasoner_role`, `critic_role`, `synthesizer_role`) and CoD roles (`judge_role`, `debater_pool_role`) reference routing chain keys in `routing:`. Each role resolves through the full `LLMRouter` stack (availability check, RL reranking, fallback chain) via `router.invoke_for_role()`. CoD uses `router.get_diverse_models(cod_debater_pool, num_debaters)` to assign distinct models from different provider families to each debater slot, ensuring genuine multi-LLM debate. Legacy `*_model` keys remain as per-function override fallbacks.

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

## Reasoned Codegen (`tools/llm/reasoned_codegen.py`)

Opt-in **Generate → Critique → Verify → Repair** wrapper that composes the CoT/CoD
engine above with `anvil_critique` and a *pluggable verifier* into one cost-bounded
loop for code-generation pipelines. Drop-in upgrade for a single `router.invoke(fn, req)`
call; byte-identical passthrough when its config resolves to `mode:off` + `critique:false`.

| Function | Signature |
|----------|-----------|
| `generate_reasoned_code(*, function, request, verifier=None, verifier_context=None, project_id=None, mode=None, critique=None, max_repair_rounds=None, router=None)` | Returns `ReasonedCodegenResult(code, passed, mode, rounds_used, critique_consensus, verification, total_cost_usd, total_tokens, stop_reason, history, ...)` |
| `resolve_config(function, router)` | Merge global + per_function `reasoned_codegen` config |
| `section_enabled(router)` | Section-level kill-switch (`reasoned_codegen.enabled`) |
| `VerificationResult(passed, score, findings, gate_result, detail)` | Injected-verifier return type; `Verifier = Callable[[str, dict], VerificationResult]` |

Config: `args/llm_config.yaml` → `reasoned_codegen` (global defaults + per_function;
all generation OFF except `code_translation`). Cost scaling + kill-switch documented inline there.

### Reasoned Codegen Advisor (`tools/llm/reasoned_codegen_advisor.py`)

Decides whether reasoned codegen pays off for a task (AI-assisted enable decision).
Hybrid deterministic-first: heuristic signals (complexity, security/compliance keywords,
file count, prior failures) give a zero-cost baseline; an optional cheap-tier LLM call
refines it. No-LLM mode → heuristics only.

| Function | Signature |
|----------|-----------|
| `recommend(function, spec, context=None, router=None, use_llm=True)` | `{recommended, mode, critique, confidence, rationale, signals, source}` |
| CLI | `python tools/llm/reasoned_codegen_advisor.py --function <fn> --spec "..." [--file-count N] [--no-llm] --json` |

**Wired pipelines:** translation (`code_translator._invoke_llm`, default ON) and the ANVIL
agentic runner (`tools/anvil/agentic_runner.py --reasoned auto|on|off`, default OFF, advisor-gated).
Bypass (no LLM generation call): child-app generator, deprecated builder `code_generator.py`,
migration generator, AAC — see `docs/security/sandbox-coverage.md`.

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
