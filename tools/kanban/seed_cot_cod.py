#!/usr/bin/env python3
"""Seed Kanban tasks for Chain of Thought / Chain of Debate (cot-*).

Decomposed from plan: bright-launching-crescent.md
Multi-LLM orchestration across ICDEV core, 12 canvases, Academy, GameDay,
workflow, digital twin, and full ecosystem.

Run: python tools/kanban/seed_cot_cod.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402
from tools.kanban.task_factory import create_tasks  # noqa: E402

_NOW = datetime.now(timezone.utc).isoformat()


def _conn():
    return get_connection()


# Project prefix: cot
TASKS = [
    # ═══════════════════════════════════════════════════════════════════════
    # EP1: FOUNDATION — Chain Orchestrator Core
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cot-fnd-01",
        "title": "COT: Create tools/llm/chain_orchestrator.py (CoT/CoD engine)",
        "description": (
            "Create the core multi-LLM orchestration engine at tools/llm/chain_orchestrator.py.\n\n"
            "Requirements:\n"
            "  - Class ChainOrchestrator with invoke_chain_of_thought() and invoke_chain_of_debate().\n"
            "  - CoT: reason -> critic -> synthesize (up to max_rounds).\n"
            "  - CoD: parallel debate turns (num_debaters x debate_rounds) -> judge synthesis.\n"
            "  - Self-consistency mode: run CoT N times in parallel, majority vote.\n"
            "  - Cost cap ($0.50 default), token cap (32K), timeout (120s). Abort on exceed.\n"
            "  - Each step flows through full router pipeline (redaction, RAG, cache, gateway, telemetry).\n"
            "  - Use ThreadPoolExecutor for parallel steps.\n"
            "  - Respect per-function config from args/llm_config.yaml.\n"
            "  - Air-gap safe; use get_connection() for any DB state.\n\n"
            "Do NOT write router integration yet — that is cot-rt-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "cot-fnd-02",
        "title": "COT: Add llm_chain_telemetry schema + migration",
        "description": (
            "Add the chain telemetry table schema to tools/db/init_icdev_db.py and create migration.\n\n"
            "Schema:\n"
            "  CREATE TABLE IF NOT EXISTS llm_chain_telemetry (\n"
            "    id SERIAL PRIMARY KEY,\n"
            "    session_id TEXT NOT NULL,\n"
            "    function TEXT NOT NULL,\n"
            "    chain_mode TEXT NOT NULL,\n"
            "    models_used JSONB NOT NULL,\n"
            "    rounds JSONB NOT NULL,\n"
            "    input_tokens INTEGER DEFAULT 0,\n"
            "    output_tokens INTEGER DEFAULT 0,\n"
            "    cost_usd NUMERIC(10,6) DEFAULT 0.0,\n"
            "    duration_ms INTEGER DEFAULT 0,\n"
            "    final_model_id TEXT,\n"
            "    stop_reason TEXT,\n"
            "    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()\n"
            "  );\n"
            "  CREATE INDEX idx_chain_telemetry_function ON llm_chain_telemetry (function);\n"
            "  CREATE INDEX idx_chain_telemetry_created ON llm_chain_telemetry (created_at);\n\n"
            "Also update canvas_ai_decisions.decision_type CHECK to include 'chain_of_thought' and 'chain_of_debate'.\n"
            "Add both tables to tests/conftest.py MINIMAL_ICDEV_SCHEMA."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-fnd-01",
    },
    {
        "id": "cot-fnd-03",
        "title": "COT: Create tools/llm/chain_prompts.py (prompt templates)",
        "description": (
            "Create Jinja2-rendered prompt templates for CoT/CoD roles.\n\n"
            "Templates:\n"
            "  _REASONER_PROMPT — 'Think step by step. Show reasoning before answering.'\n"
            "  _CRITIC_PROMPT — 'Review reasoning. Identify errors, gaps, assumptions.'\n"
            "  _SYNTHESIZER_PROMPT — 'Given reasoning and critique, produce final answer.'\n"
            "  _DEBATER_PROMPT — 'You are Debater {n}. Argue position. Address prior arguments.'\n"
            "  _JUDGE_PROMPT — 'Neutral judge. Evaluate all positions. State strongest argument.'\n\n"
            "All templates include:\n"
            "  - CUI // SP-CTI classification banner.\n"
            "  - Output schema hint if request.output_schema present.\n"
            "  - Tool schema hint if request.tools present.\n"
            "  - Configurable temperature override per role."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-fnd-01",
    },
    {
        "id": "cot-fnd-04",
        "title": "COT: Add chain_orchestration config to args/llm_config.yaml",
        "description": (
            "Add top-level chain_orchestration section to args/llm_config.yaml:\n\n"
            "  chain_orchestration:\n"
            "    enabled: true\n"
            "    default_mode: single\n"
            "    cost_cap_usd: 0.50\n"
            "    token_cap: 32000\n"
            "    timeout_seconds: 120\n"
            "    cot:\n"
            "      enabled: true\n"
            "      max_rounds: 3\n"
            "      self_consistency_runs: 1\n"
            "      reasoner_model: qwen3-local\n"
            "      critic_model: claude-sonnet\n"
            "      synthesizer_model: claude-sonnet\n"
            "      excluded_functions: [pulse_generation, news_oracle, market_scan]\n"
            "      per_function:\n"
            "        code_generation:\n"
            "          max_rounds: 5\n"
            "          self_consistency_runs: 3\n"
            "    cod:\n"
            "      enabled: true\n"
            "      num_debaters: 3\n"
            "      debate_rounds: 2\n"
            "      judge_model: claude-sonnet\n"
            "      debater_models: [qwen3-local, claude-sonnet, openai-gpt4o]\n"
            "      excluded_functions: [pulse_generation]\n"
            "      per_function:\n"
            "        architecture_review:\n"
            "          num_debaters: 5\n"
            "          debate_rounds: 3\n\n"
            "Ensure _expand_env handles any ${VAR} syntax."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP2: ROUTER — Integration hooks
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cot-rt-01",
        "title": "COT: Add invoke_chain_of_thought + invoke_chain_of_debate to router.py",
        "description": (
            "Modify tools/llm/router.py.\n\n"
            "1. Add invoke_chain_of_thought(function, request):\n"
            "   - Read chain_orchestration.cot.<function> config.\n"
            "   - Delegate to ChainOrchestrator.invoke_chain_of_thought().\n"
            "   - Return LLMResponse with aggregated metadata.\n\n"
            "2. Add invoke_chain_of_debate(function, request):\n"
            "   - Read chain_orchestration.cod.<function> config.\n"
            "   - Delegate to ChainOrchestrator.invoke_chain_of_debate().\n"
            "   - Return LLMResponse with aggregated metadata.\n\n"
            "3. Mode switch in existing invoke():\n"
            "   - After _rag_augment() and _cache_lookup(), check request.chain_mode.\n"
            "   - If 'cot': dispatch to invoke_chain_of_thought().\n"
            "   - If 'cod': dispatch to invoke_chain_of_debate().\n"
            "   - Otherwise: continue normal single-call routing.\n\n"
            "4. Extend LLMRequest in tools/llm/provider.py:\n"
            "   - Add chain_mode: str = ''\n"
            "   - Add chain_config: dict = field(default_factory=dict)\n\n"
            "5. Telemetry aggregation in _log_telemetry():\n"
            "   - input_tokens = sum of all step input_tokens.\n"
            "   - output_tokens = sum of all step output_tokens.\n"
            "   - duration_ms = wall-clock time of entire chain.\n"
            "   - model_id = comma-separated list of models used.\n"
            "   - Write per-round detail rows to llm_chain_telemetry."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-fnd-01",
    },
    {
        "id": "cot-rt-02",
        "title": "COT: Update cost_intelligence.py to recommend CoT/CoD",
        "description": (
            "Modify tools/llm/cost_intelligence.py recommend_optimizations().\n\n"
            "New detection logic:\n"
            "  - Detect functions with high output variance (stddev > 20% of mean quality score).\n"
            "  - Recommend chain_of_thought for high-variance deterministic tasks.\n"
            "  - Recommend chain_of_debate for subjective/evaluative tasks (reviews, assessments).\n"
            "  - When confidence >= 0.85, auto-enable in args/llm_config.yaml.\n"
            "  - Log multi-LLM cost deltas to llm_cost_recommendations.\n\n"
            "Reuse _model_pricing(), _is_local_model(), compare_edge_vs_cloud()."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-fnd-04",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP3: CANVAS + UI INTEGRATION
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cot-canvas-01",
        "title": "COT: Add CoT/CoD UI toggles to AADC (Agentic AI Design Canvas)",
        "description": (
            "Modify tools/agentic_ai_canvas/blueprint.py and templates.\n\n"
            "1. Add toggle switches on AADC design page:\n"
            "   - 'Enable Chain of Thought' checkbox.\n"
            "   - 'Enable Chain of Debate' checkbox.\n"
            "2. When toggled, set chain_mode on LLMRequest before router.invoke().\n"
            "3. Display CoT reasoning trace in a collapsible panel below AI responses.\n"
            "4. Display CoD debate log with debater colors and judge verdict.\n"
            "5. Store user preference in canvas-local settings."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-rt-01",
    },
    {
        "id": "cot-canvas-02",
        "title": "COT: Enable CoT for SDC threat modeling + CoD for BDC cATO review",
        "description": (
            "Modify two canvases to use CoT/CoD for specific AI features.\n\n"
            "Security Design Canvas (tools/security_canvas/blueprint.py):\n"
            "  - In threat modeling endpoint, set request.chain_mode='cot' before router.invoke().\n"
            "  - Display reasoning steps in threat analysis output.\n\n"
            "Boundary Design Canvas (tools/boundary_canvas/blueprint.py):\n"
            "  - In cATO risk assessment, set request.chain_mode='cod' before router.invoke().\n"
            "  - Display debate positions and judge verdict in assessment output.\n\n"
            "No changes needed for other 10 canvases — they opt in via config."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-rt-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP4: ACADEMY + GAMEDAY INTEGRATION
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cot-acad-01",
        "title": "COT: Integrate CoT/CoD into FORGE Academy ai_coach.py",
        "description": (
            "Modify apps/forge_academy/ai_coach.py.\n\n"
            "1. Use CoT for complex concept explanations (set chain_mode='cot' on LLMRequest).\n"
            "2. Add 'Debate Mode' toggle for multiplayer AI-vs-AI training scenarios.\n"
            "3. When Debate Mode is on, set chain_mode='cod' and display debate transcript.\n"
            "4. Add Academy lab content modules on CoT/CoD in apps/forge_academy/content/.\n"
            "5. Respect Academy's existing router usage pattern (provider, model_id, cfg = router.get_provider_for_function())."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-rt-01",
    },
    {
        "id": "cot-game-01",
        "title": "COT: Bridge GameDay to router-native CoD via GameDayChainBridge",
        "description": (
            "Create tools/ai_game_engine/chain_bridge.py.\n\n"
            "1. Class GameDayChainBridge wraps ChainOrchestrator.\n"
            "2. Translates GameDay prompts (currently direct Ollama /api/chat calls) into LLMRequest objects.\n"
            "3. Provides methods:\n"
            "   - debate_strategy(scenario) -> returns judged strategy via CoD.\n"
            "   - reason_strategy(scenario) -> returns step-by-step plan via CoT.\n"
            "4. Modify tools/ai_game_engine/agent.py to optionally use the bridge:\n"
            "   - If ICDEV_COD_ENABLED=true, route through bridge.\n"
            "   - Otherwise, keep existing direct Ollama call.\n"
            "5. Use CoD for AI league debates (Bull vs Bear vs Neutral).\n"
            "6. Use CoT for strategy generation (reason -> plan -> execute).\n\n"
            "Ensure no breaking changes to existing GameDay flow."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-fnd-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP5: WORKFLOW INTEGRATION
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cot-wf-01",
        "title": "COT: Integrate CoT traces into HITL engine auto-advance",
        "description": (
            "Modify tools/workflow_hitl/engine.py.\n\n"
            "1. In _open_approval() automated branch (lines 287-307):\n"
            "   - Before auto-advance, call record_canvas_decision(decision_type='chain_of_thought', rationale=reasoning_trace).\n"
            "   - Store the CoT trace_id in wf_approvals.cot_trace_id (add column if needed).\n"
            "2. Extend HITL templates (args/workflow_hitl_config.yaml):\n"
            "   - Add 'cot_required: true/false' to stage definitions.\n"
            "   - When cot_required=true, gate auto-advance until CoT trace is present.\n"
            "3. No changes to manual approval paths."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-rt-01",
    },
    {
        "id": "cot-wf-02",
        "title": "COT: Extend Kanban state machine with CoT trace tracking",
        "description": (
            "Modify tools/kanban/state_machine.py.\n\n"
            "1. Extend TransitionResult dataclass with cot_trace_id: str = ''.\n"
            "2. In transition(), store structured CoT rationale in kanban_status_transitions.reason JSON field.\n"
            "3. When transitioning to DONE, verify CoT trace exists if task_type requires it.\n"
            "4. Add 'cot_enabled' flag to kanban_tasks (default false).\n"
            "5. Update seed scripts to set cot_enabled on relevant task types."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-rt-01",
    },
    {
        "id": "cot-wf-03",
        "title": "COT: Integrate CoT evidence into loop engine acceptance criteria",
        "description": (
            "Modify tools/workflow/loop_engine.py.\n\n"
            "1. In verify_criterion(), store CoT evidence in the evidence parameter.\n"
            "2. Extend add_acceptance_criterion() to accept optional cot_config dict specifying required chain_mode.\n"
            "3. In close_loop(), verify all criteria have CoT evidence if cot_config is set.\n"
            "4. No breaking changes to existing loop workflows."
        ),
        "task_type": "build",
        "priority": "low",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-rt-01",
    },
    {
        "id": "cot-wf-04",
        "title": "COT: Add CoT traces to auto-remediation decision logic",
        "description": (
            "Modify tools/workflow/auto_remediate.py and tools/canvas/auto_remediator.py.\n\n"
            "1. In attempt_remediation(), carry structured CoT traces in info dict explaining chosen remediation path.\n"
            "2. CoT-based classification can augment deterministic classify_failure() heuristics.\n"
            "3. In canvas auto_remediator.py handler selection, store CoT reasoning in finding_approvals.decision_rationale.\n"
            "4. Log CoT traces to audit_trail via audit_logger.log_event()."
        ),
        "task_type": "build",
        "priority": "low",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-rt-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP6: DIGITAL TWIN INTEGRATION
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cot-twin-01",
        "title": "COT: Wrap security twin simulate_delta with CoT/CoD layer",
        "description": (
            "Modify tools/security_canvas/twin.py and blueprint.py.\n\n"
            "1. In simulate_delta(), add optional use_cot parameter (default False).\n"
            "   - When True, simulate multiple deltas, reason over STRIDE coverage, pick safest delta.\n"
            "   - Use CoD to debate competing attack paths before returning final verdict.\n"
            "2. Add POST /api/twin/<id>/simulate-cot route in blueprint.py.\n"
            "3. Return reasoning trace alongside verdict, risk_score, and attack_paths.\n"
            "4. Store CoT trace in sdc_attack_snapshots.cot_trace JSON field."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-rt-01",
    },
    {
        "id": "cot-twin-02",
        "title": "COT: Wrap boundary twin simulate_delta with CoD policy debate",
        "description": (
            "Modify tools/boundary_canvas/twin.py and blueprint.py.\n\n"
            "1. In simulate_delta(), add optional use_cod parameter (default False).\n"
            "   - When True, multiple policy deltas are proposed, debated, and judged before applying.\n"
            "   - Use CoT in crosswalk_drift() to explain why controls are satisfied in source but not target.\n"
            "2. Add POST /api/twin/<id>/simulate-cod route in blueprint.py.\n"
            "3. Return debate transcript and judge verdict alongside rating and score.\n"
            "4. Store CoD transcript in compliance_snapshots.cod_transcript JSON field."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-rt-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP7: ECOSYSTEM INTEGRATION
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cot-eco-01",
        "title": "COT: Extend AI trace mixin with chain_of_thought + chain_of_debate decision types",
        "description": (
            "Modify tools/canvas/ai_trace_mixin.py.\n\n"
            "1. Extend decision_type enum to include 'chain_of_thought' and 'chain_of_debate'.\n"
            "2. In ChainOrchestrator, call record_canvas_decision() after every CoT/CoD step.\n"
            "   - Pass structured rationale JSON into rationale parameter.\n"
            "   - Pass rejected alternatives into alternatives parameter.\n"
            "   - Set model_used to comma-separated models.\n"
            "   - Set confidence to judge/debate confidence score.\n"
            "3. Ensure dual-write to canvas_ai_decisions and audit_trail continues.\n"
            "4. Update any SQL CHECK constraints that reference decision_type."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-rt-01",
    },
    {
        "id": "cot-eco-02",
        "title": "COT: Integrate CoT/CoD with knowledge graph and event bus",
        "description": (
            "Modify tools/canvas/kg_builder.py and tools/knowledge_graph/canvas_indexer.py.\n\n"
            "1. In ChainOrchestrator, after each milestone:\n"
            "   - publish(source_canvas, 'cot_reasoning_completed', payload, target_canvas).\n"
            "   - payload includes: step_number, model, reasoning, confidence, trace_id.\n"
            "2. In kg_builder.py, support node_type='reasoning_step' in canvas_kg_nodes.\n"
            "3. In canvas_indexer.py, index reasoning steps with edge_type='supports' to decisions and edge_type='based_on' to evidence.\n"
            "4. No changes to existing KG node types."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-rt-01",
    },
    {
        "id": "cot-eco-03",
        "title": "COT: Add explainability dimension to readiness scoring",
        "description": (
            "Modify tools/canvas/orchestrator.py compute_readiness().\n\n"
            "1. Add fifth readiness dimension: explainability (0-100).\n"
            "2. Scores presence and quality of CoT traces linked to the project.\n"
            "3. Weights:\n"
            "   - completeness: 20% (was 25%)\n"
            "   - compliance: 30% (was 35%)\n"
            "   - coverage: 20% (was 25%)\n"
            "   - risk: 15% (unchanged)\n"
            "   - explainability: 10% (new)\n"
            "4. Update all callers and dashboard widgets that display readiness breakdown."
        ),
        "task_type": "build",
        "priority": "low",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-eco-01",
    },
    {
        "id": "cot-eco-04",
        "title": "COT: Register chain_orchestrator as MCP tools",
        "description": (
            "Modify tools/mcp/tool_registry.py and gap_handlers.py.\n\n"
            "1. Register new MCP tools:\n"
            "   - cot_invoke — invoke_chain_of_thought with args (function, prompt, max_rounds).\n"
            "   - cod_invoke — invoke_chain_of_debate with args (function, prompt, num_debaters).\n"
            "   - cot_stats — return chain telemetry stats.\n"
            "2. Add gap handlers in gap_handlers.py:\n"
            "   - handle_cot_invoke(args) -> chain_orchestrator.invoke_chain_of_thought().\n"
            "   - handle_cod_invoke(args) -> chain_orchestrator.invoke_chain_of_debate().\n"
            "3. Ensure allowlisted command prefixes (python tools/...)."
        ),
        "task_type": "build",
        "priority": "low",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-fnd-01",
    },
    {
        "id": "cot-eco-05",
        "title": "COT: Integrate with memory system and compliance crosswalk",
        "description": (
            "Modify tools/memory/memory_write.py usage in ChainOrchestrator.\n\n"
            "1. In ChainOrchestrator:\n"
            "   - CoT reasoning chains write entry_type='thinking' to memory_entries.\n"
            "   - Synthesized conclusions write entry_type='insight'.\n"
            "   - Source = 'orchestrator' to distinguish from manual entries.\n"
            "2. In compliance crosswalk (tools/compliance/crosswalk_engine.py):\n"
            "   - No code changes needed.\n"
            "   - CoT consumes get_gap_analysis() as context before reasoning.\n"
            "   - CoD debates control implementation strategies.\n"
            "   - map_implementation_across_frameworks() stores CoT rationale in audit details.\n"
            "3. In audit logger (tools/audit/audit_logger.py):\n"
            "   - No code changes needed.\n"
            "   - CoT/CoD traces appended to details JSON field.\n"
            "   - record_decision() stores rejected CoT branches in alternatives list."
        ),
        "task_type": "build",
        "priority": "low",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-rt-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP8: FORGE + ANVIL ARTIFACTS
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cot-forge-01",
        "title": "COT: Create FORGE goal goals/enable_llm_cot_cod.md",
        "description": (
            "Create goals/enable_llm_cot_cod.md per FORGE framework.\n\n"
            "Content:\n"
            "  - Problem: single-LLM calls lack reasoning transparency and robustness.\n"
            "  - Solution: router-native CoT/CoD multi-LLM orchestration.\n"
            "  - Workflow: detect high-variance tasks -> enable CoT/CoD -> verify quality -> report cost delta.\n"
            "  - Tools: chain_orchestrator.py, router.py, cost_intelligence.py.\n"
            "  - Expected outputs: reasoning traces, debate transcripts, quality scores, cost reports.\n"
            "  - Success criteria: quality score improvement > 15% for architecture_review within 14 days."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    {
        "id": "cot-forge-02",
        "title": "COT: Create context/llm_chain_policy.md",
        "description": (
            "Create context/llm_chain_policy.md.\n\n"
            "Sections:\n"
            "  - Chain design (CoT reason-critic-synthesize, CoD debate-judge).\n"
            "  - Cost caps and safety controls.\n"
            "  - PII handling: each step flows through _pre_invoke_redaction().\n"
            "  - Security mitigations (timing jitter, excluded_functions).\n"
            "  - Workflow integration (HITL gates, kanban transitions, loop criteria).\n"
            "  - Digital twin integration (simulate_delta with CoT/CoD).\n"
            "  - Operational runbook (enable/disable, per-function config, stats).\n"
            "  - Classification: CUI // SP-CTI."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    {
        "id": "cot-forge-03",
        "title": "COT: Create hardprompts/chain_of_thought.md + chain_of_debate.md",
        "description": (
            "Create two prompt engineering guides.\n\n"
            "hardprompts/chain_of_thought.md:\n"
            "  - Guidelines for writing reasoner-friendly prompts.\n"
            "  - How to structure step-by-step reasoning.\n"
            "  - Critic role: what to look for.\n"
            "  - Synthesizer role: how to combine reasoning and critique.\n"
            "  - Examples: before/after prompt refactor.\n\n"
            "hardprompts/chain_of_debate.md:\n"
            "  - Guidelines for writing debater-friendly prompts.\n"
            "  - How to assign positions to debaters.\n"
            "  - Judge role: neutrality and evaluation criteria.\n"
            "  - Examples: architecture review debate, risk assessment debate."
        ),
        "task_type": "build",
        "priority": "low",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    {
        "id": "cot-anvil-01",
        "title": "COT: Create ANVIL workflow .claude/commands/cod.md",
        "description": (
            "Create .claude/commands/cod.md per ANVIL workflow spec.\n\n"
            "Sections:\n"
            "  ## Run CoT: python tools/llm/chain_orchestrator.py --cot --function <name> --prompt '...' --json\n"
            "  ## Run CoD: python tools/llm/chain_orchestrator.py --cod --function <name> --prompt '...' --json\n"
            "  ## Self-consistency: python tools/llm/chain_orchestrator.py --cot --self-consistency 3 --function <name> --prompt '...' --json\n"
            "  ## Stats: python tools/llm/chain_orchestrator.py --stats --json\n"
            "  ## Config: python tools/llm/chain_orchestrator.py --show-config --json\n\n"
            "Ensure all commands are allowlisted (python tools/... prefix)."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    {
        "id": "cot-reg-01",
        "title": "COT: Register in manifest + update CLAUDE.md reference",
        "description": (
            "1. Add entries to tools/manifest/llm-system.md for chain_orchestrator.py and chain_prompts.py.\n"
            "2. Add CLI commands to CLAUDE.md docs/reference/commands.md:\n"
            "   python tools/llm/chain_orchestrator.py --cot --function code_generation --prompt '...' --json\n"
            "   python tools/llm/chain_orchestrator.py --cod --function architecture_review --prompt '...' --json\n"
            "3. Update args/llm_config.yaml docs in docs/reference/configuration.md if it exists."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-anvil-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP9: TESTING + V&V
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cot-test-01",
        "title": "COT: Write unit tests tools/llm/chain_orchestrator_test.py",
        "description": (
            "Create comprehensive unit tests.\n\n"
            "Test cases:\n"
            "  - CoT returns final synthesized response after N rounds.\n"
            "  - CoD returns judged response after debate rounds.\n"
            "  - Self-consistency picks majority answer.\n"
            "  - Cost cap aborts chain when exceeded.\n"
            "  - Token cap aborts chain when exceeded.\n"
            "  - Timeout aborts chain when exceeded.\n"
            "  - Excluded functions fall back to single-call.\n"
            "  - Telemetry writes correct aggregated stats.\n"
            "  - llm_chain_telemetry rows contain correct round data.\n"
            "  - record_canvas_decision() accepts new decision_type values.\n"
            "  - event_bus.publish() emits CoT events.\n"
            "  - compute_readiness() includes explainability dimension.\n\n"
            "Use pytest. Mock providers if unavailable."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-fnd-02",
    },
    {
        "id": "cot-vv-01",
        "title": "COT: Run health_check + coherence_checker + companion sync",
        "description": (
            "Post-implementation validation sequence:\n\n"
            "1. python tools/testing/health_check.py --json\n"
            "   - Verify no import errors after router.py changes.\n"
            "2. python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "   - Verify manifest registration, CLAUDE.md sync, schema consistency, ruff lint gate.\n"
            "3. python tools/dx/companion.py --sync --write --json\n"
            "   - Sync to icdev/ package mirror and all AI platforms.\n"
            "4. python tools/llm/cost_intelligence.py --recommend --json\n"
            "   - Verify recommendations still parse correctly.\n\n"
            "Report pass/fail for each gate."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-test-01",
    },
    {
        "id": "cot-vv-02",
        "title": "COT: End-to-end verification (live CoT/CoD)",
        "description": (
            "Manual E2E verification:\n\n"
            "1. Run CoT on code_generation:\n"
            "   python -c \"from tools.llm.router import LLMRouter; ...\"\n"
            "   - Verify telemetry shows chain_mode='cot' and models_used has reasoner+synthesizer.\n"
            "2. Run CoD on architecture_review:\n"
            "   - Verify response contains judged confidence score.\n"
            "3. Test self-consistency (runs=3): verify majority vote works.\n"
            "4. Test cost cap ($0.01): verify chain aborts with stop_reason='budget_exceeded'.\n"
            "5. Verify record_canvas_decision(decision_type='chain_of_thought') writes to both tables.\n"
            "6. Verify event_bus publish('cot_reasoning_completed') consumed by subscribed canvases.\n"
            "7. Verify compute_readiness() returns explainability score.\n"
            "8. Verify attempt_remediation() returns CoT trace in info dict.\n"
            "9. Run health_check.py and coherence_checker.py --gate — both must pass.\n"
            "10. Verify child app scaffold includes tools/llm/chain_orchestrator.py.\n\n"
            "Capture output and attach to this task."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cot-vv-01",
    },
]


def seed():
    report = create_tasks("cot", TASKS, strict=False, register_project=False)
    print(report.summary())
    return {"inserted": len(report.seeded), "skipped": len(report.skipped)}


if __name__ == "__main__":
    seed()
