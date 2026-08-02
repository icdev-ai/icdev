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
| `ChainOrchestrator.invoke_divergence(function, request)` | Run Divergence (dvg-core-*): a single STRICTLY-ISOLATED generative fan-out (one round, no cross-reading) that returns a raw pool of candidate ideas — the generative counterpart to the council. Each branch gets the problem + one GENERATIVE frame (from `args/ideation_frames.yaml` `generative` set) and is forbidden from evaluating/ranking. Scoring/clustering/deepening is the SEPARATE `tools/quality/divergence_critic.py` invocation. **OPT-IN** (`chain_orchestration.divergence.enabled` default false; per-function opt-in). Routing: `divergence_branch_pool` / `divergence_critic`. Exposed as the `divergence_invoke` MCP tool (`gap_handlers.py::handle_divergence_invoke`, `score` opt-in runs the critic + advisory trap warnings) and the `icdev-divergence` skill (`.agents/skills/icdev-divergence`, headless via `tools/skills/invoke.py`). CUI: inherits the function's LOCAL-ONLY routing, opens no new egress path. |

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
| `ChainPrompts.divergence_branch(...)` | Generative divergence branch (produce candidate ideas, no evaluation) |

---

## Ideation Frame Library (`args/ideation_frames.yaml` + `tools/config/ideation_frames.py`)

The single config-driven source of perspective sets for multi-branch LLM modes
(Divergence, Council), replacing hardcoded module-level lists. Versioned; each
frame is `{key, name, stance, prompt_fragment, mode}` where `mode` is
`generative` (produce new candidate ideas — branch system prompt forbids
evaluation) or `evaluative` (critique a proposal already on the table). Frames
within a set must be orthogonal.

| Function | Signature |
|----------|-----------|
| `get_frames(frame_set, *, mode=None, path=None)` | List of `Frame(key, name, stance, prompt_fragment, mode)`; unknown set → `[]`, malformed frames skipped (never raises) |
| `get_frame_pairs(frame_set, *, mode=None, path=None)` | `[(name, prompt_fragment), ...]` — the shape the branch/advisor builders consume |
| `get_version(path=None)` | Library version string (stamped into chain telemetry) |
| `list_frame_sets(path=None)` | Names of all defined frame sets |
| `validate_library(path=None)` | Fail-loud strict validation (tests / coherence); raises `IdeationFrameError` |

Seeded set `generative` (DoD/air-gap/accreditation-shaped, orthogonal): Adversary,
Accreditor, Sustainment Owner, Air-Gap Operator, Transplant, Inverter, Shoestring,
Naturalist. Tests: `tests/test_ideation_frames.py`.

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
| **MCP** | `cot_invoke` + `cod_invoke` tools in `tool_registry.py`; handlers in `gap_handlers.py`, retargeted (cxo-adopt-04) onto the governed Cortex facade `cortex.reason(mode='cot'\|'debate')` so every call runs the TRUST chain and writes a `cortex_audit` row. `council_query` and `divergence_invoke` tools likewise (`gap_handlers.py::handle_council_query` / `handle_divergence_invoke`), primary caller cross-repo (idea_lab), still direct `ChainOrchestrator`. |
| **Skill** | `icdev-divergence` (`.agents/skills/icdev-divergence/SKILL.md`) — interactive + headless via `python tools/skills/invoke.py --exec icdev-divergence`. |
| **Knowledge Graph** | `reasoning_step` node type indexed by `kg_builder.py` with step_name, model_id, chain_mode, trace_id, round_num |
| **Event Bus** | `cot_reasoning_completed` published after every chain invocation |
| **Cost Intelligence** | `enable_cot` / `enable_cod` recommendation types for high-cost functions |
| **Readiness Score** | 5th `explainability` dimension in `tools/canvas/orchestrator.py:compute_readiness()` |
| **MCP** | `cot_invoke` + `cod_invoke` tools in `tool_registry.py`; handlers in `gap_handlers.py`, retargeted (cxo-adopt-04) onto the governed Cortex facade `cortex.reason(mode='cot'\|'debate')`. `council_query` tool likewise (`gap_handlers.py::handle_council_query`), primary caller cross-repo (idea_lab), still direct `ChainOrchestrator`. |

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
| `chain_of_verification` (`cove.py`, agx-verify-01) | Chain-of-Verification: baseline → derive verification questions → answer each INDEPENDENTLY of the baseline → Python composes a pass/revise decision from per-question enum verdicts `{supported/partial/contradicted/unsupported}` (`compose_verification`, `cove-1.0`) → revise on any contradiction. The enforcement half of the TRUST invariant. Opt-in, budget-capped, cheap-tier-routed verifier (`cove_verify`). Independence is asserted in tests (verifier prompt excludes baseline). |
| `cove_guard` (`tools/quality/cove_guard.py`, agx-verify-01) | Optional pre-promote/pre-export gate mirroring `citation_gate`/placeholder guards: reuses `citation_grounding.parse_citations` to resolve the sources a draft cites, hands them to CoVe as evidence, returns a blocking/overridable finding (`blocked`, `needs_revision`, `revised_text`, `contradicted_claims`) with a HITL `force_override` (caller audits the override). Fails closed. |
| `self_discover` (`self_discover.py`, agx-search-01) | Self-Discover: SELECT → ADAPT → IMPLEMENT → SOLVE. The LLM names reasoning modules from a DATA bank (`context/reasoning_modules/architect_modules.yaml`, seeded with the Karpathy pre-design heuristics); Python (`select_modules`) validates the names against the bank and (`compose_structure`) assembles a task-specific reasoning structure — deterministic-picker. Optional/registry-swappable for the ANVIL Architect phase; does NOT change default Architect behavior (agx-bench-02 decides that with measurements). Unknown ids drop; empty selection falls back to the Karpathy core. |
| `tree_of_thoughts` (`tree_of_thoughts.py`, agx-search-02) | Budget-capped beam search over reasoning paths for genuinely branchy problems (COA generation, migration wave planning). HARD ceiling (`max_llm_calls` always-on + optional token/cost/time `ArchitectureBudget`): exceeding it returns best-so-far with `degraded=True`, `stop_reason="budget_exceeded"` — never a silently-truncated result presented as complete. Per-branch evaluation is a 3-value enum `{promising/maybe/dead_end}` (`tot-1.0`) composed into the beam ordering in Python. OPT-IN per call site, NEVER a default (LATS/MCTS rejected on cost). Honest per-invocation cost report in the envelope. |
| `benchmark` (`benchmark.py`, agx-bench-01) | Cross-architecture benchmark suite: grades registered architectures against each other on ICDEV-representative tasks (`args/agx/benchmark_tasks.yaml` — compliance drafting, requirement decomposition, CVE triage, code review, retrieval QA, migration planning). Reuses `tools/evolution/fitness.py::score_full` as the categorical judge (no new scoring stack). Runs every architecture on ≥2 model families (incl. local Ollama when reachable) via `router.get_diverse_models`; per-family cost/latency reported alongside quality. Injectable runner/judge seams → NEVER needs live models to build/test; unreachable providers/air-gap yield `status="unmeasured"` ("run live to populate"), never an exception. No silent caps (a `dropped[]` list records everything skipped); architectures with < `min_samples` measured cells marked `unmeasured`. Deterministic Python aggregation; results persisted to `data/agx/benchmark_latest.json` for agx-bench-02. CLI: `python tools/llm/architectures/benchmark.py --run --json` / `--dry-run`. |
| `baseline` (`baseline.py`, agx-bench-02) | The `baseline` architecture — a single direct `router.invoke`, no reasoning wrapper. Registered as the benchmark's honest reference so the leaderboard can answer "did any architecture actually beat doing nothing special?" MEASUREMENT REFERENCE ONLY: never a routing default, never changes a call site (selecting no architecture already yields this in production). Degraded/empty envelope on provider failure → treated as `unmeasured`. |
| `leaderboard` (`leaderboard.py`, agx-bench-02) | Leaderboard + evidence-based routing recommendation over the bench-01 report. `build_leaderboard` ranks architectures per (task-family × model-family) on quality with cost/latency alongside — per-family, never a blended number that hides a frontier-only win; low-sample groups shown `unmeasured`. `recommend_defaults` proposes per-function defaults WITH inline evidence but **recommends only — never writes config**; a win must hold across ALL measured model families (rejects frontier-only wins) and stay within a cost ratio, else `keep_current` (the honest "nothing beat baseline" result, never buried) or `insufficient_evidence`. Regression guard: `is_config_noop` proves the shipped config changes no runtime selection; `check_no_degradation` flags any configured architecture measured below baseline. CLI: `python tools/llm/architectures/leaderboard.py --json` / `--recommend`. |

**LLM-agnostic by construction:** no inference in this package; all adapters route through `LLMRouter`. Zero vendor-SDK imports, zero hardcoded model IDs. Enforced by `tests/llm/test_architecture_agnosticism.py` (agx-core-02).

**Tests:** `tests/llm/test_architecture_registry.py`.
