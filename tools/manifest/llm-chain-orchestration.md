# LLM Chain Orchestration (CoT / CoD)

> Multi-LLM reasoning engine. CoT = reason → critic → synthesize. CoD = parallel debate → judge.

## Tools

### `tools/llm/chain_orchestrator.py`

Multi-LLM orchestration engine.

| Method | Description |
|--------|-------------|
| `ChainOrchestrator.invoke_chain_of_thought(function, request)` | Run CoT: reasoner → critic → synthesizer (up to `max_rounds`). Returns `ChainResult`. |
| `ChainOrchestrator.invoke_chain_of_debate(function, request)` | Run CoD: N parallel debaters → neutral judge synthesis. Returns `ChainResult`. |
| `ChainOrchestrator.invoke_council(question, context)` | Run the LLM Council (adapted from Karpathy's methodology): 5 fixed-perspective advisors (Contrarian, First Principles Thinker, Expansionist, Outsider, Executor) respond independently and in parallel, anonymously peer-review each other, then a chairman synthesizes a structured verdict (agreement, clashes, blind spots, recommendation, next step). Distinct from CoD — no debate-to-a-winner, independent single-pass analysis from fixed cognitive lenses. Routing: `council_advisor_pool` / `council_chairman` in `args/llm_config.yaml`, plus `chain_orchestration.council.per_function.idealab_council_query`. Exposed as the `council_query` MCP tool (`gap_handlers.py::handle_council_query`); primary caller is cross-repo (e.g. idea_lab pressure-testing a validated idea before committing to it). |

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
| MCP | `reasoned_codegen_advise` tool (`tools/mcp/gap_handlers.py::handle_reasoned_codegen_advise`, registered in `tool_registry.py`) |
| Routing | `reasoned_codegen_advisor` cheap-tier chain in `args/llm_config.yaml` (LLM-refine; heuristic baseline needs no LLM) |

**Wired pipelines:** translation (`code_translator._invoke_llm`, default ON) and the ANVIL
agentic runner (`tools/anvil/agentic_runner.py --reasoned auto|on|off`, default OFF, advisor-gated).
Bypass (no LLM generation call): child-app generator, deprecated builder `code_generator.py`,
migration generator, AI-ify — see `docs/security/sandbox-coverage.md`.

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
| **MCP** | `cot_invoke` + `cod_invoke` tools in `tool_registry.py`; handlers in `gap_handlers.py`. `council_query` tool likewise (`gap_handlers.py::handle_council_query`), primary caller cross-repo (idea_lab). |
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

---

### Benchmark Runner (`icdev/tools/llm/benchmark_runner.py`)
- Evaluates active LLM against DeepSpec benchmark suite (GSM8K, MATH500, HumanEval, MBPP, AIME, MT-Bench, Alpaca)
- `BenchmarkRunner.run_benchmark(name, limit, data_path)` → list[BenchmarkResult]
- `BenchmarkRunner.score(results)` → accuracy dict
- CLI: `python -m icdev.tools.llm.benchmark_runner --benchmark gsm8k --limit 50 --json`

---

### AGX Architecture Registry (`tools/llm/architectures/`)

> Registry of named reasoning architectures behind ONE uniform envelope, so a canvas or router function can swap reasoning strategy by config, not code. Enabler for the `agx-` card (agentic-architecture extension). Adapted from github.com/FareedKhan-dev/all-agentic-architectures (MIT, © 2025 Fareed Khan) — pattern only, no upstream code vendored. Mirrored to `icdev/tools/llm/architectures/`.

| Symbol | Description |
|--------|-------------|
| `ArchitectureResult` (`envelope.py`) | Uniform result envelope: `output`, `steps[]`, `model_ids_used[]`, token/cost usage, `method` provenance, `degraded` flag, `stop_reason`, `schema_version`. Honesty invariant (mirrors `tools/twin_core/`): a `degraded=True` envelope never presents a fabricated verdict. |
| `ArchitectureStep`, `ArchitectureBudget` | Per-step provenance; caller budget ceiling (`max_cost_usd`/`max_tokens`/`max_seconds`) honored via the existing `BudgetExceededError` path. |
| `register(name, fn, *, overwrite=False)` / `get` / `list_architectures` / `run` / `unregister` (`registry.py`) | Registry API. Every architecture is `run(task, *, router=None, budget=None, function=..., **kw) -> ArchitectureResult`; `task` may be `str` or `LLMRequest`. |
| Built-in adapters (`adapters.py`) | Wrap existing implementations — nothing rebuilt: `chain_of_thought`, `chain_of_debate`, `council` (from `ChainOrchestrator`), `react` (from `agent_loop.run_agent_loop`). agx-verify-*/rag-*/search-*/bench-* register further architectures here. |
| `resolve_architecture` / `resolve_and_log` / `log_selection` (`selection.py`, agx-core-03) | Config-driven selection from the `architectures:` section of `args/llm_config.yaml` (single-source). Precedence: explicit arg > `functions.<fn>` > `roles.<role>` > `default`. Shipped config is all-null = current behavior (opt-in). Emits structured `agx_architecture_selected` logs for the bench (agx-bench-01) to attribute results. |

**LLM-agnostic by construction:** no inference in this package; all adapters route through `LLMRouter`. Zero vendor-SDK imports, zero hardcoded model IDs. Enforced by `tests/llm/test_architecture_agnosticism.py` (agx-core-02).

**Tests:** `tests/llm/test_architecture_registry.py`.
