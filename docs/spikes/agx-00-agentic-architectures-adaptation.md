CUI // SP-CTI

# AGX-00 — `all-agentic-architectures` Adaptation Analysis

> **Source:** [github.com/FareedKhan-dev/all-agentic-architectures](https://github.com/FareedKhan-dev/all-agentic-architectures)
> **License:** MIT — Copyright (c) 2025 Fareed Khan. Patterns are freely adaptable with attribution.
> **Date:** 2026-07-25
> **Status:** ANALYSIS — no implementation. Card `agx-` seeded behind `agx-gate-00` (held).

---

## 1. What the upstream repo is

A Python library + educational textbook implementing **35 agentic AI patterns** as
runnable classes behind a uniform interface. Every architecture exposes
`.run(task)` and returns a standardized `ArchitectureResult`, so patterns are
swappable. Ships 283 tests, 35 executable notebooks with real LLM captures, and a
**17-task benchmark leaderboard** (~78% success on recent runs).

Built on **LangGraph** state machines + LangChain, with 9 LLM providers (Nebius
default, plus OpenAI, Anthropic, Groq, Ollama, Together, Fireworks, Mistral,
Google), FAISS, and Tavily.

### The one idea worth more than the 35 architectures

**The "deterministic-picker" pattern.** Upstream's central design rule for every
LLM-as-Scorer surface:

> The LLM may only commit to **categorical features** — booleans and enums.
> **Python composes the final signal** from those categoricals.

The LLM never emits the number that decides anything. It answers
"is this claim supported? (yes/no)", "is this document relevant?
(relevant / partial / irrelevant)" — and deterministic code turns those into the
score, the ranking, the gate verdict. Upstream applies it across 13
architectures; the other 9 are "architecturally immune by design" (no scoring
surface to corrupt).

This is **FORGE's own thesis applied one level deeper**. FORGE confines
probabilistic behavior to orchestration and makes execution deterministic;
deterministic-picker confines it to *categorical judgment* and makes
*aggregation* deterministic. It is the highest-value item in this analysis and it
is not a new subsystem — it is an audit-and-convert pass over surfaces ICDEV
already ships.

It also directly targets ICDEV's recurring failure mode: free-form LLM
confidence numbers that look meaningful and are not. Compare the
`trust-confidence-is-a-per-rule-constant` lesson (a "0.9→0.33 drift" that turned
out to be a per-rule constant, not a signal) and the `phantom` trust-event class
in `ace_trust_ledger`.

---

## 2. Coverage map — 35 architectures vs. ICDEV today

ICDEV already implements, in production, **22 of the 35** in some form. Rebuilding
those would be pure waste. The table records what exists so no future session
re-derives it.

### Already covered — do NOT rebuild (22)

| # | Upstream architecture | ICDEV equivalent |
|---|---|---|
| 1 | Reflection | `ChainOrchestrator.invoke_chain_of_thought` — reason → critic → synthesize |
| 2 | Reflexion | `tools/workflow/reflexion_agent.py`, `tools/nova/reflexion_loop.py`, `reflexion_loop` weekly reflex → `agent_improvement_artifacts` |
| 6 | Self-Consistency | `ChainPrompts.self_consistency_voter` (majority-vote synthesis) |
| 10 | Ensemble | `invoke_council` + `router.get_diverse_models(role_key, count)` |
| 11 | Agentic RAG | `tools/mcp/rag_server.py` (12 tools); decompose + evaluate stages |
| 12 | Corrective RAG | `tools/rag/corrective_rag.py` — parallel multi-strategy retrieval (**partial**: no doc-grading + web-fallback loop) |
| 15 | GraphRAG | `tools/knowledge_graph/graph_rag.py` — PG-native Tier A/B |
| 16 | Episodic + Semantic memory | `tools/memory/` + `kg_edges` triples |
| 17 | Graph Memory | KG subject-predicate-object edges |
| 18 | MemGPT | `tools/llm/context_compressor.py` + `agent_loop` compression + `agent_runtime/profile_memory.py` (**partial**: no archival-tier paging) |
| 19 | Voyager | NOVA `tools/nova/skill_generator.py` + `agent_runtime/skills_lifecycle.py` (HITL-gated promotion) |
| 20 | Agent Workflow Memory | Kanban Lessons Learned Engine (8 lifecycle hooks) + Oracle predictions |
| 21 | Tool Use | `agent_loop` native tool-use + `agent_runtime/discovery.py` (440+ schemas from the MCP registry) |
| 22 | ReAct | `icdev/tools/llm/agent_loop.py::run_agent_loop` |
| 23 | Planning | `workflow_planner`, `kanban_queue_plan`, ANVIL Architect/Navigate |
| 25 | SWE-Agent | `sandbox_execute` + `agent_runtime/mutating_tools.py` + `checkpoints.py` rollback |
| 26 | BrowserAgent | Playwright MCP + `tools/browser/` |
| 27 | Multi-Agent | 16 A2A agents (8443–8460) + ACE Co-Worker + `multi_agent_orchestration` DAG |
| 29 | Debate | `invoke_chain_of_debate` (CoD) + `tools/agent/collaboration.py` debate pattern |
| 30 | STORM | Industry Research Engine 8-stage dossier pipeline; Strategos `war_council` (**partial**) |
| 31 | Meta-Controller | `tools/chat_router/intent_classifier.py` + ACE Oracle problem classification (**partial**: no architecture-level routing) |
| 32 | Dry-Run | `agent_runtime/safety.py` SafetyGate + HITL gates + `--dry-run` across tools |

Also near-covered: **33 Reflexive Metacognitive** (`ace/trust_calibrator.py` +
NOVA trust score) and **34 RLHF Self-Improvement**
(`tools/evolution/fitness.py` multi-dimensional judge + GEPA optimizer).

### Genuine gaps worth adapting (7)

| # | Architecture | Why it earns a task |
|---|---|---|
| 3 | **Chain-of-Verification (CoVe)** | Generate baseline → derive verification questions → answer them *independently of the baseline* → revise. This is the missing enforcement half of the TRUST invariant: `citation_grounding.py` validates that citations *parse and resolve*; CoVe checks whether the *claim* survives independent interrogation. Pairs with `confabulation_check`. |
| 5 | **Constitutional AI** | Per-rule pass/fail evaluation + targeted revision. ICDEV already has the constitution — `args/security_gates.yaml`, CUI/classification rules, the TRUST invariants. What is missing is the *per-rule* critique-and-revise loop over drafted artifacts, instead of one monolithic "is this compliant?" prompt. |
| 24 | **Plan-Execute-Verify (PEV)** | Per-step post-execution verification, not end-of-run. This is the structural fix for ICDEV's most expensive recurring failure: kanban tasks marked `done` that shipped nothing (`ace-coworker` "done LIED", `feedback_done_artifact_audit`). PR #180 hardened the *terminal* done-gate; PEV moves verification inside the loop. |
| 14 | **Adaptive RAG** | Pre-route by query complexity — skip retrieval for trivial queries, single-pass for simple, multi-hop for complex. Cheapest win in the set: pure cost/latency reduction on the existing retriever, no new storage. |
| 13 | **Self-RAG** | Per-document reflection scoring (relevant / supported / useful) rather than one blended rerank score. Incremental over `tools/rag/reranker.py`, and a natural deterministic-picker conversion target. |
| 4 | **Self-Discover** | SELECT → ADAPT → IMPLEMENT → SOLVE: compose a task-specific reasoning structure from a module bank before solving. Fits the ANVIL Architect phase, where the current approach is a fixed prompt regardless of task shape. |
| 7 | **Tree of Thoughts** | Beam search over reasoning paths. Worth it **only** hard-capped by budget, and only for genuinely branchy problems (COA generation, migration wave planning). Must not become the default. |

### Rejected — with reasons (6)

| # | Architecture | Disposition |
|---|---|---|
| 35 | Cellular Automata | **REJECT.** LLM rules over a grid. No ICDEV use case. Notebook curiosity. |
| 8 | LATS (MCTS + reward propagation) | **REJECT for now.** Monte Carlo tree search multiplies LLM calls per node; the cost is not defensible against ToT-with-beam-cap, which captures most of the benefit. Revisit only if ToT proves its worth and cost lands. |
| 9 | Mental Loop | **REJECT as new work.** "Simulate then deterministic-pick" is already the Digital Program Twin + `run_monte_carlo` + twin-core canonical envelope. |
| 28 | Blackboard | **REJECT as new work.** ACE `coworker_thread` shared state + `tools/canvas/` bus already cover the shared-workspace need; a second concurrency model adds risk without capability. |
| 19 | Voyager *subprocess skill execution* | **REJECT the mechanism.** Upstream executes LLM-written Python via subprocess. Non-starter at IL4+. ICDEV's HITL-gated skill promotion (`skills_lifecycle.approve_proposal` as sole writer, `trust: unverified-llm-generated` frontmatter) is the correct shape and stays. |
| — | LangGraph / LangChain stack | **REJECT the dependency.** Adopt patterns, not the stack. ICDEV's `requirements.txt` is deliberately lean, must work air-gapped from vendored wheels, and already owns the primitives (`agent_loop` for state, `ChainOrchestrator` for multi-model). Adding LangGraph would also smuggle in a provider abstraction that competes with `LLMRouter`. |

### Two engineering practices worth importing (not architectures)

1. **Uniform envelope + registry.** Upstream's `.run(task) → ArchitectureResult`
   is why 35 patterns are swappable. ICDEV exposes CoT/CoD/council as a
   `chain_mode` field on `LLMRequest` — good, but it does not generalize to
   verification, RAG routing, or search strategies. A single
   `tools/llm/architectures/` registry with one result envelope would let any
   canvas change reasoning strategy by **config, not code**. This is the enabler
   task; the other architectures plug into it.
2. **The benchmark leaderboard.** Upstream measures 35 architectures against 17
   tasks and publishes the table. ICDEV has the parts
   (`tools/evolution/fitness.py`, `tools/ace/eval_runner.py`, the Phase 65
   evaluation/red-teaming module) but no cross-architecture bake-off — so
   "which reasoning strategy for this function?" is answered by intuition and
   frozen in YAML. Measure it, then let `args/llm_config.yaml` routing cite
   evidence.

---

## 3. Hard constraint — LLM-agnostic

Upstream defaults to **Nebius** and wires providers through LangChain. ICDEV's
provider-agnosticism is an existing, hard-won property (9 providers, air-gap
Ollama routing, CUI egress rules) and this work must **preserve it, not dilute
it**. Every task in the `agx-` card carries these as acceptance criteria:

| Rule | Mechanism |
|---|---|
| No vendor SDK imports in architecture code | All inference via `LLMRouter.invoke(function, LLMRequest)` / `invoke_for_role(...)`. Zero `import anthropic` / `import openai` / `langchain_*` under `tools/llm/architectures/`. |
| No hardcoded model IDs | Model selection resolves from `args/llm_config.yaml` via `resolve_llm_config_path()`. Existing guardrail; add a test that greps the new package. |
| Multi-model architectures get diversity from config | CoVe verifiers, ensemble voters, debaters use `router.get_diverse_models(role_key, count)` — diversity comes from the configured chain, never a hardcoded vendor list. |
| Air-gap parity | Every architecture must complete against local Ollama with `ICDEV_LLM_PROVIDER=ollama` and `two_tier.enabled: false`, no cloud fallback. An architecture that only works on a frontier model is not shippable. |
| Embeddings through the abstraction | `get_embedding_provider()` only — never Ollama/nomic directly (see `embeddings-no-ollama-use-provider-abstraction`). |
| Graceful degradation | Structured-output-dependent steps need a deterministic fallback when a small local model returns malformed JSON — the categorical vocabulary must be small enough that a 7B model can hit it reliably. This is a *reason* deterministic-picker helps, not an afterthought. |
| CUI egress | Architectures that fan out to N models must respect `api_key_env`-based local-vs-cloud classification (`cli-bridge-cui-egress`); CUI content stays LOCAL-ONLY. |

**Corollary:** the deterministic-picker conversion is what makes LLM-agnosticism
achievable. A free-form "rate this 0.0–1.0" prompt produces incomparable numbers
across model families; "answer one of {supported, contradicted, unsupported}"
produces the same enum from Llama-3.3-70B and from a local 7B. Categorical
outputs are the portability layer.

---

## 4. Proposed shape — `agx-` card

16 tasks in 8 epics, all behind `agx-gate-00` (held `in_progress`; nothing
dispatches until a human releases it).

| Epic | Tasks | Content |
|---|---|---|
| `gate` | `agx-gate-00` | Manual gate. Pipeline-exempt. Never open a PR for it. |
| `core` | 01–03 | Architecture registry + uniform result envelope; LLM-agnostic conformance contract + enforcement test; config-driven selection in `args/`. |
| `pick` | 01–02 | Deterministic-picker audit of existing LLM-as-scorer surfaces; convert the highest-risk ones. |
| `verify` | 01–03 | CoVe over TRUST citations; Constitutional AI per-rule critique/revise; PEV in the kanban runner. |
| `rag` | 01–02 | Adaptive RAG complexity pre-routing; Self-RAG per-document reflection. |
| `search` | 01–02 | Self-Discover reasoning-module composition for ANVIL Architect; budget-capped Tree of Thoughts. |
| `bench` | 01–02 | Architecture benchmark suite; leaderboard + evidence-based routing. |
| `xcut` | 01 | Disposition ADR (this document's rejections, formalized) + manifest/docs/coherence close-out. |

Sequencing: `core-01` (envelope) and `pick-01` (audit) are the two prerequisites —
everything else is cheaper after them. `bench` must come last, because it grades
the rest.

---

## 5. Verdict

**ADOPT:** the deterministic-picker discipline, the uniform envelope + registry,
CoVe, Constitutional AI per-rule revision, PEV, Adaptive RAG, Self-RAG, and the
benchmark-leaderboard practice.

**REJECT:** the LangGraph/LangChain stack, Cellular Automata, LATS, Voyager's
subprocess execution, and — as *new* work — Mental Loop, Blackboard, and the 22
architectures ICDEV already runs in production.

The value here is not 35 new subsystems. It is one discipline
(deterministic-picker), one enabler (uniform envelope), five real gaps in
verification and retrieval routing, and the honesty of a measured leaderboard
instead of a hand-tuned YAML.
