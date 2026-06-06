# CUI // SP-CTI
"""Seed Kanban tasks for ACF — Autonomous Capability Foundry.

Project: acf  (task_prefix 'acf-')  — autonomous 0->1 product factory.
Spec: docs/features/phase-acf-autonomous-capability-foundry.md

ACF continuously and autonomously invents, designs, decomposes, and ships BRAND-NEW
capabilities into ICDEV (distinct from Oracle/Genesis reflexes, which incrementally
improve EXISTING tasks). It is the bridge between the noisy Innovation/Creative/Research
signal engines and the Kanban autonomous build dispatcher.

Locked design (2026-06-05): layer on existing engines (no new scanners) | FULLY AUTONOMOUS
(no human concept gate -> automated novelty/score/rate/circuit-breaker gates are load-bearing)
| continuous Genesis reflex cadence | full-Canvas output (8-component completeness gate) |
self-vetting (SIPA integrity + security/coherence gate on ACF-generated code before merge) |
**CoT/CoD-assisted decisions** (multi-LLM Chain-of-Debate go/no-go approval gate +
Chain-of-Thought synthesis assist via tools/llm/chain_orchestrator, deterministic-first fallback).

Task IDs follow the <prefix><epic>-<N> convention (acf-<epic>-NN) so
tools/project/kanban_project_sync.py auto-populates args/projects.yaml.

depends_on_task_id is single-parent (engine limitation, honored by the dispatcher:
a task dispatches only when its parent is 'done'/'decomposed'). Secondary cross-epic
prerequisites are named in the description text.

Run:
    python tools/kanban/seed_foundry.py
    python tools/kanban/seed_foundry.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402

PROJECT_ID = "acf"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [
    # =========================================================================
    # EPIC db — constants + dual-schema DB (PG-first, RLS) + registration
    # =========================================================================
    {
        "id": "acf-db-01",
        "title": "constants.py — source/concept/outcome enums + score weights + thresholds + flags",
        "description": (
            "Create tools/foundry/__init__.py and tools/foundry/constants.py. Define the enums that "
            "drive every SQL CHECK constraint (never hardcode constraints): "
            "SOURCE_ENGINES = (innovation, creative, research, genesis, telemetry); "
            "CONCEPT_STATUS = (proposed, scored, rejected, approved, decomposed, building, shipped, "
            "failed); OUTCOME_TYPES = (vv_pass, vv_fail, shipped, failed, abandoned); "
            "RUN_STATUS = (ok, rate_limited, circuit_open, error); "
            "REJECT_REASONS = (duplicate, low_novelty, below_threshold, compliance_risk, rate_limited). "
            "Add SCORE_WEIGHTS (novelty, market, fit, effort, compliance) and DEFAULT_THRESHOLDS "
            "(min_novelty, min_composite). Add RATE_LIMITS (max_concepts_per_cycle=1, "
            "max_active_projects=2) and CIRCUIT (vv_fail_rate, window). Add FEATURE_FLAG = "
            "'ICDEV_FOUNDRY_ENABLED'. Derive CHECK_* helper strings (e.g. CHECK_CONCEPT_STATUS = "
            "\"','\".join(...)) for db/init_db.py to consume. Pure-stdlib, no side effects on import. "
            "NOTE: this ACF root is gated on SIPA's final gate (sipa-vv-04) because ACF self-vets its "
            "autonomously-generated code via SIPA (tools/integrity/engine.py) at acf-engine-03 — SIPA "
            "must exist first."
        ),
        "task_type": "build",
        "priority": "critical",
        # Cross-project dependency: ACF builds after SIPA (its self-vet engine). sipa-vv-04 is SIPA's
        # final V&V gate; the dispatcher gates on parent done/decomposed regardless of project.
        "depends_on_task_id": "sipa-vv-04",
    },
    {
        "id": "acf-db-02",
        "title": "db/init_db.py — PG-first dual schema, 6 RLS tables (tenant_id/classification, append-only)",
        "description": (
            "Create tools/foundry/db/init_db.py modeled on the PG-first dual-schema pattern "
            "(SCHEMA_PG + SCHEMA_SQLITE via .replace() transforms; backend defaults to 'postgresql', "
            "SQLite fallback). These are platform findings tables (NOT canvas tables) so use the "
            "RLS-aware tools.db.storage.get_connection() (NOT get_canvas_connection) and include "
            "tenant_id + classification on EVERY table. Tables: "
            "foundry_runs(APPEND-ONLY: id, cycle_at, harvested, concepts_proposed, concepts_approved, "
            "tasks_emitted, status CHECK, detail JSON, tenant_id, classification, created_at); "
            "foundry_signals(APPEND-ONLY: id, run_id, source_engine CHECK, source_ref, theme, raw_score, "
            "keywords JSON, tenant_id, classification, created_at); "
            "foundry_concepts(id, run_id, name, slug UNIQUE, problem_statement, proposed_capability, "
            "target_users, cluster_signal_ids JSON, novelty_score, market_score, fit_score, "
            "effort_estimate, compliance_risk, composite_score, status CHECK, reject_reason CHECK NULL, "
            "tenant_id, classification, created_by, created_at, updated_at); "
            "foundry_specs(APPEND-ONLY: id, concept_id, spec_md, canvas_contract JSON, task_count, "
            "tenant_id, classification, created_at); "
            "foundry_tasks_emitted(APPEND-ONLY: id, concept_id, kanban_task_id, epic, seq, tenant_id, "
            "classification, created_at); "
            "foundry_outcomes(APPEND-ONLY: id, concept_id, outcome CHECK, metric, detail JSON, "
            "tenant_id, classification, created_at). "
            "ALL CHECK constraints derived from constants.py. init_db() idempotent + graceful if exists. "
            "Requires acf-db-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-db-01",
    },
    {
        "id": "acf-db-03",
        "title": "Register foundry_* tables in init_icdev_db.py + migration + conftest schema",
        "description": (
            "Wire the schema into the platform. (1) Add the 6 foundry_* CREATE TABLE statements to "
            "tools/db/init_icdev_db.py (both PG and SQLite paths, mirroring constants-derived CHECKs). "
            "(2) Add a numbered migration under tools/db/migrations/ (follow the existing NNN_*.py "
            "pattern) that creates the tables idempotently. (3) Add the 6 tables to "
            "tests/conftest.py MINIMAL_ICDEV_SCHEMA so SQLite-forced tests see them. Verify "
            "`python tools/db/init_icdev_db.py` runs clean and fresh pytest collection imports. "
            "Requires acf-db-02."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-db-02",
    },
    {
        "id": "acf-db-04",
        "title": "APPEND_ONLY tables + .env toggle + args/foundry_config.yaml skeleton",
        "description": (
            "(1) Add the 5 append-only tables (foundry_runs, foundry_signals, foundry_specs, "
            "foundry_tasks_emitted, foundry_outcomes) to APPEND_ONLY_TABLES in "
            ".claude/hooks/pre_tool_use.py. (2) Add ICDEV_FOUNDRY_ENABLED=false to .env(.example) "
            "(default OFF — opt-in autonomy). (3) Create args/foundry_config.yaml skeleton: "
            "cadence_hours, sources (innovation/creative/research/genesis/telemetry toggles + per-source "
            "signal caps), synthesis (min_cluster_size, llm_assist.enabled:false), novelty "
            "(catalog_sources: canvas_registry/manifests/goals, min_novelty), scoring (weights + "
            "min_composite), rate_limits (max_concepts_per_cycle, max_active_projects), circuit "
            "(vv_fail_rate, window), self_vet (require_integrity_gate:true, require_security_gate:true), "
            "deliberation (enabled:true, approval_mode:'cod', synthesis_mode:'cot', "
            "function:'capability_deliberation', min_confidence, defer_to_score_on_fallback:true). "
            "Requires acf-db-03."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": "acf-db-03",
    },

    # =========================================================================
    # EPIC harvest — pull signals from existing engines (no new scanners)
    # =========================================================================
    {
        "id": "acf-harvest-01",
        "title": "harvester.py — pull innovation + creative + research signal stores -> foundry_signals",
        "description": (
            "Create tools/foundry/harvester.py. harvest(run_id) -> list[signal]. Read EXISTING engine "
            "stores via their public modules (do not re-scan the web): innovation signals/trends "
            "(tools/innovation/signal_ranker + trend_detector), creative pain_points/gaps "
            "(tools/creative/gap_scorer + pain_extractor), research challenges/dossiers "
            "(tools/research/challenge_scorer + dossier_generator). Normalize each into the "
            "foundry_signals shape (source_engine, source_ref, theme, raw_score, keywords) and persist "
            "(append-only) under run_id, stamping tenant_id/classification. Respect per-source caps from "
            "args/foundry_config.yaml. Tests with a seeded innovation/creative/research fixture row each "
            "produce a normalized foundry_signals row. Requires acf-db-04."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-db-04",
    },
    {
        "id": "acf-harvest-02",
        "title": "harvester.py — add Genesis predictions + introspective telemetry + cross-source dedup",
        "description": (
            "Extend harvester.py with two more sources: Genesis predictions (oracle_prediction / "
            "kg self-awareness gap nodes) and internal telemetry via "
            "tools/innovation/introspective_analyzer (gate failures, unused tools, slow pipelines, "
            "knowledge gaps) — each normalized into foundry_signals. Add cross-source dedup (SHA-256 of "
            "normalized theme+keywords) so the same signal from two engines collapses to one. Tests: a "
            "duplicate signal across two sources is stored once; a telemetry gap becomes a signal. "
            "Requires acf-harvest-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "acf-harvest-01",
    },

    # =========================================================================
    # EPIC synth — cluster -> concepts, novelty gate, scoring (the core IP)
    # =========================================================================
    {
        "id": "acf-synth-01",
        "title": "synthesizer.py — cluster signals into coherent net-new concept candidates",
        "description": (
            "Create tools/foundry/synthesizer.py. synthesize(run_id) -> list[concept]. Cluster the "
            "run's foundry_signals into candidate product concepts using DETERMINISTIC keyword "
            "co-occurrence (reuse the trend_detector co-occurrence idea), min_cluster_size from config. "
            "For each cluster emit a concept draft {name, slug, problem_statement, proposed_capability, "
            "target_users, cluster_signal_ids}. OPTIONAL Chain-of-Thought synthesis assist (delegated to "
            "tools/foundry/deliberator.reason_concept in acf-deliberate-01, which wraps "
            "tools/llm/chain_orchestrator CoT reasoner->critic->synthesizer) refines the prose when "
            "config.deliberation.enabled, with the deterministic cluster summary as the ALWAYS-present "
            "fallback (air-gap safe). Persist concepts status='proposed'. Tests: N clustered signals "
            "about one theme produce exactly one concept; LLM-disabled path still yields a concept. "
            "Requires acf-harvest-02."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-harvest-02",
    },
    {
        "id": "acf-synth-02",
        "title": "novelty_gate.py — dedup vs capability catalog -> novelty score (forbid incremental)",
        "description": (
            "Create tools/foundry/novelty_gate.py. score_novelty(concept) -> {novelty_score 0..1, "
            "nearest, is_duplicate}. Build a capability catalog from EXISTING assets: "
            "tools/canvas/canvas_registry (active canvases), tools/manifest/*.md (tool names/descs), "
            "goals/manifest.md (goal workflows). Compute similarity (token/keyword overlap; optional "
            "embedding via tools/llm router if enabled, deterministic fallback) between the concept's "
            "capability and the catalog. novelty_score = 1 - max_similarity. If novelty_score < "
            "config.novelty.min_novelty, mark the concept rejected with reject_reason='duplicate' (or "
            "'low_novelty'). THIS IS THE DIFFERENTIATOR FROM ORACLE: incremental rehashes of existing "
            "canvases are filtered out here. Tests: a concept identical to an existing canvas scores ~0 "
            "and is rejected; a genuinely new one scores high. Requires acf-synth-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-synth-01",
    },
    {
        "id": "acf-synth-03",
        "title": "scorer.py — composite scoring (novelty x market x fit / effort - risk) + thresholds",
        "description": (
            "Create tools/foundry/scorer.py. score(concept) -> {market_score, fit_score, "
            "effort_estimate, compliance_risk, composite_score, status, reject_reason?}. market_score "
            "from aggregated signal raw_scores/frequency; fit_score from overlap with ICDEV domains "
            "(FORGE layers / compliance / security); effort_estimate from concept scope heuristics; "
            "compliance_risk penalty (e.g. PII/classified-data handling). composite = weighted per "
            "constants.SCORE_WEIGHTS, gated by novelty_score from acf-synth-02. Below "
            "config.scoring.min_composite -> status='rejected', reject_reason='below_threshold'; else "
            "status='scored'. Persist all sub-scores to foundry_concepts. Tests: a high-novelty "
            "high-signal concept passes; a low-signal one is rejected with reason. Requires acf-synth-02."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-synth-02",
    },

    # =========================================================================
    # EPIC deliberate — CoT/CoD multi-LLM decision layer (the autonomous arbiter)
    # =========================================================================
    {
        "id": "acf-deliberate-01",
        "title": "deliberator.py — CoD go/no-go approval gate + CoT synthesis assist (deterministic fallback)",
        "description": (
            "Create tools/foundry/deliberator.py wrapping tools/llm/chain_orchestrator.ChainOrchestrator. "
            "Two public functions, both deterministic-first / air-gap-safe: "
            "(1) reason_concept(cluster, draft) -> {concept, rationale, trace_id}: optional Chain-of-"
            "Thought (invoke_chain_of_thought, function from config.deliberation.function) that refines a "
            "deterministic concept draft (reasoner->critic->synthesizer); returns the input draft "
            "unchanged when deliberation disabled or no provider. "
            "(2) deliberate_concept(concept, signals, scores) -> {decision in (build,reject,defer), "
            "confidence 0..1, rationale, trace_id}: the GO/NO-GO APPROVAL GATE via Chain-of-Debate "
            "(invoke_chain_of_debate) — distinct debater lenses (market advocate, technical-feasibility "
            "skeptic, compliance/risk officer, novelty/differentiation critic) -> neutral judge verdict. "
            "It runs ONLY on concepts that already PASSED the deterministic novelty+score thresholds "
            "(acf-synth-02/03), so it is the final qualitative gate before autonomous build and never "
            "burns debate cost on obvious rejects. Persist the judge verdict + cot_trace_id on "
            "foundry_concepts; chain telemetry lands in llm_chain_telemetry automatically. FALLBACK: when "
            "chain_orchestration disabled / LLM unavailable / cost-cap hit, and "
            "config.deliberation.defer_to_score_on_fallback, return decision='build' iff the deterministic "
            "composite passed (i.e. the score gate stands alone) — ACF NEVER blocks on LLM availability. "
            "CONFIG ALREADY WIRED (do NOT re-add): args/llm_config.yaml has the 'capability_deliberation' "
            "function route + cod.per_function.capability_deliberation (num_debaters:4, "
            "debater_pool_role:'acf_deliberation_debaters' = 10 distinct Ollama-cloud models + local "
            "fallback, judge_role:'acf_deliberation_judge') + the matching cot.per_function override. "
            "Just call ChainOrchestrator with function='capability_deliberation'. Tests (no real LLM — "
            "monkeypatch the "
            "orchestrator per the shim-aware pattern): a mocked judge 'reject' blocks a score-passing "
            "concept; deliberation-disabled defers to the score gate; a mocked debate records cot_trace_id. "
            "Requires acf-synth-03."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-synth-03",
    },

    # =========================================================================
    # EPIC design — spec + canonical task-graph templater + emitter
    # =========================================================================
    {
        "id": "acf-design-01",
        "title": "spec_generator.py — concept -> spec markdown + canvas contract JSON",
        "description": (
            "Create tools/foundry/spec_generator.py. generate_spec(concept) -> {spec_md, "
            "canvas_contract}. Produce a feature spec (problem, proposed capability, target users, "
            "architecture sketch, DB tables, routes, success criteria) reusing the template approach in "
            "tools/creative/spec_generator + tools/innovation/solution_generator. The canvas_contract is "
            "a structured dict the task-graph templater consumes: {slug, title, env_flag, tables[], "
            "modules[], routes[], iqe_collections[], needs_mcp, needs_reflex}. Persist append-only to "
            "foundry_specs. Optional LLM-assist with deterministic template fallback. Tests: a concept "
            "yields a spec_md + a canvas_contract with >=1 table and >=1 route. Requires acf-synth-03."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-synth-03",
    },
    {
        "id": "acf-design-02",
        "title": "task_graph.py — canonical canvas-epic templater (the SIPA epic skeleton, parameterized)",
        "description": (
            "Create tools/foundry/task_graph.py. build_task_graph(concept, canvas_contract) -> "
            "list[task] where each task = {id, title, description, task_type, priority, "
            "depends_on_task_id}. Emit the CANONICAL full-canvas epic skeleton, parameterized by the "
            "contract, mirroring tools/kanban/seed_sipa_integrity.py: db (constants, db/init_db, register "
            "in init_icdev_db+migration+conftest, append-only+.env+config), core (one task per module in "
            "contract.modules), engine (orchestration + CLI), dash (blueprint, templates+mirror+IQE "
            "widget, app.py register+nav+start.md, IQE adapter+map+seed queries — the 8-component gate), "
            "mcp (if needs_mcp), reflex (if needs_reflex), doc (goal+manifest+commands), vv (CodeLens, "
            "Coherence+ruff+pytest, companion sync+health, Playwright E2E). IDs use "
            "f'{slug}-{epic}-{n:02d}', single-parent linear chain, root depends_on_task_id=None. Each "
            "build task description MUST embed the project Guardrails (get_connection/RLS, "
            "constants-derived CHECKs, append-only registration, 8-component gate) AND set an "
            "integrity_gate marker so the dispatcher self-vets via SIPA. Tests: a contract with 2 modules "
            "+ mcp + reflex yields a valid chained graph whose every depends_on_task_id resolves. "
            "Requires acf-design-01 (and acf-deliberate-01 must have approved the concept first)."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-design-01",
    },
    {
        "id": "acf-design-03",
        "title": "seeder.py — emit task graph to kanban_tasks + project sync + provenance + projects.yaml",
        "description": (
            "Create tools/foundry/seeder.py. emit(concept, tasks, dry_run=False) -> {emitted, skipped}. "
            "Insert each task into kanban_tasks (status='scheduled', project_id=concept.slug, "
            "classification='CUI') idempotently (skip existing id) via get_connection — exactly like "
            "seed_sipa_integrity.seed(). Record each (concept_id, kanban_task_id, epic, seq) in "
            "foundry_tasks_emitted (append-only, provenance). Call tools/project/kanban_project_sync to "
            "auto-populate args/projects.yaml, and ensure the project key obeys the prefix-collision rule "
            "(no task_prefix is a prefix of another). Flip concept.status='decomposed'. Tests (SQLite): "
            "emit writes N kanban_tasks + N foundry_tasks_emitted; a second emit skips existing. "
            "Requires acf-design-02."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-design-02",
    },

    # =========================================================================
    # EPIC engine — orchestration + CLI + rate limits + circuit breaker + self-vet
    # =========================================================================
    {
        "id": "acf-engine-01",
        "title": "engine.py — run_cycle() orchestration harvest->synth->novelty->score->design->emit",
        "description": (
            "Create tools/foundry/engine.py. run_cycle(dry_run=False) -> {run_id, harvested, "
            "concepts_proposed, concepts_approved, tasks_emitted, status}. Open a foundry_runs row, then "
            "orchestrate harvester.harvest -> synthesizer.synthesize -> novelty_gate.score_novelty -> "
            "scorer.score -> (for deterministic survivors only) deliberator.deliberate_concept CoD "
            "GO/NO-GO gate -> (for decision=='build') spec_generator.generate_spec -> "
            "task_graph.build_task_graph -> seeder.emit, persisting at each step (including the judge "
            "verdict + cot_trace_id) and finalizing the foundry_runs row. synthesizer.synthesize is "
            "CoT-assisted via deliberator.reason_concept. All DB access via get_connection with tenant_id/"
            "classification. Tests: a seeded signal set drives a full cycle that emits a concept's tasks "
            "(deliberator mocked 'build'); a duplicate-only set emits zero; a mocked 'reject' verdict "
            "blocks a score-passing concept. Requires acf-design-03; secondary acf-deliberate-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-design-03",
    },
    {
        "id": "acf-engine-02",
        "title": "engine.py CLI — --run/--status/--dry-run/--json + rate limits enforced",
        "description": (
            "Add an argparse CLI / __main__ to engine.py: "
            "`python tools/foundry/engine.py --run [--dry-run] [--json]`, `--status [--json]` (recent "
            "runs + active foundry projects + pipeline counts). Enforce rate limits BEFORE emit: cap "
            "approved concepts at config.rate_limits.max_concepts_per_cycle, and skip emitting if active "
            "foundry projects (distinct slugs with un-done tasks) >= max_active_projects — recording "
            "status='rate_limited' on the run. --dry-run runs the full pipeline but seeder does not "
            "write. Tests invoke main() and assert rate-limit short-circuit + JSON shape. "
            "Requires acf-engine-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-engine-01",
    },
    {
        "id": "acf-engine-03",
        "title": "engine.py — circuit breaker + SIPA/self-vet gate wiring on emitted tasks",
        "description": (
            "(1) Circuit breaker: before emitting, compute the recent V&V failure rate of "
            "ACF-spawned projects from foundry_outcomes (window from config); if it exceeds "
            "config.circuit.vv_fail_rate, abort the cycle with status='circuit_open' and open a single "
            "kanban HITL card so a human can clear it. (2) Self-vet: ensure every emitted build task "
            "carries the integrity_gate marker and that args/security_gates.yaml has a 'Foundry Self-Vet' "
            "gate requiring SIPA (tools/integrity/engine.py --gate) + security + coherence to pass on "
            "ACF-generated code before merge (require_* from config.self_vet). Tests: a high fail-rate "
            "fixture opens the breaker + writes exactly one card; emitted tasks carry the gate marker. "
            "Requires acf-engine-02; secondary: SIPA (tools/integrity) must be present."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "acf-engine-02",
    },

    # =========================================================================
    # EPIC learn — outcome capture + weight tuning (closed loop)
    # =========================================================================
    {
        "id": "acf-learn-01",
        "title": "learner.py — capture build outcomes -> foundry_outcomes -> tune scorer weights",
        "description": (
            "Create tools/foundry/learner.py. record_outcomes() reconciles each decomposed concept's "
            "emitted kanban tasks (via foundry_tasks_emitted) against current kanban_tasks status: all "
            "done -> outcome 'shipped'; vv task failed -> 'vv_fail'; etc., persisted append-only to "
            "foundry_outcomes and reflected in foundry_concepts.status (shipped/failed). tune_weights() "
            "adjusts constants.SCORE_WEIGHTS persisted in args/foundry_config.yaml toward features of "
            "shipped (vs failed) concepts (bounded step, like the Genesis goal-learner) — never below/"
            "above safe bounds. Tests: a fully-done concept yields outcome 'shipped'; a vv_fail concept "
            "nudges weights within bounds. Requires acf-engine-03."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "acf-engine-03",
    },

    # =========================================================================
    # EPIC dash — /foundry canvas (8-component completeness gate)
    # =========================================================================
    {
        "id": "acf-dash-01",
        "title": "blueprint.py — create_foundry_blueprint() factory: page routes + JSON API",
        "description": (
            "Create tools/foundry/blueprint.py with create_foundry_blueprint() (returns None if "
            "ICDEV_FOUNDRY_ENABLED is false), url_prefix='/foundry', before_request init_db. Page "
            "routes: / (concept pipeline board grouped by status: proposed/scored/approved/decomposed/"
            "building/shipped/rejected, plus recent runs) and /<concept_id> (detail: signals, scores, "
            "spec_md, emitted tasks with live kanban status, outcomes). JSON API: POST "
            "/api/foundry/run (trigger a cycle; dry_run flag), GET /api/foundry/concepts, GET "
            "/api/foundry/concept/<id>, GET /api/foundry/runs. Reads run under g.security_context (RLS); "
            "writes audit via engine. Import-safe with try/except like other canvases. Tests hit the "
            "routes with the Flask test client. Requires acf-learn-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-learn-01",
    },
    {
        "id": "acf-dash-02",
        "title": "Templates — foundry/index.html + concept.html (+ icdev/ mirror, IQE widget)",
        "description": (
            "Create tools/dashboard/templates/foundry/index.html (Kanban-style pipeline board of "
            "concepts by status with composite/novelty score badges + a 'Run Cycle' button + recent runs "
            "table) and concept.html (detail: clustered signals, sub-scores, spec_md rendered, emitted "
            "task list linking to /kanban with live status, outcomes timeline). Extend base.html, "
            "CUI//SP-CTI banner, dark mode consistent with other canvases. Use value|round(0)|int (never "
            "'%%.0f'|format). Include {% include 'includes/iqe_query_widget.html' %} and pass "
            "iqe_canvas='foundry'. Mirror both templates to icdev/tools/dashboard/templates/foundry/ (or "
            "rely on companion sync). Requires acf-dash-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "acf-dash-01",
    },
    {
        "id": "acf-dash-03",
        "title": "Register blueprint in app.py + nav link (base.html) + start.md Pages line",
        "description": (
            "(1) Register the blueprint in tools/dashboard/app.py (_CANVAS_DEFS entry "
            "'foundry'/'ICDEV_FOUNDRY_ENABLED'/'tools.foundry.blueprint'/'create_foundry_blueprint'); "
            "leave default OFF unless flag set. (2) Add a nav link (e.g. under an Autonomy/Genesis menu) "
            "in base.html pointing to /foundry. (3) Add /foundry and /foundry/<id> to the Pages: line in "
            ".claude/commands/start.md (the route verifier reads the MAIN checkout start.md). Verify the "
            "dashboard starts with no ImportError and /foundry renders. Requires acf-dash-02."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "acf-dash-02",
    },
    {
        "id": "acf-dash-04",
        "title": "IQE — adapters/foundry.py + _CANVAS_MAP + PATH_CANVAS + /api/iqe-query + seed queries",
        "description": (
            "Full IQE integration (8th completeness component): create tools/iqe/adapters/foundry.py "
            "registering read-only collections returning REAL rows (foundry.concepts, foundry.signals, "
            "foundry.runs, foundry.outcomes) — mirror tools/iqe/adapters/ai_augmentation.py. Add the "
            "'foundry' entry to _CANVAS_MAP in tools/dashboard/app.py and a PATH_CANVAS entry "
            "[/^\\/foundry/, 'foundry'] in base.html. Add POST /foundry/api/iqe-query to the blueprint. "
            "Add >=3 seed queries in context/iqe/queries/foundry/*.iqe (e.g. approved concepts this week, "
            "rejected concepts by reason, shipped vs failed outcomes). Requires acf-dash-03."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "acf-dash-03",
    },

    # =========================================================================
    # EPIC mcp — MCP gateway registration
    # =========================================================================
    {
        "id": "acf-mcp-01",
        "title": "MCP — register foundry_run + foundry_status tools in the gateway",
        "description": (
            "Register ACF in the MCP gateway: add foundry_run(dry_run=False) and foundry_status() to "
            "tools/mcp/tool_registry.py and the corresponding handlers in tools/mcp/gap_handlers.py, "
            "reusing the security/knowledge MCP server pattern (deterministic, JSON in/out, no shell). "
            "foundry_run triggers engine.run_cycle (respecting rate limits); foundry_status returns "
            "pipeline counts. Tests: registry lists the tools; handler returns JSON for a fixture run. "
            "Requires acf-dash-04."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "acf-dash-04",
    },

    # =========================================================================
    # EPIC reflex — Genesis continuous cadence (the autonomous heartbeat)
    # =========================================================================
    {
        "id": "acf-reflex-01",
        "title": "foundry_cycle.py — Genesis reflex: run a foundry cycle on cadence (continuous autonomy)",
        "description": (
            "Create tools/genesis/reflexes/foundry_cycle.py following an existing reflex "
            "(e.g. tools/genesis/reflexes/ndc_topology_drift.py: CADENCE_HOURS from "
            "args/foundry_config.yaml, run(ctx, conn) -> {harvested, concepts_proposed, tasks_emitted, "
            "status}). It calls tools/foundry/engine.run_cycle() when ICDEV_FOUNDRY_ENABLED is true, "
            "honoring rate limits + circuit breaker, and is a clean no-op (status skipped) when the flag "
            "is off. Register the reflex in ALL THREE: REFLEX_NAMES in tools/genesis/daemon.py, the "
            "reflex registry (tools/genesis/reflex_registry.py), and genesis config (per the "
            "daemon-registration gotcha). Use 127.0.0.1 not localhost for any probe. Per-reflex watchdog "
            "timeout honored. Tests: flag-off -> no-op; flag-on with seeded signals -> emits. "
            "Requires acf-mcp-01."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "acf-mcp-01",
    },

    # =========================================================================
    # EPIC doc — goal workflow + manifest shard + commands reference
    # =========================================================================
    {
        "id": "acf-doc-01",
        "title": "goals/autonomous_capability_foundry.md + manifest shard + commands.md registration",
        "description": (
            "(1) Create goals/autonomous_capability_foundry.md describing the harvest->synth->novelty->"
            "score->design->decompose->queue->learn loop, the guardrails, and when/how a human clears "
            "the circuit breaker; add a row to goals/manifest.md. (2) Create the "
            "tools/manifest/autonomous-capability-foundry.md shard documenting every tools/foundry/ "
            "module + the Genesis reflex + IQE adapter, and add its index line to tools/manifest.md. "
            "(3) Add the ACF CLI commands to docs/reference/commands.md. Requires acf-reflex-01."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "acf-reflex-01",
    },

    # =========================================================================
    # EPIC vv — CodeLens + Coherence + sync/health + Playwright E2E
    # =========================================================================
    {
        "id": "acf-vv-01",
        "title": "Comprehensive CodeLens — every tools/foundry/ module PASSes the quality gate",
        "description": (
            "Run tools/analysis/code_lens.py --file <m> --json on EVERY module under tools/foundry/ "
            "(constants, db/init_db, harvester, synthesizer, novelty_gate, scorer, deliberator, "
            "spec_generator, task_graph, seeder, engine, learner, blueprint) and on "
            "tools/iqe/adapters/foundry.py and "
            "tools/genesis/reflexes/foundry_cycle.py. Refactor any module that does not PASS "
            "(maintainability >= 0.6, zero critical smells, no high cyclomatic complexity) without "
            "changing behavior (tests stay green). Record before/after gate results in the completion "
            "notes. Requires acf-doc-01."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "acf-doc-01",
    },
    {
        "id": "acf-vv-02",
        "title": "Comprehensive Coherence — coherence_checker --all --fix --gate green + ruff + pytest",
        "description": (
            "Run the full coherence gate and make it green for ACF: `python tools/workflow/"
            "coherence_checker.py --all --fix --gate`. Resolve every check touching ACF — schema<->code "
            "parity (foundry_* INSERTs match CREATE TABLE), manifest (new modules documented), "
            "append-only (5 foundry_* tables registered), route uniqueness + nav/route parity "
            "(/foundry), sandbox coverage (harvester ingests engine-store DATA only — add the OPT-58 "
            "decision entry), security_context (RLS auto-wiring intact), IQE wiring, karpathy sync. Then "
            "`ruff check tools/foundry/` clean and `pytest tests/test_foundry_*.py -v` green (run "
            "api_surface_extractor before writing/fixing tests). Requires acf-vv-01."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "acf-vv-01",
    },
    {
        "id": "acf-vv-03",
        "title": "Companion sync (foreground) + health check + full suite green",
        "description": (
            "Run `python tools/dx/companion.py --sync --write --json` IN THE FOREGROUND (never "
            "background — background sync has deleted uncommitted work) so the foundry templates/skills "
            "propagate to all AI platforms. Run `python tools/testing/health_check.py --json` (env + DB "
            "+ deps green). Run the full `pytest tests/ -q` to confirm no regressions from the foundry_* "
            "schema additions. On Windows set $env:PYTHONPATH='C:\\AI\\ICDev'. Requires acf-vv-02."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "acf-vv-02",
    },
    {
        "id": "acf-vv-04",
        "title": "Playwright E2E — run a cycle, concept proposed->scored->tasks emitted on board + screenshots",
        "description": (
            "End-to-end via Playwright MCP against the running dashboard (ICDEV_FOUNDRY_ENABLED=true, "
            "with a seeded signal fixture so a cycle is deterministic): (1) open /foundry; (2) click 'Run "
            "Cycle' (or POST /api/foundry/run dry_run=false); (3) confirm at least one concept appears, "
            "moves to 'scored'/'approved', and a duplicate/low-novelty concept is shown 'rejected' with "
            "reason; (4) open the concept detail and confirm emitted kanban tasks are listed and visible "
            "on /kanban; (5) exercise the IQE widget with a seed query ('approved concepts') and confirm "
            "real rows; (6) screenshots to playwright/screenshots/foundry-*.png. Write the results doc "
            "docs/features/phase-acf-autonomous-capability-foundry-results.md. All gates green. "
            "Requires acf-vv-03."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "acf-vv-03",
    },
]


def seed(dry_run: bool = False) -> int:
    conn = get_connection()
    now = _now()
    inserted = 0
    skipped = 0
    try:
        for t in TASKS:
            existing = conn.execute(
                "SELECT id FROM kanban_tasks WHERE id=?", (t["id"],)
            ).fetchone()
            if existing:
                skipped += 1
                continue
            if dry_run:
                inserted += 1
                continue
            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority, status,
                    scheduled_at, created_at, updated_at, depends_on_task_id,
                    project_id, classification)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    t["id"],
                    t["title"],
                    t["description"],
                    t.get("task_type", "build"),
                    t.get("priority", "medium"),
                    "scheduled",
                    now,
                    now,
                    now,
                    t.get("depends_on_task_id"),
                    PROJECT_ID,
                    "CUI",
                ),
            )
            inserted += 1
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    verb = "Would seed" if dry_run else "Seeded"
    print(f"{verb} {inserted} tasks, skipped {skipped} existing (total defined: {len(TASKS)}).")
    return inserted


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed ACF (Autonomous Capability Foundry) kanban tasks")
    ap.add_argument("--dry-run", action="store_true", help="Print only, no DB write")
    args = ap.parse_args()
    seed(dry_run=args.dry_run)
