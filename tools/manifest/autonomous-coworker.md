# Autonomous Coworker Manifest — NOVA Initiative
## CUI // SP-CTI

NOVA (Next Operational Value Agency) synthesizes insights from three external
research repos (hermes-agent-self-evolution, OpenClaw, hexo-ai/sia) into
ICDEV™ to create a truly autonomous, self-learning digital coworker platform.

---

## ECHO — Execution Tracing & Reflexion Loop

| Tool | Location | Purpose |
|------|----------|---------|
| `trace_logger` | `tools/workflow/trace_logger.py` | Open/log/close structured execution traces for kanban task dispatches. Tables: `agent_execution_traces` (append-only). |
| `reflexion_agent` | `tools/workflow/reflexion_agent.py` | Post-completion improvement generator. Reads traces + lesson_learned outcomes → generates `agent_improvement_artifacts`. Gated by `ICDEV_HARNESS_COLEARN=true`. |
| `reflexion_loop reflex` | `tools/genesis/reflexes/reflexion_loop.py` | Weekly Genesis reflex. Runs batch reflexion pass → creates amendment kanban cards for low-success-rate task_types. |
| `improvement_fitness` | `tools/workflow/improvement_fitness.py` | Shared deterministic fitness rubric for `agent_improvement_artifacts`. `score_improvement()` scores a CANDIDATE improvement (→ `composite_score`); `score_traces()` scores the STATUS QUO from traces (→ `baseline_score`). Library — no CLI. Used by `reflexion_agent` and NOVA SELA so GEPA compares like with like. |

**CLI:**
```bash
# Test trace logging
python -c "from tools.workflow.trace_logger import start_trace, log_event, close_trace; t=start_trace('test-001','build','icdev-build'); log_event(t,'milestone',{'step':'RED'}); close_trace(t,'success'); print('ok')"

# Run reflexion batch (requires ICDEV_HARNESS_COLEARN=true)
ICDEV_HARNESS_COLEARN=true python -c "from tools.workflow.reflexion_agent import run_batch_reflexion; print(run_batch_reflexion(dry_run=True))"
```

---

## SOUL — Coworker Identity & Cross-Session Memory

| Tool | Location | Purpose |
|------|----------|---------|
| `soul_manager` | `icdev/tools/ace/soul_manager.py` | Builds identity preamble (SOUL.md + TOOLS.md + MEMORY.md) injected into dispatch. Records and prunes per-role learned facts. Tables: `ace_coworker_memory`. |
| Role identity files | `tools/ace/roles/<role_id>/` | SOUL.md (values/style), TOOLS.md (capabilities), MEMORY.md (learned facts). Exists for: `security_analyst`, `ai_developer`, `compliance_manager`, `data_analyst`, `devops_engineer`, `qa_manager`, `requirements_engineer`. |

**CLI:**
```bash
# Build identity preamble for a role
python -c "from icdev.tools.ace.soul_manager import build_identity_preamble; print(build_identity_preamble('security_analyst'))"

# Record a learning
python -c "from icdev.tools.ace.soul_manager import record_learning; print(record_learning('ai_developer', 'SQLite tests must use get_connection() not sqlite3.connect()'))"
```

---

## TRUST — Trust Calibration & Progressive Autonomy

| Tool | Location | Purpose |
|------|----------|---------|
| `trust_calibrator` | `tools/ace/trust_calibrator.py` | Records trust events (success/failure/hitl_escalation/timeout/phantom). Computes Bayesian trust updates. Weekly recalibration from kanban outcomes. Tables: `ace_trust_ledger` (append-only). |

**Trust bands:**
- `< 0.3` Probationary — all HITL, dispatch paused
- `0.3–0.6` Supervised — standard HITL, sequential dispatch
- `0.6–0.8` Trusted — HITL on irreversible only, parallel(2)
- `≥ 0.8` Autonomous — routine auto-approved, parallel(4)

**CLI:**
```bash
# Record a trust event
python -c "from tools.ace.trust_calibrator import record_trust_event; print(record_trust_event('security_analyst', 'success', 'task-001'))"

# Get dispatch config for a role
python -c "from tools.ace.trust_calibrator import get_dispatch_config; print(get_dispatch_config('ai_developer'))"

# Get trust summary
python -c "from tools.ace.trust_calibrator import get_trust_summary; print(get_trust_summary())"

# Run weekly recalibration
python -c "from tools.ace.trust_calibrator import run_weekly_recalibration; print(run_weekly_recalibration())"
```

---

## SELA — Skill & Goal Self-Evolution

| Tool | Location | Purpose |
|------|----------|---------|
| `eval_builder` | `tools/evolution/eval_builder.py` | Builds EvalDataset from golden JSONL / kanban history / synthetic LLM examples. |
| `fitness` | `tools/evolution/fitness.py` | Multi-dimensional LLM judge (correctness, procedure_following, conciseness). Fast heuristic mode for inner loop; full LLM judge for holdout. |
| `artifact_evolver` | `tools/evolution/artifact_evolver.py` | GEPA-style text mutation orchestrator. Loads skill → generates N candidates → validates (size/growth/structure gates) → scores → promotes winner as oracle_prediction. NEVER auto-merges. |
| `skill_generator` | `tools/nova/skill_generator.py` | Auto-generate ICDEV™ skill specs from session history patterns (adapt-hermes-04). Reads memory_entries session turns via FTS5, identifies high-frequency command patterns, generates skill YAML/markdown via scanner-tier LLM (template fallback), and queues results in `agent_improvement_artifacts` for Continuous Harness SELA evaluation. | --analyze, --generate PATTERN, --list-queued, --limit N, --min-count N, --dry-run, --json | {patterns}, {skill_id, queued, spec_preview}, {queued[]} |
| `evolution reflex` | `tools/genesis/reflexes/evolution.py` | Weekly Genesis reflex. Runs SELA on all `icdev-*.md` skills. |

**Config:** `args/nova_sela_config.yaml`
**Golden eval data:** `context/evolution/golden/<skill_name>.jsonl`

**CLI:**
```bash
# Dry-run evolution on one skill
python -c "from tools.evolution.artifact_evolver import evolve_artifact; print(evolve_artifact('icdev-status', dry_run=True))"

# Run full evolution batch (dry-run)
python -c "from tools.evolution.artifact_evolver import evolve_all_skills; print(evolve_all_skills(dry_run=True, limit=3))"

# Build eval dataset
python -c "from tools.evolution.eval_builder import build_dataset; d=build_dataset('icdev-status'); print(len(d.all_examples), 'examples')"
```

---

## Tables Summary

| Table | Pillar | Append-only | Notes |
|-------|--------|-------------|-------|
| `agent_execution_traces` | ECHO | Yes | Per-task dispatch trace events |
| `agent_improvement_artifacts` | ECHO | Yes | Per-task-type generational improvement text |
| `ace_coworker_memory` | SOUL | No | Per-role learned facts (pruned at 40 facts) |
| `ace_trust_ledger` | TRUST | Yes | Bayesian trust event log |

All append-only tables are registered in `.claude/hooks/pre_tool_use.py::APPEND_ONLY_TABLES`.
All schemas registered in `tests/conftest.py::MINIMAL_ICDEV_SCHEMA`.

---

## Genesis Reflexes Added

| Reflex | Cadence | Pillar |
|--------|---------|--------|
| `reflexion_loop` | Weekly (Sun 02:00 UTC) | ECHO |
| `evolution` | Weekly (Sat 03:00 UTC) | SELA |

Both registered in `tools/genesis/daemon.py::REFLEX_NAMES`.

---

## Related Manifests
- See `tools/manifest/ace.md` for ACE coworker engine details
- See `tools/manifest/workflow-automation.md` for kanban scheduler
- See `tools/manifest/genesis.md` for Genesis daemon reflexes
