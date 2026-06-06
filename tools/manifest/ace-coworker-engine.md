# ANVIL Co-Worker Engine (ACE)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

Dynamic agentic co-worker teams assembled at runtime from declarative YAML role
templates, communicating via delegation / creator-verifier / negotiation /
broadcast primitives over the existing mailbox transport. Built on existing
ICDEV infrastructure (A2A, RICOAS intake, HITL workflow engine, CodeLens,
Genesis reflexes, LLM Router). Canvas at `/coworker` (env `ICDEV_ACE_ENABLED`,
default off). Authored 2026-06-06 (task ace-infra-03 — reconstructed; the shard
was flagged done but existed on no branch).

## Core Engine (`icdev/tools/ace/`)
| Tool | File | Description | Key API | Notes |
|------|------|-------------|---------|-------|
| Controller | icdev/tools/ace/controller.py | Singleton entry point: classify problem → assemble team → spawn co-worker threads | `ACEController.get_instance()`, `.launch(problem_text, trigger_source, trigger_ref, user_id, project_id)`, `.status(id)`, `.abort(id)`, `.list_roles()` | Non-blocking launch; returns instance_id |
| Role Loader | icdev/tools/ace/role_loader.py | Load + validate `args/ace/roles/*.yaml`, 60s hot-reload | `RoleLoader().get_role()`, `.list_roles()`, `.reload()` | Raises `RoleNotFoundError` |
| Problem Classifier | icdev/tools/ace/problem_classifier.py | Oracle lens: problem text → ranked `TeamManifest` of `RoleSlot`s | `ProblemClassifierLens.analyze/score/propose` | Fallback team = ai_developer + qa_manager |
| Team Assembler | icdev/tools/ace/team_assembler.py | `TeamManifest` → `CoWorkerSpec` list + `ace_instances`/`ace_coworkers` rows | `TeamAssembler.assemble(manifest, instance_id, context)` | Respects `MAX_TEAM_SIZE`; registers a coordination session |
| Message Bus | icdev/tools/ace/message_bus.py | ACE message routing over `tools/agent/mailbox.py` | `.send/.broadcast/.negotiate/.poll_inbox` | Raises `NegotiationFailedError` after max rounds |
| Step Executor | icdev/tools/ace/step_executor.py | Dynamic dotted-path tool invocation with permission + trust-kernel gating | `StepExecutor().run(step, context, spec, trust_kernel)` | Raises `ToolPermissionDeniedError`, `TrustKernelDeniedError`; emits `ace_audit_log` |
| Co-Worker Thread | icdev/tools/ace/coworker_thread.py | Per-co-worker execution unit: step loop, inbox polling, HITL suspend/resume | `CoWorkerThread(spec, instance_id, message_bus, trust_kernel)` | `HITLGate` poll loop |
| Genesis Reflex | icdev/tools/ace/genesis_reflex.py | SUPPORT-tier reflex: detect + escalate stale instances | `run(config, db_conn)` | Registered as `ace_team_monitor` |
| Blueprint | icdev/tools/ace/blueprint.py | Flask blueprint, 9 routes under `/coworker` | `ace_bp` | Registered in `tools/dashboard/app.py` `_CANVAS_DEFS` |
| DB Init | icdev/tools/ace/db/init_db.py | Create 6 canvas tables (idempotent) | `init()` | Uses `get_canvas_connection('ICDEV_ACE_DB_URL')` |

## Args / Config
- `args/ace/ace_config.yaml` — `max_team_size`, `max_negotiation_rounds`, `hitl_threshold`, `trust_tier_default`, `stale_instance_hours`, `hot_reload_roles`.
- `args/ace/roles/*.yaml` — role templates (`ai_developer`, `qa_manager`).
- `args/ace/prompt_chains/ace_developer_analysis.yaml` — 4-step analysis chain.
- `args/ace/hitl_templates/ace_developer_review.yaml` — developer review gate.

## DB Tables (canvas — no classification/tenant_id RLS columns)
`ace_instances`, `ace_coworkers`, `ace_messages`, `ace_artifacts`,
`ace_agent_workflows`, `ace_audit_log` (append-only — in `APPEND_ONLY_TABLES`).
Schema of record: `icdev/tools/ace/db/init_db.py` (id / state / trust_tier —
**not** instance_id / status). The `tests/conftest.py` ACE block MUST mirror it.

## IQE
- Adapter: `tools/iqe/adapters/ace.py` — collections `ace.instances`, `ace.coworkers`, `ace.messages`.
- Seed queries: `context/iqe/queries/ace/`.

## CLI
```bash
python -m icdev.tools.ace.controller --launch "problem text" [--json]
python -m icdev.tools.ace.controller --status <instance_id> [--json]
python -m icdev.tools.ace.controller --abort <instance_id>
python -m icdev.tools.ace.controller --list-roles
python tools/kanban/seed_ace_kanban.py [--dry-run]
ICDEV_ACE_ENABLED=true   # enable the /coworker canvas
```

## Goal
`goals/ace_coworker.md`.
